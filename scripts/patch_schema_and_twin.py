from __future__ import annotations
from pathlib import Path
import re
import sys

ROOT = Path(".").resolve()

RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"
TWIN   = ROOT / "fantasyai" / "aurora" / "digital_twin" / "default_twin.py"
SCHEMA = ROOT / "fantasyai" / "aurora" / "contracts" / "schema_contract.py"

def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")

def write(p: Path, s: str):
    p.write_text(s, encoding="utf-8", newline="\n")

def die(msg: str):
    print("[PATCH ERROR]", msg)
    sys.exit(1)

def patch_runner():
    if not RUNNER.exists():
        die(f"Missing file: {RUNNER}")

    s = read(RUNNER)
    s0 = s

    # 1) Fix structure_contract NameError + ensure contract uses df_for_engine
    # We look for a block that assigns `contract = build_structure_contract(...)`
    # and the following _write_json(... structure_contract)
    pattern = re.compile(
        r"""
        (?P<indent>^[ \t]*)contract\s*=\s*build_structure_contract\(
            (?P<args>[^)]*)
        \)\s*\r?\n
        (?P=indent)_write_json\(\s*run_dir\s*/\s*"structure_contract\.json"\s*,\s*structure_contract\s*\)\s*
        """,
        re.MULTILINE | re.VERBOSE,
    )

    def repl(m: re.Match) -> str:
        indent = m.group("indent")
        # Force df_for_engine to be used (if present in local scope)
        # We'll preserve dataset_key arg if included.
        args = m.group("args").strip()
        # If they already pass df=..., replace it.
        if "df_for_engine" in s:
            args = re.sub(r"\bdf\s*=\s*[^,\n)]+", "df=df_for_engine", args)
            # If no df=... in args, prepend it.
            if "df=" not in args:
                args = "df=df_for_engine" + (", " + args if args else "")
        return (
            f"{indent}contract = build_structure_contract({args})\n"
            f"{indent}_write_json(run_dir / \"structure_contract.json\", contract)\n"
        )

    s, n = pattern.subn(repl, s)
    if n == 0:
        # More general fallback: directly fix the write_json line
        s2, n2 = re.subn(
            r'_write_json\(\s*run_dir\s*/\s*"structure_contract\.json"\s*,\s*structure_contract\s*\)',
            '_write_json(run_dir / "structure_contract.json", contract)',
            s,
        )
        s = s2
        if n2 == 0:
            die("Could not find the structure_contract write block to patch in run_aurora_dataset_runner.py")

    # 2) Remove deprecated infer_datetime_format=True in runner (if present)
    s = s.replace("pd.to_datetime(ss, errors=\"coerce\", infer_datetime_format=True)",
                  "pd.to_datetime(ss, errors=\"coerce\")")

    if s != s0:
        write(RUNNER, s)
        print("✅ Patched runner: fixed structure_contract NameError + removed infer_datetime_format in runner")
    else:
        print("ℹ️ Runner unchanged (already patched)")

def patch_schema_contract():
    if not SCHEMA.exists():
        print(f"ℹ️ schema_contract.py not found at {SCHEMA} (skipping)")
        return
    s = read(SCHEMA)
    s0 = s

    # Remove deprecated infer_datetime_format=True everywhere
    s = re.sub(r",\s*infer_datetime_format\s*=\s*True", "", s)

    if s != s0:
        write(SCHEMA, s)
        print("✅ Patched schema_contract.py: removed deprecated infer_datetime_format=True")
    else:
        print("ℹ️ schema_contract.py unchanged (already clean)")

