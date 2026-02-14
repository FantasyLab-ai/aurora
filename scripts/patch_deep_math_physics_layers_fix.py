from __future__ import annotations

from pathlib import Path
import re
import py_compile

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def die(msg: str):
    raise SystemExit(f"ERROR: {msg}")

def main():
    if not TARGET.exists():
        die(f"deep_math.py not found: {TARGET}")

    txt = TARGET.read_text(encoding="utf-8", errors="replace")

    # 1) Ensure physics_invariants is imported safely (optional)
    # We insert it right after physics_diagnostics import line if missing.
    if 'physics_invariants = _try_import("fantasyai.aurora.math.physics", "physics_invariants")' not in txt:
        anchor = 'physics_diagnostics = _try_import("fantasyai.aurora.math.physics", "physics_diagnostics")'
        if anchor not in txt:
            die("Could not find physics_diagnostics optional import anchor.")
        txt = txt.replace(
            anchor,
            anchor + '\nphysics_invariants = _try_import("fantasyai.aurora.math.physics", "physics_invariants")'
        )

    # 2) Remove/replace the duplicated physics blocks near the end of compute_deep_math_v3
    # We replace everything from the first "Phase 3" block OR the "AURORA_PHYSICS_PHASES_2_4" marker
    # up to the final "return out" with a single clean block.

    # Find the last "return out" inside compute_deep_math_v3
    m_return = list(re.finditer(r"\n\s+return out\s*\n", txt))
    if not m_return:
        die("Could not find 'return out' in deep_math.py")
    return_pos = m_return[-1].start()

    # Find a physics block start marker closest before return out
    markers = [
        txt.rfind("\n    # -----------------------------\n    # Phase 3: Physics discovery", 0, return_pos),
        txt.rfind("\n    # ============================================================\n    # AURORA_PHYSICS_PHASES_2_4", 0, return_pos),
    ]
    start_pos = max(markers)

    if start_pos < 0:
        die("Could not find a physics block marker to replace (Phase 3 or AURORA_PHYSICS_PHASES_2_4).")

    # Build replacement block (indented to match compute_deep_math_v3 body)
    replacement = r'''
    # ============================================================
    # Physics Layer (Phases 2–4)
    #   Phase 2: Physics v2 diagnostics (stronger ODE-fit variants)
    #   Phase 3: Physics discovery (multi-model selection)
    #   Phase 4: Invariants + physics-consistency score
    # ============================================================

    # Baseline diagnostics (kept for continuity)
    try:
        if callable(physics_diagnostics):
            out["physics"] = physics_diagnostics(df=df, target_col=ycol, time_col=tcol, seed=seed)
            if isinstance(out["physics"], dict):
                out["physics"].setdefault("note", "ok")
        else:
            out["physics"] = {"note": "skipped", "error": "physics_diagnostics_unavailable"}
    except Exception as e:
        out["physics"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    # Phase 2: v2 diagnostics (your physics_v2.py)
    try:
        if callable(physics_diagnostics_v2):
            out["physics_v2"] = physics_diagnostics_v2(df=df, target_col=ycol, time_col=tcol, seed=seed)
            if isinstance(out["physics_v2"], dict):
                out["physics_v2"].setdefault("note", "ok")
        else:
            out["physics_v2"] = {"note": "skipped", "error": "physics_diagnostics_v2_unavailable"}
    except Exception as e:
        out["physics_v2"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    # Phase 3: discovery (multi-model selection)
    try:
        if callable(physics_discovery):
            out["physics_discovery"] = physics_discovery(df=df, target_col=ycol, time_col=tcol, seed=seed)
            if isinstance(out["physics_discovery"], dict):
                out["physics_discovery"].setdefault("note", "ok")
        else:
            out["physics_discovery"] = {"note": "skipped", "error": "physics_discovery_unavailable"}
    except Exception as e:
        out["physics_discovery"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    # Phase 4: invariants + consistency score
    try:
        if callable(physics_invariants):
            inv = physics_invariants(df=df, target_col=ycol, time_col=tcol, seed=seed)
            out["physics_invariants"] = inv if isinstance(inv, dict) else {"note": "ok", "result": inv}
            if isinstance(out["physics_invariants"], dict):
                out["physics_invariants"].setdefault("note", "ok")
                out["physics_consistency_score"] = (
                    out["physics_invariants"].get("physics_consistency_score")
                    or out["physics_invariants"].get("score")
                )
            else:
                out["physics_consistency_score"] = None
        else:
            # Fallback invariant score (never crashes):
            y = pd.to_numeric(df[ycol], errors="coerce").astype(float).values
            m = np.isfinite(y)
            y2 = y[m]
            if y2.size < 200:
                out["physics_invariants"] = {"note": "skipped", "error": "physics_invariants_unavailable"}
                out["physics_consistency_score"] = None
            else:
                frac_finite = float(np.mean(m))
                frac_neg = float(np.mean(y2 < 0))
                score = max(0.0, min(1.0, frac_finite * (1.0 - frac_neg)))
                out["physics_invariants"] = {"note": "ok", "finite_fraction": frac_finite, "negative_fraction": frac_neg}
                out["physics_consistency_score"] = float(score)
    except Exception as e:
        out["physics_invariants"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}
        out["physics_consistency_score"] = None
'''

    # Replace the block region
    txt2 = txt[:start_pos] + replacement + "\n\n    return out\n" + txt[return_pos + len(m_return[-1].group(0)):]
    TARGET.write_text(txt2, encoding="utf-8")

    # Compile check
    py_compile.compile(str(TARGET), doraise=True)
    print(f"[DONE] patched + compiled clean: {TARGET}")

if __name__ == "__main__":
    main()
