"""
Playwright script to capture network requests from TRD tracking form.
"""
import asyncio
from playwright.async_api import async_playwright

TRD_URL = "https://platform.senior.com.br/logistica-tck/tms/tck-frontend/#/login/signup?tenant=ZEhKa2RISmhibk53YjNKMFpYTT0%3D"
NF_TEST = "12345"  # dummy tracking code

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        
        requests_made = []
        
        def on_request(req):
            if any(x in req.url for x in ['senior', 'tracking', 'tck', 'login']):
                requests_made.append(f"REQUEST: {req.method} {req.url}")
                if req.post_data:
                    requests_made.append(f"  BODY: {req.post_data}")
        
        def on_response(resp):
            if any(x in resp.url for x in ['senior', 'tracking', 'tck', 'login']):
                requests_made.append(f"RESPONSE: {resp.status} {resp.url}")
        
        page.on("request", on_request)
        page.on("response", on_response)
        
        print(f"Navigating to: {TRD_URL}")
        await page.goto(TRD_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        
        print("\n--- Page title/URL ---")
        print(await page.title())
        print(page.url)
        
        # Check cookies
        cookies = await ctx.cookies()
        print("\n--- Cookies ---")
        for c in cookies:
            print(f"  {c['name']} = {c['value'][:100]}")
        
        # Find input field
        print("\n--- Looking for input fields ---")
        inputs = await page.query_selector_all("input")
        for inp in inputs:
            name = await inp.get_attribute("name")
            placeholder = await inp.get_attribute("placeholder") 
            type_ = await inp.get_attribute("type")
            print(f"  input: name={name}, placeholder={placeholder}, type={type_}")
        
        # Try to fill the tracking code input and click Localizar
        try:
            tracking_input = await page.wait_for_selector('input[name="codigoTracking"]', timeout=10000)
            if tracking_input:
                print(f"\nFound codigoTracking input, filling with '{NF_TEST}'")
                await tracking_input.fill(NF_TEST)
                
                # Click Localizar button
                localizar_btn = await page.query_selector("button.btn-success")
                if localizar_btn:
                    print("Clicking Localizar button...")
                    await localizar_btn.click()
                    await asyncio.sleep(5)
                    print("Done waiting after click")
        except Exception as e:
            print(f"Error interacting: {e}")
        
        print("\n--- Network Requests captured ---")
        for r in requests_made:
            print(r)
        
        await asyncio.sleep(2)
        await browser.close()

asyncio.run(main())
