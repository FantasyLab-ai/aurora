from __future__ import annotations

from pathlib import Path
from datetime import datetime
import json
import os
import sys
import traceback
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

def _ollama_generate(prompt: str) -> Dict[str, Any]:
    """
    Minimal Ollama call without extra deps.
    Writes failures into llm_debug.json; never crashes the run.
    """
    import urllib.request

    url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "deepseek-r1:8b")

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    req = urllib.request.Request(
        url + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    out = json.loads(data)
    return out

RUNNER_TEXT = r'''
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from datetime import datetime
import traceback
from typing import Any, Dict, Optional

import pandas as pd

# =========
# Utilities
# =========

def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def _write_json(p: Path, obj: Any) -> None:
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

def _safe_str(x: Any, max_len: int = 4000) -> str:
    s = str(x)
    return s if len(s) <= max_len else (s[:max_len] + "…")

def _ollama_generate(prompt: str) -> Dict[str, Any]:
    """
    Minimal Ollama call without extra deps.
    If it fails, caller should catch and write llm_debug.json.
    """
    import urllib.request

    url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "deepseek-r1:8b")

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    req = urllib.request.Request(
        url + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    return json.loads(data)

# ==========================
# Canonical Deep Math import
# ==========================
# Prefer v3, fallback to v2, then to any alias that exists.
compute_deep_math = None
try:
    from fantasyai.aurora.math.deep_math import compute_deep_math_v3 as compute_deep_math
except Exception:
    try:
        from fantasyai.aurora.math.deep_math import compute_deep_math_v2 as compute_deep_math
    except Exception:
        try:
            from fantasyai.aurora.math.deep_math import compute_deep_math as compute_deep_math  # type: ignore
        except Exception:
            compute_deep_math = None

# ====================
# Dataset Loader (thin)
# ====================
def _resolve_dataset_table(dataset_key: str) -> Dict[str, str]:
    """
    This is intentionally conservative: it reuses your existing external folder structure.
    It expects: fantasyai/data/external/<dataset_key>/raw/<one csv>
    """
    root = Path(__file__).resolve().parents[1]
    folder = root / "fantasyai" / "data" / "external" / dataset_key / "raw"
    if not folder.exists():
        raise FileNotFoundError(f"Missing dataset folder: {folder}")

    csvs = sorted(folder.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not csvs:
        raise FileNotFoundError(f"No .csv found in: {folder}")

    table = csvs[0]
    return {"folder": str(folder), "table": str(table)}

def _load_df(table_path: str) -> pd.DataFrame:
    # Let pandas infer; your mathstack has its own parsing logic anyway.
    return pd.read_csv(table_path)

# ======================
# Math stack v2 (existing)
# ======================
def _run_math_stack_v2(df: pd.DataFrame, seed: int, out_dir: Path) -> Dict[str, Any]:
    # Call your existing mathstack_v2 if present; otherwise write a minimal stub.
    try:
        from fantasyai.aurora.mathstack_v2 import run_mathstack_v2  # type: ignore
        return run_mathstack_v2(df=df, seed=seed, out_dir=out_dir)  # type: ignore
    except Exception as e:
        return {
            "error": f"mathstack_v2_failed:{type(e).__name__}:{e}",
            "trace": traceback.format_exc()[:8000],
        }

def _build_storycard(math_results: Dict[str, Any], deep_math: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Lightweight storycard: summarizes what exists without depending on LLM.
    """
    out: Dict[str, Any] = {
        "version": "storycard_v1",
        "highlights": [],
        "notes": [],
    }
    if isinstance(math_results, dict) and "error" in math_results:
        out["notes"].append("math_results had an error; see math_results.json")
        return out

    # Pull some common fields if present
    if isinstance(math_results, dict):
        for k in ("rows", "n_rows", "n_cols", "numeric_cols_n", "targets", "top_features"):
            if k in math_results:
                out["highlights"].append({k: math_results[k]})

    if deep_math and isinstance(deep_math, dict) and "hypothesis_testing" in deep_math:
        ht = deep_math.get("hypothesis_testing", {})
        top_corr = ht.get("top_correlations", [])
        if isinstance(top_corr, list) and top_corr:
            out["highlights"].append({"top_correlations_n": len(top_corr)})
            out["highlights"].append({"top_corr_example": top_corr[0]})

    return out

def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--seed", type=int, default=42)

    ap.add_argument("--also-math", action="store_true")
    ap.add_argument("--math-v2", action="store_true")
    ap.add_argument("--deep-math", action="store_true")
    ap.add_argument("--also-llm", action="store_true")

    args = ap.parse_args()

    # Resolve dataset + output folder
    resolved = _resolve_dataset_table(args.dataset)
    out_root = Path(__file__).resolve().parents[1] / "outputs" / "aurora_dataset_runs"
    out_root.mkdir(parents=True, exist_ok=True)

    run_dir = out_root / f"{_now_stamp()}__{args.dataset}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    df = _load_df(resolved["table"])

    # Always write table selection (minimal)
    _write_json(run_dir / "tableselection.json", {
        "dataset_key": args.dataset,
        "table": resolved["table"],
        "rows": int(getattr(df, "shape", (0, 0))[0]),
        "cols": int(getattr(df, "shape", (0, 0))[1]),
    })

    math_results: Dict[str, Any] = {}
    deep_out: Optional[Dict[str, Any]] = None
    llm_used = False

    # Run math stack
    if args.also_math and args.math_v2:
        math_results = _run_math_stack_v2(df=df, seed=args.seed, out_dir=run_dir)
        _write_json(run_dir / "math_results.json", math_results)
    else:
        math_results = {"note": "math disabled (missing --also-math --math-v2)"}
        _write_json(run_dir / "math_results.json", math_results)

    # Run deep math
    if args.deep_math:
        if compute_deep_math is None:
            deep_out = {"error": "deep_math_failed:compute_deep_math_not_available"}
        else:
            try:
                # Try common signatures safely
                try:
                    deep_out = compute_deep_math(df=df, seed=args.seed)  # type: ignore
                except TypeError:
                    deep_out = compute_deep_math(df, args.seed)  # type: ignore
            except Exception as e:
                deep_out = {"error": f"deep_math_failed:{type(e).__name__}:{e}", "trace": traceback.format_exc()[:8000]}
        _write_json(run_dir / "deep_math.json", deep_out)

    # Storycard always present if math ran
    try:
        story = _build_storycard(math_results, deep_out)
    except Exception as e:
        story = {"error": f"storycard_failed:{type(e).__name__}:{e}", "trace": traceback.format_exc()[:4000]}
    _write_json(run_dir / "storycard.json", story)

    # LLM narrative (optional)
    if args.also_llm:
        provider = os.environ.get("AURORA_LLM_PROVIDER", "").strip().lower()
        debug: Dict[str, Any] = {"provider": provider}
        narrative: Dict[str, Any] = {"version": "llm_narrative_v1", "provider": provider, "text": ""}

        try:
            if provider == "ollama":
                prompt = (
                    "You are Aurora, a quantitative analyst. "
                    "Write a concise research note based on these JSON snippets.\n\n"
                    f"math_results:\n{json.dumps(math_results, indent=2)[:6000]}\n\n"
                    f"deep_math:\n{json.dumps(deep_out, indent=2)[:6000]}\n\n"
                    "Return: (1) 5 bullet insights, (2) 3 risks/assumptions, (3) 3 next analyses."
                )
                resp = _ollama_generate(prompt)
                llm_used = True
                narrative["text"] = resp.get("response", "") or resp.get("message", {}).get("content", "")
                debug["raw"] = resp
            else:
                narrative["text"] = (
                    "LLM provider not configured. Set AURORA_LLM_PROVIDER=ollama and OLLAMA_URL/OLLAMA_MODEL."
                )
        except Exception as e:
            debug["error"] = f"{type(e).__name__}:{e}"
            debug["trace"] = traceback.format_exc()[:8000]
            narrative["text"] = (
                "LLM call failed; see llm_debug.json. "
                "Core math outputs are still valid."
            )

        _write_json(run_dir / "llm_debug.json", debug)
        _write_json(run_dir / "llm_narrative.json", narrative)

    # FINAL stdout summary (so you always see it ran)
    summary = {
        "dataset_key": args.dataset,
        "folder": resolved["folder"],
        "table": resolved["table"],
        "seed": args.seed,
        "run_dir": str(run_dir),
        "math_stack": str(run_dir),
        "llm_used": bool(llm_used),
    }
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    _main()
'''.lstrip()

def main():
    if not RUNNER.exists():
        raise SystemExit(f"Missing: {RUNNER}")

    bak = backup(RUNNER)
    print(f"[OK] backup -> {bak}")

    RUNNER.write_text(RUNNER_TEXT, encoding="utf-8")
    print(f"[OK] wrote stable runner -> {RUNNER}")

    import py_compile
    py_compile.compile(str(RUNNER), doraise=True)
    print("[DONE] runner restored: full outputs + final stdout JSON")

if __name__ == "__main__":
    main()
