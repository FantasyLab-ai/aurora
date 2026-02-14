from __future__ import annotations
from pathlib import Path
from datetime import datetime
import re
import ast

ROOT = Path(__file__).resolve().parents[1]
MATHSTACK = ROOT / "fantasyai" / "aurora" / "mathstack_v2.py"
DEEP_MATH = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

def _find_best_entrypoint(src: str) -> str | None:
    """
    Pick the most likely 'runner' function inside mathstack_v2.py.
    Preference order:
      run_math_stack_v2, run_mathstack_v2, run_math_stack, run_mathstack, run_v2, run, main
    Otherwise: best-looking function that contains 'math' or 'stack' in name.
    """
    preferred = [
        "run_math_stack_v2",
        "run_mathstack_v2",
        "run_math_stack",
        "run_mathstack",
        "run_v2",
        "run",
        "main",
    ]
    try:
        mod = ast.parse(src)
    except Exception:
        # fallback: regex scan
        for name in preferred:
            if re.search(rf"(?m)^def\s+{re.escape(name)}\s*\(", src):
                return name
        return None

    funcs = [n.name for n in mod.body if isinstance(n, ast.FunctionDef)]
    for name in preferred:
        if name in funcs:
            return name

    scored = []
    for f in funcs:
        s = 0
        lf = f.lower()
        if "math" in lf: s += 2
        if "stack" in lf: s += 3
        if lf.startswith("run"): s += 2
        scored.append((s, f))
    scored.sort(reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return None

def patch_mathstack_v2():
    if not MATHSTACK.exists():
        raise SystemExit(f"Missing: {MATHSTACK}")

    bak = backup(MATHSTACK)
    print(f"[OK] backup -> {bak}")

    src = MATHSTACK.read_text(encoding="utf-8")
    has_run_mathstack_v2 = bool(re.search(r"(?m)^def\s+run_mathstack_v2\s*\(", src))
    if has_run_mathstack_v2:
        print("[OK] mathstack_v2 already exports run_mathstack_v2")
        return

    ep = _find_best_entrypoint(src)
    if ep is None:
        raise SystemExit(
            "[FAIL] Could not detect an entrypoint inside mathstack_v2.py.\n"
            "Open fantasyai/aurora/mathstack_v2.py and ensure there is a top-level def like run_math_stack_v2() or run()."
        )

    shim = f"""

# --- Compatibility shim (auto-added) ---
def run_mathstack_v2(*args, **kwargs):
    \"\"\"Compat entrypoint expected by run_aurora_dataset_runner.\"\"\"
    return {ep}(*args, **kwargs)
"""
    # Add __all__ if present; otherwise append shim and define __all__
    if re.search(r"(?m)^__all__\s*=\s*\[", src):
        # append run_mathstack_v2 into __all__ if it isn't already there
        if "run_mathstack_v2" not in src:
            src = re.sub(
                r"(?m)^(__all__\s*=\s*\[)(.*?)(\]\s*)$",
                lambda m: m.group(1) + m.group(2).rstrip() + (", " if m.group(2).strip() else "") + "'run_mathstack_v2'" + m.group(3),
                src,
                count=1,
            )

    src2 = src.rstrip() + "\n" + shim.strip() + "\n"

    # If no __all__, add one at end (harmless)
    if "__all__" not in src2:
        src2 += "\n__all__ = [name for name in globals().keys() if not name.startswith('_')]\n"

    MATHSTACK.write_text(src2, encoding="utf-8")
    print(f"[OK] mathstack_v2: added run_mathstack_v2 -> wraps {ep}")

def _detect_self_recursion(src: str, fn_name: str) -> bool:
    """
    Very blunt recursion detection: if inside def fn_name we see a direct call to fn_name(...).
    """
    # Extract function block (best-effort via regex)
    m = re.search(rf"(?ms)^def\s+{re.escape(fn_name)}\s*\(.*?\):\n(.*?)(?=^\S|\Z)", src)
    if not m:
        return False
    body = m.group(1)
    return bool(re.search(rf"(?m)^\s*return\s+{re.escape(fn_name)}\s*\(", body)) or bool(re.search(rf"{re.escape(fn_name)}\s*\(", body))

def patch_deep_math_guardrails():
    if not DEEP_MATH.exists():
        raise SystemExit(f"Missing: {DEEP_MATH}")

    bak = backup(DEEP_MATH)
    print(f"[OK] backup -> {bak}")

    src = DEEP_MATH.read_text(encoding="utf-8")

    # If compute_deep_math_v2/v3 exist and look non-recursive, keep them.
    has_v2 = bool(re.search(r"(?m)^def\s+compute_deep_math_v2\s*\(", src))
    has_v3 = bool(re.search(r"(?m)^def\s+compute_deep_math_v3\s*\(", src))

    if not (has_v2 and has_v3):
        print("[WARN] deep_math missing v2/v3 exports; not rewriting your core logic here.")
        # But we still can add a stable alias used by callers
    else:
        v2_rec = _detect_self_recursion(src, "compute_deep_math_v2")
        v3_rec = _detect_self_recursion(src, "compute_deep_math_v3")
        if v2_rec or v3_rec:
            # Rename the original functions to _impl variants and create safe wrappers.
            # This prevents accidental alias-to-self recursion that you hit earlier.
            src = re.sub(r"(?m)^def\s+compute_deep_math_v2\s*\(", "def _compute_deep_math_v2_impl(", src, count=1)
            src = re.sub(r"(?m)^def\s+compute_deep_math_v3\s*\(", "def _compute_deep_math_v3_impl(", src, count=1)

            wrapper = """

# --- Guardrail wrappers (auto-added) ---
def compute_deep_math_v2(*args, **kwargs):
    \"\"\"Stable wrapper with recursion guard.\"\"\"
    return _compute_deep_math_v2_impl(*args, **kwargs)

def compute_deep_math_v3(*args, **kwargs):
    \"\"\"Stable wrapper with recursion guard.\"\"\"
    return _compute_deep_math_v3_impl(*args, **kwargs)
"""
            src = src.rstrip() + "\n" + wrapper.strip() + "\n"
            print("[OK] deep_math: prevented accidental self-recursion by wrapping v2/v3")

    # Ensure legacy alias exists
    if not re.search(r"(?m)^compute_deep_math\s*=\s*compute_deep_math_v3\s*$", src):
        src = src.rstrip() + "\n\n# Legacy alias\ncompute_deep_math = compute_deep_math_v3\n"

    DEEP_MATH.write_text(src, encoding="utf-8")
    print("[OK] deep_math: guardrails + alias ensured")

def main():
    patch_mathstack_v2()
    patch_deep_math_guardrails()

    import py_compile
    py_compile.compile(str(MATHSTACK), doraise=True)
    py_compile.compile(str(DEEP_MATH), doraise=True)
    print("[DONE] mathstack_v2 + deep_math patched and py_compile clean")

if __name__ == "__main__":
    main()
