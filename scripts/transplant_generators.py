from pathlib import Path
import re, sys, py_compile, os
from typing import Optional

SCRIPTS_DIR = Path(r"/mnt/c/Users/bgrut/Desktop/FantasyAI/scripts")
LIVE = SCRIPTS_DIR / "pipeline_final.py"

# any of these patterns will be considered a backup
BACKUP_PATTERNS = [
    "pipeline_final.py.bak*",
    "pipeline_final.bak*",
    "pipeline_final.py.*bak*",
]

FUNC_NAMES = (
    "generate_veo_video_cdp",
    "generate_veo_video_selenium",
    "generate_veo_video",
)

def newest_backup() -> Optional[Path]:
    cands = []
    for pat in BACKUP_PATTERNS:
        cands += list(SCRIPTS_DIR.glob(pat))
    # exclude the live file if it matches a broad pattern
    cands = [p for p in cands if p.name != "pipeline_final.py"]
    if not cands:
        return None
    # pick by (mtime, size) to prefer more complete files
    cands.sort(key=lambda p: (p.stat().st_mtime, p.stat().st_size), reverse=True)
    return cands[0]

def grab(func_name: str, text: str) -> Optional[str]:
    # capture entire top-level def block until the next top-level def/class or EOF
    pat = rf'(?ms)^\s*def\s+{func_name}\s*\([^)]*\)\s*:[\s\S]*?(?=^\s*(?:def|class)\s+\w|\Z)'
    m = re.search(pat, text)
    return m.group(0) if m else None

def replace_or_insert(func_name: str, block: str, text: str) -> tuple[str,bool,str]:
    pat = rf'(?ms)^\s*def\s+{func_name}\s*\([^)]*\)\s*:[\s\S]*?(?=^\s*(?:def|class)\s+\w|\Z)'
    if re.search(pat, text):
        new = re.sub(pat, block.rstrip() + "\n", text)
        return new, (new != text), f"replaced {func_name}"
    # insert before __main__ guard if present, else append
    m = re.search(r'(?m)^\s*if\s+__name__\s*==\s*[\'"]__main__[\'"]\s*:\s*$', text)
    idx = m.start() if m else len(text)
    prefix = text[:idx]
    if not prefix.endswith("\n"):
        prefix += "\n"
    new = prefix + block.rstrip() + "\n\n" + text[idx:]
    return new, True, f"inserted {func_name}"

def normalize_indent(text: str) -> str:
    out = []
    for L in text.splitlines():
        lead_tabs = len(L) - len(L.lstrip("\t"))
        if lead_tabs:
            L = " " * (4 * lead_tabs) + L.lstrip("\t")
        out.append(L.rstrip())
    return "\n".join(out) + "\n"

def main():
    if not LIVE.exists():
        print(f"❌ Live file not found: {LIVE}")
        sys.exit(1)

    backup = newest_backup()
    if not backup:
        print(f"❌ No backup file found in {SCRIPTS_DIR}")
        sys.exit(1)

    btxt = backup.read_text(encoding="utf-8", errors="replace")
    ltxt = LIVE.read_text(encoding="utf-8", errors="replace")

    # Extract desired functions from backup
    transplanted = {}
    missing = []
    for fn in FUNC_NAMES:
        block = grab(fn, btxt)
        if block:
            transplanted[fn] = block
        else:
            missing.append(fn)

    if missing:
        print("⚠️ Not found in backup:", ", ".join(missing))

    changed = False
    actions = []
    for fn, block in transplanted.items():
        ltxt, did, act = replace_or_insert(fn, block, ltxt)
        if did:
            changed = True
            actions.append(act)

    if not changed:
        print("ℹ️ Nothing changed (functions already present & matched).")
        for name in FUNC_NAMES:
            print(f"has {name:<27}:",
                  bool(re.search(rf'(?m)^def\s+{name}\s*\(', ltxt)))
        return

    ltxt = normalize_indent(ltxt)
    LIVE.write_text(ltxt, encoding="utf-8")

    # Compile check
    try:
        py_compile.compile(str(LIVE), doraise=True)
        print(f"✅ Transplant complete from: {backup.name}")
        print("   " + "; ".join(actions))
    except Exception as e:
        print("❌ Syntax error after write:\n", e)
        sys.exit(2)

    # Presence check
    for name in FUNC_NAMES:
        print(f"has {name:<27}:",
              bool(re.search(rf'(?m)^def\s+{name}\s*\(', ltxt)))

if __name__ == "__main__":
    main()
