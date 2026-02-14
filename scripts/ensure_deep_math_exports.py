from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import py_compile

ROOT = Path(__file__).resolve().parents[1]
DEEP = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

def has_def(txt: str, name: str) -> bool:
    return re.search(rf"(?m)^def\s+{re.escape(name)}\s*\(", txt) is not None

def main() -> int:
    if not DEEP.exists():
        print(f"[ERR] Missing: {DEEP}")
        return 2

    txt = DEEP.read_text(encoding="utf-8")

    # We won't rewrite your file; we just append a small compatibility footer if needed.
    need_v2 = not has_def(txt, "compute_deep_math_v2")
    need_v3 = not has_def(txt, "compute_deep_math_v3")

    if not (need_v2 or need_v3):
        py_compile.compile(str(DEEP), doraise=True)
        print("[OK] deep_math already exports compute_deep_math_v2 and compute_deep_math_v3")
        return 0

    bak = backup(DEEP)
    print(f"[OK] backup -> {bak}")

    # Determine what the canonical function is called in *your* file.
    # We try common names in order.
    candidates = ["compute_deep_math", "compute_deep_math_core", "run_deep_math", "deep_math"]
    canonical = None
    for c in candidates:
        if has_def(txt, c):
            canonical = c
            break

    if canonical is None and not has_def(txt, "compute_deep_math_v3") and not has_def(txt, "compute_deep_math_v2"):
        print("deep_math.py has no recognizable entrypoint to alias; refusing to patch blindly.")
        return 3

    footer_lines = []
    footer_lines.append("")
    footer_lines.append("# -----------------------------------------------------------------------------")
    footer_lines.append("# Compatibility exports (auto-added)")
    footer_lines.append("# -----------------------------------------------------------------------------")
    footer_lines.append("")

    # If v3 is missing, define it as an alias to canonical (if present) else to v2 (if present).
    if need_v3:
        if canonical:
            footer_lines += [
                "def compute_deep_math_v3(*args, **kwargs):",
                f"    return {canonical}(*args, **kwargs)",
                ""
            ]
        else:
            footer_lines += [
                "def compute_deep_math_v3(*args, **kwargs):",
                "    return compute_deep_math_v2(*args, **kwargs)",
                ""
            ]

    # If v2 is missing, define it as alias to v3 (preferred) else canonical.
    if need_v2:
        if has_def(txt, "compute_deep_math_v3") or (not need_v3):
            footer_lines += [
                "def compute_deep_math_v2(*args, **kwargs):",
                "    return compute_deep_math_v3(*args, **kwargs)",
                ""
            ]
        elif canonical:
            footer_lines += [
                "def compute_deep_math_v2(*args, **kwargs):",
                f"    return {canonical}(*args, **kwargs)",
                ""
            ]
        else:
            footer_lines += [
                "def compute_deep_math_v2(*args, **kwargs):",
                "    return compute_deep_math_v3(*args, **kwargs)",
                ""
            ]

    # Optional alias for convenience if runner imports compute_deep_math (some older patches did)
    if not has_def(txt, "compute_deep_math"):
        footer_lines += [
            "def compute_deep_math(*args, **kwargs):",
            "    return compute_deep_math_v3(*args, **kwargs)",
            ""
        ]

    new_txt = txt.rstrip() + "\n" + "\n".join(footer_lines)
    DEEP.write_text(new_txt, encoding="utf-8")
    py_compile.compile(str(DEEP), doraise=True)
    print("[DONE] deep_math exports stabilized: v2/v3 (+ optional compute_deep_math alias)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
