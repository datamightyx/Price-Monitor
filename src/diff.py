"""Compute day-over-day changes per ASIN."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Snapshot


@dataclass
class Change:
    asin: str
    product: str
    kind: str          # price | bsr | coupon | deal | launched | stock | rating
    text: str          # human-readable line for the Slack "Changes" section
    emoji: str = ""    # leading emoji


def _fmt_price(s: Snapshot) -> str:
    if s.price is None:
        return "—"
    sym = {"USD": "$", "GBP": "£", "EUR": "€"}.get(s.currency, "")
    return f"{sym}{s.price:g}"


def diff_group(today: list[Snapshot], baseline: dict[str, Snapshot]) -> list[Change]:
    changes: list[Change] = []
    for cur in today:
        name = cur.product or cur.asin
        prev = baseline.get(cur.asin)

        # Newly tracked / launched
        if prev is None:
            if cur.error is None:
                changes.append(Change(cur.asin, name, "launched",
                                      f"*{name}* ({cur.asin}) — now tracked"
                                      + (f" — launched a {cur.deal}" if cur.deal else ""),
                                      "🚀"))
            continue

        if cur.error is not None:
            continue  # don't emit noise for a failed fetch

        # Price
        if cur.price is not None and prev.price not in (None, cur.price):
            delta = cur.price - prev.price
            pct = (delta / prev.price * 100) if prev.price else 0
            arrow = "dropped" if delta < 0 else "rose"
            emoji = "📉" if delta < 0 else "📈"
            changes.append(Change(
                cur.asin, name, "price",
                f"*{name}* — Price {arrow} {_fmt_price(prev)} → {_fmt_price(cur)} "
                f"({pct:+.1f}%)", emoji))

        # Coupon appeared / disappeared / changed
        if (cur.coupon or None) != (prev.coupon or None):
            if cur.coupon and not prev.coupon:
                changes.append(Change(cur.asin, name, "coupon",
                                      f"*{name}* — new coupon: {cur.coupon}", "🎟️"))
            elif prev.coupon and not cur.coupon:
                changes.append(Change(cur.asin, name, "coupon",
                                      f"*{name}* — coupon removed ({prev.coupon})", "✖️"))
            else:
                changes.append(Change(cur.asin, name, "coupon",
                                      f"*{name}* — coupon changed {prev.coupon} → {cur.coupon}", "🎟️"))

        # Deal appeared / disappeared / changed
        if (cur.deal or None) != (prev.deal or None):
            if cur.deal and not prev.deal:
                changes.append(Change(cur.asin, name, "deal",
                                      f"*{name}* — new deal: {cur.deal}", "🔥"))
            elif prev.deal and not cur.deal:
                changes.append(Change(cur.asin, name, "deal",
                                      f"*{name}* — deal ended ({prev.deal})", "🏁"))
            else:
                changes.append(Change(cur.asin, name, "deal",
                                      f"*{name}* — deal changed {prev.deal} → {cur.deal}", "🔥"))

        # Stock flip
        if cur.in_stock != prev.in_stock:
            if cur.in_stock:
                changes.append(Change(cur.asin, name, "stock",
                                      f"*{name}* — back in stock", "📦"))
            else:
                changes.append(Change(cur.asin, name, "stock",
                                      f"*{name}* — out of stock", "🚫"))

    return changes


def active_promotions(today: list[Snapshot]) -> list[str]:
    """Bullet lines describing every product currently running a promo."""
    out = []
    for s in today:
        name = s.product or s.asin
        if s.deal:
            out.append(f"🔥 {name} — {s.deal}")
        if s.coupon:
            out.append(f"🎟️ {name} — {s.coupon} coupon")
    return out
