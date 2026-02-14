from __future__ import annotations

from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"
SCRIPTS_DIR = ROOT / "scripts"

REQUIRED_TOKENS = [
    "deep_math.json",
    "llm_narrative.json",
    "llm_debug.json",
    "storycard.json",
    "--deep-math",
    "--also-llm",
]

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")

def backup_current() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = RUNNER.with_suffix(RUNNER.suffix + f".bak_{ts}")
    bak.write_bytes(RUNNER.read_bytes())
    return bak

def score(text: str) -> int:
    return sum(1 for t in REQUIRED_TOKENS if t in text)

def main() -> int:
    if not RUNNER.exists():
        raise SystemExit(f"Missing runner: {RUNNER}")

    cur = read_text(RUNNER)
    cur_score = score(cur)

    bak_files = sorted(
        SCRIPTS_DIR.glob("run_aurora_dataset_runner.py.bak_*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not bak_files:
        print("[FAIL] No backups found (run_aurora_dataset_runner.py.bak_*)")
        return 2

    best = None
    best_score = -1

    for p in bak_files:
        t = read_text(p)
        s = score(t)
        if s > best_score:
            best = p
            best_score = s
        if best_score == len(REQUIRED_TOKENS):
            break

    print(f"[INFO] current runner score: {cur_score}/{len(REQUIRED_TOKENS)}")
    print(f"[INFO] best backup score:   {best_score}/{len(REQUIRED_TOKENS)} -> {best.name}")

    if best_score <= cur_score:
        print("[OK] Current runner is already as good or better than the best backup. No restore needed.")
        return 0

    bak = backup_current()
    print(f"[OK] backed up current -> {bak.name}")

    RUNNER.write_text(read_text(best), encoding="utf-8")
    print(f"[OK] restored runner from -> {best.name}")

    # Verify the restored runner still has the required tokens
    restored = read_text(RUNNER)
    missing = [t for t in REQUIRED_TOKENS if t not in restored]
    if missing:
        print("[WARN] restored runner is still missing tokens:", missing)
        return 3

    # Ensure it prints something at the end (guard against silent runs)
    if 'print(json.dumps' not in restored and 'print(' not in restored:
        print("[WARN] runner may not print summary JSON anymore (no print detected).")

    # Compile check
    import py_compile
    py_compile.compile(str(RUNNER), doraise=True)
    print("[DONE] runner restored + py_compile clean")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
