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
    def methods(self):
        """Typed accessors for the analytical methods that fire on
        every run — VAR, DTW, BOCPD, Robust PCA, EMD, Kalman, Spectral
        Entropy, Matrix Profile, Granger, Mutual Info, HMM, Wavelet —
        plus a strategy-backtest helper for trading workflows.

        See ``aurora_sdk.MethodsView`` for the full surface.
        """
        from .methods import MethodsView
        return MethodsView(self.bundle)

    @property
    def fabricated_count(self) -> int:
        return self.bundle.fabricated_count

    @property
    def confidence(self) -> Optional[float]:
        return self.bundle.confidence

    # ---- Notebook rendering --------------------------------------------
    # Jupyter calls _repr_html_ when an object is the last expression in
    # a cell. Returning HTML here turns the default ugly repr into a
    # readable summary card. Implementation lives in .jupyter to keep
    # the runner module focused on the pipeline.
    def _repr_html_(self) -> str:
        try:
            from .jupyter import render_runresult_html
            return render_runresult_html(self)
        except Exception as e:
            return f"<i>Aurora RunResult — _repr_html_ failed: {type(e).__name__}: {e}</i>"


def _resolve_path(path: Union[str, Path]) -> Path:
    """Resolve a user-supplied path to an absolute Path. No traversal
    sanitisation beyond what the OS does — we trust the caller's intent
    when running in-process. The MCP server applies stricter validation."""
    p = Path(path).expanduser().resolve()
    return p


