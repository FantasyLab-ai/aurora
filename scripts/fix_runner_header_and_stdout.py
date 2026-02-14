from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import json
import py_compile

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

def main() -> int:
    if not RUNNER.exists():
        print(f"[ERR] Missing: {RUNNER}")
        return 2

    bak = backup(RUNNER)
    print(f"[OK] backup -> {bak}")

    txt = RUNNER.read_text(encoding="utf-8")

    # Find a stable anchor after imports. Prefer TABLE_EXTS; else fall back to first def main().
    anchor = None
    m = re.search(r"(?m)^(TABLE_EXTS\s*=.*)$", txt)
    if m:
        anchor = m.start()
    else:
        m2 = re.search(r"(?m)^def\s+main\s*\(", txt)
        if not m2:
            print("[ERR] Could not find TABLE_EXTS or def main() anchor; refusing to patch blindly.")
            return 3
        anchor = m2.start()

    body = txt[anchor:].lstrip("\n")

    # Build a clean, known-good header.
    # NOTE: we intentionally do NOT rely on whatever broken try/except blocks exist right now.
    header = "\n".join([
        "from __future__ import annotations",
        "",
        "import argparse",
        "import json",
        "import os",
        "import sys",
        "from pathlib import Path",
        "from typing import Any, Dict, Optional",
        "",
        "# --- Deep Math (stable import) ---",
        "# Prefer v3; fall back to v2; if neither exists, runner should still run and write an error payload.",
        "try:",
        "    from fantasyai.aurora.math.deep_math import compute_deep_math_v3 as compute_deep_math  # type: ignore",
        "except Exception:",
        "    try:",
        "        from fantasyai.aurora.math.deep_math import compute_deep_math_v2 as compute_deep_math  # type: ignore",
        "    except Exception:",
        "        compute_deep_math = None  # type: ignore",
        "",
        ""
    ])

    # Remove ANY prior deep-math import blocks to avoid duplicates.
    # 1) remove old direct imports
    txt_clean = re.sub(r"(?m)^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+.*\n", "", txt)
    # 2) remove old blocks that start with '# --- Deep Math' up to a blank line gap
    txt_clean = re.sub(r"(?s)\n?#\s*---\s*Deep Math.*?\n\n", "\n", txt_clean)

    # Recompute body from the *original* body anchor (not from txt_clean), because we are overwriting header anyway.
    new_txt = header + body

    # Ensure there is a final stdout JSON print.
    # If the runner already prints json.dumps(...) at the end, we don't double-add.
    if "RUNNER_FINAL_STDOUT_JSON" not in new_txt:
        # Insert a small helper near the bottom (before if __name__ == '__main__':) that prints
        # the latest run folder and key artifacts when main finishes.
        insert_marker = re.search(r"(?m)^if\s+__name__\s*==\s*['\"]__main__['\"]\s*:", new_txt)
        if not insert_marker:
            # If no __main__ guard exists, append at end.
            new_txt = new_txt.rstrip() + "\n\n"
            insert_pos = len(new_txt)
        else:
            insert_pos = insert_marker.start()

        printer = "\n".join([
            "",
            "# RUNNER_FINAL_STDOUT_JSON",
            "def _print_final_stdout_json(latest_run_dir: Optional[Path]) -> None:",
            "    try:",
            "        payload: Dict[str, Any] = {",
            "            'ok': True,",
            "            'latest_run_dir': str(latest_run_dir) if latest_run_dir else None,",
            "            'artifacts': {},",
            "        }",
            "        if latest_run_dir and latest_run_dir.exists():",
            "            for name in ['tableselection.json','math_results.json','deep_math.json','llm_narrative.json','llm_debug.json','storycard.json']:",
            "                p = latest_run_dir / name",
            "                payload['artifacts'][name] = str(p) if p.exists() else None",
            "        print(json.dumps(payload, indent=2))",
            "    except Exception as e:",
            "        # Never crash on printing.",
            "        print(json.dumps({'ok': False, 'print_error': f'{type(e).__name__}: {e}'}, indent=2))",
            "",
            ""
        ])

        new_txt = new_txt[:insert_pos] + printer + new_txt[insert_pos:]

    RUNNER.write_text(new_txt, encoding="utf-8")
    py_compile.compile(str(RUNNER), doraise=True)
    print("[DONE] runner header reset + deep-math import stabilized + final stdout JSON restored")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
