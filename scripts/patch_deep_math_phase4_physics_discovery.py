from __future__ import annotations
from pathlib import Path
import re, datetime as dt, shutil

ROOT = Path.cwd()
DEEP = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(p: Path) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_phase4_{ts}")
    shutil.copy2(p, b)
    return b

def main():
    if not DEEP.exists():
        raise SystemExit(f"ERROR not found: {DEEP}")
    txt = DEEP.read_text(encoding="utf-8", errors="ignore")
    b = backup(DEEP)
    print(f"[OK] backup -> {b}")

    if "sindy_lite" not in txt:
        txt = re.sub(r"(from __future__ import annotations\s*\n)", r"\1from .physics_discovery import sindy_lite\n", txt, count=1)
        print("[OK] added physics_discovery import")

    if '"physics_discovery"' not in txt:
        m = re.search(r"\n(\s*)return\s+deep\s*\n", txt)
        if not m:
            raise SystemExit("ERROR: could not find return deep anchor")
        indent = m.group(1)
        pos = m.start()

        inject = f"""\n{indent}# --- Phase 4: physics discovery (SINDy-lite) ---\n{indent}try:\n{indent}    # choose target: prefer existing deep['target_col'] if present\n{indent}    _tcol = None\n{indent}    if isinstance(deep.get('target_col', None), str):\n{indent}        _tcol = deep.get('target_col')\n{indent}    if not _tcol or _tcol not in df.columns:\n{indent}        # fallback: first numeric col\n{indent}        for _c in df.columns:\n{indent}            if str(_c).startswith('__aurora_'):\n{indent}                continue\n{indent}            try:\n{indent}                import pandas as pd\n{indent}                if pd.api.types.is_numeric_dtype(df[_c]):\n{indent}                    _tcol = _c\n{indent}                    break\n{indent}            except Exception:\n{indent}                pass\n{indent}    _dt = 1.0\n{indent}    if isinstance(deep.get('timebase', None), dict):\n{indent}        _dt = float(deep['timebase'].get('cadence_seconds') or 1.0)\n{indent}    deep['physics_discovery'] = sindy_lite(df, target_col=str(_tcol), dt_seconds=float(_dt))\n{indent}except Exception as e:\n{indent}    deep['physics_discovery'] = {{'note': 'error', 'error': str(e)}}\n"""
        txt = txt[:pos] + inject + txt[pos:]
        print("[OK] injected deep['physics_discovery']")

    DEEP.write_text(txt, encoding="utf-8")
    print(f"[DONE] wrote -> {DEEP}")

    import py_compile
    py_compile.compile(str(DEEP), doraise=True)
    print("[DONE] deep_math.py compiled clean")

if __name__ == "__main__":
    main()
