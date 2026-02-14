from pathlib import Path
import re, sys, py_compile

SCRIPTS = Path(r"/mnt/c/Users/bgrut/Desktop/FantasyAI/scripts")
BAK     = SCRIPTS / "pipeline_final.py.bak_fixed"
OUTMOD  = SCRIPTS / "generator_from_backup_text.py"

if not BAK.exists():
    print(f"❌ Backup not found: {BAK}")
    sys.exit(1)

btxt = BAK.read_text(encoding="utf-8")

# Helper: capture a top-level def block by name
def grab_by_name(name: str, text: str):
    pat = rf'(?ms)^(def\s+{name}\s*\([^)]*\)\s*:\s*[\s\S]*?)(?=^\s*(?:def|class)\s|\Z)'
    m = re.search(pat, text)
    return m.group(1) if m else None

# Helper: capture *any* generator-looking function (cdp/selenium keywords)
def grab_candidates(text: str, keyword: str):
    pat = r'(?ms)^(def\s+([A-Za-z_]\w*)\s*\([^)]*\)\s*:\s*[\s\S]*?)(?=^\s*(?:def|class)\s|\Z)'
    out = []
    for m in re.finditer(pat, text):
        block, name = m.group(1), m.group(2)
        if keyword.lower() in name.lower():
            out.append((name, block))
    return out

# Preferred names in order
CDP_PREF = [
    "generate_veo_video_cdp","veo_cdp_generate","cdp_generate_video",
    "generate_video_cdp","start_cdp_flow","run_cdp","generate_via_cdp"
]
SEL_PREF = [
    "generate_veo_video_selenium","veo_selenium_generate","selenium_generate_video",
    "generate_video_selenium","start_selenium_flow","run_selenium","generate_via_selenium"
]
DSP_PREF = ["generate_veo_video","dispatch_generate_video","veo_generate"]

# Try exact grabs
cdp_block = next((grab_by_name(n, btxt) for n in CDP_PREF if grab_by_name(n, btxt)), None)
sel_block = next((grab_by_name(n, btxt) for n in SEL_PREF if grab_by_name(n, btxt)), None)
dsp_block = next((grab_by_name(n, btxt) for n in DSP_PREF if grab_by_name(n, btxt)), None)

# If missing, try heuristic candidates by keyword
if not cdp_block:
    cands = grab_candidates(btxt, "cdp")
    if cands: cdp_block = cands[0][1]
if not sel_block:
    cands = grab_candidates(btxt, "selenium")
    if cands: sel_block = cands[0][1]

# Build the shim module text
parts = [
    "from __future__ import annotations",
    "# GENERATED: clean shim built from backup function blocks (no top-level code executed)",
    "import os, logging",
    "",
]

if cdp_block:
    parts.append("# --- CDP generator (from backup) ---")
    parts.append(cdp_block.strip())
else:
    parts.append("""
# --- Fallback CDP watcher (very small) ---
def generate_veo_video_cdp(prompt_text: str) -> str:
    import os, time, json, urllib.request, pathlib
    log = logging.getLogger("FantasyAI")
    port = int(os.getenv("CHROME_CDP_PORT","9222"))
    outd = pathlib.Path(os.getenv("OUTPUTS_DIR","/mnt/c/Users/bgrut/Desktop/FantasyAI/outputs")); outd.mkdir(parents=True, exist_ok=True)
    # Ping CDP
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r: r.read()
        log.info("🎥 (CDP) Connecting to Chromium at 127.0.0.1:%d …", port)
    except Exception as e:
        raise RuntimeError(f"CDP not reachable on :{port}: {e}")
    # Watch outputs for new mp4
    before = {p.stat().st_mtime: p for p in outd.glob("*.mp4")}
    deadline = time.time()+900
    while time.time() < deadline:
        time.sleep(2)
        after = sorted(outd.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if after:
            newest = after[0]
            if not before or newest.stat().st_mtime > max(before.keys(), default=0):
                log.info("Downloaded (by filesystem detection): %s", newest)
                return str(newest)
    raise RuntimeError("Timed out waiting for .mp4 in outputs/")
""".strip())

if sel_block:
    parts.append("# --- Selenium generator (from backup) ---")
    parts.append(sel_block.strip())
else:
    parts.append("""
# --- Minimal Selenium fallback (disabled unless SELENIUM_OK=1 and selenium installed) ---
def generate_veo_video_selenium(prompt_text: str) -> str:
    raise RuntimeError("Selenium path not present in backup shim.")
""".strip())

# Dispatcher: prefer backup dsp; else provide our own
if dsp_block:
    parts.append("# --- Dispatcher (from backup) ---")
    parts.append(dsp_block.strip())
else:
    parts.append("""
# --- Dispatcher (shim) ---
def generate_veo_video(prompt_text: str) -> str:
    truthy=lambda v: str(v).lower() in ("1","true","yes","y")
    if truthy(os.getenv("USE_CHROME_CDP","1")):
        return generate_veo_video_cdp(prompt_text)
    if truthy(os.getenv("SELENIUM_OK","0")):
        return generate_veo_video_selenium(prompt_text)
    return generate_veo_video_cdp(prompt_text)
""".strip())

OUTMOD.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
py_compile.compile(str(OUTMOD), doraise=True)
print(f"✅ Built shim: {OUTMOD}")
