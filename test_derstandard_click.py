from playwright.sync_api import sync_playwright
import re

url = "https://www.derstandard.at/consent/tcf/story/3000000305427/klare-sprache-wird-verstanden-stocker-ueber-eu-umgang-mit-trump"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    page.goto(url)
    page.wait_for_timeout(2000)
    print("Initial title:", page.title())
    
    # Click Der Standard consent
    try:
        btn = page.locator('button').filter(has_text=re.compile(r"^Zustimmen$", re.IGNORECASE)).first
        if btn.count() > 0:
            print("Found Zustimmen button!")
            btn.click()
            page.wait_for_load_state('domcontentloaded')
            page.wait_for_timeout(2000)
    except Exception as e:
        print("Click error:", e)
        
    times = page.locator('time').element_handles()
    print(f"Found {len(times)} time tags now")
    for t in times:
        print(f"Time: {t.get_attribute('datetime')}")
        
    browser.close()
