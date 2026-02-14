from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import py_compile

ROOT = Path(__file__).resolve().parents[1]
MS = ROOT / "fantasyai" / "aurora" / "mathstack_v2.py"

CANDIDATES = [
    "compute_math_stack_v2",
    "compute_mathstack_v2",
    "run_math_stack_v2",
    "run_mathstack_v2",
    "run_math_stack",
    "run_mathstack",
    "run_v2",
    "run",
    "main",
]

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

def find_entrypoint(src: str) -> str | None:
    # Prefer compute_* if present, otherwise any known candidate
    defs = set(re.findall(r"(?m)^def\s+([A-Za-z_]\w*)\s*\(", src))
    for name in CANDIDATES:
        if name in defs:
            return name
    return None

def main() -> int:
    if not MS.exists():
        print(f"[FAIL] Missing: {MS}")
        return 2

    src = MS.read_text(encoding="utf-8")

    # If it already exports run_mathstack_v2, do nothing.
    if re.search(r"(?m)^def\s+run_mathstack_v2\s*\(", src):
        print("[OK] mathstack_v2.py already has def run_mathstack_v2(); nothing to do.")
        py_compile.compile(str(MS), doraise=True)
        print("[DONE] py_compile clean")
        return 0

    entry = find_entrypoint(src)
    if not entry:
        print("[FAIL] Could not detect an entrypoint inside mathstack_v2.py.")
        print("       Expected one of:", ", ".join(CANDIDATES))
        return 3

    bak = backup(MS)
    print(f"[OK] backup -> {bak}")
    print(f"[OK] detected mathstack entrypoint: {entry}")

    wrapper = f"""

# =========================
# Stable public entrypoint
# =========================
def run_mathstack_v2(*args, **kwargs):
    \"\"\"Stable wrapper for dataset runner.

    Accepts extra kwargs (e.g., seed) without breaking.
    \"\"\"
    kwargs.pop("seed", None)
    kwargs.pop("random_state", None)
    return {entry}(*args, **kwargs)

# Friendly aliases (older runner names)
def run_math_stack_v2(*args, **kwargs):
    return run_mathstack_v2(*args, **kwargs)

def run_mathstack(*args, **kwargs):
    return run_mathstack_v2(*args, **kwargs)

def run_math_stack(*args, **kwargs):
    return run_mathstack_v2(*args, **kwargs)
"""

    MS.write_text(src.rstrip() + "\n" + wrapper.lstrip(), encoding="utf-8")
    py_compile.compile(str(MS), doraise=True)
    print(f"[DONE] wrote stable run_mathstack_v2 wrapper -> {MS}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
