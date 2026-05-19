"""
Kafka connector — subscribe to a topic, ingest messages into Aurora.

Config schema::

    {
        "bootstrap_servers": "broker:9092",
        "topic":             "aurora_input",
        "group_id":          "aurora-1",          # optional
        "auto_offset_reset": "latest",           # latest | earliest
        "value_deserializer": "json",             # json | raw
        "consumer_timeout_ms": 500,
        # Plus any kafka-python KafkaConsumer kwarg you want to set.
    }

Each message's value is decoded (JSON → dict, raw → ``{"raw": <bytes>}``)
and pushed into the runner as a one-row "DataFrame". Batches are
formed from consecutive messages within the poll window.

The connector is **at-least-once** — if Aurora crashes mid-batch,
Kafka replays from the last committed offset. We commit after the
ingest call returns, so a successful ingest is durable from Aurora's
side. For exactly-once semantics across Aurora restarts, use the
bundle-hash + replay pattern (the same shape contracts use).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .base import StreamingConnector

LOG = logging.getLogger(__name__)


class KafkaConnector(StreamingConnector):

    name = "kafka"

    @classmethod
    def available(cls) -> bool:
        try:
            import kafka  # noqa: F401  (kafka-python)
            return True
        except Exception:
            return False

    @classmethod
    def install_hint(cls) -> str:
        return "pip install kafka-python"

    def __init__(self, *, runner: Any, config: Dict[str, Any], **kw):
        super().__init__(runner=runner, config=config, **kw)
        self._consumer = None
        topic = self.config.get("topic")
        if not topic:
            raise ValueError("KafkaConnector requires 'topic' in config")
        self._topic = topic
        self._value_deserializer = self.config.get("value_deserializer", "json")

    def _connect(self) -> None:
        from kafka import KafkaConsumer
        # kafka-python kwargs we pass through.
        kw: Dict[str, Any] = {
            "bootstrap_servers": self.config.get("bootstrap_servers"),
            "group_id":          self.config.get("group_id"),
            "auto_offset_reset": self.config.get("auto_offset_reset", "latest"),
            "consumer_timeout_ms": int(self.config.get("consumer_timeout_ms", 500)),
            "enable_auto_commit": bool(self.config.get("enable_auto_commit", True)),
        }
        # Allow any extra kwarg to pass through (for security_protocol etc).
        for k in ("security_protocol", "ssl_cafile", "ssl_certfile",
                   "ssl_keyfile", "sasl_mechanism", "sasl_plain_username",
                   "sasl_plain_password", "client_id"):
            if k in self.config:
                kw[k] = self.config[k]
        # Strip Nones to honour kafka-python defaults.
        kw = {k: v for k, v in kw.items() if v is not None}
        self._consumer = KafkaConsumer(self._topic, **kw)

    def _disconnect(self) -> None:
        try:
            if self._consumer:
                self._consumer.close()
        finally:
            self._consumer = None

    def _decode(self, raw_value: bytes) -> Dict[str, Any]:
        if self._value_deserializer == "raw":
            return {"raw": raw_value.hex() if isinstance(raw_value, (bytes, bytearray)) else str(raw_value)}
        # JSON path (default).
        try:
            txt = raw_value.decode("utf-8") if isinstance(raw_value, (bytes, bytearray)) else str(raw_value)
            decoded = json.loads(txt)
            if isinstance(decoded, dict):
                return decoded
            return {"value": decoded}
        except Exception:
            return {"raw": (raw_value.decode("utf-8", errors="replace")
                            if isinstance(raw_value, (bytes, bytearray))
                            else str(raw_value))}

    def _poll_once(self) -> List[Dict[str, Any]]:
        if not self._consumer:
            return []
        # kafka-python's poll() returns {TopicPartition: [ConsumerRecord]}.
        partitions = self._consumer.poll(
            timeout_ms=self.config.get("consumer_timeout_ms", 500),
            max_records=self.batch_size,
        )
        rows: List[Dict[str, Any]] = []
        for _, recs in partitions.items():
            for r in recs:
                row = self._decode(r.value)
                # Attach Kafka metadata so downstream methods can reference it.
                row.setdefault("_kafka_offset", r.offset)
                row.setdefault("_kafka_partition", r.partition)
                row.setdefault("_kafka_ts", r.timestamp)
                rows.append(row)
                if len(rows) >= self.batch_size:
                    return rows
        return rows
