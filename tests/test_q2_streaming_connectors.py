"""
Q2.0 Stream B — streaming connectors tests.

Both connectors are dep-gated (kafka-python, psycopg). We test:
    * availability classmethod returns the right (name, bool, hint)
    * connector raises ConnectorUnavailableError when dep missing
    * config validation (missing required fields)
    * `_quote_ident` rejects unsafe identifiers (SQL-injection guard)
    * The connector base class's ingest path with a stub runner
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest


# ----------------------------------------------------------------------
# Availability
# ----------------------------------------------------------------------

class TestAvailability:

    def test_available_connectors_returns_tuples(self):
        from fantasyai.aurora.streaming.connectors import available_connectors
        rows = available_connectors()
        names = [r[0] for r in rows]
        assert "file_watcher" in names
        assert "kafka" in names
        assert "postgres_cdc" in names
        # Each row is (name, available_bool, install_hint).
        for name, ok, hint in rows:
            assert isinstance(name, str)
            assert isinstance(ok, bool)
            assert isinstance(hint, str)


# ----------------------------------------------------------------------
# Kafka connector
# ----------------------------------------------------------------------

class TestKafkaConnector:

    def test_availability_tuple_shape(self):
        from fantasyai.aurora.streaming.connectors import KafkaConnector
        name, ok, hint = KafkaConnector.availability()
        assert name == "kafka"
        assert isinstance(ok, bool)
        assert "kafka-python" in hint

    def test_raises_when_kafka_python_missing(self, monkeypatch):
        from fantasyai.aurora.streaming.connectors import (
            KafkaConnector, ConnectorUnavailableError,
        )
        monkeypatch.setattr(KafkaConnector, "available",
                              classmethod(lambda cls: False))
        with pytest.raises(ConnectorUnavailableError):
            KafkaConnector(runner=MagicMock(),
                            config={"topic": "x", "bootstrap_servers": "y"})

    def test_requires_topic(self, monkeypatch):
        from fantasyai.aurora.streaming.connectors import KafkaConnector
        # Pretend the dep is installed so we exercise config validation.
        monkeypatch.setattr(KafkaConnector, "available",
                              classmethod(lambda cls: True))
        with pytest.raises(ValueError):
            KafkaConnector(runner=MagicMock(),
                            config={"bootstrap_servers": "broker:9092"})

    def test_decode_json_value(self, monkeypatch):
        from fantasyai.aurora.streaming.connectors import KafkaConnector
        monkeypatch.setattr(KafkaConnector, "available",
                              classmethod(lambda cls: True))
        c = KafkaConnector(runner=MagicMock(),
                            config={"topic": "x", "bootstrap_servers": "y"})
        out = c._decode(b'{"a": 1, "b": "x"}')
        assert out == {"a": 1, "b": "x"}

    def test_decode_invalid_json_wraps_as_raw(self, monkeypatch):
        from fantasyai.aurora.streaming.connectors import KafkaConnector
        monkeypatch.setattr(KafkaConnector, "available",
                              classmethod(lambda cls: True))
        c = KafkaConnector(runner=MagicMock(),
                            config={"topic": "x", "bootstrap_servers": "y"})
        out = c._decode(b"not json")
        assert "raw" in out

    def test_config_summary_redacts_secrets(self, monkeypatch):
        from fantasyai.aurora.streaming.connectors import KafkaConnector
        monkeypatch.setattr(KafkaConnector, "available",
                              classmethod(lambda cls: True))
        c = KafkaConnector(runner=MagicMock(),
                            config={"topic": "x", "bootstrap_servers": "b",
                                    "sasl_plain_password": "supersecret",
                                    "api_key": "secret"})
        summary = c._config_summary()
        assert "supersecret" not in str(summary)
        assert "api_key" not in summary
        assert "sasl_plain_password" not in summary


# ----------------------------------------------------------------------
# Postgres CDC connector — focus on the SQL-injection guard
# ----------------------------------------------------------------------

class TestPostgresCDCConnector:

    def test_availability_tuple_shape(self):
        from fantasyai.aurora.streaming.connectors import PostgresCDCConnector
        name, ok, hint = PostgresCDCConnector.availability()
        assert name == "postgres_cdc"
        assert isinstance(ok, bool)
        assert "psycopg" in hint

    def test_quote_ident_accepts_safe_names(self):
        from fantasyai.aurora.streaming.connectors.postgres_cdc_connector import (
            PostgresCDCConnector,
        )
        assert PostgresCDCConnector._quote_ident("events") == '"events"'
        assert PostgresCDCConnector._quote_ident("my_table_2") == '"my_table_2"'

    def test_quote_ident_rejects_unsafe_input(self):
        from fantasyai.aurora.streaming.connectors.postgres_cdc_connector import (
            PostgresCDCConnector,
        )
        for bad in ('events"; drop table users;--',
                     "events--",
                     "events; rm -rf /",
                     "events.with.dot",
                     "events with space",
                     "",
                     "1events",     # leading digit
                     "events'"):
            with pytest.raises(ValueError):
                PostgresCDCConnector._quote_ident(bad)

    def test_raises_when_psycopg_missing(self, monkeypatch):
        from fantasyai.aurora.streaming.connectors import (
            PostgresCDCConnector, ConnectorUnavailableError,
        )
        monkeypatch.setattr(PostgresCDCConnector, "available",
                              classmethod(lambda cls: False))
        with pytest.raises(ConnectorUnavailableError):
            PostgresCDCConnector(
                runner=MagicMock(),
                config={"dsn": "postgresql://x", "table": "events"},
            )

    def test_requires_dsn_and_table(self, monkeypatch):
        from fantasyai.aurora.streaming.connectors import PostgresCDCConnector
        monkeypatch.setattr(PostgresCDCConnector, "available",
                              classmethod(lambda cls: True))
        with pytest.raises(ValueError):
            PostgresCDCConnector(runner=MagicMock(),
                                  config={"table": "events"})
        with pytest.raises(ValueError):
            PostgresCDCConnector(runner=MagicMock(),
                                  config={"dsn": "postgresql://x"})

    def test_config_summary_redacts_dsn_password(self, monkeypatch):
        from fantasyai.aurora.streaming.connectors import PostgresCDCConnector
        monkeypatch.setattr(PostgresCDCConnector, "available",
                              classmethod(lambda cls: True))
        c = PostgresCDCConnector(
            runner=MagicMock(),
            config={"dsn": "postgresql://alice:supersecret@h/db",
                    "table": "events"},
        )
        s = c._config_summary()
        assert "supersecret" not in str(s)
        assert "alice" in str(s)   # username preserved
        assert "…" in s["dsn"]


# ----------------------------------------------------------------------
# Base-class ingest path with a stub runner
# ----------------------------------------------------------------------

class TestBaseIngest:

    def test_ingest_batch_pushes_to_runner(self, monkeypatch):
        """The base class's _ingest_batch should produce a DataFrame
        and call runner.ingest(df, source=...)."""
        from fantasyai.aurora.streaming.connectors import KafkaConnector
        monkeypatch.setattr(KafkaConnector, "available",
                              classmethod(lambda cls: True))
        stub_runner = MagicMock()
        c = KafkaConnector(runner=stub_runner,
                            config={"topic": "x", "bootstrap_servers": "y"})
        c._ingest_batch([
            {"value": 1.0, "ts": 1716130800},
            {"value": 2.0, "ts": 1716130810},
        ])
        assert stub_runner.ingest.called
        args, kwargs = stub_runner.ingest.call_args
        # First positional arg is the DataFrame.
        df = args[0]
        assert hasattr(df, "shape")
        assert df.shape == (2, 2)
        assert kwargs.get("source") == "kafka:batch"
        assert c.status().n_batches == 1
        assert c.status().n_rows_ingested == 2
