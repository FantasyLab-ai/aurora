from __future__ import annotations
from pathlib import Path
import re

PATH = Path("scripts/run_aurora_dataset_runner.py")
txt = PATH.read_text(encoding="utf-8", errors="replace")

# 1) Ensure import exists
if "build_schema_contract" not in txt:
    # insert after pandas import
    m = re.search(r"(?m)^import pandas as pd\s*$", txt)
    if not m:
        raise SystemExit("Could not find `import pandas as pd` in run_aurora_dataset_runner.py")

    insert_at = m.end()
    txt = txt[:insert_at] + "\nfrom fantasyai.aurora.contracts.schema_contract import build_schema_contract\n" + txt[insert_at:]

# 2) After df load, build contract + swap df views
needle = 'df = _load_df(resolved["table"])'
if needle not in txt:
    raise SystemExit(f"Could not find line: {needle}")

block = '''
df = _load_df(resolved["table"])

# ==========================
# Schema / Structure Contract
# ==========================
bundle = build_schema_contract(df, seed=args.seed)
contract = bundle["contract"]
df_canonical = bundle["df_canonical"]
df_numeric = bundle["df_numeric_view"]
df_deep = bundle["df_deep_math_view"]
df_time = bundle["df_time_view"]

_write_json(run_dir / "schema_contract.json", contract)

# Downstream modules should use numeric/time views (more robust than raw)
df_for_engine = df_numeric if (df_numeric is not None and df_numeric.shape[1] >= 1) else df
'''
txt = txt.replace(needle, block.strip("\n"))

# 3) Structure assessment should use df_for_engine (not raw)
txt = txt.replace("structure = _assess_dataset_structure(df)", "structure = _assess_dataset_structure(df_for_engine)")

# 4) Table selection stats should still reflect raw load (keep df)
# no change needed

# 5) Mathstack uses df_for_engine
txt = txt.replace("_run_math_stack_v2(df=df, seed=args.seed, out_dir=run_dir)", "_run_math_stack_v2(df=df_for_engine, seed=args.seed, out_dir=run_dir)")

# 6) Deep math should use df_deep if available
txt = txt.replace(
    "deep_out = compute_deep_math(df=df_deep, seed=args.seed)",
    "deep_out = compute_deep_math(df=df_deep, seed=args.seed)"
)

# If runner currently calls compute_deep_math(df=...), rewrite to use df_deep
txt = re.sub(
    r"compute_deep_math\(\s*df\s*=\s*df\s*,\s*seed\s*=\s*args\.seed\s*\)",
    "compute_deep_math(df=df_deep, seed=args.seed)",
    txt,
)

PATH.write_text(txt, encoding="utf-8")
print("✅ Patched run_aurora_dataset_runner.py to enforce schema_contract + numeric/time views")
