# fantasyai/aurora/embedding_store.py

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple

import math


EMBED_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),  # fantasyai/
    "data",
    "aurora_problem_embeddings.jsonl",
)


@dataclass
class EmbeddedProblem:
    id: str
    text: str
    vector: List[float]
    raw: Dict[str, Any]


def simple_text_embedding(text: str, dim: int = 128) -> List[float]:
    """
    Very cheap hash-based embedding.

    Not as good as real embeddings, but:
      - requires no extra deps
      - is deterministic
      - lets you do basic similarity search now.

    Later you can replace with a real model.
    """
    import hashlib

    h = hashlib.sha256(text.encode("utf-8")).digest()
    # Repeat / slice to desired dimension
    vec = []
    for i in range(dim):
        b = h[i % len(h)]
        # map 0..255 to -1..1
        val = (b / 255.0) * 2.0 - 1.0
        vec.append(float(val))
    return vec


def _ensure_parent_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def store_problem_embedding(
    problem_id: str,
    text: str,
    raw: Dict[str, Any],
    path: str = EMBED_PATH,
) -> None:
    _ensure_parent_dir(path)
    vec = simple_text_embedding(text)
    rec = {
        "id": problem_id,
        "text": text,
        "vector": vec,
        "raw": raw,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def load_problem_embeddings(path: str = EMBED_PATH) -> List[EmbeddedProblem]:
    if not os.path.exists(path):
        return []
    out: List[EmbeddedProblem] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            out.append(
                EmbeddedProblem(
                    id=obj["id"],
                    text=obj["text"],
                    vector=obj["vector"],
                    raw=obj.get("raw", {}),
                )
            )
    return out


def cosine_sim(a: List[float], b: List[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    den_a = math.sqrt(sum(x * x for x in a))
    den_b = math.sqrt(sum(y * y for y in b))
    if den_a == 0 or den_b == 0:
        return 0.0
    return num / (den_a * den_b)


def find_similar_problems(
    query_text: str,
    top_k: int = 5,
    path: str = EMBED_PATH,
) -> List[Tuple[EmbeddedProblem, float]]:
    """
    Return top_k similar problems (by cosine sim) to the query_text.
    """
    vec_q = simple_text_embedding(query_text)
    all_embeds = load_problem_embeddings(path=path)
    scored: List[Tuple[EmbeddedProblem, float]] = []

    for ep in all_embeds:
        s = cosine_sim(vec_q, ep.vector)
        scored.append((ep, s))

    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:top_k]
