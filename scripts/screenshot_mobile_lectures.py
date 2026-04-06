"""Re-screenshot teacher lectures page in light theme for mobile."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://localhost:8000"
OUT = Path(__file__).resolve().parent.parent / "mobile_screens"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(
            viewport={"width": 430, "height": 932},
            device_scale_factor=2,
        )
        page = await ctx.new_page()

        # Force light theme via localStorage before navigating
        await page.goto(BASE)
        await page.evaluate("localStorage.setItem('ui-theme', 'light')")

        # Login as teacher
        await page.goto(f"{BASE}/login")
        await page.fill('input[name="login"]', "teacher1@example.com")
        await page.fill('input[name="password"]', "Teacher123!")
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")

        # Go to teacher lectures
        await page.goto(f"{BASE}/teacher/lectures")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(500)

        await page.screenshot(path=str(OUT / "05_teacher_lectures_mobile.png"))
        print("Saved 05_teacher_lectures_mobile.png")

        await browser.close()

asyncio.run(main())
