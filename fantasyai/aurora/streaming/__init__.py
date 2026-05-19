"""
Aurora streaming / continuous mode (Stream 1.4 Phase 1).

The v1.1 Aurora pipeline runs in *batch* mode: pick a CSV, analyse it
once, get a bundle. Many real-world use cases are **continuous** — the
data keeps arriving and the analyst wants Aurora's findings to stay
fresh without re-running the whole pipeline by hand.

Phase 1 of streaming mode ships these primitives:

  * **FileWatcher** — polls a directory or file for changes. When the
    file modtime advances or new files appear in the directory, the
    watcher fires a callback. Pure-Python polling so we don't add a
    ``watchdog`` dependency.

  * **IncrementalRunner** — wraps the standard batch pipeline so it
    can be re-invoked cheaply on the latest data. The runner keeps a
    state cache so re-runs reuse computed work where possible.

  * **RollingWindowState** — when the data is unbounded, downstream
    methods need a window over the most-recent N observations. This
    tracks the window cleanly and notifies subscribers when the
    contents change.

  * **SSE event bus** — Server-Sent Events for the Studio (and any
    MCP client) to subscribe to "new findings" + "regime changed"
    notifications. Wired into the Studio's existing ``/api/state``
    polling cadence; clients with SSE support get push, the rest
    keep polling.

Phase 2 ships:
  * Per-finding **dedupe** so ``new_finding`` events only fire for
    genuinely new findings, not every poll cycle.
  * **Decision Contracts bridge** — auto-fire contracts when a
    streaming finding matches their trigger predicate.

Phase 3+ will add: Kafka topic ingestion, Postgres CDC, BOCPD-style
truly-online detectors.
"""
from __future__ import annotations

from .watcher import (
    FileWatcher,
    FileWatcherError,
    WatcherEvent,
    WATCHER_EVENT_FILE_ADDED,
    WATCHER_EVENT_FILE_MODIFIED,
    WATCHER_EVENT_FILE_REMOVED,
)
from .runner import (
    IncrementalRunner,
    IncrementalResult,
)
from .rolling_window import RollingWindowState
from .events import (
    StreamEvent,
    StreamEventBus,
    EVENT_NEW_FINDING,
    EVENT_REGIME_CHANGED,
    EVENT_WINDOW_ADVANCED,
    EVENT_HEARTBEAT,
)
from .dedupe import (
    FindingDedupeStore,
    finding_identity,
)
from .contracts_bridge import (
    evaluate_finding_against_contracts,
    make_bridge,
)

__all__ = [
    # Watcher
    "FileWatcher",
    "FileWatcherError",
    "WatcherEvent",
    "WATCHER_EVENT_FILE_ADDED",
    "WATCHER_EVENT_FILE_MODIFIED",
    "WATCHER_EVENT_FILE_REMOVED",
    # Incremental runner
    "IncrementalRunner",
    "IncrementalResult",
    # Rolling window
    "RollingWindowState",
    # Event bus
    "StreamEvent",
    "StreamEventBus",
    "EVENT_NEW_FINDING",
    "EVENT_REGIME_CHANGED",
    "EVENT_WINDOW_ADVANCED",
    "EVENT_HEARTBEAT",
    # Phase 2: dedupe + contracts bridge
    "FindingDedupeStore",
    "finding_identity",
    "evaluate_finding_against_contracts",
    "make_bridge",
]