def patch_default_twin():
    if not TWIN.exists():
        die(f"Missing file: {TWIN}")

    s = read(TWIN)
    s0 = s

    # 1) Extend signature to accept structure_contract=None (backwards compatible)
    s, n_sig = re.subn(
        r"def\s+run_default_digital_twin\(\s*(?P<args>[^)]*schema_contract\s*:\s*dict\s*,\s*df_numeric\s*,\s*out_dir\s*:\s*Path[^)]*)\)",
        lambda m: "def run_default_digital_twin(" + m.group("args") + ", structure_contract: dict | None = None)",
        s,
        count=1,
        flags=re.DOTALL,
    )
    if n_sig == 0 and "structure_contract" not in s:
        # If signature format differs, do a simpler insertion by locating schema_contract param
        s2, n2 = re.subn(
            r"(schema_contract\s*:\s*dict\s*,\s*df_numeric\s*,\s*out_dir\s*:\s*Path)",
            r"\1, structure_contract: dict | None = None",
            s,
            count=1,
        )
        s = s2
        if n2 == 0:
            die("Could not patch run_default_digital_twin signature to accept structure_contract")

    # 2) Replace target selection logic with robust schema/structure-driven picker
    # We'll locate the existing section that starts with 'targets =' and ends before model fitting.
    # If not found, we inject helper + usage near the top of the function.
    target_block = re.compile(
        r"""
        (?P<indent>^[ \t]+)targets\s*=\s*schema_contract\.get\("recommended_targets"[^)]*\)\s*\r?\n
        (?P=indent)target\s*=\s*targets\[0\]\s*.*?\r?\n
        (?P=indent)series\s*=\s*df_numeric\[target\]\s*\r?\n
        """,
        re.MULTILINE | re.VERBOSE | re.DOTALL,
    )

    helper = r'''
def _pick_target(schema_contract: dict, structure_contract: dict | None, df_numeric):
    """
    Choose a reasonable numeric target for a default digital twin.

    Supports schema_contract["recommended_targets"] as:
      - list[str]
      - list[{"col": "..."}] / {"name": "..."} / {"target": "..."} dicts

    Rejects:
      - mostly-missing columns
      - ID-like columns (nearly unique per row)
      - constant/near-constant columns
    """
    def _as_col(x):
        if isinstance(x, str):
            return x
        if isinstance(x, dict):
            for k in ("col", "name", "target", "column"):
                if k in x and isinstance(x[k], str):
                    return x[k]
        return None

    # Candidate list from schema
    rec = schema_contract.get("recommended_targets", []) or []
    cols = []
    if isinstance(rec, list):
        for item in rec:
            c = _as_col(item)
            if c:
                cols.append(c)
    elif isinstance(rec, (str, dict)):
        c = _as_col(rec)
        if c:
            cols.append(c)

    # If structure_contract gives a suggested target, prefer it (but validate it)
    if structure_contract:
        st = structure_contract.get("suggested_target") or structure_contract.get("target_col")
        c = _as_col(st)
        if c:
            cols.insert(0, c)

    # Fallback: any numeric columns
    if not cols:
        cols = list(df_numeric.columns)

    n = len(df_numeric)
    best = None
    best_score = -1.0

    for c in cols:
        if c not in df_numeric.columns:
            continue
        s = df_numeric[c]
        # Skip mostly missing
        miss = float(s.isna().mean())
        if miss > 0.85:
            continue
        s2 = s.dropna()
        if len(s2) < max(50, int(0.05 * n)):
            continue

        # Reject ID-like (unique ratio close to 1)
        uniq_ratio = float(s2.nunique()) / float(len(s2))
        if uniq_ratio > 0.98 and len(s2) > 200:
            continue

        # Reject near-constant
        v = float(s2.var()) if hasattr(s2, "var") else 0.0
        if v <= 1e-12:
            continue

        # Simple score: prefer low missingness + high variance
        score = (1.0 - miss) * (v ** 0.5)

        if score > best_score:
            best_score = score
            best = c

    # Absolute last resort: first numeric col
    return best or (df_numeric.columns[0] if len(df_numeric.columns) else None)
'''

    if target_block.search(s):
        s = target_block.sub(
            lambda m: (
                m.group("indent") + "# --- schema/structure-driven target selection ---\n"
                + m.group("indent") + "target = _pick_target(schema_contract, structure_contract, df_numeric)\n"
                + m.group("indent") + "if target is None:\n"
                + m.group("indent") + "    raise ValueError('default_twin: no numeric target could be selected')\n"
                + m.group("indent") + "series = df_numeric[target]\n"
            ),
            s,
            count=1,
        )
        # Ensure helper exists (inject once near top of file)
        if "_pick_target(" not in s0:
            # Insert helper after imports (best effort)
            ins_at = s.find("\n\n")
            s = s[:ins_at] + "\n" + helper + "\n" + s[ins_at:]
    else:
        # If we can't find the old pattern, inject helper and add a small target selection snippet
        if "_pick_target(" not in s:
            ins_at = s.find("\n\n")
            s = s[:ins_at] + "\n" + helper + "\n" + s[ins_at:]
        # Try to locate where series is defined; if not found, leave helper only (won't break imports)
        s2, n2 = re.subn(
            r"(?m)^[ \t]+series\s*=\s*df_numeric\[[^\]]+\]\s*$",
            "    target = _pick_target(schema_contract, structure_contract, df_numeric)\n"
            "    if target is None:\n"
            "        raise ValueError('default_twin: no numeric target could be selected')\n"
            "    series = df_numeric[target]",
            s,
            count=1,
        )
        s = s2
        if n2 == 0:
            print("⚠️ default_twin.py: could not locate old target selection block; injected helper only (no breaking change).")

    if s != s0:
        write(TWIN, s)
        print("✅ Patched default_twin.py: schema+structure-driven target selection (string OR dict targets)")
    else:
        print("ℹ️ default_twin.py unchanged (already patched)")

def main():
    patch_runner()
    patch_schema_contract()
    patch_default_twin()
    print("✅ All patches applied.")

if __name__ == "__main__":
    main()
