"""
shooter_playwright.py
This takes ONE prompt from the DB and drives the generation UI (Veo3 or Sora2)
by pasting the cinematic prompt and clicking Generate exactly once.

This is called by /cmd/shoot-next.
"""

import os
import time
from pathlib import Path
from typing import Optional, Dict, Any

from studio_db import StudioDB

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


# Config knobs
PROJECT_URL = os.getenv(
    "VEO_PROJECT_URL",
    "https://labs.google/fx/tools/flow/project/388a3524-41fd-4c4a-8b00-a5653cf60416"
)
PROFILE_DIR = "/home/bgrut/Desktop/FantasyAI/playwright_profile"
CHROME_BINARY = "/usr/bin/google-chrome"  # update if different on your machine


def _ensure_dirs():
    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    out_dir = Path("/home/bgrut/Desktop/FantasyAI/outputs")
    out_dir.mkdir(parents=True, exist_ok=True)


def _open_browser(play):
    """
    Launch a persistent browser session using your profile so cookies/auth survive across runs.
    Not headless, because you want to actually be logged in visually.
    """
    browser = play.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=False,
        slow_mo=100,  # slow down actions a bit so UI stays stable
        channel="chrome",  # ask playwright to use Chrome if available
        args=[
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
        ],
        executable_path=CHROME_BINARY if Path(CHROME_BINARY).exists() else None,
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    return browser, page


def _go_to_project(page):
    """
    Make sure we’re on the right generator UI.
    We do NOT auto login here: you handle login once in the persistent profile.
    """
    page.goto(PROJECT_URL, wait_until="load", timeout=60_000)
    # minimal settle
    time.sleep(2)


def _find_prompt_box(page):
    """
    Try a few selectors. We return the first visible editable area.
    """
    candidates = [
        # rich text / contenteditable style box
        "[contenteditable='true']",
        "div[role='textbox']",
        "textarea",
        "div.prompt-editor textarea",
        "div[data-lexical-editor]",
    ]

    for sel in candidates:
        loc = page.locator(sel)
        try:
            loc.first.wait_for(state="visible", timeout=4000)
            return loc.first
        except PWTimeout:
            pass

    raise RuntimeError("Prompt box not found")


def _fill_prompt(prompt_box, text: str):
    """
    Paste the cinematic block.
    We clear box, then insert full spec.
    """
    prompt_box.click()
    # select all + delete
    prompt_box.press("Control+A")
    prompt_box.press("Backspace")
    prompt_box.type(text, delay=5)  # human-ish typing with slight delay


def _click_generate(page):
    """
    Click the Generate / Create / Render button once.
    We won't smash it.
    """
    import re
    labels = [
        "Generate",
        "Create",
        "Render",
        "Make video",
        "Make clip",
        "Submit",
        "Run"
    ]

    for label in labels:
        btn = page.get_by_role("button", name=re.compile(label, re.I))
        if btn.count() > 0:
            try:
                btn.first.click(timeout=2000)
                # tiny pause so the UI registers request
                time.sleep(2)
                return True
            except Exception:
                pass

    # fallback: any disabled-looking spinner is enough to count as "fired"
    # (Not strictly required, just returns False so caller knows we didn't click)
    return False


def shoot_next_prompt() -> Dict[str, Any]:
    """
    Pulls next awaiting prompt from DB, pastes, clicks generate, logs attempt.
    Returns dict with info, including DB ids.
    """

    db = StudioDB()
    db.init_db()

    job = db.get_next_awaiting_prompt()
    if not job:
        return {
            "ok": False,
            "error": "no awaiting_shoot prompts in queue"
        }

    _ensure_dirs()

    with sync_playwright() as play:
        browser, page = _open_browser(play)
        try:
            _go_to_project(page)

            # find editable box
            prompt_box = _find_prompt_box(page)

            # inject prompt text
            _fill_prompt(prompt_box, job["prompt_text"])

            # attempt generate
            clicked = _click_generate(page)

            # mark DB state
            db.mark_prompt_shot(job["id"])
            render_id = db.record_render_result(
                prompt_id=job["id"],
                model_used="human_browser_sora_or_veo",
                output_path="",        # we don't have final mp4 path yet
                success=1 if clicked else 0,
                notes="clicked generate" if clicked else "no button found"
            )

            result = {
                "ok": True,
                "prompt_id": job["id"],
                "render_id": render_id,
                "clicked": clicked,
                "trend_hint": job["trend_hint"],
                "persona_id": job["persona_id"],
                "platform": job["platform"],
                "note": "Browser launched. If you're not logged in yet or blocked by SSO, log in manually in that window, then re-run shoot-next."
            }
            return result

        finally:
            # leave browser OPEN so you can watch / grab output
            # don't browser.close() here
            pass
