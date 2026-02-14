# kick_veo_send.py
import os, sys, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

DBG = os.getenv("CHROME_DEBUG_ADDRESS", "127.0.0.1:9222")
WAIT = int(os.getenv("KICK_WAIT", "20"))   # seconds to wait for editor text

def attach(debug_addr: str):
    o = Options()
    o.add_experimental_option("debuggerAddress", debug_addr)
    # no excludeSwitches / useAutomationExtension (those caused your attach error)
    drv = webdriver.Chrome(options=o)
    return drv

def find_editor(drv):
    # A handful of robust selectors (Veo flows often use contenteditable)
    SELS = [
        "div[contenteditable='true']",
        "div[role='textbox']",
        "textarea",
        "div.ql-editor",
        "div.ProseMirror",
    ]
    for css in SELS:
        els = drv.find_elements(By.CSS_SELECTOR, css)
        for el in els:
            try:
                # heuristic: visible & sizeable
                if el.is_displayed() and el.size.get("width",0) > 50 and el.size.get("height",0) > 20:
                    return el
            except Exception:
                pass
    return None

def editor_text_len(drv, el):
    try:
        return int(drv.execute_script(
            "var e=arguments[0];"
            "let t='';"
            "if(e.value!==undefined){t=e.value;}else{t=e.innerText||e.textContent||'';}"
            "return t.length;", el))
    except Exception:
        return 0

def click_any_generate(drv):
    # Buttons that commonly appear in Flow/Veo
    XPATHS = [
        "//button[.//span[contains(.,'Generate')]]",
        "//button[contains(.,'Generate')]",
        "//button[contains(.,'Create') or contains(.,'Render') or contains(.,'Run') or contains(.,'Send')]",
        "//div[@role='button'][.//span[contains(.,'Generate') or contains(.,'Send')]]",
        "//*[@role='button' and (contains(.,'Generate') or contains(.,'Send') or contains(.,'Run') or contains(.,'Create') or contains(.,'Render'))]",
        # paper-plane icon container
        "//*[name()='svg' and (contains(@aria-label,'Send') or contains(@data-icon,'paper-plane') or contains(@class,'plane'))]/ancestor::*[self::button or @role='button'][1]"
    ]
    for xp in XPATHS:
        try:
            btn = WebDriverWait(drv, 3).until(EC.element_to_be_clickable((By.XPATH, xp)))
            drv.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.2)
            btn.click()
            return True
        except Exception:
            continue
    return False

def main():
    print(f"🔌 Attaching to Chrome at {DBG} ...")
    drv = attach(DBG)
    try:
        print("📄 URL:", drv.current_url)

        ed = find_editor(drv)
        if not ed:
            print("❌ Could not locate the prompt editor. Make sure the Veo project page is visible.")
            sys.exit(2)

        # Wait until something was pasted (length > 20 chars usually)
        deadline = time.time() + WAIT
        txt_len = editor_text_len(drv, ed)
        while time.time() < deadline and txt_len < 20:
            time.sleep(0.25)
            txt_len = editor_text_len(drv, ed)
        print(f"📝 Editor text length: {txt_len}")

        # Focus / click
        try: ed.click()
        except Exception: pass
        try: drv.execute_script("arguments[0].focus();", ed)
        except Exception: pass

        # Try Ctrl+Enter, then Enter
        sent = False
        try:
            ActionChains(drv).key_down(Keys.CONTROL).send_keys(Keys.ENTER).key_up(Keys.CONTROL).perform()
            print(⌨️  Sent Ctrl+Enter")
            sent = True
            time.sleep(0.6)
        except Exception as e:
            print("… Ctrl+Enter failed:", e)

        if not sent:
            try:
                ed.send_keys(Keys.ENTER)
                print("⌨️  Sent Enter")
                sent = True
                time.sleep(0.6)
            except Exception as e:
                print("… Enter failed:", e)

        # JS fallback
        if not sent:
            try:
                drv.execute_script(
                    "var el=arguments[0];"
                    "el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', bubbles:true}));"
                    "el.dispatchEvent(new KeyboardEvent('keypress',{key:'Enter', code:'Enter', bubbles:true}));"
                    "el.dispatchEvent(new KeyboardEvent('keyup',    {key:'Enter', code:'Enter', bubbles:true}));",
                    ed
                )
                print("🪄 Dispatched JS Enter events")
                sent = True
            except Exception as e:
                print("… JS Enter failed:", e)

        # If keypath didn’t trigger, try clicking a Generate/Send button
        if not click_any_generate(drv):
            print("⚠️  No obvious Generate/Send button was clickable (may still be queued if Enter worked).")
        else:
            print("✅ Clicked a Generate/Send button.")

        print("⏳ Give Veo a few seconds to start rendering …")
        time.sleep(5)

    finally:
        # Don't close the attached browser
        pass

if __name__ == "__main__":
    main()
