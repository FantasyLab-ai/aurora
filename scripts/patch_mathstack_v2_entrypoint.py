from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import sys
import py_compile

ROOT = Path(__file__).resolve().parents[1]
MATHSTACK = ROOT / "fantasyai" / "aurora" / "mathstack_v2.py"

PRIORITY = [
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

def find_best_target(txt: str) -> str | None:
    # find def <name>(...)
    defs = re.findall(r"(?m)^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", txt)
    defs_set = set(defs)

    # If already present, nothing to do
    if "run_mathstack_v2" in defs_set:
        return "run_mathstack_v2"

    for name in PRIORITY:
        if name in defs_set and name != "run_mathstack_v2":
            return name

    # Heuristic: class-based runner
    # If there's a class like MathStackV2 with a run() method, we can wrap it.
    if re.search(r"(?m)^\s*class\s+MathStackV2\b", txt) and ("MathStackV2" in txt):
        return "__CLASS__MathStackV2__"

    return None

def inject_alias(txt: str, target: str) -> str:
    if re.search(r"(?m)^\s*def\s+run_mathstack_v2\s*\(", txt):
        return txt

    if target == "__CLASS__MathStackV2__":
        alias = """
# ------------------------------
# Compat entrypoint for runner
# ------------------------------
def run_mathstack_v2(*args, **kwargs):
    \"\"\"Compat entrypoint expected by dataset runner.

    This forwards to MathStackV2().run(...) if present.
    \"\"\"
    ms = MathStackV2()
    if hasattr(ms, "run"):
        return ms.run(*args, **kwargs)
    raise AttributeError("MathStackV2 has no .run() method")
"""
        return txt.rstrip() + "\n\n" + alias.lstrip()

    alias = f"""
# ------------------------------
# Compat entrypoint for runner
# ------------------------------
def run_mathstack_v2(*args, **kwargs):
    \"\"\"Compat entrypoint expected by dataset runner.

    Forwards to `{target}`.
    \"\"\"
    return {target}(*args, **kwargs)
"""
    return txt.rstrip() + "\n\n" + alias.lstrip()

def main() -> int:
    if not MATHSTACK.exists():
        print(f"[FAIL] Missing: {MATHSTACK}")
        return 2

    txt = MATHSTACK.read_text(encoding="utf-8")

    target = find_best_target(txt)
    if target is None:
        print("[FAIL] Could not find an existing mathstack v2 entrypoint to wrap.")
        print("       Expected one of: " + ", ".join(PRIORITY))
        print("       Open mathstack_v2.py and tell me the real function name that runs the stack.")
        return 3

    bak = backup(MATHSTACK)
    print(f"[OK] backup -> {bak}")

    if target == "run_mathstack_v2":
        print("[OK] run_mathstack_v2 already exists. No change needed.")
        py_compile.compile(str(MATHSTACK), doraise=True)
        print("[DONE] py_compile OK")
        return 0

    txt2 = inject_alias(txt, target)
    MATHSTACK.write_text(txt2, encoding="utf-8")
    print(f"[OK] added compat run_mathstack_v2 -> forwards to {target}")

    py_compile.compile(str(MATHSTACK), doraise=True)
    print("[DONE] py_compile OK")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
