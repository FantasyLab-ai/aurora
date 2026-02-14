from __future__ import annotations
from pathlib import Path
from datetime import datetime

PHYSICS = Path(r"C:\Users\bgrut\Desktop\Aurora_QIE\fantasyai\aurora\math\physics.py")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

def main():
    if not PHYSICS.exists():
        raise FileNotFoundError(PHYSICS)

    txt = PHYSICS.read_text(encoding="utf-8", errors="replace")
    bak = PHYSICS.with_suffix(PHYSICS.suffix + f".bak_minpts_{STAMP}")
    bak.write_text(txt, encoding="utf-8")

    # Only change the minimum-points guard inside physics_diagnostics
    # from: if n < 5:
    # to:   if n < 3:
    new = txt.replace(
        "if n < 5:\n            return {\"note\": \"failed\", \"error\": \"too_few_points\"",
        "if n < 3:\n            return {\"note\": \"failed\", \"error\": \"too_few_points\"",
    )

    if new == txt:
        # fallback: more flexible replace if spacing differs
        new = new.replace("if n < 5:", "if n < 3:")

    PHYSICS.write_text(new, encoding="utf-8")

    import py_compile
    py_compile.compile(str(PHYSICS), doraise=True)

    print("[OK] patched min points gate (backup:", bak, ")")

if __name__ == "__main__":
    main()
