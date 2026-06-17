"""
Log adapter — append every fire envelope to a JSONL file.

Used by the "Save" demo: rewind the file at the end of the recording
to show "and here's the full audit log of everything Aurora caught
while we slept." Append-only, never reads, never deletes.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict


_WRITE_LOCK = threading.Lock()


def append(envelope: Dict[str, Any], log_file_path: str) -> Dict[str, Any]:
    """Append one line. Errors return ok=False but never raise."""
    try:
        p = Path(log_file_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(envelope, default=str, ensure_ascii=False)
        with _WRITE_LOCK:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return {"ok": True, "path": str(p), "n_bytes": len(line) + 1}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
