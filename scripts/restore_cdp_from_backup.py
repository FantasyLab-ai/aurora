from __future__ import annotations
from pathlib import Path
import re, sys, textwrap, py_compile, difflib

LIVE = Path(r"/mnt/c/Users/bgrut/Desktop/FantasyAI/scripts/pipeline_final.py")
BACK = Path(r"/mnt/data/pipeline_final.py.bak_fixed")

TARGET_ROOTS = [
    "generate_veo_video_cdp",
    "generate_veo_video_selenium",
    "generate_veo_video",
]

SAFE_IMPORTS = [
    "import os", "import re", "import sys", "import json", "import time",
    "import logging", "import pathlib", "import urllib.request", "import urllib.error",
    "from datetime import datetime", "from pathlib import Path",
]

def read(p: Path) -> str:
    if not p.exists():
        print(f"❌ Missing: {p}", file=sys.stderr)
        sys.exit(1)
    return p.read_text(encoding="utf-8", errors="ignore")

def top_level_defs(src: str) -> dict[str, tuple[int,int]]:
    """
    Map: func_name -> (start_idx, end_idx) for *top-level* def blocks.
    """
    out = {}
    # Find all top-level def/class boundaries
    it = list(re.finditer(r'(?m)^(def|class)\s+[A-Za-z_]\w*\s*\(?.*?:\s*$', src))
    ends = []
    for i, m in enumerate(it):
        start = m.start()
        end = it[i+1].start() if i+1 < len(it) else len(src)
        line = src[m.start():m.end()]
        if line.startswith("def "):
            name = re.match(r'^def\s+([A-Za-z_]\w*)', line[0:80].strip())
            if name:
                out[name.group(1)] = (start, end)
    return out

def get_calls(body: str) -> set[str]:
    """
    Roughly grab identifiers that look like function calls foo(...).
    Filter obvious builtins/common names later.
    """
    names = set(re.findall(r'\b([A-Za-z_]\w*)\s*\(', body))
    # Trim common non-helper identifiers
    skip = {
        # keywords / builtins / modules often seen
        "if","for","while","try","except","with","return","print","len","range","list","dict","set","tuple","open","Path",
        "json","os","sys","re","time","logging","datetime","pathlib","urllib","sorted","min","max","any","all","sum",
        "enumerate","next","map","filter","zip","get","setdefault","append","extend","format","join","items","keys","values",
        "encode","decode","loads","dumps","info","warning","error","exists","mkdir","write","read","close",
    }
    return {n for n in names if n not in skip}

def slice_block(src: str, span: tuple[int,int]) -> str:
    return src[span[0]:span[1]]

def insert_before_main(src: str, code: str) -> str:
    m = re.search(r'(?m)^\s*if\s+__name__\s*==\s*[\'"]__main__[\'"]\s*:\s*$', src)
    idx = m.start() if m else len(src)
    prefix, suffix = src[:idx], src[idx:]
    if not prefix.endswith("\n"): prefix += "\n"
    return prefix + code.rstrip() + "\n\n" + suffix

def ensure_imports(src: str) -> str:
    head = src.splitlines()
    # find first non-shebang line
    insert_at = 0
    if head and head[0].startswith("#!"): insert_at = 1
    # collect current imports to avoid dupes
    have = set()
    for ln in head[:80]:  # only scan top
        if ln.strip().startswith(("import ","from ")):
            have.add(ln.strip())
    adds = [imp for imp in SAFE_IMPORTS if imp not in have]
    if not adds: return src
    block = "\n".join(adds) + "\n"
    return src[:0] + block + src[0:]

def transplant():
    live = read(LIVE)
    back = read(BACK)

    back_defs = top_level_defs(back)
    live_defs = top_level_defs(live)

    # Build dependency-closed set of functions starting at TARGET_ROOTS
    needed = []
    queue = [r for r in TARGET_ROOTS if r in back_defs]
    seen = set()
    while queue:
        f = queue.pop(0)
        if f in seen: continue
        seen.add(f)
        span = back_defs[f]
        block = slice_block(back, span)
        needed.append((f, block))
        # enqueue helper defs referenced by this block if they exist in backup
        for name in get_calls(block):
            if name in back_defs and name not in seen:
                queue.append(name)

    if not needed:
        print("❌ None of the target functions were found in the backup.")
        sys.exit(1)

    # Compose insertion text in backup order
    insertion = "\n\n".join(textwrap.dedent(b) for _, b in needed).rstrip() + "\n"

    # Replace or insert each needed def in the live file
    new_live = live
    changes = []
    for name, block in needed:
        pat = re.compile(rf'(?ms)^\s*def\s+{name}\s*\([^)]*\)\s*:[\s\S]*?(?=^\s*(?:def|class)\s|\Z)')
        if pat.search(new_live):
            new_live2 = pat.sub(textwrap.dedent(block).rstrip()+"\n", new_live)
            if new_live2 != new_live:
                changes.append(f"replaced {name}")
                new_live = new_live2
        else:
            # insert before __main__
            new_live = insert_before_main(new_live, textwrap.dedent(block))
            changes.append(f"inserted {name}")

    # Make sure imports are available (idempotent)
    new_live = ensure_imports(new_live)

    if new_live == live:
        print("ℹ️ No changes were necessary (live already matches backup).")
    else:
        # show mini diff summary (first 120 lines of diff)
        diff = difflib.unified_diff(live.splitlines(), new_live.splitlines(),
                                    fromfile=str(LIVE), tofile=str(LIVE)+":patched", lineterm="")
        preview = "\n".join(list(diff)[:120])
        LIVE.write_text(new_live, encoding="utf-8")
        print("✅ Transplanted from backup:\n  - " + "\n  - ".join(changes))
        if preview:
            print("\n--- diff preview (truncated) ---")
            print(preview)

    # compile check
    try:
        py_compile.compile(str(LIVE), doraise=True)
        print("✅ Syntax OK")
    except Exception as e:
        print("❌ Still a syntax error:", e)
        sys.exit(1)

if __name__ == "__main__":
    transplant()
