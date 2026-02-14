from __future__ import annotations

from pathlib import Path
import importlib
import inspect
import re
import sys
import py_compile
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

MODULES = [
    "fantasyai.aurora.math.physics",
    "fantasyai.aurora.math.physics_v2",
    "fantasyai.aurora.math.physics_discovery",
]

def die(msg: str):
    raise SystemExit(f"ERROR: {msg}")

def _safe_import(modname: str):
    try:
        return importlib.import_module(modname)
    except Exception as e:
        return e

def _pick_callable(mod, preferred_names: list[str]) -> str | None:
    # First try preferred names
    for n in preferred_names:
        if hasattr(mod, n) and callable(getattr(mod, n)):
            return n
    # Then try any function that looks like physics / discovery
    candidates = []
    for name, obj in vars(mod).items():
        if callable(obj) and inspect.isfunction(obj):
            low = name.lower()
            if any(k in low for k in ["physics", "discovery", "invariant", "diagnostic", "ode", "model"]):
                candidates.append(name)
    # Prefer shortest "physics_*" names deterministically
    candidates = sorted(candidates, key=lambda s: (0 if s.lower().startswith("physics") else 1, len(s), s))
    return candidates[0] if candidates else None

def main():
    if not TARGET.exists():
        die(f"deep_math.py not found: {TARGET}")

    # Ensure project root is importable
    sys.path.insert(0, str(ROOT))

    imported = {m: _safe_import(m) for m in MODULES}

    # Report what imported
    for m, obj in imported.items():
        if isinstance(obj, Exception):
            print(f"[WARN] import failed: {m} -> {type(obj).__name__}: {obj}")

    # Determine best function names
    physics_fn = None
    physics_v2_fn = None
    discovery_fn = None

    # physics.py
    mod = imported.get("fantasyai.aurora.math.physics")
    if mod and not isinstance(mod, Exception):
        physics_fn = _pick_callable(mod, ["physics_diagnostics", "run_physics", "physics", "diagnostics"])

    # physics_v2.py
    mod2 = imported.get("fantasyai.aurora.math.physics_v2")
    if mod2 and not isinstance(mod2, Exception):
        physics_v2_fn = _pick_callable(mod2, ["physics_diagnostics_v2", "physics_diagnostics", "run_physics_v2", "physics_v2", "physics"])

    # physics_discovery.py
    mod3 = imported.get("fantasyai.aurora.math.physics_discovery")
    if mod3 and not isinstance(mod3, Exception):
        discovery_fn = _pick_callable(mod3, ["physics_discovery", "discover_physics", "discovery"])

    # If discovery isn't in physics_discovery, try physics_v2 as fallback
    if discovery_fn is None and mod2 and not isinstance(mod2, Exception):
        discovery_fn = _pick_callable(mod2, ["physics_discovery", "discover_physics", "discovery"])

    print("[INFO] resolved callables:")
    print(f"  physics.py        -> {physics_fn}")
    print(f"  physics_v2.py     -> {physics_v2_fn}")
    print(f"  physics_discovery -> {discovery_fn}")

    # Patch deep_math.py imports + usage with deterministic names
    txt = TARGET.read_text(encoding="utf-8", errors="replace")

    # Make a backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = TARGET.with_suffix(f".py.bak_autowire_{ts}")
    bak.write_text(txt, encoding="utf-8")
    print(f"[OK] backup -> {bak}")

    def set_try_import(varname: str, modname: str, fnname: str | None):
        nonlocal txt
        # If we didn't resolve a function name, keep the try_import but pointing to a safe non-existing name (so it stays None)
        fnname = fnname or "__missing__"
        line = f'{varname} = _try_import("{modname}", "{fnname}")'
        # Replace any existing line that assigns this varname
        pattern = rf'^{re.escape(varname)}\s*=\s*_try_import\(.+?\)\s*$'
        if re.search(pattern, txt, flags=re.M):
            txt = re.sub(pattern, line, txt, flags=re.M)
            return
        # Otherwise append it near other try_imports
        m = re.search(r'^def _try_import\(.+?\):.*?^\s*return None\s*$', txt, flags=re.S | re.M)
        if m:
            # insert after helper block
            insert_at = m.end()
            txt = txt[:insert_at] + "\n\n" + line + "\n" + txt[insert_at:]
            return
        # fallback: prepend at top
        txt = line + "\n" + txt

    # Ensure these vars exist and point to the real function names
    set_try_import("physics_diagnostics", "fantasyai.aurora.math.physics", physics_fn)
    set_try_import("physics_diagnostics_v2", "fantasyai.aurora.math.physics_v2", physics_v2_fn)
    set_try_import("physics_discovery", "fantasyai.aurora.math.physics_discovery", discovery_fn)

    # Patch call sites to just call the resolved functions (no fallbacks needed now)
    # Keep your current signatures. Most of your functions accept (df, target_col, time_col, seed).
    # If they accept different args, we’ll adapt after we see the error message (but this gets you past "unavailable").

    TARGET.write_text(txt, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)
    print(f"[DONE] deep_math.py patched + compiled clean: {TARGET}")

if __name__ == "__main__":
    main()
