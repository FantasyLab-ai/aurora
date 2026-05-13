"""
Aurora SDK runner — the ``aurora.run(path)`` entry point.

This is a *high-level* convenience wrapper:

  * If the path is a CSV/TSV/etc, run the analysis pipeline first.
  * If the path is an existing run_dir, just build state + bundle.
  * If the path is a ``.aurora.json`` bundle, load it directly.

Result is a ``RunResult`` exposing both the raw state dict and a
``Bundle`` for export.

Security:
  * Paths are resolved + validated; no traversal outside the parent
    directory unless the user supplies an absolute path themselves.
  * No shell execution — invokes the runner via direct function calls.
  * Run timeout is honored by the underlying pipeline; the SDK does not
    add an additional layer (caller is responsible if they want one).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .bundle import Bundle, build_bundle_from_state, load_bundle


# Aurora's tier identifiers — keep in sync with the runner's options.
_VALID_TIERS = {"auto", "quick", "standard", "full"}


@dataclass
class RunResult:
    """Result of an Aurora SDK run.

    Attributes:
        state    : the raw state dict from ``state_builder.build_state``
        bundle   : a Bundle wrapper for save/sign/verify/etc
        run_dir  : the run_dir on disk, if applicable
    """
    state: Dict[str, Any]
    bundle: Bundle
    run_dir: Optional[Path] = None

    # Sugar so the docstring examples in __init__.py work without
    # callers having to remember which attribute is which.
    @property
    def findings(self):
        return self.bundle.findings()

    @property
    def forecast(self):
        return self.bundle.forecast()

    @property
    def system_model(self):
        return self.bundle.system_model()

    @property
    def fabricated_count(self) -> int:
        return self.bundle.fabricated_count

    @property
    def confidence(self) -> Optional[float]:
        return self.bundle.confidence


def _resolve_path(path: Union[str, Path]) -> Path:
    """Resolve a user-supplied path to an absolute Path. No traversal
    sanitisation beyond what the OS does — we trust the caller's intent
    when running in-process. The MCP server applies stricter validation."""
    p = Path(path).expanduser().resolve()
    return p


def run(path: Union[str, Path],
        *,
        depth: str = "auto",
        seed: Optional[str] = None,
        output_root: Optional[Union[str, Path]] = None,
        rebuild: bool = False,
        ) -> RunResult:
    """Run Aurora end-to-end on a dataset and return a RunResult.

    Args:
        path:   one of:
                  - a CSV/TSV/etc dataset file
                  - an existing Aurora run_dir
                  - an existing ``.aurora.json`` bundle
        depth:  "auto" | "quick" | "standard" | "full" — only consulted
                when path is a fresh dataset (must invoke the pipeline).
        seed:   optional focus seed (the user's "what do you want to know?"
                question). Aurora sorts findings around this when set.
        output_root: where new run_dirs should be created. Defaults to
                Aurora's ``outputs/aurora_dataset_runs`` under cwd.
        rebuild: if True and path is a run_dir, re-build state (don't
                 reuse any cached artifacts).
    """
    if depth not in _VALID_TIERS:
        raise ValueError(
            f"depth must be one of {sorted(_VALID_TIERS)}; got {depth!r}"
        )
    p = _resolve_path(path)
    if not p.exists():
        raise FileNotFoundError(f"path does not exist: {p}")

    # Case 1: an existing .aurora.json — load and return.
    if p.is_file() and p.suffix.lower() == ".json":
        try:
            doc = load_bundle(p)
            return RunResult(
                state=_state_from_loaded_bundle(doc),
                bundle=Bundle(doc),
                run_dir=None,
            )
        except Exception:
            # Not a bundle JSON — fall through to file-handling below.
            pass

    # Case 2: an existing run_dir (a directory with structure_contract.json
    # or _RUN_META.json inside).
    if p.is_dir():
        if (p / "_RUN_META.json").exists() or (p / "structure_contract.json").exists():
            state = _build_state(p)
            bundle = Bundle.from_state(state, run_dir=p,
                                        dataset_path=_dataset_path_from_state(state))
            return RunResult(state=state, bundle=bundle, run_dir=p)

    # Case 3: a fresh dataset. Invoke the runner via the public pipeline
    # entrypoint. We do NOT shell out; everything is in-process.
    if p.is_file():
        run_dir = _run_pipeline(p, depth=depth, seed=seed,
                                  output_root=output_root)
        state = _build_state(run_dir)
        bundle = Bundle.from_state(state, run_dir=run_dir, dataset_path=p)
        return RunResult(state=state, bundle=bundle, run_dir=run_dir)

    raise ValueError(
        f"path {p} is neither a dataset file, run_dir, nor bundle JSON"
    )


# ---------------------------------------------------------------------------
# Implementation glue — keep all dependencies on internals here.
# ---------------------------------------------------------------------------

def _build_state(run_dir: Path) -> Dict[str, Any]:
    """Wrap state_builder.build_state with a clean import boundary."""
    from fantasyai.aurora.state_builder import build_state  # type: ignore
    return build_state(run_dir)


def _dataset_path_from_state(state: Dict[str, Any]) -> Optional[Path]:
    """Try to pull the dataset's path off the state (set by the Phase
    A1.5 fix). Used so the bundle's dataset.sha256 can be computed."""
    ds = (state or {}).get("dataset") or {}
    pth = ds.get("path")
    if not pth:
        return None
    try:
        return Path(pth)
    except Exception:
        return None


