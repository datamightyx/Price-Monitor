"""
Amazon product-page scraper (Playwright + BeautifulSoup).

Reality check (option 1, self-scraping):
- Amazon serves slightly different HTML per session/region/A-B test, so every
  field parser below tries several selectors and falls back to a regex over the
  whole page text. Treat the selectors as a starting point — re-verify against a
  live page if a field starts coming back empty.
- Without good (residential) proxies you WILL hit "Robot Check" / CAPTCHA pages.
  Set PROXY_SERVER (and PROXY_USERNAME / PROXY_PASSWORD) in the environment.
- This respects neither Keepa nor an official API; it is a direct scrape. Keep
  request rates low (see scraper.min_delay_sec) and use it responsibly.
"""
from __future__ import annotations

import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from .config import Config
from .models import Snapshot

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
]

CAPTCHA_MARKERS = (
    "Type the characters you see in this image",
    "Enter the characters you see below",
    "/errors/validateCaptcha",
    "To discuss automated access to Amazon data",
)

DEBUG_DIR = Path(os.getenv("DEBUG_DIR", "debug"))


def _save_debug(page, asin: str, reason: str) -> None:
    """Dump a full-page screenshot + HTML so you can see what Amazon returned."""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = DEBUG_DIR / f"{reason}_{asin}_{stamp}"
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        base.with_suffix(".html").write_text(page.content(), encoding="utf-8")
        print(f"  [{asin}] saved {reason} → {base}.png / .html")
    except Exception as e:  # noqa: BLE001
        print(f"  [{asin}] could not save debug artifacts: {e}")


# ── field parsers ────────────────────────────────────────────────────────────

def _text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def parse_title(soup: BeautifulSoup) -> Optional[str]:
    el = soup.select_one("#productTitle")
    return _text(el) or None


def _assemble_from_parts(container) -> tuple[Optional[float], str]:
    """Rebuild a price from Amazon's split spans:
       <span class=a-price-symbol>$</span>
       <span class=a-price-whole>19<span class=a-price-decimal>.</span></span>
       <span class=a-price-fraction>99</span>
    """
    whole_el = container.select_one(".a-price-whole")
    if not whole_el:
        return None, "USD"
    whole = re.sub(r"\D", "", whole_el.get_text())          # "19." -> "19"
    frac_el = container.select_one(".a-price-fraction")
    frac = re.sub(r"\D", "", frac_el.get_text()) if frac_el else ""
    if not whole:
        return None, "USD"
    sym_el = container.select_one(".a-price-symbol")
    sym = sym_el.get_text() if sym_el else ""
    currency = "GBP" if "£" in sym else "EUR" if "€" in sym else "USD"
    try:
        return float(f"{whole}.{frac or '0'}"), currency
    except ValueError:
        return None, currency


def parse_price(soup: BeautifulSoup) -> tuple[Optional[float], str]:
    # Prefer the actual buy-box price; :not(.a-text-price) skips the struck-through
    # "was" / list price so we don't grab the higher number.
    containers = [
        "#corePriceDisplay_desktop_feature_div .priceToPay",
        "#corePriceDisplay_desktop_feature_div span.a-price:not(.a-text-price)",
        "#corePrice_feature_div span.a-price:not(.a-text-price)",
        "#apex_desktop span.a-price:not(.a-text-price)",
        ".priceToPay",
        "span.a-price:not(.a-text-price)",
    ]
    for sel in containers:
        el = soup.select_one(sel)
        if not el:
            continue
        # 1) full string in .a-offscreen if present
        off = el.select_one("span.a-offscreen")
        if off and off.text.strip():
            price, cur = _money(off.text)
            if price is not None:
                return price, cur
        # 2) fall back to assembling from whole + fraction spans
        price, cur = _assemble_from_parts(el)
        if price is not None:
            return price, cur
    # last resort: any whole/fraction pair anywhere on the page
    return _assemble_from_parts(soup)


def _money(raw: str) -> tuple[Optional[float], str]:
    raw = raw.strip()
    currency = "USD"
    if "£" in raw:
        currency = "GBP"
    elif "€" in raw:
        currency = "EUR"
    elif "$" in raw:
        currency = "USD"
    m = re.search(r"[\d.,]+", raw.replace("\u202f", ""))
    if not m:
        return None, currency
    num = m.group(0)
    # Normalise: drop thousands separators, keep last separator as decimal.
    if "," in num and "." in num:
        num = num.replace(",", "")
    elif "," in num:
        # European style "21,99" -> "21.99"
        num = num.replace(",", ".")
    try:
        return float(num), currency
    except ValueError:
        return None, currency


