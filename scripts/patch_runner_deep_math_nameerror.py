from __future__ import annotations

import re
import sys
from pathlib import Path
from datetime import datetime


def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[OK] backup -> {bak}")
    return bak


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    runner = repo / "scripts" / "run_aurora_dataset_runner.py"
    if not runner.exists():
        raise FileNotFoundError(runner)

    backup(runner)
    txt = runner.read_text(encoding="utf-8")

    # --- Remove any old v1 import lines ---
    txt = re.sub(
        r"^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+compute_deep_math_v1\s*$\n?",
        "",
        txt,
        flags=re.M,
    )

    # --- Ensure v2 import exists ---
    if re.search(r"from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+compute_deep_math_v2", txt) is None:
        # Insert after compute_math_stack_v2 import if possible
        m = re.search(
            r"^from\s+fantasyai\.aurora\.mathstack_v2\s+import\s+compute_math_stack_v2\s*$",
            txt,
            flags=re.M,
        )
        if m:
            ins = m.end()
            txt = txt[:ins] + "\nfrom fantasyai.aurora.math.deep_math import compute_deep_math_v2\n" + txt[ins:]
        else:
            # fallback: place near top after other imports
            txt = "from fantasyai.aurora.math.deep_math import compute_deep_math_v2\n" + txt

    # --- Ensure compute_deep_math alias exists (so runner can call compute_deep_math(...)) ---
    if re.search(r"^\s*compute_deep_math\s*=\s*compute_deep_math_v2\s*$", txt, flags=re.M) is None:
        # place alias immediately after v2 import line
        txt = re.sub(
            r"(^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+compute_deep_math_v2\s*$)",
            r"\1\ncompute_deep_math = compute_deep_math_v2",
            txt,
            flags=re.M,
        )

    # --- Replace any accidental calls to v1 with v2 ---
    txt = txt.replace("compute_deep_math_v1(", "compute_deep_math_v2(")

    # --- De-dupe argparse --deep-math definitions (keep first) ---
    arg_pat = r'^\s*(ap|parser)\.add_argument\(\s*[\'"]--deep-math[\'"][^\n]*\)\s*$'
    lines = txt.splitlines(True)
    seen = 0
    out_lines = []
    for line in lines:
        if re.match(arg_pat, line):
            seen += 1
            if seen > 1:
                continue
        out_lines.append(line)
    txt = "".join(out_lines)

    runner.write_text(txt, encoding="utf-8")

    print(f"[OK] patched runner -> {runner}")
    print(f"[OK] deep_math import v2 present: {'compute_deep_math_v2' in txt}")
    print(f"[OK] alias present: {'compute_deep_math = compute_deep_math_v2' in txt}")
    print(f"[OK] calls compute_deep_math(): {'compute_deep_math(' in txt}")

    # quick compile check
    import py_compile
    py_compile.compile(str(runner), doraise=True)
    print("[OK] py_compile runner passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        sys.exit(1)
