from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re

RUNNER = Path(r".\scripts\run_aurora_dataset_runner.py")

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    b.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    return b

def main():
    if not RUNNER.exists():
        raise SystemExit(f"Missing runner: {RUNNER}")

    txt = RUNNER.read_text(encoding="utf-8")

    # --- 1) Remove any prior deep-math imports (v1/v2) ---
    txt = re.sub(r"^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+compute_deep_math_v1\s*\r?\n", "", txt, flags=re.M)
    txt = re.sub(r"^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+compute_deep_math_v2\s*\r?\n", "", txt, flags=re.M)

    # --- 2) Remove any prior "robust import block" we might have injected (compute_deep_math = None etc.) ---
    # We delete a tight signature block if present.
    txt = re.sub(
        r"(?ms)^\s*try:\s*\n\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+compute_deep_math_v2\s+as\s+compute_deep_math\s*\n\s*except\s+Exception:\s*\n\s*try:\s*\n\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+compute_deep_math_v1\s+as\s+compute_deep_math\s*\n\s*except\s+Exception:\s*\n\s*compute_deep_math\s*=\s*None\s*\n",
        "",
        txt
    )

    # --- 3) Ensure argparse has exactly ONE --deep-math definition ---
    lines = txt.splitlines(True)
    new_lines = []
    deep_arg_seen = 0
    for line in lines:
        if ("add_argument" in line) and ("--deep-math" in line):
            deep_arg_seen += 1
            if deep_arg_seen > 1:
                continue
        new_lines.append(line)
    txt = "".join(new_lines)

    # If there is no --deep-math at all, add it right after --math-v2 if possible.
    if txt.count("--deep-math") == 0:
        m = re.search(r'^(?P<indent>\s*)ap\.add_argument\(\s*["\']--math-v2["\'].*\)\s*\r?\n', txt, flags=re.M)
        if m:
            indent = m.group("indent")
            insert_at = m.end()
            add = f'{indent}ap.add_argument("--deep-math", action="store_true", help="run deep math extras (hypothesis tests, PCA, multivariate, regimes, uncertainty)")\n'
            txt = txt[:insert_at] + add + txt[insert_at:]
        else:
            # fallback: add near other args
            m2 = re.search(r'^(?P<indent>\s*)ap\s*=\s*argparse\.ArgumentParser.*\r?\n', txt, flags=re.M)
            if m2:
                indent = m2.group("indent")
                insert_at = m2.end()
                add = f'{indent}ap.add_argument("--deep-math", action="store_true", help="run deep math extras (hypothesis tests, PCA, multivariate, regimes, uncertainty)")\n'
                txt = txt[:insert_at] + add + txt[insert_at:]

    # --- 4) Insert a NON-SILENT deep-math import block (captures error text for deep_math.json) ---
    import_block = (
        "\n"
        "# ---- Deep Math import (v2-first, then v1). Captures import error for diagnostics. ----\n"
        "compute_deep_math = None\n"
        "_DEEP_MATH_IMPORT_ERROR = None\n"
        "try:\n"
        "    from fantasyai.aurora.math.deep_math import compute_deep_math_v2 as compute_deep_math\n"
        "except Exception as _e1:\n"
        "    try:\n"
        "        from fantasyai.aurora.math.deep_math import compute_deep_math_v1 as compute_deep_math\n"
        "    except Exception as _e2:\n"
        "        _DEEP_MATH_IMPORT_ERROR = f\"{type(_e2).__name__}:{_e2} (after {type(_e1).__name__}:{_e1})\"\n"
    )

    # Insert after mathstack_v2 import if present, else after last import.
    if "compute_deep_math = None" not in txt:
        m = re.search(r"^from\s+fantasyai\.aurora\.mathstack_v2\s+import\s+compute_math_stack_v2\s*\r?\n", txt, flags=re.M)
        if m:
            insert_at = m.end()
            txt = txt[:insert_at] + import_block + txt[insert_at:]
        else:
            imports = list(re.finditer(r"^(import|from)\s+.+\r?\n", txt, flags=re.M))
            insert_at = imports[-1].end() if imports else 0
            txt = txt[:insert_at] + import_block + txt[insert_at:]

    # --- 5) Wire the deep-math call reliably.
    # We look for the spot where math_results.json is written, then add deep math write right after.
    marker = '(run_dir / "math_results.json").write_text(json.dumps(math_results, indent=2), encoding="utf-8")'
    if marker in txt and "deep_math.json" not in txt:
        add_call = (
            "\n"
            "        # ---- Deep Math run (only when requested) ----\n"
            "        if getattr(args, \"deep_math\", False):\n"
            "            if compute_deep_math is None:\n"
            "                deep_out = {\"error\": f\"deep_math_import_failed:{_DEEP_MATH_IMPORT_ERROR}\"}\n"
            "            else:\n"
            "                try:\n"
            "                    deep_out = compute_deep_math(df, seed=int(args.seed))\n"
            "                except Exception as e:\n"
            "                    deep_out = {\"error\": f\"deep_math_failed:{type(e).__name__}:{e}\"}\n"
            "            (run_dir / \"deep_math.json\").write_text(json.dumps(deep_out, indent=2), encoding=\"utf-8\")\n"
            "            math_results[\"_deep_math_file\"] = \"deep_math.json\"\n"
        )
        txt = txt.replace(marker, marker + add_call)

    b = backup(RUNNER)
    RUNNER.write_text(txt, encoding="utf-8")

    print(f"[OK] backup -> {b}")
    print(f"[OK] runner patched -> {RUNNER.resolve()}")
    print(f"[OK] deep-math argparse count: {txt.count('--deep-math')}")
    print(f"[OK] deep-math import block present: {('compute_deep_math = None' in txt)}")
    print(f"[OK] deep-math write present: {('deep_math.json' in txt)}")

if __name__ == "__main__":
    main()
