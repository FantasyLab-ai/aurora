from __future__ import annotations

from pathlib import Path
import re
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"

def backup_file(p: Path) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b

def main():
    if not RUNNER.exists():
        raise FileNotFoundError(f"Missing runner: {RUNNER}")

    txt = RUNNER.read_text(encoding="utf-8")

    b = backup_file(RUNNER)
    print(f"[OK] backup -> {b}")

    # 1) Remove the broken "worldclass" deep-math block (it has an empty try:)
    start_key = "\n        # --- deep math (worldclass) ---"
    if start_key in txt:
        start = txt.index(start_key)

        # end right before the storycard assignment in the --also-math block
        m_end = re.search(r"\n\s*storycard\s*=\s*_build_storycard\(", txt[start:])
        if not m_end:
            raise RuntimeError("Could not find end of worldclass block (storycard assignment).")
        end = start + m_end.start()

        txt = txt[:start] + "\n\n" + txt[end:]
        print("[OK] removed broken deep math (worldclass) block")

    # 2) De-dupe repeated compute_math_stack_v2 imports (keep first occurrence only)
    lines = txt.splitlines()
    out = []
    seen = set()
    for ln in lines:
        key = ln.strip()
        if key == "from fantasyai.aurora.mathstack_v2 import compute_math_stack_v2":
            if key in seen:
                continue
            seen.add(key)
        out.append(ln)
    txt = "\n".join(out) + "\n"

    # 3) Sanity: ensure there is exactly one --deep-math argparse definition
    deep_arg_count = len(re.findall(r'^\s*ap\.add_argument\(\s*["\']--deep-math["\']', txt, flags=re.M))
    if deep_arg_count != 1:
        print(f"[WARN] deep-math argparse count is {deep_arg_count} (expected 1). Not changing it here.")

    RUNNER.write_text(txt, encoding="utf-8")
    print(f"[OK] wrote -> {RUNNER}")

if __name__ == "__main__":
    main()
