from selenium.webdriver import Chrome
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
import os, time

# ---- Config ----
WIN_PROFILE_DIR = r"C:\ChromeVeoProfile"
WSL_PROFILE_DIR = "/mnt/c/ChromeVeoProfile"
VEO_URL = os.getenv("VEO_URL", "https://veo.google.com/")

def _new_persistent_chrome():
    opts = Options()
    # Reuse the real, pre-logged-in profile
    opts.add_argument(f"--user-data-dir={WSL_PROFILE_DIR}")
    opts.add_experimental_option("detach", True)

    # Hide automation fingerprints so Google doesn’t block
    opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")
    # Use a normal UA string (adjust if you want)
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

    driver = Chrome(options=opts)
    driver.maximize_window()
    return driver

def _wait_css(driver, sel, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el: return el
        except NoSuchElementException:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"Not found: {sel}")

def _paste_prompt(driver, text):
    # Try a broad set of selectors (update if the UI moves)
    selectors = [
        "textarea[placeholder*='Describe']",
        "textarea[placeholder*='prompt']",
        "div[contenteditable='true']",
        "form textarea",
        "textarea",
    ]
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            el.clear()
            el.send_keys(text)
            return True
        except Exception:
            continue
    return False

def _click_generate(driver):
    # Prefer buttons whose visible text contains 'Generate'
    for btn in driver.find_elements(By.CSS_SELECTOR, "button"):
        try:
            label = (btn.text or "").strip().lower()
            if "generate" in label and btn.is_displayed() and btn.is_enabled():
                btn.click()
                return True
        except Exception:
            pass
    # Fallback: some UIs hide text inside spans
    try:
        driver.find_element(By.XPATH, "//button[.//*[contains(text(),'Generate')]]").click()
        return True
    except Exception:
        return False

def _wait_for_completion(driver, timeout=240):
    # TODO: replace with the site’s real “done”/download indicator.
    t0 = time.time()
    while time.time() - t0 < timeout:
        # Heuristic: look for “Download”, “Saved”, or “Done”
        done = driver.find_elements(By.XPATH, "//*[contains(.,'Download') or contains(.,'Saved') or contains(.,'Done')]")
        if done: return ""
        time.sleep(2)
    return ""

def generate_veo_video(prompt_text: str) -> str:
    driver = _new_persistent_chrome()
    driver.get(VEO_URL)
    time.sleep(5)  # let the app load

    if not _paste_prompt(driver, prompt_text):
        raise RuntimeError("Prompt box not found")

    if not _click_generate(driver):
        raise RuntimeError("Generate button not found")

    return _wait_for_completion(driver) or ""
