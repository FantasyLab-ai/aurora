"""
Postgres CDC connector — poll a table for new rows.

This is the **simple-CDC** flavour: we don't subscribe to a logical-
replication slot directly (that requires WAL-level access + the
``wal2json``/``pgoutput`` plugin, which most users haven't set up).
Instead, we poll a watermark column on a regular table — the
classic "rows newer than the highest id/timestamp we've seen" loop.

Two watermark modes:

  * **Numeric id** (default) — the table has a monotonically
    increasing primary key. We track the highest id we've ingested
    and `SELECT * WHERE id > last_id ORDER BY id LIMIT batch_size`.

  * **Timestamp** — when ``watermark_kind="timestamp"`` is set, we
    track the highest timestamp value instead. Note that timestamp-
    based CDC misses out-of-order inserts; numeric-id is preferred
    when available.

Config schema::

    {
        "dsn":              "postgresql://user:pass@host:5432/db",
        "table":            "events",
        "watermark_column": "id",          # or "created_at" for timestamps
        "watermark_kind":   "numeric",     # or "timestamp"
        "select_columns":   ["*"],         # or an explicit list
        "limit_per_poll":   500,
    }

The connector tracks its position in memory; on restart it resumes
from the last persisted watermark (`<workspace_dir>/connectors/<name>.json`)
when a workspace context is available. Otherwise it starts fresh.

We use ``psycopg`` (v3) when installed. The dep stays optional.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import StreamingConnector

LOG = logging.getLogger(__name__)


class PostgresCDCConnector(StreamingConnector):

    name = "postgres_cdc"

    @classmethod
    def available(cls) -> bool:
        try:
            import psycopg  # noqa: F401
            return True
        except Exception:
            return False

    @classmethod
    def install_hint(cls) -> str:
        return "pip install 'psycopg[binary]'"

    def __init__(self, *, runner: Any, config: Dict[str, Any], **kw):
        super().__init__(runner=runner, config=config, **kw)
        self._conn = None
        if not self.config.get("dsn"):
            raise ValueError("PostgresCDCConnector requires 'dsn' in config")
        if not self.config.get("table"):
            raise ValueError("PostgresCDCConnector requires 'table' in config")
        self._table = self.config["table"]
        self._watermark_col = self.config.get("watermark_column", "id")
        self._watermark_kind = self.config.get("watermark_kind", "numeric")
        cols = self.config.get("select_columns") or ["*"]
        self._select_columns_sql = ", ".join(self._quote_ident(c) if c != "*"
                                              else "*" for c in cols)
        self._limit_per_poll = int(self.config.get("limit_per_poll", 500))
        self._last_watermark: Any = self._load_watermark()

    def _config_summary(self) -> Dict[str, Any]:
        """Override to redact the DSN's password component."""
        cfg = super()._config_summary()
        dsn = cfg.get("dsn")
        if isinstance(dsn, str) and "@" in dsn:
            # postgresql://user:pass@host/... → postgresql://user:…@host/...
            try:
                head, tail = dsn.split("@", 1)
                if "://" in head and ":" in head.split("://", 1)[1]:
                    scheme, rest = head.split("://", 1)
                    user, _ = rest.split(":", 1)
                    cfg["dsn"] = f"{scheme}://{user}:…@{tail}"
            except Exception:
                cfg["dsn"] = "…[redacted]"
        return cfg

    # ------------------------------------------------------------------
    # Watermark persistence (best-effort, workspace-scoped)
    # ------------------------------------------------------------------

    def _watermark_file(self) -> Optional[Path]:
        try:
            from fantasyai.aurora.auth import workspace_dir
            base = workspace_dir(None)
        except Exception:
            env = os.environ.get("AURORA_DATA_ROOT", "").strip()
            if not env:
                return None
            base = Path(env).expanduser() / "workspaces" / "default"
        return (base / "connectors" /
                f"{self.config.get('table', 'unknown')}.json")

    def _load_watermark(self) -> Any:
        wf = self._watermark_file()
        if wf is None or not wf.exists():
            return None
        try:
            data = json.loads(wf.read_text(encoding="utf-8"))
            return data.get("watermark")
        except Exception:
            return None

    def _save_watermark(self, value: Any) -> None:
        wf = self._watermark_file()
        if wf is None:
            return
        try:
            wf.parent.mkdir(parents=True, exist_ok=True)
            wf.write_text(json.dumps({"watermark": value,
                                        "table": self._table}, default=str),
                            encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        import psycopg
        self._conn = psycopg.connect(self.config["dsn"], autocommit=True)

    def _disconnect(self) -> None:
        try:
            if self._conn:
                self._conn.close()
        finally:
            self._conn = None

    # ------------------------------------------------------------------
    # SQL safety + polling
    # ------------------------------------------------------------------

    _IDENT_RE = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    @classmethod
    def _quote_ident(cls, name: str) -> str:
        """Whitelist-validate an identifier and quote it. We refuse
        anything that isn't a bare identifier — no SQL injection
        surface."""
        if not isinstance(name, str) or not cls._IDENT_RE.match(name):
            raise ValueError(f"unsafe column / table identifier: {name!r}")
        return f'"{name}"'

    def _poll_once(self) -> List[Dict[str, Any]]:
        if not self._conn:
            return []
        # Build a parameterised query. The table + watermark column
        # come from config, so we whitelist-validate them as
        # identifiers — psycopg's placeholders only work for VALUES.
        tbl_sql = self._quote_ident(self._table)
        wm_sql = self._quote_ident(self._watermark_col)
        if self._last_watermark is None:
            sql = (f"SELECT {self._select_columns_sql} FROM {tbl_sql} "
                   f"ORDER BY {wm_sql} ASC LIMIT %s")
            params = (self._limit_per_poll,)
        else:
            sql = (f"SELECT {self._select_columns_sql} FROM {tbl_sql} "
                   f"WHERE {wm_sql} > %s ORDER BY {wm_sql} ASC LIMIT %s")
            params = (self._last_watermark, self._limit_per_poll)
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            colnames = [d.name for d in cur.description] if cur.description else []
            rows = [dict(zip(colnames, r)) for r in cur.fetchall()]
        if rows:
            # Advance watermark to the max value we just saw.
            wm_vals = [r[self._watermark_col] for r in rows
                        if r.get(self._watermark_col) is not None]
            if wm_vals:
                self._last_watermark = max(wm_vals)
                self._save_watermark(self._last_watermark)
        return rows