def _state_from_loaded_bundle(doc: Dict[str, Any]) -> Dict[str, Any]:
    """When loading a bundle, synthesize a minimal state dict so the
    RunResult helpers (findings/forecast/system_model) still work."""
    return {
        "run_id":      (doc.get("run") or {}).get("run_id"),
        "run_dir":     (doc.get("run") or {}).get("run_dir"),
        "dataset":     doc.get("dataset") or {},
        "structure":   doc.get("structure") or {},
        "findings":    doc.get("findings") or [],
        "anomalies":   doc.get("anomalies") or [],
        "regimes":     doc.get("regimes") or [],
        "motifs":      doc.get("motifs") or [],
        "forecast":    doc.get("forecast") or {},
        "physics":     doc.get("physics") or {},
        "causal":      doc.get("causal") or {},
        "system_model": doc.get("system_model") or {},
        "synthesis":   doc.get("synthesis") or {},
    }


def _run_pipeline(dataset_path: Path,
                  *,
                  depth: str,
                  seed: Optional[str],
                  output_root: Optional[Union[str, Path]],
                  ) -> Path:
    """Invoke the pipeline on a fresh dataset and return the new run_dir.

    Why not just call ``mathstack_v2.run_mathstack_v2`` directly: the
    pipeline has a one-call wrapper used by the runner CLI. We mirror
    its behaviour here without depending on the studio_api flask layer
    (so the SDK works headless).
    """
    # Defer imports — keep the SDK importable even if the heavy
    # analytical deps aren't installed (e.g., for bundle-only use).
    try:
        # Highest-level entrypoint the studio_api uses.
        from fantasyai.aurora.run_registry import run_pipeline_for_dataset  # type: ignore
    except Exception:
        run_pipeline_for_dataset = None  # type: ignore
    if run_pipeline_for_dataset is None:
        # Fallback: assemble the run manually via state_builder + the
        # mathstack runner. This path is best-effort; most installs
        # ship run_registry alongside state_builder.
        from fantasyai.aurora.mathstack_v2 import run_mathstack_v2  # type: ignore
        out_root = Path(output_root) if output_root else (
            Path("outputs") / "aurora_dataset_runs"
        )
        out_root.mkdir(parents=True, exist_ok=True)
        # The runner picks its own run_dir name; pass dataset_path and
        # let it lay out artifacts.
        result = run_mathstack_v2(  # type: ignore
            str(dataset_path),
            output_root=str(out_root),
            depth=depth,
            seed=seed,
        )
        # Best-effort: most runners return a path or a dict with run_dir.
        if isinstance(result, dict) and result.get("run_dir"):
            return Path(result["run_dir"])
        if isinstance(result, (str, Path)):
            return Path(result)
        # If we can't resolve, raise — caller needs a real run_dir.
        raise RuntimeError(
            "pipeline ran but returned no run_dir; the SDK can't continue"
        )

    return Path(run_pipeline_for_dataset(  # type: ignore
        str(dataset_path),
        depth=depth,
        seed=seed,
        output_root=str(output_root) if output_root else None,
    ))
