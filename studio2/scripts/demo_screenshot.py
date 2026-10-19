import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parents[2] / "docs" / "day35_demo.png"


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1600, "height": 900})
        await page.goto("http://localhost:5174", wait_until="networkidle")
        await page.fill('input[placeholder="TC-4217"]', "TC-4217")
        await page.get_by_role("button", name="▶ Запустить").click()
        await page.wait_for_timeout(6000)
        await page.click("text=Pipeline")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(OUT), full_page=False)
        await browser.close()
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
