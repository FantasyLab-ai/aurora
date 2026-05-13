"""Test the upload flow end-to-end via headless browser."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
from playwright.sync_api import sync_playwright


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 900})
        page = ctx.new_page()

        console_msgs = []
        page_errors = []
        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        page.goto("http://127.0.0.1:8000", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=10000)

        # Find a NOAA test file.
        ROOT = Path(r"C:\Users\bgrut\Desktop\Aurora_QIE\.claude\worktrees\peaceful-easley-8514ec")
        noaa = next((p for p in (ROOT / "data" / "uploads").glob("*noaa*.csv")), None)
        if not noaa:
            print("!! no NOAA test file found; create one first")
            browser.close(); return
        print(f"Test file: {noaa.name} ({noaa.stat().st_size} bytes)")

        # Upload via the file input.
        page.set_input_files("#uploadInput", str(noaa))
        # Wait for upload to complete + auto-run to fire.
        page.wait_for_timeout(5000)

        ds = page.evaluate("document.querySelector('#datasetCard .name')?.textContent || ''")
        last = page.evaluate("window.__lastDataset || ''")
        banner = page.evaluate("document.getElementById('topRunningBanner').style.display")
        print(f"  dataset card: {ds}")
        print(f"  __lastDataset: {last.split(chr(92))[-1] if last else ''}")
        print(f"  banner display: {banner!r}")

        # Click RUN ANALYSIS again (to test the button independently).
        print("\n=== Click RUN ANALYSIS button ===")
        page.evaluate("document.getElementById('runAnalysisBtn').click()")
        page.wait_for_timeout(3500)
        ds2 = page.evaluate("document.querySelector('#datasetCard .name')?.textContent || ''")
        print(f"  dataset card after RUN ANALYSIS: {ds2}")

        print(f"\n=== Console (last 20) ===")
        for m in console_msgs[-20:]:
            try: print(" ", m[:240])
            except Exception: print(" ", m.encode('ascii','replace').decode()[:240])
        print(f"\n=== Page errors: {len(page_errors)} ===")
        for e in page_errors:
            try: print(" !!", e)
            except Exception: print(" !!", e.encode('ascii','replace').decode())

        browser.close()


if __name__ == "__main__":
    run()
