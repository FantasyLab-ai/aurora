from __future__ import annotations
from pathlib import Path
import re
import py_compile
from datetime import datetime

CAUSAL = Path(r"fantasyai/aurora/math/causal.py")

def main():
    if not CAUSAL.exists():
        raise SystemExit(f"[FAIL] not found: {CAUSAL}")

    txt = CAUSAL.read_text(encoding="utf-8", errors="ignore")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = CAUSAL.with_suffix(CAUSAL.suffix + f".bak_{stamp}")
    bak.write_text(txt, encoding="utf-8")

    new = re.sub(r'pd\.to_numeric\(([^)]*),\s*errors\s*=\s*["\']ignore["\']\)',
                 r'pd.to_numeric(\1, errors="coerce")', txt)

    if new == txt:
        print("[OK] no errors='ignore' usage found (nothing to do).")
    else:
        CAUSAL.write_text(new, encoding="utf-8")
        print("[OK] replaced deprecated errors='ignore' -> errors='coerce'")

    py_compile.compile(str(CAUSAL), doraise=True)
    print("[DONE] causal.py compiled clean")

if __name__ == "__main__":
    main()
