"""
Base class for streaming connectors.

A connector is a thread-managed source that pushes rows into the
IncrementalRunner. The contract is small:

  * ``start()`` / ``stop()`` — lifecycle.
  * ``status()`` — return a ``ConnectorStatus`` snapshot.
  * ``availability()`` classmethod — report whether the dep is
    installed so the frontend can grey out unavailable connectors.

Concrete connectors (Kafka, Postgres CDC) inherit from
``StreamingConnector`` and implement the polling loop.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Tuple


class ConnectorUnavailableError(RuntimeError):
    """Raised when a connector is invoked but its dependency isn't installed."""


@dataclass
class ConnectorStatus:
    """A snapshot of a connector's state."""
    name:           str
    running:        bool
    n_batches:      int = 0
    n_rows_ingested: int = 0
    last_batch_at:  Optional[float] = None
    last_error:     Optional[str] = None
    config_summary: Dict[str, Any] = field(default_factory=dict)
    errors_recent:  List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StreamingConnector(ABC):
    """Base class for non-file streaming sources.

    The connector runs a daemon thread that polls its upstream
    (Kafka, Postgres, …), collects rows into a buffer, and pushes
    batches into the IncrementalRunner via ``runner.ingest()``.

    Subclasses implement ``_poll_once()`` — read one batch from the
    upstream and return a list of dict rows. The base class handles
    threading, batching cadence, and the runner ingest call.
    """

    name = "abstract"

    def __init__(
        self,
        *,
        runner:        Any,
        config:        Dict[str, Any],
        poll_interval_s: float = 1.0,
        batch_size:    int = 100,
    ):
        if not self.available():
            raise ConnectorUnavailableError(
                f"{self.name} connector requires a dependency that's not "
                f"installed. {self.install_hint()}"
            )
        self.runner = runner
        self.config = dict(config or {})
        self.poll_interval_s = float(poll_interval_s)
        self.batch_size = int(batch_size)
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self.status_obj = ConnectorStatus(
            name=self.name, running=False,
            config_summary=self._config_summary(),
        )

    # ------------------------------------------------------------------
    # Hooks for subclasses
    # ------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def available(cls) -> bool:
        """True iff the connector's deps are installed."""

    @classmethod
    @abstractmethod
    def install_hint(cls) -> str:
        """Human-readable install line for the missing dep."""

    @classmethod
    def availability(cls) -> Tuple[str, bool, str]:
        """The tuple the frontend reads to render the connector dropdown."""
        return (cls.name, cls.available(), cls.install_hint())

    @abstractmethod
    def _connect(self) -> None:
        """Establish the upstream connection."""

    @abstractmethod
    def _poll_once(self) -> List[Dict[str, Any]]:
        """Read one batch from the upstream. Return a list of dict rows."""

    @abstractmethod
    def _disconnect(self) -> None:
        """Close the upstream connection."""

    def _config_summary(self) -> Dict[str, Any]:
        """Public-safe subset of the config (no creds!) for status."""
        return {k: v for k, v in self.config.items()
                if "password" not in k.lower()
                and "secret" not in k.lower()
                and "token" not in k.lower()
                and "api_key" not in k.lower()}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self.status_obj.running:
                return
            self._stop_evt.clear()
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True,
                name=f"aurora-{self.name}-connector",
            )
            self.status_obj.running = True
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_evt.set()
            self.status_obj.running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

    def status(self) -> ConnectorStatus:
        return self.status_obj

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        import time
        try:
            self._connect()
        except Exception as e:
            self.status_obj.last_error = f"connect failed: {type(e).__name__}: {e}"
            self.status_obj.errors_recent.append(self.status_obj.last_error)
            self.status_obj.running = False
            return
        try:
            while not self._stop_evt.is_set():
                try:
                    rows = self._poll_once()
                    if rows:
                        self._ingest_batch(rows)
                except Exception as e:
                    msg = f"poll failed: {type(e).__name__}: {e}"
                    self.status_obj.last_error = msg
                    self.status_obj.errors_recent.append(msg)
                    self.status_obj.errors_recent = self.status_obj.errors_recent[-5:]
                # Respect the stop flag during the wait.
                self._stop_evt.wait(self.poll_interval_s)
        finally:
            try:
                self._disconnect()
            except Exception:
                pass
            self.status_obj.running = False

    def _ingest_batch(self, rows: List[Dict[str, Any]]) -> None:
        """Convert a list of dicts to a DataFrame + push to the runner."""
        import time
        try:
            import pandas as pd
            df = pd.DataFrame(rows)
        except Exception:
            return
        try:
            self.runner.ingest(df, source=f"{self.name}:batch")
            self.status_obj.n_batches += 1
            self.status_obj.n_rows_ingested += len(rows)
            self.status_obj.last_batch_at = time.time()
        except Exception as e:
            msg = f"ingest failed: {type(e).__name__}: {e}"
            self.status_obj.last_error = msg
            self.status_obj.errors_recent.append(msg)
            self.status_obj.errors_recent = self.status_obj.errors_recent[-5:]
