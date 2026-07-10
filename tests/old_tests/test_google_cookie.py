from playwright.sync_api import sync_playwright

url = "https://news.google.com/rss/articles/CBMirwFBVV95cUxNVHdzZ0dROXQxbEt3WnJrd0hib2Y2eVRBWFJ1dk1sckY2anREUHVVYzhGSUtVdGs0VTBUc2lwb2Zfb2l6bWxsTzJzcXAtT0ZTSU5Yc0JabDAtZG1Jb21mTWx5Qkdtb2VHcHdCU0RrZUZxa2JfVkx0UDJiSm9CTHFLMFFsUDVpWmxYb3hmeTQ0WTVZc0hoc3B0a1J2MjJORG5fVHZ6UFA2Z0I5TlIzUUNR?oc=5"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    # Important: Set the locale/lang so it matches a specific cookie language if needed, 
    # but the cookie value usually works universally.
    context = browser.new_context()
    context.add_cookies([
        {
            'name': 'CONSENT',
            'value': 'YES+cb.20210418-17-p0.en+FX+808',
            'domain': '.google.com',
            'path': '/'
        }
    ])
    page = context.new_page()
    page.goto(url, wait_until='domcontentloaded')
    page.wait_for_timeout(3000)
    print("Title:", page.title())
    print("URL:", page.url)
    browser.close()
