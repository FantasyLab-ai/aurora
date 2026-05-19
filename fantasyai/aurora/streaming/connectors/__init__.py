"""
Aurora streaming connectors (v2.0 Stream B).

Phase 1 of streaming ships a file-watcher: point Aurora at a directory,
re-analyse when a new file lands. That covers a lot of real workflows
(NOAA buoy hourly drops, ETL output, log shipper) but not the
event-streaming or change-data-capture cases the rest of the world
runs on.

This module ships two new connectors that drive the IncrementalRunner
from non-file sources:

  * **`KafkaConnector`** — subscribes to a Kafka topic, decodes each
    message into a dict, batches them into a DataFrame, and ingests
    via ``runner.ingest()``. Uses ``kafka-python`` when installed;
    skips with a clear error when not.

  * **`PostgresCDCConnector`** — polls a Postgres logical-replication
    slot (or a simple "rows newer than last_seen_id" query when the
    slot isn't available) and ingests new rows. Uses ``psycopg`` (v3)
    when installed; skips with a clear error otherwise.

Both connectors share the same interface as the file watcher: they
run in a background thread, push rows into the runner, and respect
the bus's bounded queues + heartbeat. The streaming runner doesn't
care which kind of source pushed the rows — it just runs the
analysis pipeline.

Deps stay optional. We don't add `kafka-python` or `psycopg` to
`requirements.txt`; users who need them install separately. The
connector classes report unavailability via a clear `available()`
classmethod the frontend reads, so the streaming popover can grey
out the connectors the deploy doesn't have set up.
"""
from __future__ import annotations

from .base import (
    StreamingConnector,
    ConnectorStatus,
    ConnectorUnavailableError,
)
from .kafka_connector import (
    KafkaConnector,
)
from .postgres_cdc_connector import (
    PostgresCDCConnector,
)


def available_connectors():
    """Return a list of (name, available, install_hint) tuples for each
    connector — the streaming popover renders this to grey out
    connectors whose deps aren't installed."""
    return [
        ("file_watcher",     True, "always available (no extra deps)"),
        KafkaConnector.availability(),
        PostgresCDCConnector.availability(),
    ]


__all__ = [
    "StreamingConnector",
    "ConnectorStatus",
    "ConnectorUnavailableError",
    "KafkaConnector",
    "PostgresCDCConnector",
    "available_connectors",
]
