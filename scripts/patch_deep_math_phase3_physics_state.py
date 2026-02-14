from __future__ import annotations
from pathlib import Path
import re, datetime as dt, shutil

ROOT = Path.cwd()
DEEP = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(p: Path) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_phase3_{ts}")
    shutil.copy2(p, b)
    return b

def main():
    if not DEEP.exists():
        raise SystemExit(f"ERROR not found: {DEEP}")
    txt = DEEP.read_text(encoding="utf-8", errors="ignore")
    b = backup(DEEP)
    print(f"[OK] backup -> {b}")

    if "fit_var1_state" not in txt:
        txt = re.sub(r"(from __future__ import annotations\s*\n)", r"\1from .physics_state import fit_var1_state\n", txt, count=1)
        print("[OK] added physics_state import")

    if '"physics_state"' not in txt:
        # Insert near where deep['physics'] exists or near end before return deep
        anchor = re.search(r"\n(\s*)deep\[['\"]physics['\"]\]\s*=\s*.*\n", txt)
        if anchor:
            indent = anchor.group(1)
            pos = anchor.end()
        else:
            # fallback: before final "return deep"
            m = re.search(r"\n(\s*)return\s+deep\s*\n", txt)
            if not m:
                raise SystemExit("ERROR: could not find insertion anchor.")
            indent = m.group(1)
            pos = m.start()

        inject = f"""\n{indent}# --- Phase 3: multivariate physics state model (VAR1/LDS) ---\n{indent}try:\n{indent}    deep['physics_state'] = fit_var1_state(df)\n{indent}except Exception as e:\n{indent}    deep['physics_state'] = {{'note': 'error', 'error': str(e)}}\n"""
        txt = txt[:pos] + inject + txt[pos:]
        print("[OK] injected deep['physics_state']")

    DEEP.write_text(txt, encoding="utf-8")
    print(f"[DONE] wrote -> {DEEP}")

    import py_compile
    py_compile.compile(str(DEEP), doraise=True)
    print("[DONE] deep_math.py compiled clean")

if __name__ == "__main__":
    main()
