import os, time, re
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

# persistent profile on the Linux side (WSL)
PROFILE_DIR = "/home/bgrut/Desktop/FantasyAI/playwright_profile"

# your Veo project URL (the actual project tab you want automated)
PROJECT_URL = os.getenv(
    "VEO_PROJECT_URL",
    "https://labs.google/fx/tools/flow/project/388a3524-41fd-4c4a-8b00-a5653cf60416"
)

# candidate Chrome paths inside WSL Ubuntu
CHROME_BIN_CANDIDATES = [
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/opt/google/chrome/google-chrome",
]

def _pick_chrome_binary():
    for p in CHROME_BIN_CANDIDATES:
        if os.path.exists(p):
            return p
    return None  # fallback to bundled chromium (not ideal, but we handle it)


def _launch_persistent(playwright):
    """
    Launch a persistent browser context that keeps cookies/login,
    using real Chrome (best), or fallback Chromium (meh).
    """
    chrome_path = _pick_chrome_binary()
    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)

    launch_args = [
        "--start-maximized",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
    ]

    print("[playwright] launching persistent browser profile:", PROFILE_DIR, flush=True)
    if chrome_path:
        print(f"[playwright] using system Chrome binary: {chrome_path}", flush=True)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            executable_path=chrome_path,
            args=launch_args,
        )
    else:
        print("[playwright] WARNING: falling back to bundled chromium (Google may block login)", flush=True)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            args=launch_args,
        )

    # Use first tab, or open new tab if none
    page = context.pages[0] if context.pages else context.new_page()
    return context, page


def _goto_project(page):
    """
    Navigate (or re-navigate) to your Veo project.
    If you're already there from last run, harmless.
    """
    print("[playwright] navigating to project:", PROJECT_URL, flush=True)
    page.goto(PROJECT_URL, wait_until="load", timeout=120_000)


def _looks_like_login(page) -> bool:
    """
    Try to guess: are we still on a Google login / auth interstitial,
    not inside the actual Veo editor yet?

    Signals:
    - URL contains 'accounts.google.' or 'ServiceLogin'
    - Page text has obvious login phrases
    - An email/identifier input is visible
    """
    try:
        url_now = page.url
    except Exception:
        url_now = ""

    # obvious login host
    if "accounts.google." in url_now or "ServiceLogin" in url_now:
        print("[detect] page URL looks like Google login flow:", url_now, flush=True)
        return True

    # check for common login markers
    login_markers = [
        "Sign in",
        "Welcome",
        "Next",
        "Enter your email",
        "Choose an account",
        "Verify it's you",
        "2-Step Verification",
        "Something went wrong",
        "Review permissions",
    ]

    try:
        body_txt = page.locator("body").inner_text(timeout=2000)
    except Exception:
        body_txt = ""

    for marker in login_markers:
        if marker.lower() in body_txt.lower():
            print(f"[detect] saw login marker text: {marker}", flush=True)
            return True

    # also: look for email/identifier input
    try:
        if page.locator("input[type='email']").first.is_visible():
            print("[detect] found email input, treating as login screen.", flush=True)
            return True
    except Exception:
        pass

    return False


def _find_prompt_box(page):
    """
    Find the main multi-line prompt text region in the Veo UI.
    We try multiple selectors because Google likes to change classnames.
    """
    candidates = [
        "[contenteditable='true']",
        "textarea",
        "div[role='textbox']",
    ]

    for sel in candidates:
        locator = page.locator(sel).first
        try:
            locator.wait_for(state="visible", timeout=4000)
            print(f"[playwright] found prompt box via {sel}", flush=True)
            return locator
        except PWTimeoutError:
            pass
        except Exception:
            pass

    raise RuntimeError("Prompt box not found")


def _paste_prompt(locator, full_text: str):
    """
    Put our whole cinematic script into the box.
    DO NOT hit Enter at the end (we don't want multiple runs).
    """
    locator.click(timeout=3000)

    # try wiping existing content
    try:
        locator.fill("")
    except Exception:
        # fallback for contenteditable
        try:
            locator.press("Control+A")
            locator.press("Backspace")
        except Exception:
            pass

    locator.type(full_text, delay=1)


