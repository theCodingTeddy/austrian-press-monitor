from playwright.sync_api import sync_playwright
import re

url = "https://news.google.com/rss/articles/CBMirwFBVV95cUxNVHdzZ0dROXQxbEt3WnJrd0hib2Y2eVRBWFJ1dk1sckY2anREUHVVYzhGSUtVdGs0VTBUc2lwb2Zfb2l6bWxsTzJzcXAtT0ZTSU5Yc0JabDAtZG1Jb21mTWx5Qkdtb2VHcHdCU0RrZUZxa2JfVkx0UDJiSm9CTHFLMFFsUDVpWmxYb3hmeTQ0WTVZc0hoc3B0a1J2MjJORG5fVHZ6UFA2Z0I5TlIzUUNR?oc=5"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(locale="en-US")
    page = context.new_page()
    page.goto(url)
    
    # Check if we hit consent
    if "consent.google.com" in page.url or "Before you continue" in page.title():
        print("Hit consent wall, attempting to click accept...")
        try:
            # The button might be deeply nested or have aria-labels
            btn = page.locator('button, [role="button"]').filter(has_text=re.compile(r"Accept all", re.IGNORECASE)).first
            if btn.count() > 0:
                print("Found Accept all button!")
                btn.click()
                page.wait_for_selector('body', timeout=10000)
                page.wait_for_load_state('networkidle', timeout=10000)
            else:
                print("Button not found. HTML snippet:")
                print(page.content()[:1000])
        except Exception as e:
            print("Error clicking:", e)
            
    print("New Title:", page.title())
    print("New URL:", page.url)
    browser.close()
