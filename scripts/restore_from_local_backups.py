from __future__ import annotations
from pathlib import Path
import re, sys, textwrap, difflib, py_compile

SCRIPTS = Path(r"/mnt/c/Users/bgrut/Desktop/FantasyAI/scripts")
LIVE = SCRIPTS / "pipeline_final.py"

# what we’ll copy back from backups
ROOT_FUNCS = [
    "generate_veo_video_cdp",
    "generate_veo_video_selenium",
    "generate_veo_video",
]

# acceptable backup filename patterns in *the same folder*
BACKUP_PATTERNS = [
    "pipeline_final.py.bak*",
    "pipeline_final.bak*",
    "pipeline_final.py.*bak*",
    "pipeline_final_backup*.py",
]

SAFE_IMPORTS = [
    "import os","import re","import sys","import json","import time","import logging",
    "import pathlib","import urllib.request","import urllib.error","from datetime import datetime",
    "from pathlib import Path",
]

def newest_backup() -> Path|None:
    cands = []
    for pat in BACKUP_PATTERNS:
        cands += list(SCRIPTS.glob(pat))
    cands = [p for p in cands if p.name != "pipeline_final.py"]
    return max(cands, key=lambda p: (p.stat().st_mtime, p.stat().st_size)) if cands else None

def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")

def top_level_defs(src: str):
    out = {}
    it = list(re.finditer(r'(?m)^(def|class)\s+[A-Za-z_]\w*\s*\(?.*?:\s*$', src))
    for i, m in enumerate(it):
        start = m.start()
        end = it[i+1].start() if i+1 < len(it) else len(src)
        if src[m.start():].startswith("def "):
            nm = re.match(r'^def\s+([A-Za-z_]\w*)', src[m.start():m.end()].strip())
            if nm: out[nm.group(1)] = (start, end)
    return out

def get_calls(body: str) -> set[str]:
    names = set(re.findall(r'\b([A-Za-z_]\w*)\s*\(', body))
    skip = {"if","for","while","try","except","with","return","print","len","range","list","dict","set","tuple","open",
            "Path","json","os","sys","re","time","logging","datetime","pathlib","urllib","sorted","min","max","any","all",
            "sum","enumerate","next","map","filter","zip","get","setdefault","append","extend","format","join","items",
            "keys","values","encode","decode","loads","dumps","info","warning","error","exists","mkdir","write","read",
            "close"}
    return {n for n in names if n not in skip}

def slice_block(src, span): return src[span[0]:span[1]]

def insert_before_main(src: str, code: str) -> str:
    m = re.search(r'(?m)^\s*if\s+__name__\s*==\s*["\']__main__["\']\s*:\s*$', src)
    i = m.start() if m else len(src)
    head, tail = src[:i], src[i:]
    if not head.endswith("\n"): head += "\n"
    return head + code.rstrip() + "\n\n" + tail

def ensure_imports(src: str) -> str:
    lines = src.splitlines()
    have = set(ln.strip() for ln in lines[:100] if ln.strip().startswith(("import ","from ")))
    adds = [imp for imp in SAFE_IMPORTS if imp not in have]
    return ("\n".join(adds) + "\n" + src) if adds else src

def transplant():
    back_path = newest_backup()
    if not back_path:
        print("❌ No backups found in scripts/. Place a file matching patterns like pipeline_final.py.bak*", file=sys.stderr)
        sys.exit(1)

    live = read(LIVE)
    back = read(back_path)
    back_defs = top_level_defs(back)
    live_defs = top_level_defs(live)

    # collect root funcs + any helper defs they call (closure)
    needed = []
    queue = [f for f in ROOT_FUNCS if f in back_defs]
    seen = set()
    while queue:
        f = queue.pop(0)
        if f in seen: continue
        seen.add(f)
        span = back_defs[f]
        block = slice_block(back, span)
        needed.append((f, block))
        for name in get_calls(block):
            if name in back_defs and name not in seen:
                queue.append(name)

    if not needed:
        print(f"❌ None of {ROOT_FUNCS} found in backup {back_path.name}", file=sys.stderr)
        sys.exit(1)

    new_live, changes = live, []
    for name, block in needed:
        pat = re.compile(rf'(?ms)^\s*def\s+{name}\s*\([^)]*\)\s*:[\s\S]*?(?=^\s*(?:def|class)\s|\Z)')
        ded = textwrap.dedent(block).rstrip() + "\n"
        if pat.search(new_live):
            new_live2 = pat.sub(ded, new_live)
            if new_live2 != new_live:
                changes.append(f"replaced {name}")
                new_live = new_live2
        else:
            new_live = insert_before_main(new_live, ded)
            changes.append(f"inserted {name}")

    new_live = ensure_imports(new_live)

    if new_live != live:
        diff = difflib.unified_diff(live.splitlines(), new_live.splitlines(),
                                    fromfile=str(LIVE), tofile=str(LIVE)+":patched", lineterm="")
        preview = "\n".join(list(diff)[:120])
        LIVE.write_text(new_live, encoding="utf-8")
        print(f"✅ Transplanted from backup: {back_path.name}")
        for ch in changes: print("  -", ch)
        if preview:
            print("\n--- diff preview (truncated) ---")
            print(preview)
    else:
        print("ℹ️ Live file already matches backup for these functions.")

    # compile check
    try:
        py_compile.compile(str(LIVE), doraise=True)
        print("✅ Syntax OK")
    except Exception as e:
        print("❌ Still a syntax error:", e)
        sys.exit(1)

if __name__ == "__main__":
    transplant()
