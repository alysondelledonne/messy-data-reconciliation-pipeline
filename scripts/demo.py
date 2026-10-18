"""Capture the README screenshots and walkthrough GIF from a running review app (D-044).

    uv run streamlit run app/streamlit_app.py        # in one terminal
    uv run --with playwright python scripts/demo.py   # in another

Needs a browser for Playwright: run `uv run --with playwright playwright install chromium`
once, or pass --chromium /path/to/chrome. Writes docs/img/*.png and docs/img/walkthrough.gif.
"""

import argparse
import asyncio
import io
from pathlib import Path

from PIL import Image
from playwright.async_api import Page, async_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "img"


async def pick_pack(page: Page, pack: str) -> None:
    await page.locator("[data-testid='stSidebar'] [data-testid='stSelectbox']").first.click()
    await page.get_by_role("option", name=pack).click()
    await page.wait_for_timeout(2500)


async def pick_source(page: Page, suffixes: tuple[str, ...]) -> None:
    radios = page.locator("[data-testid='stRadio'] label")
    for i in range(await radios.count()):
        label = await radios.nth(i).inner_text()
        if any(s in label for s in suffixes):
            await radios.nth(i).click()
            await page.wait_for_timeout(2500)
            return


async def main(url: str, chromium: str | None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frames: list[bytes] = []

    async def shot(page: Page, name: str) -> None:
        png = await page.screenshot()
        (OUT / f"{name}.png").write_bytes(png)
        frames.append(png)
        print(f"wrote docs/img/{name}.png")

    async with async_playwright() as p:
        browser = (
            await p.chromium.launch(executable_path=chromium)
            if chromium
            else (await p.chromium.launch())
        )
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(url)
        await page.wait_for_timeout(5000)
        await pick_pack(page, "invoices")
        await shot(page, "overview")

        await page.goto(f"{url}/review")
        await page.wait_for_timeout(4000)
        await pick_pack(page, "invoices")
        await pick_source(page, (".pdf",))
        await shot(page, "review_pdf")
        await pick_source(page, (".csv", ".xlsx"))
        await shot(page, "review_rows")
        await page.get_by_text("Name matches").click()
        await page.wait_for_timeout(2000)
        await shot(page, "name_matches")

        await page.goto(f"{url}/exceptions")
        await page.wait_for_timeout(4000)
        await pick_pack(page, "invoices")
        await page.get_by_text("Conflicts (").click()
        await page.wait_for_timeout(2000)
        await shot(page, "exceptions")

        await page.goto(f"{url}/guide")
        await page.wait_for_timeout(4000)
        await shot(page, "guide")
        await browser.close()

    images = [Image.open(io.BytesIO(f)).convert("RGB").resize((1080, 675)) for f in frames]
    images[0].save(
        OUT / "walkthrough.gif",
        save_all=True,
        append_images=images[1:],
        duration=2800,
        loop=0,
        optimize=True,
    )
    print("wrote docs/img/walkthrough.gif")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", default="http://localhost:8501")
    ap.add_argument("--chromium", help="path to a Chromium/Chrome binary")
    args = ap.parse_args()
    asyncio.run(main(args.url, args.chromium))
