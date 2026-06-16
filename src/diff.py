"""Compute day-over-day changes per ASIN."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Snapshot


@dataclass
class Change:
    asin: str
    product: str
    kind: str          # price | bsr | coupon | stp | launched | stock | rating
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
                promos = []
                if cur.coupon:
                    pct = cur.coupon.lstrip("-\u2212").rstrip("%").strip()
                    promos.append(f"a {pct}%-off coupon" if pct.isdigit() else f"a coupon ({cur.coupon})")
                if cur.stp:
                    pct = cur.stp.lstrip("-\u2212").rstrip("%").strip()
                    art = "an" if pct.startswith("8") else "a"
                    promos.append(f"{art} {pct}% STP")
                if cur.ltd:
                    pct = cur.ltd.lstrip("-\u2212").rstrip("%").strip()
                    art = "an" if pct.startswith("8") else "a"
                    promos.append(f"{art} {pct}% LTD")
                promo_note = " — launched " + ", ".join(promos) if promos else ""
                changes.append(Change(cur.asin, name, "launched",
                                      f"*{name}* ({cur.asin}) — now tracked{promo_note}",
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

        # STP appeared / disappeared / changed
        if (cur.stp or None) != (prev.stp or None):
            if cur.stp and not prev.stp:
                changes.append(Change(cur.asin, name, "stp",
                                      f"*{name}* — new STP: {cur.stp}", "🔥"))
            elif prev.stp and not cur.stp:
                changes.append(Change(cur.asin, name, "stp",
                                      f"*{name}* — STP ended ({prev.stp})", "🏁"))
            else:
                changes.append(Change(cur.asin, name, "stp",
                                      f"*{name}* — STP changed {prev.stp} → {cur.stp}", "🔥"))

        # LTD appeared / disappeared / changed
        if (cur.ltd or None) != (prev.ltd or None):
            if cur.ltd and not prev.ltd:
                changes.append(Change(cur.asin, name, "ltd",
                                      f"*{name}* — new LTD: {cur.ltd}", "⚡"))
            elif prev.ltd and not cur.ltd:
                changes.append(Change(cur.asin, name, "ltd",
                                      f"*{name}* — LTD ended ({prev.ltd})", "🏁"))
            else:
                changes.append(Change(cur.asin, name, "ltd",
                                      f"*{name}* — LTD changed {prev.ltd} → {cur.ltd}", "⚡"))

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
        if s.stp:
            out.append(f"🔥 {name} — {s.stp}")
        if s.ltd:
            out.append(f"⚡ {name} — {s.ltd} (LTD)")
        if s.coupon:
            out.append(f"🎟️ {name} — {s.coupon} coupon")
    return out
