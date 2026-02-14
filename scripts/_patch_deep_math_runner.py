import re
from pathlib import Path

runner = Path(r"scripts/run_aurora_dataset_runner.py")
text = runner.read_text(encoding="utf-8")

# 1) Ensure argparse flag exists
if "--deep-math" not in text:
    # support either ap=ArgumentParser() or parser=ArgumentParser()
    m = re.search(r"(add_argument\\(\"--math-v2\"[\\s\\S]*?\\)\\s*)", text)
    if not m:
        raise SystemExit("Could not find --math-v2 add_argument block to anchor --deep-math.")
    insert = m.group(1) + '    ' + 'parser.add_argument("--deep-math", action="store_true", help="run deep math and write deep_math.json")\\n'
    # normalize: if file uses ap., switch to parser.
    if "ap.add_argument" in m.group(1):
        insert = m.group(1) + '    ' + 'ap.add_argument("--deep-math", action="store_true", help="run deep math and write deep_math.json")\\n'
    text = text.replace(m.group(1), insert)

# 2) Ensure import exists
if "compute_deep_math_v1" not in text:
    anchor = "from fantasyai.aurora.mathstack_v2 import compute_math_stack_v2"
    if anchor not in text:
        raise SystemExit("Could not find mathstack_v2 import anchor.")
    text = text.replace(anchor, anchor + "\\nfrom fantasyai.aurora.math.deep_math import compute_deep_math_v1")

# 3) Ensure deep math is executed + written
# We add a block AFTER writing math_results.json, since run_dir is guaranteed there.
needle = '(run_dir / "math_results.json").write_text(json.dumps(math_results, indent=2), encoding="utf-8")'
if needle not in text:
    raise SystemExit("Could not find math_results.json write_text line to anchor deep-math.")

if "deep_math.json" not in text:
    block = (
        needle
        + "\\n"
        + "        if getattr(args, 'deep_math', False):\\n"
        + "            try:\\n"
        + "                deep_out = compute_deep_math_v1(df, seed=int(args.seed))\\n"
        + "            except Exception as e:\\n"
        + "                deep_out = {'error': f'deep_math_failed:{type(e).__name__}:{e}'}\\n"
        + "            (run_dir / 'deep_math.json').write_text(json.dumps(deep_out, indent=2), encoding='utf-8')\\n"
        + "            math_results['_deep_math_file'] = 'deep_math.json'\\n"
    )
    text = text.replace(needle, block)

runner.write_text(text, encoding="utf-8")
print("[OK] runner patched for --deep-math")