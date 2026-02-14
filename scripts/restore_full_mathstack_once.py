from __future__ import annotations
from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"
MATHSTACK = ROOT / "fantasyai" / "aurora" / "mathstack_v2.py"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

def ensure_mathstack_wrapper():
    if not MATHSTACK.exists():
        raise SystemExit(f"Missing: {MATHSTACK}")

    txt = MATHSTACK.read_text(encoding="utf-8", errors="replace")

    # If wrapper already exists, do nothing.
    if re.search(r"(?m)^\s*def\s+run_mathstack_v2\s*\(", txt):
        print("[OK] mathstack_v2.py already has run_mathstack_v2(); nothing to do.")
        return

    # Sanity: make sure compute_math_stack_v2 exists (real implementation)
    if not re.search(r"(?m)^\s*def\s+compute_math_stack_v2\s*\(", txt):
        raise SystemExit("mathstack_v2.py missing compute_math_stack_v2(); refusing to patch blindly.")

    bak = backup(MATHSTACK)
    print(f"[OK] backup -> {bak}")

    wrapper = """
# ---------------------------------------------------------------------------
# Stable public entrypoint (runner/API-safe)
# ---------------------------------------------------------------------------
def run_mathstack_v2(df: pd.DataFrame, seed: int | None = None, max_corr_cols: int = 40) -> Dict[str, Any]:
    \"\"\"Compatibility wrapper for the dataset runner + API surface.

    - Accepts seed for CLI compatibility (ignored by this mathstack).
    - Delegates to compute_math_stack_v2(df, max_corr_cols=...).
    \"\"\"
    return compute_math_stack_v2(df, max_corr_cols=max_corr_cols)

# Common alias variants (older code paths may import these)
run_math_stack_v2 = run_mathstack_v2
""".lstrip("\n")

    # Append at end with a newline guard
    txt2 = txt.rstrip() + "\n\n" + wrapper + "\n"
    MATHSTACK.write_text(txt2, encoding="utf-8")
    print("[OK] added run_mathstack_v2 wrapper + alias ->", MATHSTACK)

def patch_runner_mathstack_call():
    if not RUNNER.exists():
        raise SystemExit(f"Missing: {RUNNER}")

    txt = RUNNER.read_text(encoding="utf-8", errors="replace")
    bak = backup(RUNNER)
    print(f"[OK] backup -> {bak}")

    # Ensure runner imports run_mathstack_v2 (not compute_*).
    # Remove any existing import lines from mathstack_v2, then insert a stable one.
    txt = re.sub(
        r"(?m)^\s*from\s+fantasyai\.aurora\.mathstack_v2\s+import\s+.*\n",
        "",
        txt,
    )

    import_block = (
        "from fantasyai.aurora.mathstack_v2 import run_mathstack_v2  # stable wrapper\n"
    )

    # Insert after the main import header (after last contiguous import/from line at top)
    lines = txt.splitlines()
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            insert_at = i + 1
        else:
            if i > 0:
                break
    lines.insert(insert_at, import_block.rstrip("\n"))
    txt = "\n".join(lines) + "\n"

    # Fix bad call patterns: remove seed kwarg etc.
    # Examples seen: run_mathstack_v2(df, seed=seed) or compute_math_stack_v2(... seed=...)
    txt = re.sub(r"run_mathstack_v2\(\s*df\s*,\s*seed\s*=\s*seed\s*\)", "run_mathstack_v2(df)", txt)
    txt = re.sub(r"run_mathstack_v2\(\s*df\s*,\s*seed\s*=\s*args\.seed\s*\)", "run_mathstack_v2(df)", txt)
    txt = re.sub(r"run_mathstack_v2\(\s*df\s*,\s*seed\s*=\s*([^)]+)\)", "run_mathstack_v2(df)", txt)

    # If runner still calls compute_math_stack_v2 directly with seed kw, neutralize.
    txt = re.sub(r"compute_math_stack_v2\(\s*df\s*,\s*seed\s*=\s*[^)]+\)", "compute_math_stack_v2(df)", txt)

    RUNNER.write_text(txt, encoding="utf-8")
    print("[OK] runner mathstack import/call stabilized ->", RUNNER)

def compile_all():
    import py_compile
    py_compile.compile(str(MATHSTACK), doraise=True)
    py_compile.compile(str(RUNNER), doraise=True)
    print("[DONE] py_compile clean (mathstack_v2 + runner)")

def main():
    ensure_mathstack_wrapper()
    patch_runner_mathstack_call()
    compile_all()

if __name__ == "__main__":
    main()
