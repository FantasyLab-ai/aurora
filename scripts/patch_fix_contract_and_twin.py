from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]  # Aurora_QIE/
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"
TWIN   = ROOT / "fantasyai" / "aurora" / "digital_twin" / "default_twin.py"
SCHEMA = ROOT / "fantasyai" / "aurora" / "contracts" / "schema_contract.py"

def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")

def _write(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8")

def patch_runner():
    txt = _read(RUNNER)

    # Ensure df_for_engine exists after df load (idempotent)
    # Looks for: df = _load_df(resolved["table"])
    pat_df = r'(df\s*=\s*_load_df\(\s*resolved\["table"\]\s*\)\s*\r?\n)'
    if re.search(pat_df, txt) and "df_for_engine" not in txt:
        txt = re.sub(
            pat_df,
            r'\1    # Canonical df passed to downstream engine modules (can be sampled/cleaned later)\n'
            r'    df_for_engine = df\n',
            txt,
            count=1
        )

    # Fix structure contract assignment + write:
    # - normalize to structure_contract variable name
    # - always build using df_for_engine if present, else df
    # Handles either contract=... or structure_contract=... existing code.
    # 1) Replace assignment line
    txt = re.sub(
        r'^\s*(contract|structure_contract)\s*=\s*build_structure_contract\(\s*df\s*,\s*dataset_key\s*=\s*resolved\.get\([\'"]dataset_key[\'"]\)\s*\)\s*$',
        r'    structure_contract = build_structure_contract(df_for_engine if "df_for_engine" in locals() else df, dataset_key=resolved.get("dataset_key"))',
        txt,
        flags=re.MULTILINE
    )

    # If assignment exists but uses df_for_engine already, keep it (no-op)

    # 2) Replace the write_json call (handles either contract or structure_contract)
    txt = re.sub(
        r'(_write_json\(\s*run_dir\s*/\s*["\']structure_contract\.json["\']\s*,\s*)(contract|structure_contract)(\s*\))',
        r'\1structure_contract\3',
        txt
    )

    # If file never had structure contract block (rare), inject it right after df_for_engine block
    if "structure_contract.json" not in txt:
        inject = (
            "\n"
            "    # Build rich schema/structure contract\n"
            "    structure_contract = build_structure_contract(df_for_engine if \"df_for_engine\" in locals() else df, dataset_key=resolved.get(\"dataset_key\"))\n"
            "    _write_json(run_dir / \"structure_contract.json\", structure_contract)\n"
        )
        # inject after df_for_engine line if present, else after df load
        if "df_for_engine = df" in txt:
            txt = txt.replace("df_for_engine = df\n", "df_for_engine = df\n" + inject, 1)
        else:
            m = re.search(pat_df, txt)
            if not m:
                raise RuntimeError("Could not locate df = _load_df(resolved[\"table\"]) to inject contract block.")
            txt = re.sub(pat_df, r'\1' + inject, txt, count=1)

    _write(RUNNER, txt)
    return True

def patch_default_twin():
    txt = _read(TWIN)

    # Add a tiny helper to choose target robustly (string targets OR dict targets)
    if "_pick_target(" not in txt:
        helper = r'''
def _pick_target(schema_contract: dict, df_numeric) -> str:
    """
    Pick a target column robustly.
    Supports schema_contract["recommended_targets"] as:
      - ["colA", "colB", ...]
      - [{"col":"colA"}, {"name":"colB"}, ...]
    Falls back to first numeric column.
    """
    targets = (schema_contract or {}).get("recommended_targets", []) or []
    for t in targets:
        if isinstance(t, str) and t in df_numeric.columns:
            return t
        if isinstance(t, dict):
            c = t.get("col") or t.get("name") or t.get("target") or t.get("column")
            if isinstance(c, str) and c in df_numeric.columns:
                return c
    return df_numeric.columns[0]
'''
        # insert after imports
        if "import numpy as np" in txt:
            txt = txt.replace("import numpy as np\n", "import numpy as np\n" + helper + "\n", 1)
        else:
            txt = helper + "\n" + txt

    # Replace existing target selection block (works even if your text differs slightly)
    # Find the first occurrence of "targets = schema_contract.get("recommended_targets"..."
    txt = re.sub(
        r'(?s)#\s*🎯\s*SCHEMA-DRIVEN TARGET SELECTION.*?target\s*=\s*targets\[0\].*?\n',
        '# 🎯 SCHEMA-DRIVEN TARGET SELECTION\n    target = _pick_target(schema_contract, df_numeric)\n',
        txt,
        count=1
    )

    # If that exact block wasn't found, do a lighter replacement:
    if "target = _pick_target" not in txt:
        txt = re.sub(
            r'targets\s*=\s*schema_contract\.get\("recommended_targets",\s*\[\]\)\s*\r?\n\s*target\s*=\s*targets\[0\]\s*if\s*targets\s*else\s*df_numeric\.columns\[0\]',
            'target = _pick_target(schema_contract, df_numeric)',
            txt,
            count=1
        )

    _write(TWIN, txt)
    return True

def patch_schema_contract_warnings():
    # Optional: remove deprecated infer_datetime_format=True
    if not SCHEMA.exists():
        return False

    txt = _read(SCHEMA)
    if "infer_datetime_format" not in txt:
        return False

    # Remove infer_datetime_format=... from pd.to_datetime calls
    txt2 = re.sub(r',\s*infer_datetime_format\s*=\s*(True|False)\s*', '', txt)
    txt2 = re.sub(r',\s*infer_datetime_format\s*=\s*(True|False)\s*\)', ')', txt2)
    _write(SCHEMA, txt2)
    return True

if __name__ == "__main__":
    if not RUNNER.exists():
        raise SystemExit(f"Runner not found: {RUNNER}")
    if not TWIN.exists():
        raise SystemExit(f"Default twin not found: {TWIN}")

    patch_runner()
    patch_default_twin()
    patched_schema = patch_schema_contract_warnings()

    print("✅ Patched run_aurora_dataset_runner.py (structure_contract NameError + df_for_engine)")
    print("✅ Patched default_twin.py (schema-driven target selection: strings OR dict targets)")
    if patched_schema:
        print("✅ Patched schema_contract.py (removed deprecated infer_datetime_format usage)")
    else:
        print("ℹ️ schema_contract.py unchanged (either missing or already clean)")
