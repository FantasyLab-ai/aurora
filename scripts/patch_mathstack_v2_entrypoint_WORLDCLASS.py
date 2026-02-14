from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "fantasyai" / "aurora" / "mathstack_v2.py"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

def main() -> int:
    if not TARGET.exists():
        print(f"[FAIL] Missing: {TARGET}")
        return 2

    txt = TARGET.read_text(encoding="utf-8")

    # If already present, do nothing
    if re.search(r"(?m)^\s*def\s+run_mathstack_v2\s*\(", txt):
        print("[OK] run_mathstack_v2 already exists; no changes needed.")
        return 0

    # We need an underlying callable to delegate to.
    # Your runner earlier warnings referenced compute_math_stack_v2 in this file,
    # so we look for that.
    if not re.search(r"(?m)^\s*def\s+compute_math_stack_v2\s*\(", txt):
        print("[FAIL] Could not find compute_math_stack_v2() in mathstack_v2.py. Refusing to patch blindly.")
        print("       Open fantasyai/aurora/mathstack_v2.py and confirm the main function name.")
        return 3

    inject = r'''

# ==========================
# Public Entrypoint (Runner)
# ==========================
def run_mathstack_v2(df, seed: int = 42, out_dir=None):
    """
    Stable public entrypoint expected by scripts/run_aurora_dataset_runner.py.

    Contract:
      - returns a JSON-serializable dict
      - does NOT require pandas typing in signature (keeps it flexible)
      - out_dir may be Path/str/None
    """
    from pathlib import Path
    import traceback

    try:
        # Normalize out_dir
        if out_dir is None:
            out_path = None
        else:
            out_path = Path(out_dir)

        # Delegate to the real implementation
        res = compute_math_stack_v2(df=df, seed=seed, out_dir=out_path)  # type: ignore
        return res if isinstance(res, dict) else {"result": res}
    except Exception as e:
        return {
            "error": f"mathstack_v2_failed:{type(e).__name__}:{e}",
            "trace": traceback.format_exc()[:8000],
        }
'''.lstrip("\n")

    # Append near bottom (safe + simple)
    txt2 = txt.rstrip() + "\n\n" + inject + "\n"
    bak = backup(TARGET)
    TARGET.write_text(txt2, encoding="utf-8")

    print(f"[OK] backup -> {bak}")
    print(f"[OK] added run_mathstack_v2 -> {TARGET}")

    # Compile check
    import py_compile
    py_compile.compile(str(TARGET), doraise=True)
    print("[DONE] mathstack_v2 entrypoint restored + py_compile clean")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
