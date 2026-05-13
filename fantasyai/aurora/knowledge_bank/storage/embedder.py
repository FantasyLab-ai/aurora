"""
Knowledge bank embedder.

Default device is CPU for universal compatibility. Aurora is designed to
run on any hardware (laptops, modest workstations, GPU-equipped
desktops) without configuration. CPU embedding adds ~200-500ms latency
per synthesis call, which is negligible compared to LLM generation
(5-15s on Gemma 12B).

Opt-in GPU acceleration::

    AURORA_EMBEDDER_DEVICE=cuda

Compatibility: stable PyTorch builds support compute capabilities
``sm_50`` through ``sm_90`` (Maxwell through Hopper). Newer GPUs
(Blackwell ``sm_120``+) require PyTorch nightly builds. The
``_resolve_device()`` helper checks ``torch.cuda.get_arch_list()``
against the device capability and falls back to CPU with a clear
warning when a requested GPU isn't supported by the installed torch.

Two backends:

* :class:`SentenceTransformerEmbedder` — wraps ``sentence-transformers``
                                          when available. Default model
                                          ``all-mpnet-base-v2`` (768-d).
* :class:`HashedTfIdfEmbedder`         — pure-numpy hashed TF-IDF
                                          fallback. Deterministic. Works
                                          without ML deps. 384-d default.

Run ``python -m fantasyai.aurora.knowledge_bank.preflight`` for a
device-status report.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from typing import Any, List, Optional, Sequence

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


_log = logging.getLogger("aurora.embedder")


_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


# ---------------------------------------------------------------------------
# Hashed TF-IDF embedder (default fallback)
# ---------------------------------------------------------------------------

class HashedTfIdfEmbedder:
    """Pure-numpy embedder that hashes tokens to a fixed-dim vector and
    weights by sublinear TF + log-IDF.

    Properties:
      * Deterministic for the same input.
      * No external deps beyond numpy.
      * 384-dim by default (small enough that brute-force cosine on
        millions of entries is still fast).

    Limitations:
      * Doesn't capture true semantic similarity the way a transformer
        does ("fast" vs "rapid" land in different buckets).
      * IDF is computed per-batch; for cross-batch consistency we expose
        ``corpus_idf`` so the ingest pipeline can fit IDF over the full
        corpus once.
    """

    name = "hashed_tfidf"

    def __init__(self, dim: int = 384, *, corpus_idf: Optional[Sequence[float]] = None):
        self.dim = int(dim)
        self.model = f"hashed_tfidf_{self.dim}"
        self._idf = (np.asarray(corpus_idf, dtype=float)
                       if corpus_idf is not None else None)

    # ---------- core helpers ----------

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        if not text:
            return []
        return [t.lower() for t in _TOKEN_RE.findall(text)]

    def _hash_to_dim(self, token: str) -> int:
        h = int.from_bytes(
            hashlib.md5(token.encode("utf-8")).digest()[:4], "big"
        )
        return h % self.dim

    # ---------- public API ----------

    def fit_idf(self, corpus_texts: Sequence[str]) -> "np.ndarray":
        """Compute log IDF over the corpus and store on self for reuse."""
        if np is None:
            raise RuntimeError("numpy required for HashedTfIdfEmbedder")
        N = max(1, len(corpus_texts))
        df = np.zeros(self.dim, dtype=float)
        for txt in corpus_texts:
            seen = set()
            for tok in self._tokenize(txt):
                idx = self._hash_to_dim(tok)
                if idx not in seen:
                    df[idx] += 1
                    seen.add(idx)
        # Smoothed log IDF.
        idf = np.log((N + 1) / (df + 1)) + 1.0
        self._idf = idf
        return idf

    def embed(self, text: str) -> List[float]:
        if np is None:
            raise RuntimeError("numpy required for embedder")
        v = np.zeros(self.dim, dtype=float)
        tokens = self._tokenize(text)
        if not tokens:
            return v.tolist()
        # Sublinear TF (1 + log) — robust to repeated tokens.
        tf = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        for tok, c in tf.items():
            idx = self._hash_to_dim(tok)
            weight = 1.0 + math.log(c)
            if self._idf is not None:
                weight *= float(self._idf[idx])
            v[idx] += weight
        # L2-normalise so cosine similarity = dot product.
        norm = float(np.linalg.norm(v))
        if norm > 0:
            v /= norm
        return v.tolist()

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


# ---------------------------------------------------------------------------
# Optional sentence-transformers backend
# ---------------------------------------------------------------------------

def _resolve_device() -> str:
    """Resolve which device the SentenceTransformer should run on.

    Default is CPU (universal compatibility — Aurora must work on any
    hardware out of the box). Users opt in to GPU acceleration via
    ``AURORA_EMBEDDER_DEVICE=cuda``. When ``cuda`` is requested:

      1. We verify torch.cuda is actually available.
      2. We check the GPU's compute capability (e.g. ``sm_120`` for
         Blackwell, ``sm_86`` for Ampere) against
         ``torch.cuda.get_arch_list()``. If the GPU isn't in torch's
         compiled arch list, the kernel will fail at first use with
         "no kernel image is available". We fall back to CPU FIRST and
         emit a clear warning rather than wait for the kernel error.

    Returns one of:
      * ``"cpu"``  — universal default
      * ``"cuda"`` — explicitly opted-in AND verified compatible
    """
    requested = (os.environ.get("AURORA_EMBEDDER_DEVICE") or "cpu").lower().strip()

    if requested == "cpu":
        return "cpu"

    if requested == "cuda":
        try:
            import torch
        except Exception as e:
            _log.warning(
                "AURORA_EMBEDDER_DEVICE=cuda requested but torch is not "
                "importable (%s). Falling back to CPU.", e,
            )
            return "cpu"
        try:
            if not torch.cuda.is_available():
                _log.warning(
                    "AURORA_EMBEDDER_DEVICE=cuda requested but "
                    "torch.cuda.is_available() is False. Falling back to CPU."
                )
                return "cpu"
            cap = torch.cuda.get_device_capability()
            arch_str = f"sm_{cap[0]}{cap[1]}"
            arch_list = list(torch.cuda.get_arch_list() or [])
            if arch_str not in arch_list:
                _log.warning(
                    "AURORA_EMBEDDER_DEVICE=cuda requested but GPU "
                    "compute capability %s is not in the installed torch's "
                    "compiled arch list %s. Falling back to CPU. "
                    "(Install a PyTorch build that supports your GPU — "
                    "newer GPUs may need nightly builds.)",
                    arch_str, arch_list,
                )
                return "cpu"
            return "cuda"
        except Exception as e:
            _log.warning(
                "Failed to verify CUDA compatibility (%s). Falling back to CPU.",
                e, exc_info=True,
            )
            return "cpu"

    _log.warning(
        "Unknown AURORA_EMBEDDER_DEVICE=%r. Valid values: 'cpu' (default) "
        "or 'cuda'. Falling back to CPU.", requested,
    )
    return "cpu"


class SentenceTransformerEmbedder:
    """Real transformer embedder. Lazy-imported so absence doesn't break
    knowledge_bank imports.

    Device resolution is handled by :func:`_resolve_device`. Unless the
    user sets ``AURORA_EMBEDDER_DEVICE=cuda`` AND the GPU is compatible
    with the installed PyTorch, the model runs on CPU.
    """

    name = "sentence_transformer"

    def __init__(self, model_name: str = "all-mpnet-base-v2"):
        from sentence_transformers import SentenceTransformer  # noqa
        device = _resolve_device()
        self._model = SentenceTransformer(model_name, device=device)
        self.model = model_name
        self._device = device
        _log.info(
            "Embedder initialised: model=%s, device=%s", model_name, device,
        )
        # ``get_embedding_dimension`` is the current method (sentence-
        # transformers ≥ 3.0); fall back to the deprecated alias for
        # older installs to avoid breaking the maintainer's environment.
        getter = (getattr(self._model, "get_embedding_dimension", None)
                    or getattr(self._model, "get_sentence_embedding_dimension"))
        self.dim = int(getter())

    def embed(self, text: str) -> List[float]:
        v = self._model.encode([text or ""], normalize_embeddings=True)[0]
        return v.tolist()

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        v = self._model.encode(list(texts), normalize_embeddings=True)
        return [list(map(float, row)) for row in v]


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_EMBEDDER: Any = None


def get_embedder() -> Any:
    """Return the active embedder. Prefers sentence-transformers when
    importable; otherwise the hashed-TF-IDF fallback. Cached."""
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    try:
        _EMBEDDER = SentenceTransformerEmbedder()
    except Exception:
        _EMBEDDER = HashedTfIdfEmbedder()
    return _EMBEDDER
