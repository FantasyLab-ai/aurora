from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
PHYSICS = ROOT / "fantasyai" / "aurora" / "math" / "physics.py"

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

def backup(path: Path) -> Path:
    b = path.with_suffix(path.suffix + f".bak_axisfix_{STAMP}")
    b.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return b

def replace_func_block(src: str, name: str, new_block: str) -> tuple[str, bool]:
    # Replace the FIRST top-level def <name>(...) block
    pat = re.compile(rf"^def\s+{re.escape(name)}\s*\(.*?\):\s*\n", re.M | re.S)
    m = pat.search(src)
    if not m:
        return (src.rstrip() + "\n\n" + new_block.strip() + "\n", False)

    start = m.start()
    tail = src[m.end():]
    m2 = re.search(r"^(def\s+|class\s+)", tail, flags=re.M)
    end = m.end() + (m2.start(0) if m2 else len(tail))
    out = src[:start] + new_block.strip() + "\n\n" + src[end:]
    return out, True

AXIS_SHIM = r'''
def _build_time_axis_seconds(df, time_col):
    """
    Backwards-compat helper expected by physics_discovery + older engine code.

    IMPORTANT: Aurora's build_time_axis() signature/returns can vary by version.
    This shim adapts to both:
      - (t_seconds, time_axis, dt_seconds)
      - (t_seconds, time_axis)
      - (t_seconds, dt_seconds)
    Returns ALWAYS: (t_seconds, time_axis, dt_seconds)
    """
    out = build_time_axis(df, time_col=time_col)

    # Normalize different return shapes
    if isinstance(out, tuple):
        if len(out) == 3:
            t_seconds, time_axis, dt_seconds = out
        elif len(out) == 2:
            a, b = out
            # Heuristic: if second is scalar -> dt_seconds, else it's time_axis
            try:
                import numpy as np
                is_scalar = np.isscalar(b)
            except Exception:
                is_scalar = isinstance(b, (int, float))

            if is_scalar:
                t_seconds = a
                dt_seconds = float(b)
                # Provide a simple time_axis aligned to t_seconds
                try:
                    time_axis = list(range(len(t_seconds)))
                except Exception:
                    time_axis = []
            else:
                t_seconds = a
                time_axis = b
                # Estimate dt_seconds from t_seconds if possible
                try:
                    import numpy as np
                    ts = np.asarray(t_seconds, dtype=float)
                    dt = np.diff(ts)
                    dt_seconds = float(np.median(dt)) if dt.size else 1.0
                except Exception:
                    dt_seconds = 1.0
        else:
            raise ValueError(f"build_time_axis returned unexpected tuple length: {len(out)}")
    else:
        raise ValueError("build_time_axis did not return a tuple")

    return t_seconds, time_axis, float(dt_seconds)
'''.strip()

def main():
    if not PHYSICS.exists():
        raise FileNotFoundError(PHYSICS)

    src = PHYSICS.read_text(encoding="utf-8", errors="replace")
    b = backup(PHYSICS)

    src2, replaced = replace_func_block(src, "_build_time_axis_seconds", AXIS_SHIM)
    PHYSICS.write_text(src2, encoding="utf-8")

    import py_compile
    py_compile.compile(str(PHYSICS), doraise=True)

    print("[OK] axis shim patched + compiled clean")
    print(" - file:", PHYSICS)
    print(" - backup:", b)
    print(" - replaced_existing:", replaced)

if __name__ == "__main__":
    main()
