from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TARGETS = [
    ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py",
    ROOT / "engine_artifacts.py",
    ROOT / "scripts" / "run_aurora_with_steps_1_4.py",
]

def clean_file(p: Path) -> None:
    if not p.exists():
        return
    s = p.read_text(encoding="utf-8", errors="replace")
    if "\ufeff" not in s:
        print(f"[OK] no BOM found: {p}")
        return
    # Remove BOM anywhere, not just first char (prevents mid-file SyntaxError)
    s2 = s.replace("\ufeff", "")
    bak = p.with_suffix(p.suffix + ".bak_bomfix")
    bak.write_text(s, encoding="utf-8")
    p.write_text(s2, encoding="utf-8")
    print(f"[FIXED] removed BOM(s): {p} (backup: {bak.name})")

def main() -> None:
    for p in TARGETS:
        clean_file(p)

if __name__ == "__main__":
    main()