def _is_busy(page):
    """
    Heuristic check to see if job already queued so we don't double-trigger.
    """
    busy_regex = re.compile(
        r"(Queued|Generating|Rendering|Processing|Working|In progress|Uploading|Preparing)",
        re.I,
    )

    # any disabled button?
    try:
        disabled_buttons = page.locator("button:disabled")
        if disabled_buttons.count() > 0:
            return True
    except Exception:
        pass

    # busy text in DOM?
    try:
        body_text = page.locator("body").inner_text(timeout=1000)
        if busy_regex.search(body_text or ""):
            return True
    except Exception:
        pass

    return False


def _click_generate_once(page):
    """
    Click exactly one 'generate' style button.
    Then confirm we look busy, so we don't smash the button again.
    """
    if _is_busy(page):
        print("[submit] UI already busy, skipping click.", flush=True)
        return True

    labels = [
        "Generate",
        "Generate video",
        "Create video",
        "Create",
        "Render",
        "Make video",
        "Make Video",
        "Submit",
        "Run",
        "Start",
        "Go",
    ]

    for label in labels:
        btn = page.get_by_role("button", name=re.compile(rf"{label}", re.I)).first
        try:
            btn.wait_for(state="visible", timeout=2000)
            btn.click(timeout=2000)
            print(f"[submit] Clicked button '{label}'", flush=True)

            # wait up to ~10s for busy state
            for _ in range(20):
                if _is_busy(page):
                    print("[submit] Generation appears to have started.", flush=True)
                    return True
                time.sleep(0.5)

            return True  # we clicked, that's enough
        except PWTimeoutError:
            continue
        except Exception as e:
            print(f"[submit] couldn't click '{label}': {e}", flush=True)
            continue

    print("[submit] No visible generate/run button found.", flush=True)
    return False


def _wait_a_bit_for_job(seconds=5):
    for i in range(seconds):
        print(f"[wait] {i+1}/{seconds}...", flush=True)
        time.sleep(1)


def generate_veo_video(prompt_text: str) -> str:
    """
    Main entry called from run_end_to_end.py.

    Behavior now:
    1. Launch persistent Chrome in WSL (same profile dir every run).
    2. Go to Veo project tab.
    3. Detect if we're still in login / auth.
       - If yes: DON'T FAIL. Just leave the browser up so you can finish login,
         save storage_state, then return "" early.
    4. If we're in the editor already:
       - Find the prompt box, paste text.
       - Single-click Generate.
       - Wait a few seconds.
       - Save state.
       - Return "" (future: scrape mp4 path).
    """

    print("[playwright] starting generate_veo_video(headless=False)", flush=True)
    print("[playwright] profile dir:", PROFILE_DIR, flush=True)
    print("[playwright] project URL:", PROJECT_URL, flush=True)

    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        context, page = _launch_persistent(pw)
        _goto_project(page)

        # STEP A: are we still logging in?
        if _looks_like_login(page):
            print("[auth] Looks like login/consent screen.", flush=True)
            print("[auth] Please finish auth in the opened Chrome window.", flush=True)
            print("[auth] After you're fully in the Veo project UI, just re-run the command.", flush=True)
            # try to persist whatever auth progress we have so far
            try:
                context.storage_state(path=str(Path(PROFILE_DIR) / "storage_state.json"))
                print("[auth] storage_state saved for next run.", flush=True)
            except Exception as e:
                print(f"[auth] could not save storage_state: {e}", flush=True)
            # IMPORTANT: don't close the browser/context here.
            # Leave it open so you can keep clicking through login.
            return ""

        # STEP B: we're past login, so now we should be on the actual Veo UI
        print("[playwright] We appear to be in the editor, proceeding.", flush=True)

        prompt_box = _find_prompt_box(page)
        _paste_prompt(prompt_box, prompt_text)

        _click_generate_once(page)

        _wait_a_bit_for_job(seconds=5)

        # save state for future runs
        try:
            context.storage_state(path=str(Path(PROFILE_DIR) / "storage_state.json"))
            print("[playwright] storage_state saved.", flush=True)
        except Exception as e:
            print(f"[playwright] could not save storage_state: {e}", flush=True)

        # TODO: scrape final downloadable video path into output_path
        return ""