def parse_rating(soup: BeautifulSoup) -> Optional[float]:
    for sel in ["#acrPopover", "#averageCustomerReviews .a-icon-alt",
                "span[data-hook='rating-out-of-text']", "i.a-icon-star span.a-icon-alt"]:
        el = soup.select_one(sel)
        raw = (el.get("title") if el and el.has_attr("title") else _text(el)) if el else ""
        m = re.search(r"([0-5](?:\.\d)?)\s*out of\s*5", raw)
        if m:
            return float(m.group(1))
    return None


def parse_reviews(soup: BeautifulSoup) -> Optional[int]:
    for sel in ["#acrCustomerReviewText", "[data-hook='total-review-count']"]:
        el = soup.select_one(sel)
        m = re.search(r"([\d.,]+)", _text(el))
        if m:
            try:
                return int(m.group(1).replace(",", "").replace(".", ""))
            except ValueError:
                pass
    return None


def _parse_ranks_from_el(el) -> list[tuple[int, str]]:
    """Extract all #N in Category pairs from a BeautifulSoup element."""
    ranks: list[tuple[int, str]] = []
    # Prefer <li> items (table-format details page)
    items = el.select("li") or [el]
    for item in items:
        m = re.search(r"#([\d,]+)\s+in\s+([^#(\n]+)", _text(item))
        if m:
            ranks.append((int(m.group(1).replace(",", "")),
                          m.group(2).strip().rstrip("(").strip()))
    return ranks


def parse_bsr(soup: BeautifulSoup) -> tuple[
        Optional[int], Optional[str], Optional[int], Optional[str]]:
    """Return (bsr, bsr_category, bsr2, bsr_category2).

    Parses both the primary category rank and the first subcategory rank that
    Amazon lists in the product details section.  Also handles the '#1 Best
    Seller' badge shown near the title.
    """
    # ── Method 1: <th>Best Sellers Rank</th> → adjacent <td> ──
    for th in soup.find_all("th"):
        if "Best Sellers Rank" in _text(th):
            td = th.find_next_sibling("td")
            if td:
                ranks = _parse_ranks_from_el(td)
                if ranks:
                    r1, c1 = ranks[0]
                    r2, c2 = ranks[1] if len(ranks) > 1 else (None, None)
                    return r1, c1, r2, c2

    # ── Method 2: bullet-list format (<span class="a-text-bold">) ──
    for bold in soup.find_all("span", class_="a-text-bold"):
        if "Best Sellers Rank" in _text(bold):
            container = bold.find_parent("li") or bold.find_parent("div")
            if container:
                ranks = _parse_ranks_from_el(container)
                if ranks:
                    r1, c1 = ranks[0]
                    r2, c2 = ranks[1] if len(ranks) > 1 else (None, None)
                    return r1, c1, r2, c2

    # ── Method 3: regex on known detail-section blocks ──
    candidates = []
    for sel in ["#productDetails_detailBullets_sections1",
                "#detailBulletsWrapper_feature_div",
                "#productDetails_db_sections", "#prodDetails"]:
        block = soup.select_one(sel)
        if block:
            candidates.append(_text(block))
    candidates.append(soup.get_text(" ", strip=True))

    for text in candidates:
        pos = text.find("Best Sellers Rank")
        if pos == -1:
            continue
        vicinity = text[pos: pos + 400]
        pairs = re.findall(r"#([\d,]+)\s+in\s+([^#(\n]+)", vicinity)
        if pairs:
            r1 = int(pairs[0][0].replace(",", ""))
            c1 = pairs[0][1].strip().rstrip("(").strip()
            r2 = int(pairs[1][0].replace(",", "")) if len(pairs) > 1 else None
            c2 = pairs[1][1].strip().rstrip("(").strip() if len(pairs) > 1 else None
            return r1, c1, r2, c2

    # ── Method 4: '#1 Best Seller' badge near the title ──
    for badge_sel in ["#acBadge_feature_div",
                      "[data-feature-name='acBadge']",
                      ".ac-badge-wrapper",
                      "#bestSellerBadge_feature_div"]:
        el = soup.select_one(badge_sel)
        if not el:
            continue
        t = _text(el)
        if "#1" not in t:
            continue
        cat_m = re.search(r"in\s+(.+)", t, re.IGNORECASE)
        category = cat_m.group(1).strip() if cat_m else None
        return 1, category, None, None

    return None, None, None, None


