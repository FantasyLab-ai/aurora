from __future__ import annotations
from pathlib import Path
import re
import py_compile
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def die(msg: str):
    raise SystemExit(f"ERROR: {msg}")

def backup(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak_phase34_{ts}")
    bak.write_bytes(path.read_bytes())
    print(f"[OK] backup -> {bak}")
    return bak

def inject_after(text: str, needle: str, blob: str) -> str:
    i = text.find(needle)
    if i == -1:
        die(f"needle not found: {needle}")
    j = i + len(needle)
    return text[:j] + blob + text[j:]

def main():
    if not TARGET.exists():
        die(f"deep_math.py not found at {TARGET}")

    src = TARGET.read_text(encoding="utf-8", errors="strict")

    # Idempotency guard
    if "PHASE34_PATCH_APPLIED" in src:
        print("[OK] Phase 3–4 patch already present; skipping.")
        py_compile.compile(str(TARGET), doraise=True)
        print("[DONE] deep_math.py compiled clean")
        return

    backup(TARGET)

    # 1) Ensure the always-present keys exist (so UI never breaks)
    # Find the block where out["physics_discovery"] is set up-front; add invariants keys next to it.
    setup_anchor = 'out["physics_discovery"] = {"note": "skipped", "error": None}\n'
    if setup_anchor not in src:
        die("Could not find physics_discovery setup anchor in compute_deep_math_v3 (expected keys block).")

    src = inject_after(
        src,
        setup_anchor,
        '    out["physics_invariants"] = {"note": "skipped", "error": None}\n'
        '    out["physics_consistency_score"] = None\n'
        '    # PHASE34_PATCH_APPLIED\n'
    )

    # 2) Insert Phase 3 (physics discovery) + Phase 4 (constraints/invariants scoring)
    # Anchor on final "return out" of compute_deep_math_v3
    m = re.search(r"\n\s*return out\s*\n", src)
    if not m:
        die("Could not find 'return out' in compute_deep_math_v3.")

    insert_point = m.start()

    phase34_block = r'''
    # -------------------------------------------
    # Phase 3 — Physics discovery (multi-model selection)
    # -------------------------------------------
    try:
        if callable(physics_diagnostics_v2):
            out["physics_v2"] = physics_diagnostics_v2(df=df, target_col=ycol, time_col=tcol, seed=seed)
            if isinstance(out["physics_v2"], dict):
                out["physics_v2"].setdefault("note", "ok")
                out["physics_v2"].setdefault("error", None)
        else:
            out["physics_v2"] = {"note": "skipped", "error": "physics_diagnostics_v2_unavailable"}
    except Exception as e:
        out["physics_v2"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    try:
        if callable(physics_discovery):
            # expects physics_discovery(df=..., target_col=..., time_col=..., seed=...)
            out["physics_discovery"] = physics_discovery(df=df, target_col=ycol, time_col=tcol, seed=seed)
            if isinstance(out["physics_discovery"], dict):
                out["physics_discovery"].setdefault("note", "ok")
                out["physics_discovery"].setdefault("error", None)
        else:
            out["physics_discovery"] = {"note": "skipped", "error": "physics_discovery_unavailable"}
    except Exception as e:
        out["physics_discovery"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    # -------------------------------------------
    # Phase 4 — Constraints + invariants layer (physics-consistency score)
    # -------------------------------------------
    try:
        y = pd.to_numeric(df[ycol], errors="coerce").replace([np.inf, -np.inf], np.nan).values.astype(float)
        finite = np.isfinite(y)
        y2 = y[finite]
        inv = {"note": "ok", "error": None}

        if y2.size < 200:
            inv.update({"note": "skipped", "error": "not_enough_finite_samples"})
            out["physics_invariants"] = inv
            out["physics_consistency_score"] = None
        else:
            # Basic invariants that generalize across scientific telemetry:
            # - Finite fraction
            # - Non-negativity fraction
            # - Boundedness (robust range)
            # - Smoothness (median absolute delta)
            finite_frac = float(np.mean(finite))
            nonneg_frac = float(np.mean(y2 >= 0.0))

            q01, q99 = np.quantile(y2, [0.01, 0.99])
            robust_range = float(q99 - q01)
            mad_delta = float(np.nanmedian(np.abs(np.diff(y2)))) if y2.size > 2 else float("nan")

            inv.update({
                "finite_fraction": finite_frac,
                "nonneg_fraction": nonneg_frac,
                "robust_q01": float(q01),
                "robust_q99": float(q99),
                "robust_range": robust_range,
                "mad_delta": mad_delta,
            })

            # Score [0,1] – conservative: rewards finiteness + not wildly negative + not exploding range.
            # No domain assumptions beyond "telemetry should be mostly finite".
            score = 0.0
            score += 0.55 * max(0.0, min(1.0, finite_frac))
            score += 0.25 * max(0.0, min(1.0, nonneg_frac))

            # Range penalty (relative to scale). Use robust scale to avoid outliers.
            scale = float(np.nanmedian(np.abs(y2))) + 1e-9
            range_ratio = robust_range / (scale + 1e-9)
            # If range_ratio is huge, penalize. If moderate, OK.
            range_component = 1.0 / (1.0 + max(0.0, range_ratio - 50.0) / 50.0)
            score += 0.20 * max(0.0, min(1.0, float(range_component)))

            out["physics_invariants"] = inv
            out["physics_consistency_score"] = float(max(0.0, min(1.0, score)))
    except Exception as e:
        out["physics_invariants"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}
        out["physics_consistency_score"] = None
'''

    src = src[:insert_point] + phase34_block + src[insert_point:]

    TARGET.write_text(src, encoding="utf-8")
    print(f"[OK] patched -> {TARGET}")

    py_compile.compile(str(TARGET), doraise=True)
    print("[DONE] deep_math.py compiled clean")

if __name__ == "__main__":
    main()
