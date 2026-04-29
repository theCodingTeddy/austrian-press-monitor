from playwright.sync_api import sync_playwright

url = "https://www.derstandard.at/story/3000000305427/klare-sprache-wird-verstanden-stocker-ueber-eu-umgang-mit-trump"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    page.goto(url)
    page.wait_for_timeout(3000)
    print("Title:", page.title())
    
    # Let's print all <time> tags
    times = page.locator('time').element_handles()
    print(f"Found {len(times)} time tags")
    for t in times:
        print(f"Time class: {t.get_attribute('class')} datetime: {t.get_attribute('datetime')}")
        
    # Let's print all meta tags
    metas = page.locator('meta').element_handles()
    for m in metas:
        prop = m.get_attribute('property') or m.get_attribute('name')
        if prop and 'date' in prop.lower() or prop and 'time' in prop.lower():
            print(f"Meta {prop}: {m.get_attribute('content')}")
            
    browser.close()
