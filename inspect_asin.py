"""
Open ONE ASIN in a visible browser to see exactly what Amazon returns
(product page vs. "Robot Check" CAPTCHA). Saves a screenshot + HTML to debug/.

Usage:
    python inspect_asin.py B07Y7H3FHB
    python inspect_asin.py B07Y7H3FHB --headless     # no window, just save files

Proxy is read from PROXY_SERVER / PROXY_USERNAME / PROXY_PASSWORD if set.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.config import load_config
from src.scraper import CAPTCHA_MARKERS, USER_AGENTS, _proxy_cfg, Scraper

DEBUG_DIR = Path(os.getenv("DEBUG_DIR", "debug"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asin")
    ap.add_argument("--headless", action="store_true",
                    help="run without a window (still saves screenshot + HTML)")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    url = f"https://{cfg.domain}/dp/{args.asin}?language={cfg.language}"
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Opening {url}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=args.headless,
            proxy=_proxy_cfg(),
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            user_agent=USER_AGENTS[0], locale="en-US",
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        # Set US delivery zip if configured (same logic as the real scraper)
        if cfg.scraper.zip_code:
            sc = Scraper(cfg)
            sc._browser = browser
            sc._set_us_zip(page, cfg.scraper.zip_code)

        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        html = page.content()
        blocked = any(m in html for m in CAPTCHA_MARKERS)
        title = page.title()
        print(f"Page title : {title}")
        print(f"CAPTCHA?    : {'YES — Robot Check / blocked' if blocked else 'no'}")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = DEBUG_DIR / f"inspect_{args.asin}_{stamp}"
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        base.with_suffix(".html").write_text(html, encoding="utf-8")
        print(f"Saved       : {base}.png  and  {base}.html")

        if not args.headless:
            try:
                input("\nBrowser is open. Inspect it (you can even solve the CAPTCHA "
                      "by hand), then press Enter here to close...")
            except EOFError:
                page.wait_for_timeout(15000)
        browser.close()


if __name__ == "__main__":
    main()
