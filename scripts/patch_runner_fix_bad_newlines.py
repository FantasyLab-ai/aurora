import re
import sys
from pathlib import Path
from datetime import datetime

def main():
    # --- args ---
    if "--file" not in sys.argv:
        print("usage: python patch_runner_fix_bad_newlines.py --file <path>")
        sys.exit(2)
    fpath = Path(sys.argv[sys.argv.index("--file")+1]).resolve()
    if not fpath.exists():
        print(f"ERROR: file not found: {fpath}")
        sys.exit(2)

    txt = fpath.read_text(encoding="utf-8")

    # --- backup ---
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = fpath.with_suffix(fpath.suffix + f".bak_{stamp}")
    bak.write_text(txt, encoding="utf-8")
    print(f"[OK] backup -> {bak}")

    # --- fix: literal '\n                ' embedded inside a python statement ---
    # Replace any:  math_json = ...[:9000]\n                deep_json = ...
    # with proper separate lines.
    pat = re.compile(
        r"(math_json\s*=\s*json\.dumps\(\s*math_results\s*,\s*indent\s*=\s*2\s*\)\s*\[:\s*9000\s*\])\\n(\s*deep_json\s*=\s*json\.dumps\(\s*deep_out\s*,\s*indent\s*=\s*2\s*\)\s*\[:\s*9000\s*\])",
        flags=re.MULTILINE
    )

    new_txt, n = pat.subn(r"\1\n                \2", txt)

    if n == 0:
        # Also handle if spacing differs / deep_out may be None or different var
        pat2 = re.compile(r"(math_json\s*=.*?\[:\s*9000\s*\])\\n(\s*deep_json\s*=.*?\[:\s*9000\s*\])", flags=re.DOTALL)
        new_txt, n2 = pat2.subn(r"\1\n                \2", txt)
        n = n2

    if n == 0:
        print("ERROR: did not find the embedded \\\\n sequence to fix. No changes made.")
        sys.exit(1)

    fpath.write_text(new_txt, encoding="utf-8")
    print(f"[OK] fixed embedded newline sequence (replacements={n})")
    sys.exit(0)

if __name__ == "__main__":
    main()
