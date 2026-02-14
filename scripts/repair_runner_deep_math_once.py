from pathlib import Path
import hashlib
import datetime

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"

def backup(p: Path):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    b.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    return b

def main():
    txt = RUNNER.read_text(encoding="utf-8")
    backup(RUNNER)

    # 1. REMOVE broken try block completely
    txt = txt.replace(
        "try:\n    # preferred\nexcept Exception:\n    compute_deep_math = None\n",
        ""
    )

    # 2. REMOVE all deep-math imports
    lines = []
    for ln in txt.splitlines():
        if "compute_deep_math_v" in ln:
            continue
        if "aurora.math import deep_math" in ln:
            continue
        if ln.strip().startswith("compute_deep_math ="):
            continue
        lines.append(ln)
    txt = "\n".join(lines)

    # 3. INSERT ONE clean import
    anchor = "import pandas as pd"
    insert = (
        "import pandas as pd\n\n"
        "from fantasyai.aurora.mathstack_v2 import compute_math_stack_v2\n"
        "from fantasyai.aurora.math.deep_math import compute_deep_math_v3 as compute_deep_math\n"
    )
    txt = txt.replace(anchor, insert, 1)

    # 4. REMOVE all existing deep-math execution blocks
    cleaned = []
    skip = False
    for ln in txt.splitlines():
        if "deep_math.json" in ln:
            skip = True
        if skip and ln.strip() == "":
            skip = False
            continue
        if not skip:
            cleaned.append(ln)
    txt = "\n".join(cleaned)

    # 5. INSERT ONE canonical deep-math block
    marker = '(run_dir / "math_results.json").write_text'
    parts = txt.split(marker)
    if len(parts) != 2:
        raise RuntimeError("Could not find math_results.json write marker")

    canonical = (
        marker + parts[1].splitlines(True)[0] +
        """
        # --- deep math (canonical, single execution) ---
        if getattr(args, "deep_math", False):
            try:
                deep_out = compute_deep_math(df, seed=int(args.seed))
            except Exception as e:
                deep_out = {"error": f"deep_math_failed:{type(e).__name__}:{e}"}
            (run_dir / "deep_math.json").write_text(json.dumps(deep_out, indent=2), encoding="utf-8")
            math_results["_deep_math_file"] = "deep_math.json"
"""
    )

    txt = parts[0] + canonical + "\n".join(parts[1].splitlines()[1:])

    RUNNER.write_text(txt, encoding="utf-8")
    print("[OK] Runner repaired cleanly — deep-math canonicalized")

if __name__ == "__main__":
    main()
