# fantasyai/aurora/research_memory.py

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence


DEFAULT_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),  # fantasyai/
    "data",
    "aurora_experiments.jsonl",
)


def _to_jsonable(obj: Any) -> Any:
    """
    Best-effort conversion of arbitrary Python objects into JSON-serializable structures.
    """
    if obj is None:
        return None
    if is_dataclass(obj):
        obj = asdict(obj)
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(x) for x in obj]
    try:
        return float(obj)
    except Exception:
        return str(obj)


def log_experiment(
    spec: Any,
    solution: Any = None,
    tag: str = "generic",
    path: str = DEFAULT_LOG_PATH,
    extra: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> None:
    """
    Append a single experiment record to the Aurora research log.

    Backwards-compatible with older call sites:
      log_experiment(spec, best, tag="xyz")
    and with newer call sites that pass keyword arguments like `result=...`.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)

    record: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "tag": tag,
        "spec": _to_jsonable(spec),
        "solution": _to_jsonable(solution),
    }

    if extra:
        record["extra"] = _to_jsonable(extra)

    # Any additional keyword args get tucked into "meta"
    if kwargs:
        record.setdefault("meta", {})
        record["meta"].update(_to_jsonable(kwargs))

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def load_experiments(path: str = DEFAULT_LOG_PATH) -> List[Dict[str, Any]]:
    """
    Load all experiment records from the log file (if present).
    """
    if not os.path.exists(path):
        return []

    records: List[Dict[str, Any]] = []
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


def summarize_experiment_history(
    tag_filter: Optional[str] = None,
    max_records: int = 1000,
    path: str = DEFAULT_LOG_PATH,
) -> Dict[str, Any]:
    """
    High-level summary of Aurora's experiment memory:
      - number of records
      - distinct tags + counts
      - recent examples
    """
    records = load_experiments(path)
    if tag_filter:
        records = [r for r in records if r.get("tag") == tag_filter]

    records = records[-max_records:]
    tag_counts: Dict[str, int] = {}
    for r in records:
        tag = r.get("tag", "unknown")
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

    recent_examples = records[-5:]

    return {
        "num_records": len(records),
        "tag_counts": tag_counts,
        "recent_examples": recent_examples,
    }


def _numeric_vector_from_record(
    record: Dict[str, Any],
    numeric_keys: Sequence[str],
) -> List[float]:
    """
    Extract a numeric feature vector from an experiment record's solution/extra fields.
    Missing keys become NaN.
    """
    sol = record.get("solution") or {}
    extra = record.get("extra") or {}
    meta = record.get("meta") or {}

    vec: List[float] = []
    for k in numeric_keys:
        val = None
        # priority: solution -> extra -> meta
        if isinstance(sol, dict) and k in sol:
            val = sol[k]
        elif isinstance(extra, dict) and k in extra:
            val = extra[k]
        elif isinstance(meta, dict) and k in meta:
            val = meta[k]

        try:
            vec.append(float(val))
        except Exception:
            vec.append(float("nan"))
    return vec


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    a_arr = [x for x in a if not math.isnan(x)]
    b_arr = [x for x in b if not math.isnan(x)]
    if len(a_arr) == 0 or len(b_arr) == 0 or len(a_arr) != len(b_arr):
        return 0.0
    num = sum(x * y for x, y in zip(a_arr, b_arr))
    denom = math.sqrt(sum(x * x for x in a_arr)) * math.sqrt(
        sum(y * y for y in b_arr)
    )
    if denom <= 0:
        return 0.0
    return num / denom


def find_similar_experiments(
    query_record: Dict[str, Any],
    numeric_keys: Sequence[str],
    top_k: int = 5,
    path: str = DEFAULT_LOG_PATH,
) -> Dict[str, Any]:
    """
    Given a query record (for example, a new risk stratification config),
    find up to `top_k` similar past experiments based on simple cosine
    similarity in the provided numeric_keys feature space.
    """
    records = load_experiments(path)
    if not records:
        return {"matches": [], "message": "No experiments logged yet."}

    q_vec = _numeric_vector_from_record(query_record, numeric_keys)
    scored = []
    for r in records:
        v = _numeric_vector_from_record(r, numeric_keys)
        sim = _cosine_similarity(q_vec, v)
        scored.append((sim, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    return {
        "matches": [
            {
                "similarity": float(sim),
                "record": rec,
            }
            for sim, rec in top
            if sim > 0
        ],
        "num_considered": len(records),
    }
