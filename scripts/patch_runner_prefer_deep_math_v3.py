from __future__ import annotations
from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

def main():
    if not RUNNER.exists():
        raise SystemExit(f"Missing: {RUNNER}")
    bak = backup(RUNNER)
    print(f"[OK] backup -> {bak}")

    txt = RUNNER.read_text(encoding="utf-8")

    # Replace any deep_math import to a safe "try v3 else v2" block
    import_block = r"""
# --- Deep Math (stable import) ---
try:
    from fantasyai.aurora.math.deep_math import compute_deep_math_v3 as compute_deep_math
except Exception:
    from fantasyai.aurora.math.deep_math import compute_deep_math_v2 as compute_deep_math
""".strip()

    # Remove older deep_math import lines if present
    txt2 = re.sub(
        r"(?m)^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+.*\n+",
        "",
        txt
    )

    # Insert import block near the top after other imports.
    # Put it after argparse/json imports if possible; otherwise just after the first import section.
    lines = txt2.splitlines()
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            insert_at = i + 1
        else:
            # stop after contiguous import header
            if i > 0:
                break
    lines.insert(insert_at, import_block)
    txt3 = "\n".join(lines) + "\n"

    RUNNER.write_text(txt3, encoding="utf-8")
    print("[OK] runner now prefers compute_deep_math_v3 (fallback v2)")

    import py_compile
    py_compile.compile(str(RUNNER), doraise=True)
    print("[DONE] runner patched + py_compile clean")

if __name__ == "__main__":
    main()
