from __future__ import annotations

import re
import shutil
from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parents[1]
DEEP = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(p: Path, tag: str) -> Path:
    b = p.with_suffix(p.suffix + f".bak_{tag}")
    shutil.copy2(p, b)
    return b

def ensure_import(src: str, import_line: str) -> str:
    if import_line in src:
        return src
    # insert after last import/from at top
    lines = src.splitlines()
    insert_at = 0
    for i, line in enumerate(lines[:120]):
        if line.startswith("import ") or line.startswith("from "):
            insert_at = i + 1
    lines.insert(insert_at, import_line)
    return "\n".join(lines) + ("\n" if not src.endswith("\n") else "")

def main():
    if not DEEP.exists():
        raise SystemExit(f"ERROR: not found: {DEEP}")

    src = DEEP.read_text(encoding="utf-8", errors="replace")
    backup(DEEP, "phase34")

    # Imports
    src = ensure_import(src, "from .physics import physics_diagnostics, physics_constraints")
    src = ensure_import(src, "from .physics_discovery import discover_physics_models")

    # Inject block before last `return deep`
    m = list(re.finditer(r"^\s*return\s+deep\s*$", src, flags=re.M))
    if not m:
        raise SystemExit("ERROR: could not find `return deep` in deep_math.py")

    last = m[-1]
    insert_pos = last.start()

    injection = r'''
    # ------------------------------
    # Phase 2–4 Physics Layer
    # ------------------------------
    try:
        # Phase 2 baseline physics diagnostics (linear ODE fit)
        deep.setdefault("physics", {})
        deep["physics"] = physics_diagnostics(df, target_col=deep.get("target_col"))
    except Exception as e:
        deep["physics"] = {"note": "error", "error": f"{type(e).__name__}: {e}"}

    try:
        # Phase 3 discovery (multi-model selection) + Phase 4 constraints/invariants
        deep.setdefault("physics_discovery", {})
        deep["physics_discovery"] = discover_physics_models(df, target_col=deep.get("target_col"))
        # convenience mirror
        deep["physics_consistency_score"] = float(deep["physics_discovery"].get("physics_consistency_score", 0.0))
        deep.setdefault("physics_constraints", {})
        deep["physics_constraints"] = deep["physics_discovery"].get("constraints", {})
    except Exception as e:
        deep["physics_discovery"] = {"note": "error", "error": f"{type(e).__name__}: {e}"}
        deep["physics_constraints"] = {"note": "error", "error": f"{type(e).__name__}: {e}"}
        deep["physics_consistency_score"] = 0.0
'''

    # Prevent double insertion
    if "Phase 2–4 Physics Layer" in src:
        print("[SKIP] deep_math.py already has Phase 2–4 physics block.")
    else:
        src = src[:insert_pos] + injection + "\n" + src[insert_pos:]

    DEEP.write_text(src, encoding="utf-8", newline="\n")
    py_compile.compile(str(DEEP), doraise=True)

    print(f"[DONE] patched -> {DEEP}")

if __name__ == "__main__":
    main()
