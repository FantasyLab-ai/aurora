from __future__ import annotations

import re
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEEP = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(path: Path, tag: str) -> Path:
    b = path.with_suffix(path.suffix + f".bak_{tag}")
    b.write_bytes(path.read_bytes())
    return b

def main():
    text = DEEP.read_text(encoding="utf-8")
    b = backup(DEEP, "phases2_4")

    # 1) Add optional imports near existing physics_diagnostics import
    if "physics_diagnostics_v2" not in text:
        text = text.replace(
            'physics_diagnostics = _try_import("fantasyai.aurora.math.physics", "physics_diagnostics")',
            'physics_diagnostics = _try_import("fantasyai.aurora.math.physics", "physics_diagnostics")\n'
            'physics_diagnostics_v2 = _try_import("fantasyai.aurora.math.physics_v2", "physics_diagnostics_v2")\n'
            'physics_discovery = _try_import("fantasyai.aurora.math.physics_discovery", "physics_discovery")'
        )

    # 2) Ensure output keys exist (add if missing)
    if '"physics_v2"' not in text:
        text = text.replace(
            'out["physics"] = {"note": "skipped", "error": None}',
            'out["physics"] = {"note": "skipped", "error": None}\n'
            '    out["physics_v2"] = {"note": "skipped", "error": None}\n'
            '    out["physics_discovery"] = {"note": "skipped", "error": None}'
        )

    # 3) After physics_diagnostics block, add v2 + discovery blocks (safe)
    if "out[\"physics_discovery\"]" not in text:
        anchor = re.search(r"# 8\) Physics-inspired diagnostics.*?\n\s*try:\n\s*if callable\(physics_diagnostics\):.*?\n\s*out\[\"physics\"\]\s*=.*?\n\s*except Exception as e:\n\s*out\[\"physics\"\]\s*=.*?\n", text, re.S)
        if not anchor:
            raise RuntimeError("Could not find physics diagnostics block to extend")

        insert = anchor.group(0) + r'''
    # 8b) Physics diagnostics v2 (normalized + differenced)
    try:
        if callable(physics_diagnostics_v2):
            out["physics_v2"] = physics_diagnostics_v2(df=df, target_col=ycol, time_col=tcol, seed=seed)
            if isinstance(out["physics_v2"], dict):
                out["physics_v2"].setdefault("note", "ok")
        else:
            out["physics_v2"] = {"note": "skipped", "error": "physics_diagnostics_v2_unavailable"}
    except Exception as e:
        out["physics_v2"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    # 8c) Physics discovery (multi-hypothesis ranking)
    try:
        if callable(physics_discovery):
            out["physics_discovery"] = physics_discovery(df=df, target_col=ycol, time_col=tcol, seed=seed)
            if isinstance(out["physics_discovery"], dict):
                out["physics_discovery"].setdefault("note", "ok")
        else:
            out["physics_discovery"] = {"note": "skipped", "error": "physics_discovery_unavailable"}
    except Exception as e:
        out["physics_discovery"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}
'''
        text = text.replace(anchor.group(0), insert)

    DEEP.write_text(text, encoding="utf-8")
    py_compile.compile(str(DEEP), doraise=True)
    print(f"[OK] patched deep_math.py for phases 2–4 (backup: {b.name})")

if __name__ == "__main__":
    main()
