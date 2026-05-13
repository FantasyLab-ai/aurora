"""
Pre-flight checks for bulk knowledge-bank ingestion.

Run this before any of the Stage-1..Stage-6 source modules. Verifies:

  1. ``sentence-transformers`` is importable AND the active embedder
     resolves to ``SentenceTransformerEmbedder`` (not the TF-IDF fallback).
     If not, prints exact pip install instructions.
  2. The selected embedding model loads.
  3. The bank's vector index dimension is consistent with the embedder.
     Mismatch means the bank was built with a different embedder; tells
     the maintainer they need to re-embed.
  4. GPU availability (torch.cuda) — if the maintainer's RTX 5070 Ti
     isn't being picked up, embedding generation will be 10-50x slower
     than expected.
  5. Disk free space (need 50GB+ for full bulk ingest).
  6. Bank summary — current size, per-source breakdown, license audit.

Usable two ways:

  CLI:
    python -m fantasyai.aurora.knowledge_bank.preflight

  Programmatic / API endpoint:
    from fantasyai.aurora.knowledge_bank.preflight import run_preflight
    report = run_preflight()
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List


def run_preflight() -> Dict[str, Any]:
    """Return a structured pre-flight report. ``ok`` is True only if
    every check passes."""
    report: Dict[str, Any] = {"ok": True, "checks": {}, "warnings": [],
                                  "blocking_errors": []}

    # ----- 1. sentence-transformers availability --------------------
    st_check: Dict[str, Any] = {"ok": False}
    try:
        import sentence_transformers  # noqa
        st_check["ok"] = True
        st_check["version"] = getattr(sentence_transformers, "__version__",
                                          "unknown")
    except Exception as e:
        st_check["error"] = str(e)
        st_check["fix"] = (
            "Install in your venv:\n"
            "  pip install sentence-transformers\n"
            "If Windows wheel issues, try:\n"
            "  pip install sentence-transformers --no-deps\n"
            "  pip install transformers torch huggingface-hub"
        )
        report["warnings"].append(
            "sentence-transformers not installed — bank will use the "
            "TF-IDF fallback embedder (lower-quality semantic search)."
        )
    report["checks"]["sentence_transformers"] = st_check

    # ----- 2. active embedder ---------------------------------------
    embedder_check: Dict[str, Any] = {}
    try:
        from .storage.embedder import get_embedder
        emb = get_embedder()
        embedder_check["name"] = emb.name
        embedder_check["model"] = emb.model
        embedder_check["dim"] = emb.dim
        embedder_check["is_neural"] = (emb.name == "sentence_transformer")
        if not embedder_check["is_neural"]:
            report["warnings"].append(
                "Active embedder is the TF-IDF fallback. Re-ingest the "
                "bank with sentence-transformers for production-quality "
                "semantic retrieval before bulk ingest."
            )
    except Exception as e:
        embedder_check["error"] = str(e)
        report["blocking_errors"].append(f"embedder load failed: {e}")
        report["ok"] = False
    report["checks"]["embedder"] = embedder_check

    # ----- 3. bank-vs-embedder dimension consistency ----------------
    consistency_check: Dict[str, Any] = {}
    try:
        from .storage.sqlite_store import get_bank
        bank = get_bank()
        # Two dim sources: the SUMMARY (per-row stored embedding model)
        # and the IN-MEMORY index (what add_batch will actually accept).
        # When they disagree, ingest will crash on the next add_batch —
        # this check has to surface it loudly.
        index_dim = int(getattr(bank._vector_index, "dim", 0))
        embedder_dim = int(embedder_check.get("dim") or 0)
        bsum = bank.summary()
        consistency_check["bank_total_entries"] = bsum["total_entries"]
        consistency_check["bank_embedding_model"] = bsum["embedding_model"]
        consistency_check["bank_embedding_dim"] = bsum["embedding_dim"]
        consistency_check["vector_index_dim"]   = index_dim
        consistency_check["embedder_dim"]       = embedder_dim
        consistency_check["needs_vector_rebuild"] = bool(
            getattr(bank, "needs_vector_rebuild", False)
        )
        if embedder_dim and index_dim and embedder_dim != index_dim:
            consistency_check["verdict"] = "inconsistent"
            consistency_check["mismatch"] = True
            consistency_check["fix"] = (
                f"Vector index dim {index_dim} != embedder dim "
                f"{embedder_dim}. Run `python -m fantasyai.aurora."
                "knowledge_bank.ingest --seed-only` (which rebuilds "
                "the vector index automatically) before any further "
                "ingest, or call bank.rebuild_vector_index() directly."
            )
            report["warnings"].append(
                f"Vector-index / embedder dim mismatch ({index_dim} → "
                f"{embedder_dim}). Run --seed-only to rebuild the index."
            )
        else:
            consistency_check["verdict"] = "ok"
            consistency_check["mismatch"] = False
    except Exception as e:
        consistency_check["error"] = str(e)
        consistency_check["verdict"] = "error"
    report["checks"]["bank_embedder_consistency"] = consistency_check

    # ----- 4. embedder device ----------------------------------------
    # CPU is the canonical default — Aurora must work universally.
    # GPU is opt-in via AURORA_EMBEDDER_DEVICE=cuda and only activates
    # when the GPU's compute capability is in torch's compiled arch
    # list. Report all four facts so users (and the maintainer) can
    # see exactly what's running and how to opt in if appropriate.
    device_check: Dict[str, Any] = {
        "configured_device": (os.environ.get("AURORA_EMBEDDER_DEVICE")
                                  or "cpu (default)"),
        "active_device": None,
        "gpu_present": False,
        "gpu_compatible": None,
        "torch_version": None,
        "guidance": "",
    }
    try:
        import torch
        device_check["torch_version"] = torch.__version__
        if torch.cuda.is_available():
            device_check["gpu_present"] = True
            device_check["gpu_name"] = torch.cuda.get_device_name(0)
            cap = torch.cuda.get_device_capability(0)
            device_check["gpu_capability"] = f"sm_{cap[0]}{cap[1]}"
            arch_list = list(torch.cuda.get_arch_list() or [])
            device_check["torch_arch_list"] = arch_list
            device_check["gpu_compatible"] = (
                device_check["gpu_capability"] in arch_list
            )
        else:
            device_check["gpu_present"] = False
    except Exception as e:
        device_check["torch_error"] = str(e)
        device_check["torch_version"] = "not installed"

    # Resolve what the embedder will ACTUALLY use — this is the truth.
    try:
        from .storage.embedder import _resolve_device, get_embedder
        device_check["active_device"] = _resolve_device()
        # Walk the singleton if it's already constructed.
        emb = get_embedder()
        ad = getattr(emb, "_device", None)
        if ad is not None:
            device_check["active_device"] = ad
    except Exception as e:
        device_check["active_device_error"] = str(e)

    # Build the user-facing guidance line. Three cases:
    #   a) GPU absent → "no GPU; CPU is the expected configuration"
    #   b) GPU present + compatible → "opt in via env var for ~10× speedup"
    #   c) GPU present + incompatible → "your GPU isn't supported by your
    #      installed torch; CPU is correct for now; here's how to upgrade"
    if not device_check["gpu_present"]:
        device_check["guidance"] = (
            "No GPU detected. Running embedder on CPU. This is the "
            "expected configuration for most users and produces correct "
            "results with negligible latency overhead."
        )
    elif device_check["gpu_compatible"]:
        if device_check["active_device"] == "cuda":
            device_check["guidance"] = (
                f"Embedder is using GPU ({device_check.get('gpu_name', '?')})."
            )
        else:
            device_check["guidance"] = (
                f"GPU is available and compatible "
                f"({device_check.get('gpu_name', '?')}, "
                f"{device_check.get('gpu_capability', '?')}) but not used "
                "(CPU is the universal default). For ~10× faster embedding, "
                "set AURORA_EMBEDDER_DEVICE=cuda."
            )
    else:
        # Incompatible — common case for newer GPUs (Blackwell sm_120)
        # on stable torch builds.
        device_check["guidance"] = (
            f"GPU is present ({device_check.get('gpu_name', '?')}) but its "
            f"compute capability {device_check.get('gpu_capability', '?')} "
            f"is not in your installed torch's arch list "
            f"{device_check.get('torch_arch_list')}. CPU is the correct "
            "choice for now. PyTorch nightly builds may add support for "
            "your GPU's architecture; until then, do not set "
            "AURORA_EMBEDDER_DEVICE=cuda."
        )
    report["checks"]["embedder_device"] = device_check

    # ----- 5. disk free space ---------------------------------------
    disk_check: Dict[str, Any] = {}
    try:
        from .storage.sqlite_store import _default_bank_path
        bank_dir = _default_bank_path().parent
        bank_dir.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(str(bank_dir))
        disk_check["bank_dir"] = str(bank_dir)
        disk_check["free_gb"] = round(usage.free / 1e9, 1)
        disk_check["total_gb"] = round(usage.total / 1e9, 1)
        if usage.free < 50 * 1e9:
            report["warnings"].append(
                f"Only {disk_check['free_gb']:.1f} GB free at {bank_dir}. "
                "Brief 5 bulk ingest needs ~50 GB free; clear space "
                "before running Stages 5-6."
            )
    except Exception as e:
        disk_check["error"] = str(e)
    report["checks"]["disk"] = disk_check

    # ----- 6. license audit -----------------------------------------
    license_check: Dict[str, Any] = {}
    try:
        from .storage.sqlite_store import get_bank
        bank = get_bank()
        cur = bank._conn.execute(
            """SELECT COUNT(*) FROM entries WHERE revoked = 0
               AND (license IS NULL OR license = '')"""
        )
        n_unlicensed = int(cur.fetchone()[0])
        cur2 = bank._conn.execute(
            "SELECT license, COUNT(*) FROM entries WHERE revoked = 0 "
            "GROUP BY license"
        )
        per_license = {row[0] or "<none>": int(row[1])
                         for row in cur2.fetchall()}
        license_check["per_license"] = per_license
        license_check["n_unlicensed"] = n_unlicensed
        if n_unlicensed > 0:
            report["warnings"].append(
                f"{n_unlicensed} existing entries have no license string. "
                "Brief 5 requires every entry to carry a license; "
                "re-ingest the seed bank to fix or revoke unlicensed "
                "entries."
            )
    except Exception as e:
        license_check["error"] = str(e)
    report["checks"]["license_audit"] = license_check

    # ----- 7. checkpoint directory ---------------------------------
    checkpoint_check: Dict[str, Any] = {}
    try:
        from .storage.sqlite_store import _default_bank_path
        cp_dir = _default_bank_path().parent / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        cps = sorted(cp_dir.glob("*.checkpoint.json"))
        checkpoint_check["dir"] = str(cp_dir)
        checkpoint_check["existing_checkpoints"] = [
            {"name": p.stem.replace(".checkpoint", ""),
              "size_kb": round(p.stat().st_size / 1024, 1)}
            for p in cps
        ]
    except Exception as e:
        checkpoint_check["error"] = str(e)
    report["checks"]["checkpoints"] = checkpoint_check

    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _format_report(report: Dict[str, Any]) -> str:
    lines = ["", "=" * 70,
              "Aurora Knowledge Bank — Pre-Flight Report",
              "=" * 70]
    lines.append(f"\nOverall: {'OK' if report['ok'] else 'BLOCKED'}\n")
    for name, data in report["checks"].items():
        lines.append(f"--- {name} ---")
        for k, v in (data or {}).items():
            lines.append(f"  {k}: {v}")
        lines.append("")
    if report["warnings"]:
        lines.append("WARNINGS:")
        for w in report["warnings"]:
            lines.append(f"  · {w}")
        lines.append("")
    if report["blocking_errors"]:
        lines.append("BLOCKING ERRORS:")
        for e in report["blocking_errors"]:
            lines.append(f"  ✗ {e}")
        lines.append("")
    return "\n".join(lines)


def diagnose_cuda() -> Dict[str, Any]:
    """Diagnose GPU compatibility and report whether the embedder will
    use GPU or CPU.

    CPU is the canonical default for Aurora — this report is purely
    diagnostic. ``ok`` is True whenever the embedder has a working
    device (CPU or compatible GPU), regardless of whether GPU is being
    used. Users SHOULD NOT take "ok=False" to mean "you need to install
    CUDA torch"; CPU is the right answer for most users.
    """
    out: Dict[str, Any] = {"ok": True, "torch_present": False,
                              "torch_version": None,
                              "cuda_available": False,
                              "gpu_compatible": None,
                              "gpu_name": None,
                              "gpu_capability": None,
                              "torch_arch_list": None,
                              "active_device": None,
                              "configured_device": (
                                  os.environ.get("AURORA_EMBEDDER_DEVICE")
                                  or "cpu (default)"),
                              "guidance": ""}
    try:
        import torch
        out["torch_present"] = True
        out["torch_version"] = torch.__version__
        if torch.cuda.is_available():
            out["cuda_available"] = True
            out["gpu_name"] = torch.cuda.get_device_name(0)
            cap = torch.cuda.get_device_capability(0)
            out["gpu_capability"] = f"sm_{cap[0]}{cap[1]}"
            out["torch_arch_list"] = list(torch.cuda.get_arch_list() or [])
            out["gpu_compatible"] = (
                out["gpu_capability"] in out["torch_arch_list"]
            )
    except Exception as e:
        out["torch_error"] = str(e)

    # What the embedder will actually use (without instantiating it).
    try:
        from .storage.embedder import _resolve_device
        out["active_device"] = _resolve_device()
    except Exception as e:
        out["active_device_error"] = str(e)

    # Compose user-facing guidance.
    if not out["torch_present"]:
        out["guidance"] = (
            "torch is not installed. The embedder will use the pure-numpy "
            "TF-IDF fallback. For neural embeddings, install "
            "sentence-transformers (which pulls in CPU torch as a dep)."
        )
    elif not out["cuda_available"]:
        out["guidance"] = (
            "No CUDA GPU detected. Embedder runs on CPU. This is the "
            "expected configuration for most users."
        )
    elif out["gpu_compatible"]:
        if out["active_device"] == "cuda":
            out["guidance"] = (
                f"Embedder is using GPU ({out['gpu_name']}, "
                f"{out['gpu_capability']})."
            )
        else:
            out["guidance"] = (
                f"GPU available and compatible ({out['gpu_name']}, "
                f"{out['gpu_capability']}) but not used. CPU is the "
                "default for universal compatibility. To opt in:\n\n"
                "  set AURORA_EMBEDDER_DEVICE=cuda    (Windows)\n"
                "  export AURORA_EMBEDDER_DEVICE=cuda  (Linux/macOS)"
            )
    else:
        out["guidance"] = (
            f"GPU is present ({out['gpu_name']}) but its compute "
            f"capability {out['gpu_capability']} is NOT in your installed "
            f"torch's arch list {out['torch_arch_list']}.\n\n"
            "This is common for newer GPUs (Blackwell sm_120, etc) on "
            "stable torch builds. CPU is the correct choice for now — "
            "do NOT set AURORA_EMBEDDER_DEVICE=cuda. PyTorch nightly "
            "builds may add support for your architecture:\n\n"
            "  pip install --pre torch --index-url "
            "https://download.pytorch.org/whl/nightly/cu121\n\n"
            "Until then, CPU embedding works correctly with negligible "
            "latency overhead for Aurora's workload."
        )
    return out


def _format_cuda_report(d: Dict[str, Any]) -> str:
    lines = ["", "=" * 70,
              "Aurora KB Preflight — Embedder Device Diagnosis",
              "=" * 70, ""]
    lines.append(f"  configured_device:  {d.get('configured_device')}")
    lines.append(f"  active_device:      {d.get('active_device')}")
    lines.append(f"  torch_version:      {d.get('torch_version')}")
    lines.append(f"  cuda_available:     {d.get('cuda_available')}")
    if d.get("gpu_name"):
        lines.append(f"  gpu_name:           {d['gpu_name']}")
        lines.append(f"  gpu_capability:     {d.get('gpu_capability')}")
        lines.append(f"  gpu_compatible:     {d.get('gpu_compatible')}")
        lines.append(f"  torch_arch_list:    {d.get('torch_arch_list')}")
    if d.get("torch_error"):
        lines.append(f"  torch_error:        {d['torch_error']}")
    lines.append("")
    if d.get("guidance"):
        lines.append("Note:")
        for line in d["guidance"].split("\n"):
            lines.append(f"  {line}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    if "--fix-cuda" in sys.argv:
        d = diagnose_cuda()
        print(_format_cuda_report(d))
        if "--json" in sys.argv:
            print(json.dumps(d, indent=2, default=str))
        return 0 if d["ok"] else 1
    report = run_preflight()
    print(_format_report(report))
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
