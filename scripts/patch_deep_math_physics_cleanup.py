from __future__ import annotations

import re
from pathlib import Path
import py_compile
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
DEEP = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def main():
    if not DEEP.exists():
        raise FileNotFoundError(f"deep_math.py not found: {DEEP}")

    src = DEEP.read_text(encoding="utf-8")

    # Backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = DEEP.with_suffix(f".py.bak_phys_cleanup_{ts}")
    bak.write_text(src, encoding="utf-8")

    # ---- 1) Fix causal import preference (robust_v2 then fallback) ----
    # Replace any duplicated causal_what_if_linear lines with robust preference block.
    src = re.sub(
        r"(?m)^causal_what_if_linear\s*=\s*_try_import\(\"fantasyai\.aurora\.math\.causal\",\s*\"causal_what_if_linear\"\)\s*\n"
        r"(?:^causal_what_if_linear\s*=\s*_try_import\(\"fantasyai\.aurora\.math\.causal\",\s*\"causal_what_if_linear\"\)\s*\n)+",
        'causal_what_if_linear = _try_import("fantasyai.aurora.math.causal", "causal_what_if_linear_robust_v2")\n'
        'if causal_what_if_linear is None:\n'
        '    causal_what_if_linear = _try_import("fantasyai.aurora.math.causal", "causal_what_if_linear")\n',
        src
    )

    # Also handle the case where only one duplicate exists (still replace single-line with robust preference)
    src = re.sub(
        r'(?m)^# Prefer robust wrapper if present \(your patched causal\.py adds robust_v2\)\s*\n'
        r'^causal_what_if_linear\s*=\s*_try_import\("fantasyai\.aurora\.math\.causal",\s*"causal_what_if_linear"\)\s*\n',
        '# Prefer robust wrapper if present (your patched causal.py adds robust_v2)\n'
        'causal_what_if_linear = _try_import("fantasyai.aurora.math.causal", "causal_what_if_linear_robust_v2")\n'
        'if causal_what_if_linear is None:\n'
        '    causal_what_if_linear = _try_import("fantasyai.aurora.math.causal", "causal_what_if_linear")\n',
        src
    )

    # ---- 2) Remove the duplicate Phase 3–4 block that overwrites physics outputs ----
    # This is the block that starts with:
    #     # -----------------------------
    #     # Phase 3: Physics discovery...
    # and ends right before:
    #     # ============================================================
    block_pat = re.compile(
        r"\n\s*#\s*-{5,}\s*\n\s*#\s*Phase 3: Physics discovery.*?\n\s*#\s*=+\s*\n",
        re.DOTALL
    )

    m = block_pat.search(src)
    if not m:
        raise RuntimeError("Could not find the duplicate Phase 3–4 block to remove (pattern not found).")

    src = src[:m.start()] + "\n\n" + src[m.end():]

    # ---- 3) Ensure the final Phases 2–4 block exists (sanity check) ----
    if "Physics Layer (Phases 2–4)" not in src:
        raise RuntimeError("Expected 'Physics Layer (Phases 2–4)' section not found. Refusing to patch further.")

    # Write + compile check
    DEEP.write_text(src, encoding="utf-8")
    py_compile.compile(str(DEEP), doraise=True)

    print(f"[OK] backup -> {bak}")
    print(f"[DONE] patched + compiled clean: {DEEP}")

if __name__ == "__main__":
    main()
