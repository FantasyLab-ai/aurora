"""
StreamEventBus — pub/sub for streaming-mode notifications.

Lets multiple consumers (the Studio's SSE endpoint, an MCP client, a
Decision Contract trigger) subscribe to "something changed" events
emitted by the streaming runner.

Pure stdlib (``threading.Lock``, ``queue.Queue``). Each subscriber gets
their own queue — slow consumers don't block the producer. Bounded
queue size with a fail-open policy: if a subscriber's queue is full,
events for them are dropped (and a counter recorded) rather than
blocking the bus.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

LOG = logging.getLogger(__name__)


EVENT_NEW_FINDING = "new_finding"
EVENT_REGIME_CHANGED = "regime_changed"
EVENT_WINDOW_ADVANCED = "window_advanced"
EVENT_HEARTBEAT = "heartbeat"


@dataclass(frozen=True)
class StreamEvent:
    """One event passing through the bus.

    Attributes:
        kind:       Event token. Use the EVENT_* constants for
                    well-known kinds; arbitrary strings are also fine.
        payload:    JSON-serialisable dict. Keep small — SSE delivery
                    is one event per HTTP chunk.
        timestamp:  ``time.time()`` when the event was published.
        source:     Optional identifier of who published it.
    """
    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = ""


# Per-subscriber bounded queue size. If a consumer falls this far
# behind, we drop their oldest events rather than back-pressuring the
# producer. Streaming is best-effort — clients that need guaranteed
# delivery use the bundle hash + replay.
SUBSCRIBER_QUEUE_MAX = 256


class StreamEventBus:
    """Topic-less pub/sub. All subscribers see all events; they filter
    by ``event.kind`` themselves. This keeps the bus simple — adding
    new event kinds doesn't require schema changes.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: List[queue.Queue] = []
        self._drop_counts: Dict[int, int] = {}  # id(queue) → dropped events

    # ---- Publish -------------------------------------------------------

    def publish(self, event: StreamEvent) -> int:
        """Send an event to every subscriber. Returns the count of
        subscribers it was queued for (i.e. excludes those whose queue
        was full)."""
        delivered = 0
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
                delivered += 1
            except queue.Full:
                # Drop oldest, retry once. This is the "ring buffer"
                # behaviour clients expect for monitoring streams.
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                self._drop_counts[id(q)] = self._drop_counts.get(id(q), 0) + 1
                try:
                    q.put_nowait(event)
                    delivered += 1
                except queue.Full:
                    pass
        return delivered

    # ---- Subscribe -----------------------------------------------------

    def subscribe(self) -> queue.Queue:
        """Register a new subscriber. Returns the subscriber's queue.
        Read events with ``q.get(timeout=...)`` or use the iterator
        helper below.

        Caller MUST call ``unsubscribe(q)`` when done; otherwise the
        queue keeps receiving events forever."""
        q: queue.Queue = queue.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass
            self._drop_counts.pop(id(q), None)

    def iterate(
        self,
        q: queue.Queue,
        *,
        timeout_s: float = 30.0,
    ) -> Iterator[StreamEvent]:
        """Helper: yield events from ``q`` until either an explicit
        ``None`` sentinel arrives or the timeout elapses with no
        traffic. Useful for SSE handlers."""
        while True:
            try:
                ev = q.get(timeout=timeout_s)
            except queue.Empty:
                # Heartbeat keeps SSE connections alive through proxies.
                yield StreamEvent(kind=EVENT_HEARTBEAT, source="bus")
                continue
            if ev is None:
                return
            yield ev

    # ---- Diagnostics ---------------------------------------------------

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def drop_count(self, q: queue.Queue) -> int:
        return int(self._drop_counts.get(id(q), 0))
