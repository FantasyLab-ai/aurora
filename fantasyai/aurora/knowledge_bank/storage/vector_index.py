"""
Pure-numpy vector index for the knowledge bank.

A FAISS / sqlite-vss replacement that needs no external dependencies.
Brute-force cosine similarity is O(N·d) per query; with N ≈ 2M entries
and d = 384 (hashed TF-IDF) or d = 768 (transformer), that's ~700-1500 MB
of working memory and a few hundred ms per query. Acceptable for
Aurora's runtime.

For really large banks the maintainer can install FAISS and swap the
``rank_topk`` implementation; the API stays the same.

Persistence: a single ``.npy`` file holding (N × d) float32 + a
companion ``.ids.json`` file mapping row index → entry id.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


class NumpyVectorIndex:
    """In-memory vector index with disk persistence."""

    def __init__(self, dim: int):
        if np is None:
            raise RuntimeError("numpy required for NumpyVectorIndex")
        self.dim = dim
        self._matrix: Optional[np.ndarray] = None  # (N × dim), float32, L2-normed
        self._ids: List[str] = []
        self._id_to_row: Dict[str, int] = {}

    # ---------- bulk operations ----------

    def reset(self) -> None:
        self._matrix = None
        self._ids = []
        self._id_to_row = {}

    def add(self, entry_id: str, vector: Sequence[float]) -> None:
        v = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        if v.shape[1] != self.dim:
            raise ValueError(
                f"vector dim {v.shape[1]} != index dim {self.dim}"
            )
        # Re-normalise defensively.
        n = float(np.linalg.norm(v))
        if n > 0:
            v = v / n
        if self._matrix is None:
            self._matrix = v.copy()
        else:
            self._matrix = np.vstack([self._matrix, v])
        self._id_to_row[entry_id] = len(self._ids)
        self._ids.append(entry_id)

    def add_batch(self, entry_ids: Sequence[str],
                    vectors: Sequence[Sequence[float]]) -> None:
        if len(entry_ids) != len(vectors):
            raise ValueError("entry_ids / vectors length mismatch")
        if not entry_ids:
            return
        m = np.asarray(vectors, dtype=np.float32)
        if m.ndim != 2 or m.shape[1] != self.dim:
            raise ValueError(f"vectors shape {m.shape} incompatible with dim {self.dim}")
        # Re-normalise rows.
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        m = m / norms
        if self._matrix is None:
            self._matrix = m.copy()
        else:
            self._matrix = np.vstack([self._matrix, m])
        start = len(self._ids)
        for i, eid in enumerate(entry_ids):
            self._id_to_row[eid] = start + i
            self._ids.append(eid)

    def remove(self, entry_id: str) -> None:
        if entry_id not in self._id_to_row:
            return
        row = self._id_to_row[entry_id]
        if self._matrix is not None:
            mask = np.ones(self._matrix.shape[0], dtype=bool)
            mask[row] = False
            self._matrix = self._matrix[mask]
        # Rebuild id list / lookup.
        new_ids = [eid for i, eid in enumerate(self._ids) if i != row]
        self._ids = new_ids
        self._id_to_row = {eid: i for i, eid in enumerate(new_ids)}

    # ---------- query ----------

    def search(self, query_vector: Sequence[float], top_k: int = 20,
                 *, candidate_ids: Optional[Sequence[str]] = None) -> List[Tuple[str, float]]:
        """Cosine-similarity top-k.

        When ``candidate_ids`` is supplied, the search is restricted to
        those rows (used by the hybrid ranker to re-score a structured
        candidate set).
        """
        if self._matrix is None or self._matrix.shape[0] == 0:
            return []
        q = np.asarray(query_vector, dtype=np.float32)
        if q.shape[0] != self.dim:
            raise ValueError(f"query dim {q.shape[0]} != index dim {self.dim}")
        n = float(np.linalg.norm(q))
        if n > 0:
            q = q / n
        if candidate_ids is None:
            sims = self._matrix @ q
            n_results = min(top_k, sims.shape[0])
            if n_results == 0:
                return []
            top_idx = np.argpartition(-sims, n_results - 1)[:n_results]
            top_idx = top_idx[np.argsort(-sims[top_idx])]
            return [(self._ids[int(i)], float(sims[int(i)])) for i in top_idx]
        # Restricted search.
        rows = [self._id_to_row[c] for c in candidate_ids
                  if c in self._id_to_row]
        if not rows:
            return []
        sims = self._matrix[rows] @ q
        n_results = min(top_k, sims.shape[0])
        order = np.argsort(-sims)[:n_results]
        return [(self._ids[rows[int(o)]], float(sims[int(o)]))
                  for o in order]

    # ---------- persistence ----------

    def save(self, base_path: Path) -> None:
        base = Path(base_path)
        base.parent.mkdir(parents=True, exist_ok=True)
        if self._matrix is None:
            return
        np.save(str(base) + ".npy", self._matrix)
        meta = {"dim": self.dim, "ids": self._ids}
        Path(str(base) + ".ids.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )

    def load(self, base_path: Path) -> bool:
        npy = Path(str(base_path) + ".npy")
        idsf = Path(str(base_path) + ".ids.json")
        if not (npy.exists() and idsf.exists()):
            return False
        self._matrix = np.load(str(npy))
        meta = json.loads(idsf.read_text(encoding="utf-8"))
        self.dim = int(meta.get("dim") or self._matrix.shape[1])
        self._ids = list(meta.get("ids") or [])
        self._id_to_row = {eid: i for i, eid in enumerate(self._ids)}
        return True

    def __len__(self) -> int:
        return self._matrix.shape[0] if self._matrix is not None else 0
