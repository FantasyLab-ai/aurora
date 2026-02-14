from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
MATH = ROOT / "fantasyai" / "aurora" / "math"
PHYSICS = MATH / "physics.py"
DISCOVERY = MATH / "physics_discovery.py"
PHYSICS_V2 = MATH / "physics_v2.py"

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

def backup(p: Path) -> Path:
    b = p.with_suffix(p.suffix + f".bak_seedfix_{STAMP}")
    b.write_text(p.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return b

def ensure_alias_in_physics(src: str) -> tuple[str, bool]:
    """
    Ensure physics_invariants exists in physics.py (simple, stable implementation).
    This satisfies optional import expectations without touching existing logic.
    """
    if re.search(r"^def\s+physics_invariants\s*\(", src, flags=re.M):
        return src, False

    block = r'''
def physics_invariants(df, target_col=None, time_col=None, seed=None, **kwargs):
    """
    Minimal invariants summary (compat shim).

    Returns a small, stable dict used by deep_math aggregations:
      - finite_fraction
      - negative_fraction

    Does NOT change any core physics behavior.
    """
    try:
        import numpy as np
        import pandas as pd

        if not isinstance(df, pd.DataFrame):
            df = pd.DataFrame(df)

        if target_col is None or target_col not in df.columns:
            try:
                target_col = select_numeric_target(df)
            except Exception:
                target_col = None

        if target_col is None or target_col not in df.columns:
            return {"note": "failed", "error": "no_numeric_target"}

        y = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=float)
        finite = np.isfinite(y)
        finite_fraction = float(finite.mean()) if y.size else 0.0
        y2 = y[finite]
        negative_fraction = float((y2 < 0).mean()) if y2.size else 0.0

        return {"note": "ok", "finite_fraction": finite_fraction, "negative_fraction": negative_fraction}
    except Exception as e:
        return {"note": "failed", "error": f"{type(e).__name__}: {e}"}
'''.strip()

    return src.rstrip() + "\n\n" + block + "\n", True

def patch_signature(src: str, func_name: str, new_def_line: str) -> tuple[str, bool]:
    """
    Replace ONLY the 'def <func_name>(...)' line at top-level.
    """
    pat = re.compile(rf"^(def\s+{re.escape(func_name)}\s*)\((.*?)\)\s*:\s*$", re.M)
    m = pat.search(src)
    if not m:
        return src, False
    old_line = src[m.start():m.end()]
    return src.replace(old_line, new_def_line, 1), True

def ensure_physics_v2_aliases(src: str) -> tuple[str, bool]:
    """
    Ensure physics_v2.py exposes physics_discovery (alias) because deep_math expects it.
    """
    changed = False

    if "physics_discovery" not in src:
        alias = r'''
# ----------------------------------------------------------------------
# Back-compat alias: some pipelines expect physics_discovery inside physics_v2
# ----------------------------------------------------------------------
try:
    from .physics_discovery import physics_discovery as physics_discovery
except Exception:
    physics_discovery = None
'''.strip()
        src = src.rstrip() + "\n\n" + alias + "\n"
        changed = True

    return src, changed

def main():
    # --- physics.py ---
    if not PHYSICS.exists():
        raise FileNotFoundError(PHYSICS)

    psrc = PHYSICS.read_text(encoding="utf-8", errors="replace")
    backup(PHYSICS)

    # Make physics_diagnostics accept seed + **kwargs (ignored)
    psrc, ch1 = patch_signature(
        psrc,
        "physics_diagnostics",
        "def physics_diagnostics(df, target_col=None, time_col=None, seed=None, **kwargs):"
    )

    # Add physics_invariants if missing
    psrc, ch2 = ensure_alias_in_physics(psrc)

    if ch1 or ch2:
        PHYSICS.write_text(psrc, encoding="utf-8")

    # --- physics_discovery.py ---
    if not DISCOVERY.exists():
        raise FileNotFoundError(DISCOVERY)

    dsrc = DISCOVERY.read_text(encoding="utf-8", errors="replace")
    backup(DISCOVERY)

    # Make discover_physics_models accept seed + **kwargs (ignored)
    dsrc, ch3 = patch_signature(
        dsrc,
        "discover_physics_models",
        "def discover_physics_models(df, target_col=None, time_col=None, seed=None, **kwargs):"
    )

    # Make physics_discovery wrapper accept seed + **kwargs too (if present)
    dsrc, ch4 = patch_signature(
        dsrc,
        "physics_discovery",
        "def physics_discovery(*args, seed=None, **kwargs):"
    )

    if ch3 or ch4:
        DISCOVERY.write_text(dsrc, encoding="utf-8")

    # --- physics_v2.py ---
    if PHYSICS_V2.exists():
        vsrc = PHYSICS_V2.read_text(encoding="utf-8", errors="replace")
        backup(PHYSICS_V2)

        # Make physics_diagnostics_v2 accept seed + **kwargs if it exists
        vsrc, ch5 = patch_signature(
            vsrc,
            "physics_diagnostics_v2",
            "def physics_diagnostics_v2(df, target_col=None, time_col=None, seed=None, **kwargs):"
        )

        vsrc, ch6 = ensure_physics_v2_aliases(vsrc)

        if ch5 or ch6:
            PHYSICS_V2.write_text(vsrc, encoding="utf-8")

    # Compile check
    import py_compile
    py_compile.compile(str(PHYSICS), doraise=True)
    py_compile.compile(str(DISCOVERY), doraise=True)
    if PHYSICS_V2.exists():
        py_compile.compile(str(PHYSICS_V2), doraise=True)

    print("[OK] seed/kwargs compat patch applied + compiled clean")
    print(" -", PHYSICS)
    print(" -", DISCOVERY)
    if PHYSICS_V2.exists():
        print(" -", PHYSICS_V2)

if __name__ == "__main__":
    main()
