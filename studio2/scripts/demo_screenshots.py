import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

DOCS = Path(__file__).resolve().parents[2] / "docs"
TABS = {
    "Модели": "day35_models.png",
    "RAG": "day35_rag.png",
    "MCP": "day35_mcp.png",
    "Задачи": "day35_tasks.png",
}


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1600, "height": 900})
        await page.goto("http://localhost:5174", wait_until="networkidle")
        for tab, filename in TABS.items():
            await page.click(f"text={tab}")
            await page.wait_for_timeout(2500)
            await page.screenshot(path=str(DOCS / filename), full_page=False)
            print("saved", filename)
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
