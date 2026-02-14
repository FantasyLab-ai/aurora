from __future__ import annotations

import re
from pathlib import Path
import hashlib

RUNNER = Path("scripts/run_aurora_dataset_runner.py")

def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

def main():
    if not RUNNER.exists():
        raise SystemExit(f"Missing runner: {RUNNER.resolve()}")

    txt = RUNNER.read_text(encoding="utf-8")

    # 0) backup
    bak = RUNNER.with_suffix(".py.bak_clean_deep_math")
    bak.write_text(txt, encoding="utf-8")
    print(f"[OK] backup -> {bak}")

    # 1) Remove duplicate deep_math imports + normalize to ONE import
    # Kill: from fantasyai.aurora.math import deep_math  (module import not needed)
    txt = re.sub(r"^\s*from\s+fantasyai\.aurora\.math\s+import\s+deep_math\s*\r?\n", "", txt, flags=re.M)

    # Kill any old imports of compute_deep_math_v1/v2/v3 (we'll re-add clean)
    txt = re.sub(r"^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+compute_deep_math_v[123]\s*\r?\n", "", txt, flags=re.M)

    # There are duplicate compute_math_stack_v2 imports in your file; keep only one.
    # Remove all of them then add one clean import near the top.
    txt = re.sub(r"^\s*from\s+fantasyai\.aurora\.mathstack_v2\s+import\s+compute_math_stack_v2\s*\r?\n", "", txt, flags=re.M)

    # Insert clean imports right after pandas import (keeps it stable)
    anchor = re.search(r"^\s*import\s+pandas\s+as\s+pd\s*\r?\n", txt, flags=re.M)
    if not anchor:
        raise SystemExit("Could not find 'import pandas as pd' to anchor imports.")

    insert = (
        "import pandas as pd\n\n"
        "from fantasyai.aurora.mathstack_v2 import compute_math_stack_v2\n"
        "from fantasyai.aurora.math.deep_math import compute_deep_math_v3\n\n"
        "# Deep-math function handle used by the runner\n"
        "compute_deep_math = compute_deep_math_v3\n\n"
    )
    # Replace the pandas import line with our block (includes pandas line)
    txt = re.sub(r"^\s*import\s+pandas\s+as\s+pd\s*\r?\n", insert, txt, count=1, flags=re.M)

    # 2) Remove the broken empty try/except block completely
    # Your file currently has:
    # try:
    #     # preferred
    # except Exception:
    #     compute_deep_math = None
    txt = re.sub(
        r"(?ms)^\s*try:\s*\r?\n\s*#\s*preferred\s*\r?\n\s*except\s+Exception:\s*\r?\n\s*compute_deep_math\s*=\s*None\s*\r?\n",
        "",
        txt
    )

    # 3) Remove ALL duplicate deep-math execution blocks, keep ONE canonical block.
    # We will replace any region between markers if present:
    txt = re.sub(r"(?ms)^\s*#\s*DEEP_MATH_BEGIN.*?#\s*DEEP_MATH_END\s*\r?\n", "", txt)

    # Remove the old "Optional deep-math extras" block
    txt = re.sub(r"(?ms)^\s*#\s*Optional deep-math extras.*?math_results\['_deep_math_file'\]\s*=\s*\"deep_math\.json\".*?\r?\n", "", txt)

    # Also remove the earlier "worldclass" block if duplicated (the one that calls compute_deep_math_v3 directly)
    # We'll remove any block that writes deep_math.json inside the --also-math section, then reinsert one clean block.
    txt = re.sub(
        r"(?ms)^\s*#\s*---\s*deep math.*?\(run_dir\s*/\s*\"deep_math\.json\"\)\.write_text\(json\.dumps\(deep_out,\s*indent=2\),\s*encoding=\"utf-8\"\)\s*\r?\n\s*math_results\[_deep_math_file\"\]\s*=\s*\"deep_math\.json\"\s*\r?\n",
        "",
        txt
    )

    # 4) Insert ONE deterministic deep-math block immediately after writing math_results.json
    # Find the math_results.json write line and inject after it
    m = re.search(r"(?m)^\s*\(run_dir\s*/\s*\"math_results\.json\"\)\.write_text\(\s*json\.dumps\(math_results,\s*indent=2\),\s*encoding=\"utf-8\"\s*\)\s*\r?\n", txt)
    if not m:
        raise SystemExit("Could not find math_results.json write line to insert deep-math block after.")

    deep_block = (
        "\n"
        "        # --- deep math (single canonical call) ---\n"
        "        if getattr(args, \"deep_math\", False):\n"
        "            try:\n"
        "                deep_out = compute_deep_math(df, seed=int(args.seed))\n"
        "            except Exception as e:\n"
        "                deep_out = {\"error\": f\"deep_math_failed:{type(e).__name__}:{e}\"}\n"
        "            (run_dir / \"deep_math.json\").write_text(json.dumps(deep_out, indent=2), encoding=\"utf-8\")\n"
        "            math_results[\"_deep_math_file\"] = \"deep_math.json\"\n"
        "\n"
    )

    # Only insert if not already present
    if "deep math (single canonical call)" not in txt:
        insert_at = m.end()
        txt = txt[:insert_at] + deep_block + txt[insert_at:]

    # 5) Ensure argparse defines --deep-math only once (yours already does; just dedupe if patch scripts duplicated it)
    # If multiple occurrences exist, keep the first and remove the rest.
    occurrences = list(re.finditer(r'^\s*ap\.add_argument\(\s*["\']--deep-math["\'].*\)\s*$', txt, flags=re.M))
    if len(occurrences) > 1:
        # remove all but the first occurrence line
        first_span = occurrences[0].span()
        # build a mask to delete other lines
        lines = txt.splitlines(True)
        out_lines = []
        seen = 0
        for line in lines:
            if re.search(r'^\s*ap\.add_argument\(\s*["\']--deep-math["\']', line):
                seen += 1
                if seen > 1:
                    continue
            out_lines.append(line)
        txt = "".join(out_lines)

    RUNNER.write_text(txt, encoding="utf-8")
    print(f"[OK] wrote runner -> {RUNNER.resolve()} (sha={sha(txt)})")
    print(f"[OK] deep-math argparse count: {len(re.findall(r\"^\\s*ap\\.add_argument\\(\\s*['\\\"]--deep-math\", txt, flags=re.M))}")
    print(f"[OK] deep-math canonical block present: {('deep math (single canonical call)' in txt)}")

if __name__ == "__main__":
    main()