def parse_coupon(soup: BeautifulSoup) -> Optional[str]:
    found_badge = False  # tracks that a coupon badge exists even without a value
    for sel in [".couponBadge", ".newCouponBadge", "#couponBadgeRegularVpc",
                "#promoPriceBlockMessage_feature_div", "label[id*=couponText]"]:
        el = soup.select_one(sel)
        t = _text(el)
        if not t or "coupon" not in t.lower():
            continue
        found_badge = True
        pct = re.search(r"(\d+%)", t)
        amt = re.search(r"([£$€]\s?\d[\d.,]*)", t)
        if pct:
            return f"{pct.group(1)} off"
        if amt:
            return f"{amt.group(1)} off"
    # text fallback — catches "Apply 10% coupon" checkbox labels
    m = re.search(r"Apply\s+(\d+%|[£$€]\s?\d[\d.,]*)\s+coupon", soup.get_text(" "))
    if m:
        return f"{m.group(1)} off"
    # return generic only if badge was found but had no parseable value
    return "coupon" if found_badge else None


_SNS_IDS = {"subscriptionPrice", "snsDetailPagePrice",
            "sns-base-price", "sns-tiered-price"}


def _buybox_savings_pct(soup: BeautifulSoup) -> Optional[str]:
    """Return the buy-box discount % (e.g. '10%'), skipping S&S-only badges."""
    for el in soup.select(".apex-savings-percentage"):
        if any(p.get("id") in _SNS_IDS for p in el.parents):
            continue
        m = re.search(r"\d+%", _text(el))
        if m:
            return m.group(0)
    return None


def _has_ltd_badge(soup: BeautifulSoup) -> bool:
    """True if Amazon's 'Limited time deal' badge is present on the page."""
    for sel in ["#dealBadge", ".dealBadge", "#dealBadge_feature_div",
                "[data-feature-name='dealBadge']"]:
        el = soup.select_one(sel)
        if not el:
            continue
        # Countdown timer elements — always mean LTD
        if el.select_one("[data-target-time], .detailpage-dealBadge-countdown-timer"):
            return True
        # Screen-reader labels with NO_OF_* placeholders also signal LTD
        if "NO_OF_" in el.decode_contents():
            return True
        if "limited time" in _text(el).lower():
            return True
    return False


def parse_ltd(soup: BeautifulSoup) -> Optional[str]:
    """Return the LTD discount % (e.g. '-27%') when a 'Limited time deal' badge
    is present, or 'yes' if the badge is there but no % can be parsed."""
    if not _has_ltd_badge(soup):
        return None
    pct = _buybox_savings_pct(soup)
    return f"-{pct}" if pct else "yes"


def parse_stp(soup: BeautifulSoup) -> Optional[str]:
    """Return the strike-through price discount % for regular markdowns.
    Returns None when the discount is driven by an LTD badge (captured in ltd)."""
    if _has_ltd_badge(soup):
        return None
    # Non-LTD deal badges (e.g. Prime-exclusive, Best Deal)
    for sel in ["#dealBadge", ".dealBadge", "#dealBadge_feature_div",
                "[data-feature-name='dealBadge']"]:
        el = soup.select_one(sel)
        if not el:
            continue
        t = _text(el)
        m = re.search(r"-?\s?(\d+%)", t)
        if m:
            return f"-{m.group(1)}"
    # Regular buy-box markdown % (no badge, just a struck-out list price)
    pct = _buybox_savings_pct(soup)
    if pct:
        return f"-{pct}"
    return None


