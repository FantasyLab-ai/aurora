from __future__ import annotations
import os, time, json, logging, pathlib, urllib.request

log = logging.getLogger("FantasyAI")

def generate_veo_video_cdp(prompt_text: str) -> str:
    """
    Minimal CDP-assisted flow:
      - verifies DevTools on :9222 (or CHROME_CDP_PORT)
      - (optionally) relies on an already-open Flow tab
      - watches OUTPUTS_DIR for a new .mp4 and returns its path
    """
    port = int(os.getenv("CHROME_CDP_PORT", "9222"))
    outdir = pathlib.Path(os.getenv("OUTPUTS_DIR",
                    "/mnt/c/Users/bgrut/Desktop/FantasyAI/outputs"))
    outdir.mkdir(parents=True, exist_ok=True)

    # Ping CDP
    try:
        with urllib.request.urlopen(f"http://{os.getenv('CHROME_CDP_HOST', '127.0.0.1')}:{port}/json/version", timeout=3) as r:
            _ = r.read()
        log.info("🎥 (CDP) Connecting to Chromium at 127.0.0.1:%d …", port)
    except Exception as e:
        raise RuntimeError(f"CDP not reachable on :{port}. Start Edge/Chrome with --remote-debugging-port={port}. ({e})")

    # Baseline existing mp4s
    before_mtime = 0.0
    try:
        before_mtime = max((p.stat().st_mtime for p in outdir.glob("*.mp4")), default=0.0)
    except Exception:
        pass

    log.info("Generation started; polling for .mp4 in %s …", outdir)
    deadline = time.time() + 900  # 15 minutes
    while time.time() < deadline:
        time.sleep(2)
        try:
            newest = max(outdir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, default=None)
            if newest and newest.stat().st_mtime > before_mtime:
                log.info("Downloaded (by filesystem detection): %s", newest)
                return str(newest)
        except Exception:
            pass

    raise RuntimeError("Timed out waiting for a new .mp4 in outputs dir")

def generate_veo_video_selenium(prompt_text: str) -> str:
    raise RuntimeError("Selenium path not wired in this shim (CDP-only fast path).")

def generate_veo_video(prompt_text: str) -> str:
    """Dispatcher: prefer CDP; fall back to Selenium if explicitly enabled."""
    truthy = lambda v: str(v).lower() in ("1", "true", "yes", "y")
    if truthy(os.getenv("USE_CHROME_CDP", "1")):
        return generate_veo_video_cdp(prompt_text)
    if truthy(os.getenv("SELENIUM_OK", "0")):
        return generate_veo_video_selenium(prompt_text)
    # default to CDP
    return generate_veo_video_cdp(prompt_text)
