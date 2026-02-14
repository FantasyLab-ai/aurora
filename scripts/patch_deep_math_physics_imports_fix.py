from __future__ import annotations
from pathlib import Path
import re
import py_compile

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def die(msg: str):
    raise SystemExit(f"ERROR: {msg}")

def main():
    if not TARGET.exists():
        die(f"deep_math.py not found: {TARGET}")

    txt = TARGET.read_text(encoding="utf-8", errors="replace")

    # Helper: replace one optional import line (string match, safe)
    def repl_line(old: str, new: str):
        nonlocal txt
        if old in txt:
            txt = txt.replace(old, new)
            return True
        return False

    changed = False

    # 1) Physics baseline diagnostics:
    # Prefer physics.py exporting physics_diagnostics; if not present, try physics_v2 as fallback.
    # We patch the optional import to first try physics.py, but we ALSO add a second optional import for fallback.

    # Replace current physics_diagnostics import target (if present)
    changed |= repl_line(
        'physics_diagnostics = _try_import("fantasyai.aurora.math.physics", "physics_diagnostics")',
        'physics_diagnostics = _try_import("fantasyai.aurora.math.physics", "physics_diagnostics")\n'
        'physics_diagnostics_fallback = _try_import("fantasyai.aurora.math.physics_v2", "physics_diagnostics")'
    )

    # If the above line didn't exist (because earlier patches moved it), inject fallback right after physics_diagnostics line.
    if "physics_diagnostics_fallback" not in txt:
        m = re.search(r'physics_diagnostics\s*=\s*_try_import\("fantasyai\.aurora\.math\.physics",\s*"physics_diagnostics"\)', txt)
        if m:
            insert_at = m.end()
            txt = txt[:insert_at] + '\nphysics_diagnostics_fallback = _try_import("fantasyai.aurora.math.physics_v2", "physics_diagnostics")' + txt[insert_at:]
            changed = True

    # 2) Physics v2 diagnostics: should come from physics_v2.py
    # Replace old import if it exists
    changed |= repl_line(
        'physics_diagnostics_v2 = _try_import("fantasyai.aurora.math.physics", "physics_diagnostics_v2")',
        'physics_diagnostics_v2 = _try_import("fantasyai.aurora.math.physics_v2", "physics_diagnostics_v2")'
    )

    # If not found, try to add it after other physics imports
    if 'physics_diagnostics_v2 = _try_import("fantasyai.aurora.math.physics_v2", "physics_diagnostics_v2")' not in txt:
        # insert after any line containing physics_diagnostics_fallback or physics_diagnostics
        m2 = re.search(r'physics_diagnostics_fallback.*\n', txt)
        if not m2:
            m2 = re.search(r'physics_diagnostics\s*=.*\n', txt)
        if m2:
            insert_at = m2.end()
            txt = txt[:insert_at] + 'physics_diagnostics_v2 = _try_import("fantasyai.aurora.math.physics_v2", "physics_diagnostics_v2")\n' + txt[insert_at:]
            changed = True

    # 3) Discovery: allow either physics_discovery.py:physics_discovery OR physics_v2.py:physics_discovery
    # Replace old one-liner to include fallback
    if 'physics_discovery = _try_import("fantasyai.aurora.math.physics_discovery", "physics_discovery")' in txt and "physics_discovery_fallback" not in txt:
        txt = txt.replace(
            'physics_discovery = _try_import("fantasyai.aurora.math.physics_discovery", "physics_discovery")',
            'physics_discovery = _try_import("fantasyai.aurora.math.physics_discovery", "physics_discovery")\n'
            'physics_discovery_fallback = _try_import("fantasyai.aurora.math.physics_v2", "physics_discovery")'
        )
        changed = True

    # If discovery line doesn't exist, inject both
    if "physics_discovery" not in txt:
        # insert after physics_diagnostics_v2 if possible
        m3 = re.search(r'physics_diagnostics_v2.*\n', txt)
        if m3:
            insert_at = m3.end()
            txt = txt[:insert_at] + (
                'physics_discovery = _try_import("fantasyai.aurora.math.physics_discovery", "physics_discovery")\n'
                'physics_discovery_fallback = _try_import("fantasyai.aurora.math.physics_v2", "physics_discovery")\n'
            ) + txt[insert_at:]
            changed = True

    # 4) Now patch the execution block to use fallbacks when primary imports are missing.
    # We do small safe replacements in your unified physics layer.
    # Replace: if callable(physics_diagnostics):  -> if callable(physics_diagnostics) or callable(physics_diagnostics_fallback):
    txt_new = txt

    txt_new = txt_new.replace(
        "if callable(physics_diagnostics):",
        "if callable(physics_diagnostics) or callable(globals().get('physics_diagnostics_fallback')):"
    )
    txt_new = txt_new.replace(
        'out["physics"] = physics_diagnostics(df=df, target_col=ycol, time_col=tcol, seed=seed)',
        'fn = physics_diagnostics if callable(physics_diagnostics) else globals().get("physics_diagnostics_fallback")\n'
        '            out["physics"] = fn(df=df, target_col=ycol, time_col=tcol, seed=seed)'
    )

    txt_new = txt_new.replace(
        "if callable(physics_discovery):",
        "if callable(physics_discovery) or callable(globals().get('physics_discovery_fallback')):"
    )
    txt_new = txt_new.replace(
        'out["physics_discovery"] = physics_discovery(df=df, target_col=ycol, time_col=tcol, seed=seed)',
        'fn = physics_discovery if callable(physics_discovery) else globals().get("physics_discovery_fallback")\n'
        '            out["physics_discovery"] = fn(df=df, target_col=ycol, time_col=tcol, seed=seed)'
    )

    if txt_new != txt:
        txt = txt_new
        changed = True

    if not changed:
        print("[INFO] No changes detected (already patched).")
    else:
        TARGET.write_text(txt, encoding="utf-8")
        py_compile.compile(str(TARGET), doraise=True)
        print(f"[DONE] patched + compiled clean: {TARGET}")

if __name__ == "__main__":
    main()