def parse_brand(soup: BeautifulSoup) -> Optional[str]:
    # Method 1: bylineInfo — "Visit the Nike Store" or "Brand: Nike"
    el = soup.select_one("#bylineInfo")
    if el:
        t = _text(el)
        m = re.search(r"Brand[:\s]+(.+?)$", t, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        m = re.search(r"Visit the (.+?) Store", t, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # Fallback: strip known prefixes
        clean = re.sub(r"^(Visit the\s+|Brand:\s*|by\s+)", "", t, flags=re.IGNORECASE).strip()
        if clean:
            return clean
    # Method 2: product details table <th>Brand</th> → <td>
    for th in soup.find_all("th"):
        if _text(th).strip().lower() == "brand":
            td = th.find_next_sibling("td")
            if td:
                v = _text(td).strip()
                if v:
                    return v
    # Method 3: product overview bullet (.po-brand)
    el = soup.select_one(".po-brand .a-span9 span")
    if el:
        v = _text(el).strip()
        if v:
            return v
    # Method 4: #brand element
    el = soup.select_one("#brand")
    if el:
        v = _text(el).strip()
        if v:
            return v
    return None


def parse_in_stock(soup: BeautifulSoup) -> bool:
    el = soup.select_one("#availability")
    t = _text(el).lower()
    if not t:
        return True  # assume in stock if section absent
    return not any(k in t for k in ("unavailable", "out of stock", "currently"))


def parse_page(asin: str, group: str, html: str) -> Snapshot:
    soup = BeautifulSoup(html, "lxml")
    price, currency = parse_price(soup)
    bsr, bsr_cat, bsr2, bsr_cat2 = parse_bsr(soup)
    return Snapshot(
        asin=asin,
        group=group,
        product=parse_title(soup),
        brand=parse_brand(soup),
        price=price,
        currency=currency,
        bsr=bsr,
        bsr_category=bsr_cat,
        bsr2=bsr2,
        bsr_category2=bsr_cat2,
        rating=parse_rating(soup),
        reviews=parse_reviews(soup),
        coupon=parse_coupon(soup),
        stp=parse_stp(soup),
        ltd=parse_ltd(soup),
        in_stock=parse_in_stock(soup),
    )


# ── browser driver ───────────────────────────────────────────────────────────

def _proxy_cfg() -> Optional[dict]:
    server = os.getenv("PROXY_SERVER")
    if not server:
        return None
    cfg = {"server": server}
    if os.getenv("PROXY_USERNAME"):
        cfg["username"] = os.getenv("PROXY_USERNAME")
        cfg["password"] = os.getenv("PROXY_PASSWORD", "")
    return cfg


class Scraper:
    """Context manager wrapping a single Playwright browser for a whole run."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._pw = None
        self._browser = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.cfg.scraper.headless,
            proxy=_proxy_cfg(),
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        return self

    def __exit__(self, *exc):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def _set_us_zip(self, page, zip_code: str) -> None:
        """Set delivery zip so Amazon shows US buy-box prices.

        Tries an AJAX call first (fast, no UI); falls back to clicking through
        the "Deliver to" popup with several selector variants in case Amazon
        A/B-tests the markup.
        """
        page.goto(f"https://{self.cfg.domain}/",
                  timeout=self.cfg.scraper.timeout_ms,
                  wait_until="domcontentloaded")
        page.wait_for_timeout(1_500)

        # ── 1. Direct AJAX call (most reliable, no UI interaction needed) ──
        try:
            status = page.evaluate(
                """async (zipCode) => {
                const body = new URLSearchParams({
                    locationType: 'LOCATION_INPUT',
                    zipCode: zipCode,
                    storeContext: 'generic',
                    deviceType: 'web',
                    pageType: 'Gateway',
                    actionSource: 'glow',
                });
                const r = await fetch('/gp/delivery/ajax/address-change.html', {
                    method: 'POST',
                    credentials: 'include',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: body.toString(),
                });
                return r.status;
            }""",
                zip_code,
            )
            if status == 200:
                print(f"  [location] zip set via AJAX -> {zip_code}")
                return
            print(f"  [location] AJAX returned {status}, trying UI...")
        except Exception as e:  # noqa: BLE001
            print(f"  [location] AJAX failed ({e}), trying UI...")

        # ── 2. UI fallback: click "Deliver to" popup ──
        try:
            for btn_sel in [
                "#nav-global-location-popover-link",
                "#nav-global-location-slot a",
                "[data-nav-ref='nav_cs_change_address']",
            ]:
                try:
                    page.click(btn_sel, timeout=3_000)
                    break
                except PWTimeout:
                    continue

            page.wait_for_selector("#GLUXZipUpdateInput", timeout=6_000)
            page.fill("#GLUXZipUpdateInput", zip_code)

            for apply_sel in [
                "[data-action='GLUXPostalUpdateAction']",
                "#GLUXZipUpdate input[type='submit']",
                "input.a-button-input[aria-labelledby*='GLUXZipUpdate']",
                "span.a-button-inner > input[type='submit']",
            ]:
                try:
                    page.click(apply_sel, timeout=3_000)
                    break
                except PWTimeout:
                    continue

            page.wait_for_timeout(1_500)
            print(f"  [location] zip set via UI -> {zip_code}")
        except Exception as e:  # noqa: BLE001
            print(f"  [location] UI fallback also failed: {e}")

    def _fetch_html(self, url: str, asin: str = "page") -> str:
        ctx = self._browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        try:
            if self.cfg.scraper.zip_code:
                self._set_us_zip(page, self.cfg.scraper.zip_code)
            page.goto(url, timeout=self.cfg.scraper.timeout_ms,
                      wait_until="domcontentloaded")
            try:
                page.wait_for_selector("#productTitle",
                                       timeout=self.cfg.scraper.timeout_ms)
            except PWTimeout:
                pass  # CAPTCHA or layout change — caller inspects content
            # Amazon's apex pricing engine inserts the .priceToPay container
            # immediately but fills .a-price-whole with the number only after
            # an XHR/JS round-trip.  Wait until that span has actual digit
            # content, not just the empty placeholder.
            try:
                page.wait_for_function(
                    "() => { const el = document.querySelector("
                    "'.priceToPay .a-price-whole,"
                    " #corePriceDisplay_desktop_feature_div .a-price-whole,"
                    " #apex_desktop .a-price-whole');"
                    " return el && el.textContent.replace(/\\D/g, '').length > 0; }",
                    timeout=10_000,
                )
            except PWTimeout:
                pass  # price may not exist (add-on item, out-of-stock, etc.)
            html = page.content()
            # Save a screenshot + HTML when blocked (or when DEBUG_SAVE is set),
            # so you can literally see what Amazon returned.
            blocked = any(m in html for m in CAPTCHA_MARKERS)
            if blocked or os.getenv("DEBUG_SAVE"):
                _save_debug(page, asin, "captcha" if blocked else "debug")
            return html
        finally:
            ctx.close()

    def scrape_asin(self, asin: str, group: str) -> Snapshot:
        url = f"https://{self.cfg.domain}/dp/{asin}?language={self.cfg.language}"
        last_err = "unknown"
        for attempt in range(1, self.cfg.scraper.retries + 1):
            try:
                html = self._fetch_html(url, asin)
                if any(marker in html for marker in CAPTCHA_MARKERS):
                    last_err = "captcha"
                    print(f"  [{asin}] CAPTCHA hit (attempt {attempt})")
                    time.sleep(self.cfg.scraper.min_delay_sec * attempt)
                    continue
                snap = parse_page(asin, group, html)
                if snap.product is None and snap.price is None:
                    last_err = "empty-parse"
                    print(f"  [{asin}] empty parse (attempt {attempt})")
                    continue
                if snap.price is None and snap.product is not None:
                    last_err = "price-missing"
                    print(f"  [{asin}] title found but price missing (attempt {attempt})")
                    time.sleep(self.cfg.scraper.min_delay_sec)
                    continue
                return snap
            except Exception as e:  # noqa: BLE001
                last_err = str(e)[:120]
                print(f"  [{asin}] error: {last_err} (attempt {attempt})")
                time.sleep(self.cfg.scraper.min_delay_sec)
        return Snapshot(asin=asin, group=group, error=last_err, in_stock=False)


def scrape_groups(cfg: Config) -> dict[str, list[Snapshot]]:
    """Scrape every ASIN of every group. Returns {group_name: [Snapshot, ...]}."""
    results: dict[str, list[Snapshot]] = {}
    with Scraper(cfg) as sc:
        for group in cfg.groups:
            snaps = []
            asins = group.all_asins
            for i, asin in enumerate(asins):
                print(f"[{group.name}] scraping {asin} ...")
                snaps.append(sc.scrape_asin(asin, group.name))
                if i < len(asins) - 1:
                    time.sleep(random.uniform(cfg.scraper.min_delay_sec,
                                              cfg.scraper.max_delay_sec))
            results[group.name] = snaps
    return results
