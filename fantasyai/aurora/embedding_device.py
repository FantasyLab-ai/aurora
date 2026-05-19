"""
Sentence-transformer device selection (v2.0 Stream F).

Aurora's RAG retriever embeds findings + queries with a sentence-
transformer model. The CPU path is the safe default — it runs on
every machine — but on hosts with a GPU it's roughly 3-10× slower
than necessary.

This module ships a tiny device-selection helper that the
knowledge_bank and any synthesis embed-callers consult:

  * Honours an explicit override via ``AURORA_EMBEDDINGS_DEVICE``
    (``cpu`` / ``cuda`` / ``mps`` / ``auto``).
  * Defaults to ``auto`` which picks the best-available accelerator:
    CUDA → MPS (Apple Silicon) → CPU.
  * Returns the SAME callable shape regardless of device, so existing
    embed-callers don't have to change.

The module never imports torch eagerly. We probe lazily and cache
the chosen device on first call.

Failure modes:
  * If the user sets ``AURORA_EMBEDDINGS_DEVICE=cuda`` but CUDA isn't
    available, we fall back to CPU with a single one-shot warning
    (never crash).
  * If torch isn't installed at all, we return "cpu" — sentence-
    transformers' fallback path still works.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional, Tuple

LOG = logging.getLogger(__name__)


# Thread-safe lazy cache.
_LOCK = threading.Lock()
_CACHED_DEVICE: Optional[str] = None
_FALLBACK_WARNED: bool = False


def _probe_torch_device(preferred: str) -> Tuple[str, Optional[str]]:
    """Probe whether the preferred device is actually available.

    Returns ``(chosen_device, warning_or_None)``. Never raises.
    """
    try:
        import torch
    except Exception:
        if preferred not in ("cpu", "auto"):
            return ("cpu",
                    f"torch not installed; falling back to CPU "
                    f"(requested {preferred!r})")
        return ("cpu", None)

    if preferred == "auto":
        if getattr(torch, "cuda", None) and torch.cuda.is_available():
            return ("cuda", None)
        # Apple Silicon Metal.
        mps_backend = getattr(torch.backends, "mps", None)
        if mps_backend is not None and mps_backend.is_available():
            return ("mps", None)
        return ("cpu", None)

    if preferred == "cuda":
        if getattr(torch, "cuda", None) and torch.cuda.is_available():
            return ("cuda", None)
        return ("cpu",
                "AURORA_EMBEDDINGS_DEVICE=cuda but CUDA not available; "
                "falling back to CPU")

    if preferred == "mps":
        mps_backend = getattr(torch.backends, "mps", None)
        if mps_backend is not None and mps_backend.is_available():
            return ("mps", None)
        return ("cpu",
                "AURORA_EMBEDDINGS_DEVICE=mps but MPS not available; "
                "falling back to CPU")

    if preferred == "cpu":
        return ("cpu", None)

    # Unknown preference string.
    return ("cpu",
            f"unknown AURORA_EMBEDDINGS_DEVICE={preferred!r}; "
            f"falling back to CPU")


def get_embedding_device(*, refresh: bool = False) -> str:
    """Return the device sentence-transformer should be loaded on.

    The result is cached after the first call so subsequent embed
    calls don't re-probe torch. Pass ``refresh=True`` to force re-
    detection (useful after a runtime install).
    """
    global _CACHED_DEVICE, _FALLBACK_WARNED
    with _LOCK:
        if _CACHED_DEVICE is not None and not refresh:
            return _CACHED_DEVICE
        preferred = (os.environ.get("AURORA_EMBEDDINGS_DEVICE")
                      or "auto").strip().lower()
        device, warn = _probe_torch_device(preferred)
        _CACHED_DEVICE = device
        if warn and not _FALLBACK_WARNED:
            LOG.warning("aurora.embedding_device: %s", warn)
            _FALLBACK_WARNED = True
        return device


def reset_embedding_device_cache() -> None:
    """Drop the cached device choice. Used in tests."""
    global _CACHED_DEVICE, _FALLBACK_WARNED
    with _LOCK:
        _CACHED_DEVICE = None
        _FALLBACK_WARNED = False


def embedding_device_info() -> dict:
    """Public-safe dict describing the device we'd use. Useful for
    the frontend's status panel."""
    preferred = (os.environ.get("AURORA_EMBEDDINGS_DEVICE")
                  or "auto").strip().lower()
    chosen = get_embedding_device()
    try:
        import torch
        torch_version = torch.__version__
        cuda_available = bool(getattr(torch, "cuda", None)
                                and torch.cuda.is_available())
        mps_available = bool(getattr(torch.backends, "mps", None)
                              and torch.backends.mps.is_available())
    except Exception:
        torch_version = None
        cuda_available = False
        mps_available = False
    return {
        "preferred":      preferred,
        "chosen":         chosen,
        "torch_version":  torch_version,
        "cuda_available": cuda_available,
        "mps_available":  mps_available,
    }
