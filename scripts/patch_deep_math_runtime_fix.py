from __future__ import annotations

import re
import sys
from pathlib import Path
from datetime import datetime


def backup(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak_{ts}")
    bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[OK] backup -> {bak}")
    return bak


def write(path: Path, txt: str) -> None:
    path.write_text(txt, encoding="utf-8")
    print(f"[OK] wrote -> {path}")


def ensure_deep_math_exports(deep_math_path: Path) -> None:
    txt = deep_math_path.read_text(encoding="utf-8")

    has_v1 = re.search(r"^def\s+compute_deep_math_v1\s*\(", txt, flags=re.M) is not None
    has_v2 = re.search(r"^def\s+compute_deep_math_v2\s*\(", txt, flags=re.M) is not None

    if not has_v1 and not has_v2:
        raise RuntimeError("deep_math.py has neither compute_deep_math_v1 nor compute_deep_math_v2")

    # If only v1 exists, alias v2 -> v1
    if has_v1 and not has_v2:
        txt += "\n\n# --- Back-compat alias (auto-added) ---\n"
        txt += "def compute_deep_math_v2(df, seed: int = 0):\n"
        txt += "    return compute_deep_math_v1(df, seed=seed)\n"
        print("[OK] added alias: compute_deep_math_v2 -> compute_deep_math_v1")

    # If only v2 exists, alias v1 -> v2
    if has_v2 and not has_v1:
        txt += "\n\n# --- Back-compat alias (auto-added) ---\n"
        txt += "def compute_deep_math_v1(df, seed: int = 0):\n"
        txt += "    return compute_deep_math_v2(df, seed=seed)\n"
        print("[OK] added alias: compute_deep_math_v1 -> compute_deep_math_v2")

    write(deep_math_path, txt)


def fix_runner(runner_path: Path) -> None:
    txt = runner_path.read_text(encoding="utf-8")

    # 1) Force import to v2 (keep idempotent)
    # Remove any v1 import lines
    txt = re.sub(
        r"^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+compute_deep_math_v1\s*$\n?",
        "",
        txt,
        flags=re.M,
    )

    # Ensure v2 import exists
    if re.search(r"from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+compute_deep_math_v2", txt) is None:
        # Insert after mathstack_v2 import if possible, else near top
        m = re.search(r"^from\s+fantasyai\.aurora\.mathstack_v2\s+import\s+compute_math_stack_v2\s*$", txt, flags=re.M)
        if m:
            insert_at = m.end()
            txt = txt[:insert_at] + "\nfrom fantasyai.aurora.math.deep_math import compute_deep_math_v2\n" + txt[insert_at:]
        else:
            # fallback: after other imports block
            txt = "from fantasyai.aurora.math.deep_math import compute_deep_math_v2\n" + txt
        print("[OK] ensured import: compute_deep_math_v2")

    # 2) Replace any calls to v1 with v2
    txt = txt.replace("compute_deep_math_v1(", "compute_deep_math_v2(")

    # 3) Remove duplicate argparse definitions for --deep-math (keep first occurrence)
    # Works even if parser variable is ap or parser.
    pattern = r'^\s*(ap|parser)\.add_argument\(\s*[\'"]--deep-math[\'"][^\n]*\)\s*$'
    matches = list(re.finditer(pattern, txt, flags=re.M))
    if len(matches) > 1:
        # delete all but first
        first = matches[0].span()
        keep_start, keep_end = first
        rebuilt = []
        last = 0
        kept_one = False
        for m in re.finditer(pattern, txt, flags=re.M):
            if not kept_one:
                kept_one = True
                continue
            # remove this line
            rebuilt.append(txt[last:m.start()])
            last = m.end()
        rebuilt.append(txt[last:])
        txt = "".join(rebuilt)
        print(f"[OK] removed duplicate --deep-math argparse lines: {len(matches) - 1}")

    write(runner_path, txt)

    # small sanity printouts (no regex footguns)
    deep_arg_count = txt.count("--deep-math")
    print(f"[OK] runner contains '--deep-math' tokens: {deep_arg_count}")
    print(f"[OK] runner calls v2: {'compute_deep_math_v2(' in txt}")


def compile_check(repo_root: Path) -> None:
    import py_compile

    runner = repo_root / "scripts" / "run_aurora_dataset_runner.py"
    deep_math = repo_root / "fantasyai" / "aurora" / "math" / "deep_math.py"

    py_compile.compile(str(runner), doraise=True)
    py_compile.compile(str(deep_math), doraise=True)
    print("[OK] py_compile passed (runner + deep_math)")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    deep_math_path = repo_root / "fantasyai" / "aurora" / "math" / "deep_math.py"
    runner_path = repo_root / "scripts" / "run_aurora_dataset_runner.py"

    if not deep_math_path.exists():
        raise FileNotFoundError(f"Missing: {deep_math_path}")
    if not runner_path.exists():
        raise FileNotFoundError(f"Missing: {runner_path}")

    backup(deep_math_path)
    backup(runner_path)

    ensure_deep_math_exports(deep_math_path)
    fix_runner(runner_path)

    compile_check(repo_root)
    print("[DONE] deep math runtime fix applied cleanly")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        sys.exit(1)
