from __future__ import annotations

import shutil
from pathlib import Path
from datetime import datetime
import re

def backup(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak_{ts}")
    shutil.copy2(path, bak)
    return bak

def main() -> int:
    root = Path.cwd()
    path = root / "fantasyai" / "aurora" / "math" / "regimes.py"
    if not path.exists():
        raise SystemExit(f"Missing: {path}")

    txt = path.read_text(encoding="utf-8")
    bak = backup(path)
    print(f"[OK] backup -> {bak}")

    lines = txt.splitlines(True)

    # Remove ALL occurrences first
    future_line = "from __future__ import annotations\n"
    had_future = any(l.strip() == "from __future__ import annotations" for l in lines)
    lines_wo_future = [l for l in lines if l.strip() != "from __future__ import annotations"]

    # Decide whether to reinsert at top.
    # (Not required for Py3.12, but safe. We'll insert a single one at the correct spot.)
    insert_future = True

    if insert_future:
        # Find insertion point: after shebang/encoding comment, and after module docstring if present.
        i = 0
        # shebang
        if i < len(lines_wo_future) and lines_wo_future[i].startswith("#!"):
            i += 1
        # encoding cookie
        if i < len(lines_wo_future) and re.match(r"^#.*coding[:=]\s*[-\w.]+", lines_wo_future[i]):
            i += 1

        # If there's a module docstring, insert after it.
        def is_triple_quote_start(s: str) -> bool:
            st = s.lstrip()
            return st.startswith('"""') or st.startswith("'''")

        if i < len(lines_wo_future) and is_triple_quote_start(lines_wo_future[i]):
            quote = '"""' if lines_wo_future[i].lstrip().startswith('"""') else "'''"
            i += 1
            # scan until docstring closes
            while i < len(lines_wo_future):
                if quote in lines_wo_future[i]:
                    i += 1
                    break
                i += 1

        # Insert one future import
        lines_wo_future.insert(i, future_line)
        # Ensure a blank line after it if needed
        if i + 1 < len(lines_wo_future) and lines_wo_future[i + 1].strip() != "":
            lines_wo_future.insert(i + 1, "\n")

    path.write_text("".join(lines_wo_future), encoding="utf-8")
    print(f"[OK] fixed future-import placement -> {path} (had_future={had_future})")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
