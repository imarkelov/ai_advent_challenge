import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / ".superpowers" / "sdd" / "day35-qa-lead-orchestrator" / "video"
OUT = ROOT / "docs" / "day35_demo.webm"


async def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1440, "height": 820},
            record_video_dir=str(RAW),
            record_video_size={"width": 1440, "height": 820},
        )
        page = await context.new_page()
        await page.goto("http://localhost:5174", wait_until="networkidle")

        await page.click("text=Модели")
        await page.wait_for_timeout(1800)
        await page.get_by_role("button", name="Проверить").first.click()
        await page.wait_for_timeout(1500)

        await page.click("text=RAG")
        await page.wait_for_timeout(1400)
        await page.click("text=Переиндексировать")
        await page.wait_for_timeout(1200)
        await page.click("text=kb/patterns.md")
        await page.wait_for_timeout(1600)

        await page.click("text=MCP")
        await page.wait_for_timeout(1400)

        await page.click("text=Задачи")
        await page.wait_for_timeout(1400)

        await page.click("text=Pipeline")
        await page.fill('input[placeholder="TC-4217"]', "TC-4217")
        await page.wait_for_timeout(600)
        await page.get_by_role("button", name="▶ Запустить").click()
        await page.wait_for_timeout(9000)

        video = page.video
        path = await video.path() if video else None
        await context.close()
        await browser.close()
        if path:
            import shutil
            shutil.copyfile(path, OUT)
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
