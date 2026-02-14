from pathlib import Path
import re

p = Path("scripts/run_aurora_dataset_runner.py")
s = p.read_text(encoding="utf-8")

# 1) Ensure V2 import exists
if "compute_math_stack_v2" not in s:
    if "import pandas as pd" in s:
        s = s.replace(
            "import pandas as pd",
            "import pandas as pd\n\nfrom fantasyai.aurora.mathstack_v2 import compute_math_stack_v2"
        )
    else:
        # fallback: append near top
        s = "from fantasyai.aurora.mathstack_v2 import compute_math_stack_v2\n" + s

# 2) Add CLI flag --math-v2
if "--math-v2" not in s and "math_v2" not in s:
    # try to insert right after --also-llm
    needle = 'ap.add_argument("--also-llm", action="store_true")'
    if needle in s:
        s = s.replace(
            needle,
            needle + '\n    ap.add_argument("--math-v2", action="store_true", help="use deeper MathStackV2")'
        )
    else:
        # fallback: insert after parser creation
        s = re.sub(
            r"(ap\s*=\s*argparse\.ArgumentParser\([^\n]*\)\n)",
            r"\1    ap.add_argument(\"--math-v2\", action=\"store_true\", help=\"use deeper MathStackV2\")\n",
            s,
            count=1,
        )

# 3) Switch math computation to use V2 when requested.
# We look for the block that computes math_results under args.also_math and replace it.
# (This is safer than relying on exact formatting.)
block_pat = re.compile(r"""
math_results\s*=\s*\{\}\s*
if\s+args\.also_math\s*:\s*
(?P<body>(?:.|\n)*?)
save_json\(\s*run_dir\s*/\s*["']math_results\.json["']\s*,\s*math_results\s*\)
""", re.VERBOSE)

m = block_pat.search(s)
if m:
    replacement = """math_results = {}
    if args.also_math:
        if getattr(args, "math_v2", False):
            math_results = compute_math_stack_v2(df)
        else:
            math_results = compute_math_stack(df)
        save_json(run_dir / "math_results.json", math_results)
"""
    s = s[:m.start()] + replacement + s[m.end():]
else:
    # If we can't find the block, don't guess — just add a small shim:
    # We'll leave the existing math_results and only override if args.math_v2 exists.
    # (This keeps your runner stable.)
    if "getattr(args, \"math_v2\"" not in s:
        s = s.replace(
            "math_results = compute_math_stack(df)",
            "math_results = compute_math_stack_v2(df) if getattr(args,'math_v2',False) else compute_math_stack(df)"
        )

p.write_text(s, encoding="utf-8")
print("[OK] Patched runner: added --math-v2 + MathStackV2 import/dispatch.")
