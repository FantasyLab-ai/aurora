from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # .../FantasyAI
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"
DEEP_MATH_DIR = ROOT / "fantasyai" / "aurora" / "math"
DEEP_MATH_PY = DEEP_MATH_DIR / "deep_math.py"

def backup(p: Path) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b

def ensure_deep_math_dir():
    DEEP_MATH_DIR.mkdir(parents=True, exist_ok=True)

def patch_runner():
    if not RUNNER.exists():
        raise FileNotFoundError(f"Missing runner: {RUNNER}")

    txt = RUNNER.read_text(encoding="utf-8")

    # --- 1) Remove duplicate argparse flag definitions for --deep-math ---
    # Keep the FIRST occurrence, remove all subsequent ones.
    lines = txt.splitlines(True)
    out_lines = []
    deep_math_arg_seen = 0

    deep_math_arg_re = re.compile(r'^\s*ap\.add_argument\(\s*[\'"]--deep-math[\'"]')

    for line in lines:
        if deep_math_arg_re.search(line):
            deep_math_arg_seen += 1
            if deep_math_arg_seen == 1:
                out_lines.append(line)
            else:
                # drop duplicates
                continue
        else:
            out_lines.append(line)

    txt2 = "".join(out_lines)

    # If there was no --deep-math arg at all, add one right after --math-v2
    if deep_math_arg_seen == 0:
        m = re.search(r'^\s*ap\.add_argument\(\s*[\'"]--math-v2[\'"].*$', txt2, flags=re.MULTILINE)
        if not m:
            raise RuntimeError("Could not find --math-v2 argument line to anchor --deep-math insertion.")
        insert = '\n    ap.add_argument("--deep-math", action="store_true", help="run deep math (tests, PCA, OLS, regimes, uncertainty)")\n'
        txt2 = txt2[:m.end()] + insert + txt2[m.end():]

    # --- 2) Ensure we import compute_deep_math_v1 ---
    if "from fantasyai.aurora.math.deep_math import compute_deep_math_v1" not in txt2:
        # Anchor right after mathstack_v2 import if present
        anchor = "from fantasyai.aurora.mathstack_v2 import compute_math_stack_v2"
        if anchor in txt2:
            txt2 = txt2.replace(
                anchor,
                anchor + "\nfrom fantasyai.aurora.math.deep_math import compute_deep_math_v1",
                1
            )
        else:
            # fallback: inject after pandas import
            m2 = re.search(r'^\s*import pandas as pd\s*$', txt2, flags=re.MULTILINE)
            if not m2:
                raise RuntimeError("Could not find a safe import anchor for compute_deep_math_v1.")
            txt2 = txt2[:m2.end()] + "\n\nfrom fantasyai.aurora.math.deep_math import compute_deep_math_v1\n" + txt2[m2.end():]

    # --- 3) Ensure deep_math write block exists (only once) ---
    # If we already call compute_deep_math_v1(df, ...), leave it alone.
    if "compute_deep_math_v1(df" not in txt2:
        # Insert immediately after math_results.json write_text call
        marker_re = re.compile(
            r'\(run_dir\s*/\s*"math_results\.json"\)\.write_text\(\s*json\.dumps\(\s*math_results,\s*indent=2\s*\)\s*,\s*encoding="utf-8"\s*\)\s*',
            flags=re.MULTILINE
        )
        m3 = marker_re.search(txt2)
        if not m3:
            raise RuntimeError("Could not locate math_results.json write marker in runner to insert deep-math block.")

        block = """
        # Optional deep-math extras (deterministic)
        if getattr(args, "deep_math", False):
            try:
                deep_out = compute_deep_math_v1(df, seed=int(args.seed))
            except Exception as e:
                deep_out = {"error": f"deep_math_failed:{type(e).__name__}:{e}"}
            (run_dir / "deep_math.json").write_text(json.dumps(deep_out, indent=2), encoding="utf-8")
            math_results["_deep_math_file"] = "deep_math.json"
"""
        txt2 = txt2[:m3.end()] + "\n" + block + txt2[m3.end():]

    # Write back
    RUNNER.write_text(txt2, encoding="utf-8", newline="\n")

    # Report
    new_txt = RUNNER.read_text(encoding="utf-8")
    found = len(re.findall(r'\bap\.add_argument\(\s*[\'"]--deep-math[\'"]', new_txt))
    print(f"[OK] runner patched: {RUNNER}")
    print(f"[OK] --deep-math argparse definitions now: {found}")
    print(f"[OK] deep-math call present: {'compute_deep_math_v1(df' in new_txt}")

def main():
    ensure_deep_math_dir()

    if RUNNER.exists():
        b = backup(RUNNER)
        print(f"[OK] backup -> {b}")

    patch_runner()

if __name__ == "__main__":
    main()
