"""End-to-end test of the Aurora frontend run pipeline."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
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
        if page_errors:
            print(f"!! page errors at load: {page_errors}")
            browser.close(); return

        def click_demo_chip_by_id(demo_id):
            page.evaluate("document.getElementById('demoBtn').click()")
            page.wait_for_timeout(900)
            ok = page.evaluate(f"""(() => {{
                const c = document.querySelector('.demo-chip[data-demo-id="{demo_id}"]');
                if (!c) return false;
                c.click();
                return true;
            }})()""")
            return ok

        def get_dataset_name():
            return page.evaluate(
                "document.querySelector('#datasetCard .name')?.textContent || ''"
            )

        # 1. Click factory_bearing
        print("=== Test 1: click factory_bearing ===")
        ok = click_demo_chip_by_id("factory_bearing")
        print(f"  click ok: {ok}")
        page.wait_for_timeout(3500)
        ds = get_dataset_name()
        print(f"  dataset card now: {ds}")

        # 2. Click patient_cohort right after
        print("\n=== Test 2: click patient_cohort right after ===")
        ok = click_demo_chip_by_id("patient_cohort")
        print(f"  click ok: {ok}")
        page.wait_for_timeout(3500)
        ds = get_dataset_name()
        print(f"  dataset card now: {ds}")
        if "patient" not in ds.lower():
            print("  !! UI did NOT update to patient_cohort run")

        # 3. Click factory_bearing again (should be cached)
        print("\n=== Test 3: click factory_bearing again (cache hit) ===")
        ok = click_demo_chip_by_id("factory_bearing")
        print(f"  click ok: {ok}")
        page.wait_for_timeout(2500)
        ds = get_dataset_name()
        print(f"  dataset card now: {ds}")
        if "factory" not in ds.lower() and "bearing" not in ds.lower():
            print("  !! UI did NOT update to factory_bearing run")

        # 4. Click climate_buoy
        print("\n=== Test 4: click climate_buoy ===")
        ok = click_demo_chip_by_id("climate_buoy")
        print(f"  click ok: {ok}")
        page.wait_for_timeout(2500)
        ds = get_dataset_name()
        print(f"  dataset card now: {ds}")
        if "climate" not in ds.lower() and "buoy" not in ds.lower():
            print("  !! UI did NOT update to climate_buoy run")

        # Summary
        print("\n=== Console msgs ===")
        for m in console_msgs[-25:]:
            try: print(" ", m[:240])
            except Exception: print(" ", m.encode('ascii','replace').decode()[:240])
        print(f"\n=== Page errors: {len(page_errors)} ===")
        for e in page_errors:
            try: print(" !!", e)
            except Exception: print(" !!", e.encode('ascii','replace').decode())

        browser.close()


if __name__ == "__main__":
    run()
