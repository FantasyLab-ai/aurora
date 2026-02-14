from pathlib import Path
import re, sys, py_compile

LIVE = Path(r"/mnt/c/Users/bgrut/Desktop/FantasyAI/scripts/pipeline_final.py")
BAK  = Path(r"/mnt/c/Users/bgrut/Desktop/FantasyAI/scripts/pipeline_final.py.bak_fixed")

if not BAK.exists():
    print(f"❌ Backup not found: {BAK}")
    sys.exit(1)

ltxt = LIVE.read_text(encoding="utf-8")
btxt = BAK.read_text(encoding="utf-8")

def grab(name:str, text:str):
    m = re.search(rf'(?ms)^\s*def\s+{name}\s*\([^)]*\)\s*:[\s\S]*?(?=^\s*(?:def|class)\s|\Z)', text)
    return m.group(0) if m else None

def replace(name:str, src:str, repl:str):
    pat = rf'(?ms)^\s*def\s+{name}\s*\([^)]*\)\s*:[\s\S]*?(?=^\s*(?:def|class)\s|\Z)'
    if re.search(pat, src):
        return re.sub(pat, repl.rstrip()+"\n", src), True
    # If missing, insert before __main__ guard or EOF
    m = re.search(r'(?m)^\s*if\s+__name__\s*==\s*[\'"]__main__[\'"]\s*:\s*$', src)
    idx = m.start() if m else len(src)
    prefix = src[:idx]
    if not prefix.endswith("\n"): prefix += "\n"
    return prefix + repl.rstrip()+"\n\n" + src[idx:], True

names = ["generate_veo_video_cdp","generate_veo_video_selenium","generate_veo_video"]
blocks = {}
for n in names:
    blk = grab(n, btxt)
    if not blk:
        print(f"❌ Could not find {n} in backup.")
        sys.exit(2)
    blocks[n] = blk

changed = False
for n in names:
    ltxt, did = replace(n, ltxt, blocks[n])
    changed = changed or did
    print(f"✅ staged {n}")

LIVE.write_text(ltxt, encoding="utf-8")
print("✅ Wrote live file:", LIVE)

py_compile.compile(str(LIVE), doraise=True)
print("✅ Syntax OK")
