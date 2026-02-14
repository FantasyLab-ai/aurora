from __future__ import annotations
import re
from pathlib import Path
from datetime import datetime

P = Path(r".\fantasyai\aurora\math\deep_math.py")

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    b.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    return b

def main():
    if not P.exists():
        raise SystemExit(f"Missing: {P}")

    txt = P.read_text(encoding="utf-8")

    has_v1 = re.search(r"def\s+compute_deep_math_v1\s*\(", txt) is not None
    has_v2 = re.search(r"def\s+compute_deep_math_v2\s*\(", txt) is not None

    if (not has_v1) and has_v2:
        # Add a thin wrapper v1 -> v2 at end of file
        wrapper = """

# ---- Backward-compat alias: v1 -> v2 ----
def compute_deep_math_v1(df, seed: int = 42):
    return compute_deep_math_v2(df, seed=seed)
"""
        b = backup(P)
        P.write_text(txt + wrapper, encoding="utf-8")
        print(f"[OK] backup -> {b}")
        print("[OK] added compute_deep_math_v1 wrapper -> v2")
    else:
        print(f"[OK] no change (has_v1={has_v1}, has_v2={has_v2})")

if __name__ == "__main__":
    main()
