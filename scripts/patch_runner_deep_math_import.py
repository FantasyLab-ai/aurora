from __future__ import annotations
import re
from pathlib import Path
from datetime import datetime

RUNNER = Path(r".\scripts\run_aurora_dataset_runner.py")

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    b.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    return b

def main():
    if not RUNNER.exists():
        raise SystemExit(f"Missing runner: {RUNNER}")

    txt = RUNNER.read_text(encoding="utf-8")

    # 1) Remove any direct imports of compute_deep_math_v1 / v2 (we'll insert a robust block)
    txt = re.sub(r"^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+compute_deep_math_v1\s*\r?\n", "", txt, flags=re.M)
    txt = re.sub(r"^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+compute_deep_math_v2\s*\r?\n", "", txt, flags=re.M)

    # 2) Insert robust import block right after mathstack_v2 import (or near other imports)
    robust_block = (
        "try:\n"
        "    from fantasyai.aurora.math.deep_math import compute_deep_math_v2 as compute_deep_math\n"
        "except Exception:\n"
        "    try:\n"
        "        from fantasyai.aurora.math.deep_math import compute_deep_math_v1 as compute_deep_math\n"
        "    except Exception:\n"
        "        compute_deep_math = None\n"
    )

    if "compute_deep_math =" not in txt:
        m = re.search(r"^from\s+fantasyai\.aurora\.mathstack_v2\s+import\s+compute_math_stack_v2\s*\r?\n", txt, flags=re.M)
        if m:
            insert_at = m.end()
            txt = txt[:insert_at] + robust_block + txt[insert_at:]
        else:
            # fallback: insert after last import line
            imports = list(re.finditer(r"^(import|from)\s+.+\r?\n", txt, flags=re.M))
            insert_at = imports[-1].end() if imports else 0
            txt = txt[:insert_at] + "\n" + robust_block + "\n" + txt[insert_at:]

    # 3) Replace any old calls to compute_deep_math_v1/v2 with compute_deep_math
    txt = re.sub(r"\bcompute_deep_math_v1\b", "compute_deep_math", txt)
    txt = re.sub(r"\bcompute_deep_math_v2\b", "compute_deep_math", txt)

    # 4) Ensure argparse has --deep-math only once (remove duplicates)
    # Keep the FIRST definition; remove subsequent duplicates.
    lines = txt.splitlines(True)
    out = []
    seen = 0
    for line in lines:
        if re.search(r'add_argument\(\s*[\'"]--deep-math[\'"]', line):
            seen += 1
            if seen > 1:
                continue
        out.append(line)
    txt = "".join(out)

    b = backup(RUNNER)
    RUNNER.write_text(txt, encoding="utf-8")

    print(f"[OK] backup -> {b}")
    print(f"[OK] patched runner -> {RUNNER.resolve()}")
    print(f"[OK] --deep-math argparse definitions now: {len(re.findall(r'add_argument\\(\\s*[\\\"\\\']--deep-math', txt))}")
    print(f"[OK] deep-math import block present: {'compute_deep_math =' in txt}")

if __name__ == "__main__":
    main()
