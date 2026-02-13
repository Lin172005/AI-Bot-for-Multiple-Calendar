import asyncio
from playwright.async_api import async_playwright

async def generate_auth():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://accounts.google.com/signin")
        print("⚡ Log in manually to your bot account in the browser window...")
        await asyncio.sleep(120)  # Give yourself 2 minutes to log in
        await context.storage_state(path="auth.json")
        print("✅ Auth saved to auth.json")
        await browser.close()

asyncio.run(generate_auth())