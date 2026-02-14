from __future__ import annotations
import re
from pathlib import Path
from datetime import datetime

RUNNER = Path(r"scripts\run_aurora_dataset_runner.py")

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    b.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[OK] backup -> {b}")
    return b

def main() -> None:
    if not RUNNER.exists():
        raise SystemExit(f"Missing runner: {RUNNER}")

    txt = RUNNER.read_text(encoding="utf-8")

    backup(RUNNER)

    # Replace the entire argparse+parse_args section inside main() with a known-good block.
    # This fixes:
    #  - unexpected indent / args at column 0
    #  - duplicate --deep-math definitions
    #  - ensures deep-math implies math-v2 (your current logic already expects that)
    pattern = re.compile(
        r"(def main\(\)\s*->\s*None:\s*\n)"
        r"([\s\S]*?)"
        r"(\n\s*reg\s*=\s*_load_registry\(\))",
        re.M
    )

    m = pattern.search(txt)
    if not m:
        raise SystemExit("Could not locate main() argparse block to patch (pattern not found).")

    head = m.group(1)
    tail_start = m.start(3)
    tail = txt[tail_start:]  # starts at "reg = _load_registry()"

    good_block = (
        "def main() -> None:\n"
        "    ap = argparse.ArgumentParser()\n"
        "    ap.add_argument(\"--dataset\", required=True, help=\"dataset key or RANDOM\")\n"
        "    ap.add_argument(\"--seed\", type=int, default=0)\n"
        "    ap.add_argument(\"--also-math\", action=\"store_true\")\n"
        "    ap.add_argument(\"--also-llm\", action=\"store_true\")\n"
        "    ap.add_argument(\"--math-v2\", action=\"store_true\", help=\"use deeper MathStackV2\")\n"
        "    ap.add_argument(\"--deep-math\", action=\"store_true\", help=\"run deep math extras (hypothesis/PCA/models/regimes)\")\n"
        "    args = ap.parse_args()\n"
    )

    txt2 = txt[:m.start()] + good_block + tail

    # Hard safety: ensure only ONE --deep-math add_argument remains
    deep_defs = re.findall(r'ap\.add_argument\(\s*"--deep-math"', txt2)
    if len(deep_defs) != 1:
        # If something weird happened, de-dupe by keeping the first and removing others
        txt2 = re.sub(r'\n\s*ap\.add_argument\(\s*"--deep-math"[\s\S]*?\)\s*', '\n', txt2, count=max(0, len(deep_defs)-1))
        deep_defs2 = re.findall(r'ap\.add_argument\(\s*"--deep-math"', txt2)
        print(f"[WARN] deep-math defs after cleanup: {len(deep_defs2)}")

    RUNNER.write_text(txt2, encoding="utf-8")
    print(f"[OK] patched runner: {RUNNER}")

if __name__ == "__main__":
    main()