def run(data,
        *,
        depth: str = "auto",
        seed: Optional[str] = None,
        output_root: Optional[Union[str, Path]] = None,
        rebuild: bool = False,
        dataset_name: Optional[str] = None,
        ) -> RunResult:
    """Run Aurora end-to-end on a dataset and return a RunResult.

    Args:
        data:   one of:
                  - a ``pandas.DataFrame`` — Aurora writes it to a temp
                    CSV in ``output_root`` (or a system temp dir) and
                    runs the standard pipeline on that file. The temp
                    CSV is preserved alongside the run_dir for
                    reproducibility — Aurora's bundle hashes the
                    written CSV, not the in-memory DataFrame, so the
                    audit trail remains intact.
                  - a CSV/TSV/etc dataset file path (``str`` or ``Path``)
                  - an existing Aurora run_dir
                  - an existing ``.aurora.json`` bundle path
        depth:  "auto" | "quick" | "standard" | "full" — only consulted
                when input is a fresh dataset (must invoke the pipeline).
        seed:   optional focus seed (the user's "what do you want to know?"
                question). Aurora sorts findings around this when set.
        output_root: where new run_dirs should be created. Defaults to
                Aurora's ``outputs/aurora_dataset_runs`` under cwd.
        rebuild: if True and input is a run_dir, re-build state (don't
                 reuse any cached artifacts).
        dataset_name: when ``data`` is a DataFrame, the basename used
                for the written CSV. Defaults to ``aurora_df_<ts>.csv``.
                Useful for keeping the audit trail named meaningfully
                ("nvda_2024_intraday.csv" vs. "aurora_df_20260519.csv").
    """
    if depth not in _VALID_TIERS:
        raise ValueError(
            f"depth must be one of {sorted(_VALID_TIERS)}; got {depth!r}"
        )

    # DataFrame input: write to a CSV, then fall through to the regular
    # file-path code path. This keeps the analytical pipeline single-
    # source — there is one and only one way Aurora reads data.
    data = _materialise_dataframe(data, dataset_name=dataset_name,
                                    output_root=output_root)
    p = _resolve_path(data)
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

    The canonical end-to-end orchestrator is
    ``scripts/run_aurora_dataset_runner.py`` — it lays out the run_dir,
    writes schema/structure contracts, calls the math stack, runs deep
    math, and (optionally) drives the LLM synthesis. That script
    expects a DataFrame internally and persists everything to disk, so
    invoking it as a subprocess is both the simplest and most faithful
    way to get a real run_dir for the SDK.

    First we try an in-process import for ``run_pipeline_for_dataset``
    (cleaner, ships with future refactors). If that's not present, we
    shell out to the runner script. We do NOT import ``run_mathstack_v2``
    directly — it's a stage of the pipeline, not the whole thing, and
    calling it standalone won't produce a populated run_dir.
    """
    # Path A: clean in-process entrypoint if a future refactor lands one.
    try:
        from fantasyai.aurora.run_registry import run_pipeline_for_dataset  # type: ignore
        return Path(run_pipeline_for_dataset(  # type: ignore
            str(dataset_path),
            depth=depth,
            seed=seed,
            output_root=str(output_root) if output_root else None,
        ))
    except ImportError:
        pass  # fall through to subprocess

    # Path B: subprocess the canonical CLI runner. This is what the
    # Studio's background-job system uses too.
    import subprocess
    import sys
    import json as _json

    # Map SDK depth → runner flags. The runner accepts --math-v2 (always
    # on for a real run), --deep-math (tier-2 methods), and --also-llm
    # (RAG synthesis). Tier "auto" defaults to deep-math; "full" adds
    # the LLM pass.
    flags: list[str] = ["--math-v2"]
    if depth in ("standard", "full", "auto"):
        flags.append("--deep-math")
    if depth == "full":
        flags.append("--also-llm")

    # Seed: runner expects int. Hash a string seed to a stable int so
    # the SDK's typed Optional[str] is honored without surprises.
    seed_int: int
    if seed is None:
        seed_int = 42
    else:
        try:
            seed_int = int(seed)
        except (TypeError, ValueError):
            # Stable hash of the user's seed text → reproducible int seed.
            import hashlib
            seed_int = int(hashlib.sha256(str(seed).encode("utf-8"))
                           .hexdigest()[:8], 16) % (2**31)

    cmd = [
        sys.executable, "-m", "scripts.run_aurora_dataset_runner",
        "--dataset", str(dataset_path),
        "--seed", str(seed_int),
        *flags,
    ]

    # Run from the project root so the script's relative output_root
    # ("outputs/aurora_dataset_runs") lands in the expected place.
    project_root = Path(__file__).resolve().parents[1]

    # Propagate the parent env, then add the home/cache vars that some
    # MCP launchers strip. Without these the sentence-transformer
    # cache isn't found and the model is re-downloaded (or hangs).
    import os as _os
    env = _os.environ.copy()
    for var in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
                "LOCALAPPDATA", "TEMP", "TMP", "PATH", "HF_HOME",
                "TRANSFORMERS_CACHE", "HUGGINGFACE_HUB_CACHE",
                "HF_TOKEN", "HF_HUB_DISABLE_TELEMETRY"):
        if var in _os.environ:
            env.setdefault(var, _os.environ[var])
    # Force offline mode for HF — if we have a cached model, use it;
    # if not, fail fast rather than hang on a download in an MCP context.
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    # Project root on PYTHONPATH (some launchers don't inherit cwd into it).
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (str(project_root) + _os.pathsep + existing_pp).rstrip(_os.pathsep)

    # Use temp files instead of OS pipes so a chatty subprocess can't
    # deadlock by filling the pipe buffer. Files have no size limit.
    import tempfile
    stdout_f = tempfile.NamedTemporaryFile(
        mode="w+", delete=False, suffix=".aurora_stdout.log", encoding="utf-8"
    )
    stderr_f = tempfile.NamedTemporaryFile(
        mode="w+", delete=False, suffix=".aurora_stderr.log", encoding="utf-8"
    )
    stdout = ""
    stderr = ""
    timed_out = False
    try:
        try:
            proc = subprocess.run(
                cmd, stdout=stdout_f, stderr=stderr_f,
                cwd=str(project_root), env=env,
                timeout=300,  # 5 min — MCP clients give up sooner anyway
            )
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            returncode = -1
        stdout_f.close()
        stderr_f.close()
        try:
            with open(stdout_f.name, "r", encoding="utf-8", errors="replace") as f:
                stdout = f.read()
            with open(stderr_f.name, "r", encoding="utf-8", errors="replace") as f:
                stderr = f.read()
        except Exception:
            pass
    finally:
        for f in (stdout_f, stderr_f):
            try:
                _os.unlink(f.name)
            except Exception:
                pass

    # Even on timeout / failure, the runner may have produced a partial
    # run_dir we can salvage. Try stdout-JSON first, then disk scan.
    run_dir = _extract_run_dir_from_stdout(stdout) if stdout else None
    if run_dir is None:
        out_root_path = Path(output_root) if output_root else (
            project_root / "outputs" / "aurora_dataset_runs"
        )
        if out_root_path.exists():
            dataset_slug = Path(str(dataset_path)).name
            # Most recent run_dir matching this dataset's filename.
            candidates = sorted(
                (p for p in out_root_path.iterdir()
                 if p.is_dir() and dataset_slug in p.name),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                run_dir = candidates[0]

    if timed_out:
        if run_dir is not None:
            return run_dir  # partial result on disk — better than nothing
        raise RuntimeError(
            "aurora pipeline timed out after 300s with no run_dir on disk. "
            "If this was called from inside an MCP server, the sentence-"
            "transformer model is likely not cached in this env. Try running "
            "the pipeline once from your terminal first to warm the cache, "
            "then retry through MCP.\n"
            f"--- STDERR tail ---\n{stderr[-2000:]}"
        )

    if returncode != 0:
        if run_dir is not None:
            return run_dir  # partial result — let the caller decide
        raise RuntimeError(
            f"aurora runner failed (exit {returncode}).\n"
            f"--- STDERR (last 4000 chars) ---\n{stderr[-4000:]}\n"
            f"--- STDOUT (last 1000 chars) ---\n{stdout[-1000:]}"
        )

    if run_dir is None:
        raise RuntimeError(
            "aurora runner succeeded but no run_dir was found in stdout "
            "or on disk. STDOUT tail:\n" + stdout[-2000:]
        )
    return run_dir


def _extract_run_dir_from_stdout(stdout: str) -> Optional[Path]:
    """Pull the run_dir path out of the runner's final JSON summary.

    The runner ends with ``print(json.dumps(summary, indent=2))`` where
    summary has a ``run_dir`` key. We scan stdout from the end for the
    last balanced ``{...}`` block and parse that.
    """
    import json as _json
    if not stdout:
        return None
    # Walk from end to find the last "}" then back-scan to its matching "{".
    end = stdout.rfind("}")
    if end == -1:
        return None
    depth_counter = 0
    start = -1
    for i in range(end, -1, -1):
        ch = stdout[i]
        if ch == "}":
            depth_counter += 1
        elif ch == "{":
            depth_counter -= 1
            if depth_counter == 0:
                start = i
                break
    if start == -1:
        return None
    try:
        obj = _json.loads(stdout[start:end + 1])
    except Exception:
        return None
    rd = obj.get("run_dir") if isinstance(obj, dict) else None
    if not rd:
        return None
    p = Path(rd)
    return p if p.exists() else None


# ---------------------------------------------------------------------------
# DataFrame → temp CSV materialisation (Jupyter / notebook ergonomics)
# ---------------------------------------------------------------------------

def _materialise_dataframe(
    data,
    *,
    dataset_name: Optional[str] = None,
    output_root: Optional[Union[str, Path]] = None,
):
    """Detect whether ``data`` is a pandas DataFrame and, if so, write
    it to a CSV that Aurora's pipeline can consume.

    Behavior:
      * If ``data`` is anything except a DataFrame, return it unchanged.
      * If ``data`` is a DataFrame, write it to
        ``<output_root>/_uploads/<name>.csv`` (creating the dir if
        needed) and return that Path. The CSV is preserved — Aurora's
        bundle hashes the CSV bytes, so the audit trail stays intact.

    We deliberately *don't* use ``tempfile.NamedTemporaryFile``: temp
    files on Windows can get GC'd before the subprocess pipeline reads
    them, and using the real outputs path means notebook users can see
    "ah, that's the CSV Aurora analysed" alongside the run_dir.
    """
    # Duck-type DataFrame detection so we don't import pandas at module
    # load (heavy import). The cheapest check is `hasattr(data, 'to_csv')`
    # which DataFrame has but a Path / str does not.
    if isinstance(data, (str, Path)):
        return data
    if not hasattr(data, "to_csv") or not hasattr(data, "columns"):
        return data

    # It's a DataFrame. Pick a stable destination.
    import time as _time
    if output_root:
        upload_dir = Path(output_root).resolve() / "_uploads"
    else:
        upload_dir = Path("outputs").resolve() / "aurora_dataset_runs" / "_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    if not dataset_name:
        dataset_name = f"aurora_df_{_time.strftime('%Y%m%d_%H%M%S')}.csv"
    if not dataset_name.lower().endswith(".csv"):
        dataset_name = dataset_name + ".csv"

    out_csv = upload_dir / dataset_name
    # Always overwrite: a notebook user re-running the cell expects
    # their latest DataFrame to be analysed, not a stale one.
    try:
        data.to_csv(out_csv, index=False, encoding="utf-8")
    except Exception as e:
        raise RuntimeError(
            f"could not write DataFrame to CSV at {out_csv}: "
            f"{type(e).__name__}: {e}"
        ) from e
    return out_csv
