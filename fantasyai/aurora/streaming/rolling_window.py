"""
RollingWindowState — fixed-size sliding window over a stream.

For streaming use the analyst usually doesn't want to analyse 5
million observations every time a new row arrives. A rolling window of
the most-recent N (or last T duration) is the right abstraction:
analyses run on the window, get fresh as the window slides forward.

Two policies are supported:

  * **count-based** — keep the last N rows
  * **time-based** — keep all rows whose timestamp is within
    ``window_seconds`` of the newest row

Both are tracked in pure Python so this works without pandas; users
can convert to a DataFrame with ``.to_dataframe()`` when they're
ready to run analyses.

Thread-safe. The watcher pushes from one thread; the runner reads
from another.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, List, Optional, Sequence, Tuple


@dataclass
class RollingWindowState:
    """A sliding window of (timestamp, row_dict) tuples.

    Either ``max_rows`` OR ``max_age_seconds`` (or both) must be set.
    """
    max_rows: Optional[int] = None
    max_age_seconds: Optional[float] = None

    _buffer: Deque[Tuple[float, dict]] = None  # type: ignore
    _lock: threading.Lock = None                # type: ignore

    def __post_init__(self):
        if self.max_rows is None and self.max_age_seconds is None:
            raise ValueError(
                "RollingWindowState requires max_rows OR max_age_seconds"
            )
        self._buffer = deque(maxlen=self.max_rows if self.max_rows else None)
        self._lock = threading.Lock()

    # ---- Add / evict ---------------------------------------------------

    def append(self, timestamp: float, row: dict) -> None:
        """Add a row to the window. Evicts older rows according to the
        configured policy."""
        with self._lock:
            self._buffer.append((float(timestamp), dict(row)))
            self._evict_aged_locked()

    def extend(self, rows: Sequence[Tuple[float, dict]]) -> None:
        """Batch-add. Faster than calling ``append`` in a loop."""
        with self._lock:
            for t, r in rows:
                self._buffer.append((float(t), dict(r)))
            self._evict_aged_locked()

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    # ---- Inspect -------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    def latest(self) -> Optional[Tuple[float, dict]]:
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def oldest(self) -> Optional[Tuple[float, dict]]:
        with self._lock:
            return self._buffer[0] if self._buffer else None

    def snapshot(self) -> List[Tuple[float, dict]]:
        """Atomic snapshot of the current window. Safe to iterate
        outside the lock."""
        with self._lock:
            return list(self._buffer)

    def to_dataframe(self):
        """Render the window as a pandas DataFrame. Timestamps go in a
        ``ts`` column; row keys become the rest. Empty window → empty df."""
        try:
            import pandas as pd
        except ImportError:
            raise RuntimeError("pandas is required for to_dataframe()")
        snap = self.snapshot()
        if not snap:
            return pd.DataFrame()
        out = []
        for t, row in snap:
            r = {"ts": t}
            r.update(row)
            out.append(r)
        return pd.DataFrame(out)

    # ---- Internals ------------------------------------------------------

    def _evict_aged_locked(self) -> None:
        if self.max_age_seconds is None or not self._buffer:
            return
        newest = self._buffer[-1][0]
        cutoff = newest - float(self.max_age_seconds)
        while self._buffer and self._buffer[0][0] < cutoff:
            self._buffer.popleft()
