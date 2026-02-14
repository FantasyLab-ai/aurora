# fantasyai/aurora/research_memory.py

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Dict, Optional


DEFAULT_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),  # fantasyai/
    "data",
    "aurora_experiments.jsonl",
)


def _ensure_parent_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _serialize(obj: Any) -> Any:
    """
    Best-effort serializer for dataclasses and other Python objects.
    """
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, (dict, list, str, int, float, bool)) or obj is None:
        return obj
    # Fallback: string representation
    return repr(obj)


def log_experiment(
    spec: Any,
    solution: Any,
    *,
    tag: Optional[str] = None,
    path: str = DEFAULT_LOG_PATH,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Append a single experiment record to a JSONL log file.

    This is Aurora's long-term research memory:
      - stores ProblemSpec + MathSolution (or any other objects)
      - attaches timestamp, tag, and diagnostics
    """
    _ensure_parent_dir(path)

    record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "tag": tag,
        "spec": _serialize(spec),
        "solution": _serialize(solution),
        "extra": extra or {},
    }

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def load_experiments(path: str = DEFAULT_LOG_PATH) -> list[Dict[str, Any]]:
    """
    Load all experiment records from the log file (if present).
    """
    if not os.path.exists(path):
        return []

    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records
