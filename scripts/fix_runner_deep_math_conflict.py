from __future__ import annotations

import re
from pathlib import Path
import datetime

ROOT = Path.cwd()
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"

def backup(p: Path) -> Path:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    b.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    return b

def main() -> None:
    if not RUNNER.exists():
        raise FileNotFoundError(f"Missing runner: {RUNNER}")

    backup(RUNNER)
    txt = RUNNER.read_text(encoding="utf-8")

    # ---- 1) Remove duplicate --deep-math argparse definitions (keep the first, delete the rest) ----
    # This deletes every ap.add_argument("--deep-math"... ) after the first occurrence.
    lines = txt.splitlines(True)
    out = []
    seen = 0
    for ln in lines:
        if re.search(r'\bap\.add_argument\(\s*"--deep-math"\b', ln):
            seen += 1
            if seen >= 2:
                continue
        out.append(ln)
    txt = "".join(out)

    # ---- 2) Ensure runner imports compute_deep_math_v1 (idempotent) ----
    if "compute_deep_math_v1" not in txt:
        txt = re.sub(
            r'(from\s+fantasyai\.aurora\.mathstack_v2\s+import\s+compute_math_stack_v2\s*\n)',
            r'\1from fantasyai.aurora.math.deep_math import compute_deep_math_v1\n',
            txt,
            count=1,
        )

    # ---- 3) Replace placeholder deep-math behavior with deterministic call ----
    # If the runner still contains the placeholder note text, replace that whole block.
    placeholder = "deep_math module present but no known entrypoint found (compute/run/analyze)"
    if placeholder in txt:
        # Replace any block that writes that placeholder with a real call.
        # We target the write of deep_math.json and rebuild around it.
        txt = re.sub(
            r'(?s)\n\s*# Optional deep-math extras.*?\n\s*math_results\["_deep_math_file"\]\s*=\s*"deep_math\.json"\s*\n',
            "\n        # Optional deep-math extras (deterministic)\n"
            "        if getattr(args, \"deep_math\", False):\n"
            "            try:\n"
            "                deep_out = compute_deep_math_v1(df, seed=int(args.seed))\n"
            "            except Exception as e:\n"
            "                deep_out = {\"error\": f\"deep_math_failed:{type(e).__name__}:{e}\"}\n"
            "            (run_dir / \"deep_math.json\").write_text(json.dumps(deep_out, indent=2), encoding=\"utf-8\")\n"
            "            math_results[\"_deep_math_file\"] = \"deep_math.json\"\n\n",
            txt,
            count=1,
        )

    # ---- 4) If there is still no deep-math write at all, insert after math_results.json write ----
    if "deep_math.json" not in txt:
        m = re.search(r'\(run_dir\s*/\s*"math_results\.json"\)\.write_text\(', txt)
        if not m:
            raise RuntimeError("Could not locate math_results.json write in runner to insert deep-math block.")
        insert_at = m.end()
        insert_block = (
            "\n        # Optional deep-math extras (deterministic)\n"
            "        if getattr(args, \"deep_math\", False):\n"
            "            try:\n"
            "                deep_out = compute_deep_math_v1(df, seed=int(args.seed))\n"
            "            except Exception as e:\n"
            "                deep_out = {\"error\": f\"deep_math_failed:{type(e).__name__}:{e}\"}\n"
            "            (run_dir / \"deep_math.json\").write_text(json.dumps(deep_out, indent=2), encoding=\"utf-8\")\n"
            "            math_results[\"_deep_math_file\"] = \"deep_math.json\"\n"
        )
        txt = txt[:insert_at] + insert_block + txt[insert_at:]

    RUNNER.write_text(txt, encoding="utf-8", newline="\n")
    print(f"[OK] patched: {RUNNER}")
    if __name__ == "__main__":
    main()
