"""Takes the screenshot the local ODeX's login page shows: the real ODeX India login
page, as anyone sees it before logging in. Nothing is typed or sent.

    uv run python scripts/capture_odex_login.py      (make odex-login-screen)

It saves assets/private/odex/login.png (kept out of git, like the guide's
screenshots), then checks that the page's fields are still where
local_portals/odex_screens.py puts its live ones, and says which moved if any did.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from playwright.sync_api import sync_playwright  # noqa: E402

from local_portals.odex_screens import LOGIN, LOGIN_FIELDS  # noqa: E402
from local_portals.settings import screens_dir  # noqa: E402

URL = "https://in.odexglobal.com/"
# How far, in the screenshot's pixels, a real field may sit from its live stand-in.
SLACK = 4


def main() -> int:
    target = screens_dir() / LOGIN.file
    target.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": LOGIN.width, "height": LOGIN.height})
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_selector("#loginId", timeout=30000)
        page.wait_for_timeout(1500)  # the banners on the right settle
        page.screenshot(path=str(target))
        found = {}
        for selector in LOGIN_FIELDS:
            box = page.locator(selector).bounding_box()
            if box:
                found[selector] = (round(box["x"]), round(box["y"]), round(box["x"] + box["width"]), round(box["y"] + box["height"]))
        browser.close()
    print(f"Saved {target}")
    moved = {
        selector: box for selector, box in found.items()
        if any(abs(a - b) > SLACK for a, b in zip(box, LOGIN_FIELDS[selector]))
    }
    missing = [selector for selector in LOGIN_FIELDS if selector not in found]
    for selector in missing:
        print(f"ODeX's login page has no {selector} now: local_portals/odex_screens.py needs a look.")
    for selector, box in moved.items():
        print(f"{selector} has moved to {box}: update LOGIN_FIELDS in local_portals/odex_screens.py.")
    if not moved and not missing:
        print("Every field is where the local login page puts its live one.")
    return 1 if moved or missing else 0


if __name__ == "__main__":
    sys.exit(main())
