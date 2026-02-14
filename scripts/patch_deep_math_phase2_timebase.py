from __future__ import annotations
from pathlib import Path
import re, datetime as dt, shutil

ROOT = Path.cwd()
DEEP = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(p: Path) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_phase2_{ts}")
    shutil.copy2(p, b)
    return b

def main():
    if not DEEP.exists():
        raise SystemExit(f"ERROR not found: {DEEP}")
    txt = DEEP.read_text(encoding="utf-8", errors="ignore")
    b = backup(DEEP)
    print(f"[OK] backup -> {b}")

    if "build_canonical_time" not in txt:
        # add import near other aurora math imports
        txt = re.sub(r"(from __future__ import annotations\s*\n)", r"\1from .timebase import build_canonical_time\n", txt, count=1)
        print("[OK] added timebase import")

    # Insert execution early inside main entry (look for first df load / start of run)
    # We’ll place after the first place where df is defined/loaded in deep_math() function.
    if '"timebase"' not in txt:
        # naive but robust: after first occurrence of "df =" that loads/receives df
        m = re.search(r"\n(\s*)df\s*=\s*.*\n", txt)
        if not m:
            raise SystemExit("ERROR: could not find a df assignment to anchor timebase insertion.")
        indent = m.group(1)

        inject = f"""\n{indent}# --- Phase 2: canonical timebase ---\n{indent}try:\n{indent}    df, timebase_meta = build_canonical_time(df)\n{indent}    deep['timebase'] = timebase_meta\n{indent}except Exception as e:\n{indent}    deep['timebase'] = {{'note': 'error', 'error': str(e)}}\n"""
        pos = m.end()
        txt = txt[:pos] + inject + txt[pos:]
        print("[OK] injected timebase execution + deep['timebase']")

    DEEP.write_text(txt, encoding="utf-8")
    print(f"[DONE] wrote -> {DEEP}")

    import py_compile
    py_compile.compile(str(DEEP), doraise=True)
    print("[DONE] deep_math.py compiled clean")

if __name__ == "__main__":
    main()
