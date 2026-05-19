# Aurora Streaming Mode (Phase 1)

Aurora's v1.1 pipeline runs in **batch** mode — one CSV, one
analysis, one bundle. v1.2 ships Stream 1.4 Phase 1: a streaming /
continuous mode so Aurora can keep findings fresh as new data arrives
without you re-running by hand.

This page describes what's shipping now (Phase 1) and what's coming
next.

## What ships in Phase 1

| Component | Module |
|---|---|
| **FileWatcher** — polls a directory or file for changes | `fantasyai.aurora.streaming.watcher.FileWatcher` |
| **RollingWindowState** — fixed-size sliding window | `fantasyai.aurora.streaming.rolling_window.RollingWindowState` |
| **StreamEventBus** — pub/sub for streaming notifications | `fantasyai.aurora.streaming.events.StreamEventBus` |
| **IncrementalRunner** — file change → window update → analysis → events | `fantasyai.aurora.streaming.runner.IncrementalRunner` |
| **HTTP endpoints** | `/api/stream/start`, `/api/stream/stop`, `/api/stream/status`, `/api/stream/events` (SSE) |

## Use case

You have a directory at `/data/incoming/` where a sensor or ETL job
drops a fresh CSV every hour. You want Aurora's findings to refresh
automatically.

```bash
# Tell Aurora to watch the directory.
curl -X POST http://localhost:8000/api/stream/start \
  -H 'Content-Type: application/json' \
  -d '{"path": "/data/incoming/", "file_glob": "*.csv", "poll_interval_s": 2.0}'

# Check it's running.
curl http://localhost:8000/api/stream/status

# Subscribe to live events via Server-Sent Events.
curl -N http://localhost:8000/api/stream/events
```

Drop a new CSV into `/data/incoming/`. Within ~2 seconds Aurora picks
it up, runs the extended-methods stack on the rolling window, and
publishes:

- `window_advanced` — every time the window updates
- `new_finding` — when the analysis produces findings (with severity rollup)
- `regime_changed` — when a regime-style finding's title differs from the previous run

## Subscribing from JavaScript (the Studio + custom UIs)

```javascript
const es = new EventSource("/api/stream/events");
es.addEventListener("new_finding", (e) => {
    const data = JSON.parse(e.data);
    console.log("New findings:", data.payload);
});
es.addEventListener("regime_changed", (e) => {
    const data = JSON.parse(e.data);
    console.log("Regime change:", data.payload.title);
});
es.addEventListener("heartbeat", () => {
    // Server's saying "I'm still here" — keepalive through proxies.
});
```

The Studio's frontend cube can listen for these and pop a small
"new findings since last view" indicator next to the run banner.
That UI hook lands in Phase 2.

## Programmatic ingestion (no file watcher)

When data comes from a non-file source — Kafka topic, Postgres CDC,
an MCP tool call — push it into the runner directly:

```python
import pandas as pd
from fantasyai.aurora.streaming import (
    IncrementalRunner, StreamEventBus, RollingWindowState,
)

bus = StreamEventBus()
runner = IncrementalRunner(
    data_path=".",   # unused when ingesting programmatically
    event_bus=bus,
    window=RollingWindowState(max_rows=10_000),
)

# Subscribe to the bus.
sub = bus.subscribe()

# Each time new rows arrive, ingest them.
new_rows = pd.DataFrame({"ts": [...], "value": [...]})
runner.ingest(new_rows, source="kafka:my-topic")

# Pull events.
import queue
try:
    ev = sub.get(timeout=5)
    print(ev.kind, ev.payload)
except queue.Empty:
    pass
```

## What's intentionally NOT in Phase 1

| Feature | Why deferred | Phase |
|---|---|---|
| Kafka topic ingestion | Adds `kafka-python` dep — needs review | Phase 2 |
| Postgres CDC | Specialised; build per-customer at first | Phase 2 |
| Truly-incremental methods (BOCPD-style only-recompute-the-tip) | Phase 1 does full re-runs on each change; analyses on < 10K rows finish in seconds anyway | Phase 2 |
| Streaming integration with Decision Contracts (auto-fire webhook on `new_finding`) | One-line addition once contracts know about the bus | Phase 2 |
| Frontend UI hook (cube indicator for new findings) | Frontend session deferred — currently surfaces via /api/stream/events directly | Phase 2 |
| Multi-tenant streaming (per-user buses + isolation) | Aurora Cloud Phase 2/3 | Phase 3 |

## Behaviour notes

- **Polling cadence:** 2s default. Sub-second polling thrashes for no
  gain — analyses take longer than 2s anyway. Set higher
  (`poll_interval_s: 5.0`) on slow networks.

- **What counts as "new findings":** Phase 1 publishes a single event
  per run with the severity rollup. Phase 2 will dedupe at the
  per-finding level so the bus only fires when an *individual* finding
  is genuinely new.

- **Backpressure:** subscribers have a bounded queue (256 events). Slow
  consumers see their oldest events dropped; the bus records the drop
  count but never blocks the producer. Streaming is best-effort —
  clients that need exactly-once delivery use the bundle hash + replay
  pattern.

- **Heartbeats:** the SSE endpoint emits a `heartbeat` event every 30
  seconds of idle so proxies (nginx, Cloudflare) don't reap the
  long-lived connection.

## Tests

Run the streaming suite locally:

```bash
pytest tests/test_streaming.py -v
```

17 tests cover watcher polling, window eviction, bus pub/sub,
heartbeat behaviour, and end-to-end ingest → events flow.

## See also

- `fantasyai/aurora/streaming/` — source of truth
- [docs/concepts.md](concepts.md) — Aurora's glass-box principles
- [ROADMAP.md](../ROADMAP.md) — Stream 1.4 / 2.5 future work
