from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import py_compile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RUNNER = SCRIPTS / "run_aurora_dataset_runner.py"


REQUIRED_TOKENS = [
    "math_results.json",
    "tableselection.json",
    "deep_math.json",
    "llm_narrative.json",
    "llm_debug.json",
    "storycard.json",
]

def backup_file(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

def score_runner_text(txt: str) -> int:
    score = 0
    for tok in REQUIRED_TOKENS:
        if tok in txt:
            score += 1
    # strong signal that it prints summary JSON
    if re.search(r'print\(\s*\{', txt) or re.search(r'print\(json\.dumps\(', txt):
        score += 2
    # deep-math flag wired
    if "--deep-math" in txt:
        score += 2
    # also-llm flag wired
    if "--also-llm" in txt or "also_llm" in txt:
        score += 1
    return score

def find_best_backup() -> Path | None:
    # match many of your backup naming patterns
    cands = []
    patterns = [
        "run_aurora_dataset_runner.py.bak*",
        "run_aurora_dataset_runner.py.bak_*",
        "run_aurora_dataset_runner.py.bak1",
        "run_aurora_dataset_runner.py.bak2",
        "run_aurora_dataset_runner.py.bak3",
    ]
    for pat in patterns:
        cands.extend(SCRIPTS.glob(pat))

    # de-dupe
    uniq = []
    seen = set()
    for p in cands:
        if p.exists() and p.is_file():
            rp = str(p.resolve()).lower()
            if rp not in seen:
                seen.add(rp)
                uniq.append(p)

    best = None
    best_score = -1
    best_mtime = -1.0

    for p in uniq:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        sc = score_runner_text(txt)
        mt = p.stat().st_mtime

        # Must at least contain base + deep math token names to be considered “full outputs”
        if "math_results.json" not in txt or "tableselection.json" not in txt:
            continue
        if "deep_math.json" not in txt:
            continue
        if "llm_narrative.json" not in txt:
            continue
        if "storycard.json" not in txt:
            continue

        if sc > best_score or (sc == best_score and mt > best_mtime):
            best_score = sc
            best_mtime = mt
            best = p

    return best

def fix_empty_try_blocks(txt: str) -> str:
    """
    Fix the exact failure mode you've been hitting:
        try:
        except Exception:
    by inserting a 'pass' (or a minimal statement) into the try block.
    """
    # This targets: try: \n (only whitespace/newlines) \n except
    pattern = re.compile(r"(?m)^(?P<indent>[ \t]*)try:\s*\n(?P<ws>(?:[ \t]*\n)*)^(?P=indent)except\b", re.M)
    def repl(m: re.Match) -> str:
        indent = m.group("indent")
        return f"{indent}try:\n{indent}    pass\n{indent}except"
    return re.sub(pattern, repl, txt)

def canonicalize_deep_math_import(txt: str) -> str:
    """
    Ensure runner uses:
      try import v3 as compute_deep_math
      except import v2 as compute_deep_math
    without creating indentation problems.
    """
    block = (
        "# --- Deep Math (stable import; prefer v3) ---\n"
        "try:\n"
        "    from fantasyai.aurora.math.deep_math import compute_deep_math_v3 as compute_deep_math\n"
        "except Exception:\n"
        "    from fantasyai.aurora.math.deep_math import compute_deep_math_v2 as compute_deep_math\n"
    )

    # remove any prior deep_math import lines
    txt2 = re.sub(
        r"(?m)^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+.*\n+",
        "",
        txt,
    )

    # remove any old inserted deep-math import blocks (start marker)
    txt2 = re.sub(
        r"(?s)#\s*---\s*Deep Math.*?prefer v3\)\s*---\n.*?(?=\n\n|\n#|\ndef |\nclass |\Z)",
        "",
        txt2,
    )

    # insert after initial import header
    lines = txt2.splitlines()
    insert_at = 0
    saw_non_import = False
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            insert_at = i + 1
        else:
            if i > 0:
                saw_non_import = True
            if saw_non_import:
                break

    # Avoid double insert
    if "compute_deep_math_v3 as compute_deep_math" not in txt2:
        lines.insert(insert_at, block.rstrip("\n"))

    return "\n".join(lines) + "\n"

def ensure_summary_print(txt: str) -> str:
    """
    You said: “it’s not giving the json response anymore”.
    This adds a safe end-of-run print if the runner has none.
    """
    if re.search(r"print\(\s*\{", txt) or re.search(r"print\(json\.dumps\(", txt):
        return txt

    # Add a minimal print right before a final return/exit if we can find main()
    # fallback: append at end (safe).
    tail = (
        "\n\n# --- worldclass: final summary print (added) ---\n"
        "try:\n"
        "    import json as _json\n"
        "    if 'out' in globals() and isinstance(globals()['out'], dict):\n"
        "        print(_json.dumps(globals()['out'], indent=2))\n"
        "except Exception:\n"
        "    pass\n"
    )

    return txt + tail

def main() -> int:
    if not RUNNER.exists():
        raise SystemExit(f"Missing runner: {RUNNER}")

    best = find_best_backup()
    if not best:
        raise SystemExit(
            "Could not find a backup that contains deep_math.json + llm_narrative.json + storycard.json.\n"
            "Check scripts/ for run_aurora_dataset_runner.py.bak_* files."
        )

    # Backup current runner
    bak = backup_file(RUNNER)
    print(f"[OK] backup current -> {bak}")

    # Restore best backup
    restored_txt = best.read_text(encoding="utf-8", errors="replace")
    print(f"[OK] restoring best backup -> {best.name}")

    # Canonicalize + harden against the exact indentation error class
    restored_txt = canonicalize_deep_math_import(restored_txt)
    restored_txt = fix_empty_try_blocks(restored_txt)
    restored_txt = ensure_summary_print(restored_txt)

    RUNNER.write_text(restored_txt, encoding="utf-8")

    # Compile check
    py_compile.compile(str(RUNNER), doraise=True)
    print("[DONE] runner restored to full outputs + canonicalized (deep-math v3 preferred, no empty try blocks).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
