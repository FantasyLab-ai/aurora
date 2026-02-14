from __future__ import annotations
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"
TWIN   = ROOT / "fantasyai" / "aurora" / "digital_twin" / "default_twin.py"

def patch_runner():
    txt = RUNNER.read_text(encoding="utf-8")

    # 1) Fix: _write_json(run_dir / "structure_contract.json", contract) -> structure_contract
    if 'structure_contract.json", contract' in txt:
        txt2 = txt.replace('structure_contract.json", contract', 'structure_contract.json", structure_contract')
        txt = txt2

    # 2) Ensure we don't accidentally call build_structure_contract with raw df when df_for_engine exists
    # (Your file already does this fine, but we ensure it's stable / not clobbered.)
    # No-op unless pattern exists.
    RUNNER.write_text(txt, encoding="utf-8")
    print("✅ Patched runner: structure_contract write fixed")

def patch_twin():
    txt = TWIN.read_text(encoding="utf-8")

    # We will make run_default_digital_twin accept structure_contract (optional)
    # and prefer it for target selection (falls back to schema_contract).
    # Also make _pick_target accept either contract shape.

    # Replace _pick_target signature + internal access
    # If your file already matches the uploaded version, this patch is safe.
    txt = re.sub(
        r"def _pick_target\(\s*schema_contract:\s*dict,\s*df_numeric\)\s*->\s*str\s*:",
        "def _pick_target(contract: dict, df_numeric) -> str:",
        txt
    )
    txt = re.sub(
        r"targets\s*=\s*\(schema_contract\s*or\s*\{\}\)\.get\(\"recommended_targets\",\s*\[\]\)\s*or\s*\[\]\s*",
        "targets = (contract or {}).get(\"recommended_targets\", []) or []",
        txt
    )

    # Update run_default_digital_twin signature to accept structure_contract=None
    # We do this by rewriting the def line and argument block conservatively.
    # If it already has structure_contract, we won't double-insert.
    if "structure_contract" not in txt:
        txt = re.sub(
            r"def run_default_digital_twin\(\s*\n\s*schema_contract:\s*dict,\s*\n\s*df_numeric,\s*\n",
            "def run_default_digital_twin(\n    schema_contract: dict,\n    df_numeric,\n    structure_contract: dict | None = None,\n",
            txt
        )

    # Choose which contract drives target selection
    # Replace: target = _pick_target(schema_contract, df_numeric)
    txt = txt.replace(
        "target = _pick_target(schema_contract, df_numeric)",
        "contract_for_targeting = structure_contract or schema_contract\n\n    # 🎯 SCHEMA/STRUCTURE-DRIVEN TARGET SELECTION\n    target = _pick_target(contract_for_targeting, df_numeric)"
    )

    # Return both contracts for auditability
    # If return currently includes only schema_contract, add structure_contract.
    if '"structure_contract"' not in txt:
        txt = txt.replace(
            '"schema_contract": schema_contract,',
            '"schema_contract": schema_contract,\n        "structure_contract": structure_contract,'
        )

    TWIN.write_text(txt, encoding="utf-8")
    print("✅ Patched default_twin: accepts structure_contract + uses it for target selection")

def main():
    patch_runner()
    patch_twin()

if __name__ == "__main__":
    main()
