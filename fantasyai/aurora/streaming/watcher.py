"""
FileWatcher — polling-based file/directory change detection.

Watches either:

  * A single file — fires on modtime / size change
  * A directory — fires on file add / modify / remove

Polling vs. an OS-level inotify/FSEvents library: we don't want to
add ``watchdog`` (50MB+ when wheels-built across platforms) as a
runtime dep. Polling at 1-second granularity is sufficient for
Aurora's use cases (analyses take 10+ seconds anyway), works
identically on Windows / macOS / Linux, and has no platform-specific
quirks.

The watcher runs in a daemon thread. ``start()`` is non-blocking;
``stop()`` joins cleanly. Failures in user callbacks are logged but
don't kill the watcher loop.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set

LOG = logging.getLogger(__name__)


WATCHER_EVENT_FILE_ADDED = "file_added"
WATCHER_EVENT_FILE_MODIFIED = "file_modified"
WATCHER_EVENT_FILE_REMOVED = "file_removed"


class FileWatcherError(Exception):
    pass


@dataclass(frozen=True)
class WatcherEvent:
    """One observed change. Passed to user callbacks."""
    kind: str            # one of WATCHER_EVENT_*
    path: Path
    timestamp: float     # time.time() of detection
    new_mtime: Optional[float] = None
    new_size: Optional[int] = None


# Callback signature.
WatcherCallback = Callable[[WatcherEvent], None]


class FileWatcher:
    """Poll-based watcher for files and directories.

    Usage:

        def on_change(ev: WatcherEvent):
            print(f"{ev.kind}: {ev.path}")

        w = FileWatcher("/data/incoming/", on_change, glob="*.csv",
                         poll_interval_s=2.0)
        w.start()
        # ...
        w.stop()
    """

    def __init__(
        self,
        path: Any,
        callback: WatcherCallback,
        *,
        glob: str = "*",
        recursive: bool = False,
        poll_interval_s: float = 2.0,
        watch_self_only: Optional[bool] = None,
    ):
        self.path = Path(path).expanduser()
        self.callback = callback
        self.glob = glob
        self.recursive = recursive
        self.poll_interval_s = max(0.1, float(poll_interval_s))

        # If caller didn't say, infer: file → watch self; directory → watch children.
        if watch_self_only is None:
            watch_self_only = self.path.is_file()
        self.watch_self_only = watch_self_only

        self._snapshot: Dict[Path, Dict[str, float]] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin polling. Idempotent — calling twice is a no-op."""
        if self._thread and self._thread.is_alive():
            return
        if not self.path.exists():
            raise FileWatcherError(f"watcher target does not exist: {self.path}")
        # Seed the snapshot so we don't fire ADDED for files that
        # already exist when the watcher first starts.
        self._snapshot = self._scan()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True,
            name=f"aurora-watcher[{self.path.name}]",
        )
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 5.0) -> None:
        """Signal the loop to exit and join the thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=join_timeout_s)
        self._thread = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _scan(self) -> Dict[Path, Dict[str, float]]:
        """Take a snapshot of the watched targets. Returns {path: {mtime, size}}.
        Missing files are absent from the dict (not zeroed)."""
        snap: Dict[Path, Dict[str, float]] = {}
        if self.watch_self_only:
            if self.path.exists() and self.path.is_file():
                st = self.path.stat()
                snap[self.path] = {"mtime": st.st_mtime, "size": st.st_size}
            return snap

        if not self.path.is_dir():
            # Path was a dir, then disappeared. Empty snapshot is fine.
            return snap
        iterator = self.path.rglob(self.glob) if self.recursive \
                   else self.path.glob(self.glob)
        for child in iterator:
            try:
                if not child.is_file():
                    continue
                st = child.stat()
                snap[child] = {"mtime": st.st_mtime, "size": st.st_size}
            except OSError:
                # File raced away between glob + stat. Ignore.
                continue
        return snap

    def _loop(self) -> None:
        """The polling loop. Exits when ``_stop_event`` is set."""
        while not self._stop_event.is_set():
            try:
                new_snap = self._scan()
                events = self._diff(self._snapshot, new_snap)
                self._snapshot = new_snap
                for ev in events:
                    try:
                        self.callback(ev)
                    except Exception:
                        LOG.exception(
                            f"watcher callback raised for {ev.kind} {ev.path}",
                        )
            except Exception:
                LOG.exception("watcher loop iteration crashed")
            # Sleep in small increments so stop() is responsive.
            slept = 0.0
            while slept < self.poll_interval_s and not self._stop_event.is_set():
                time.sleep(min(0.1, self.poll_interval_s - slept))
                slept += 0.1

    def _diff(
        self,
        old: Dict[Path, Dict[str, float]],
        new: Dict[Path, Dict[str, float]],
    ):
        """Compute ADDED / MODIFIED / REMOVED events between two snapshots."""
        now = time.time()
        events = []
        for path, info in new.items():
            if path not in old:
                events.append(WatcherEvent(
                    kind=WATCHER_EVENT_FILE_ADDED, path=path,
                    timestamp=now,
                    new_mtime=info["mtime"], new_size=int(info["size"]),
                ))
            else:
                old_info = old[path]
                if (info["mtime"] != old_info["mtime"]
                        or info["size"] != old_info["size"]):
                    events.append(WatcherEvent(
                        kind=WATCHER_EVENT_FILE_MODIFIED, path=path,
                        timestamp=now,
                        new_mtime=info["mtime"], new_size=int(info["size"]),
                    ))
        for path in old.keys():
            if path not in new:
                events.append(WatcherEvent(
                    kind=WATCHER_EVENT_FILE_REMOVED, path=path,
                    timestamp=now,
                ))
        # Deterministic ordering for tests + for users who care.
        events.sort(key=lambda e: (e.kind, str(e.path)))
        return events
