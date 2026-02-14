from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re as _re

ROOT = Path(__file__).resolve().parents[1]
FP = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(p: Path) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[OK] backup -> {bak}")

def main() -> None:
    if not FP.exists():
        raise FileNotFoundError(f"Missing: {FP}")

    txt = FP.read_text(encoding="utf-8")

    # If it already imports re anywhere, do nothing.
    if _re.search(r"(?m)^\s*import\s+re\s*$", txt) or _re.search(r"(?m)^\s*from\s+re\s+import\s+", txt):
        print("[OK] deep_math.py already imports re")
        return

    backup(FP)

    # Insert `import re` after the last import in the initial import block.
    lines = txt.splitlines(True)

    # Find the insertion point: after the last contiguous import line near the top.
    insert_at = None
    for i, line in enumerate(lines[:120]):  # only scan the top chunk
        if _re.match(r"^\s*(from\s+\S+\s+import\s+.+|import\s+\S+)\s*$", line):
            insert_at = i + 1
        elif insert_at is not None and line.strip() != "" and not line.lstrip().startswith("#"):
            # first non-import statement after imports
            break

    if insert_at is None:
        # fallback: after future import if present, else top of file
        for i, line in enumerate(lines[:40]):
            if "from __future__ import" in line:
                insert_at = i + 1
                break
        if insert_at is None:
            insert_at = 0

    lines.insert(insert_at, "import re\n")
    new_txt = "".join(lines)
    FP.write_text(new_txt, encoding="utf-8")
    print(f"[OK] inserted `import re` -> {FP}")

if __name__ == "__main__":
    main()
