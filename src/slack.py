"""
Build and send the Slack digest (one per group), matching the layout:
  header → intro → your product → Changes → Current Snapshot (table)
        → Active Promotions → Notes
Slack has no real tables, so the snapshot is a monospace code block.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Optional

import requests

from .config import Config, Group
from .diff import Change
from .models import Snapshot

SLACK_POST_URL = "https://slack.com/api/chat.postMessage"


# ── table rendering ──────────────────────────────────────────────────────────

def _cell(v) -> str:
    return "—" if v in (None, "", "—") else str(v)


def _bsr_cell(s: Snapshot) -> str:
    if s.bsr is None:
        return "—"
    return f"#{s.bsr:,}"


def _price(s: Snapshot) -> str:
    if s.price is None:
        return "—"
    sym = {"USD": "$", "GBP": "£", "EUR": "€"}.get(s.currency, "")
    return f"{sym}{s.price:g}"


def render_table(snaps: list[Snapshot], my_asins) -> str:
    """Render a monospace table. my_asins can be a str or list[str]."""
    if isinstance(my_asins, str):
        my_asins = [my_asins]
    my_set = set(my_asins)
    headers = ["", "ASIN", "Product", "Brand", "Price", "BSR", "Rating", "Reviews", "Coupon", "In stock", "STP", "LTD"]
    rows = [headers]
    for s in snaps:
        star = "*" if s.asin in my_set else " "
        rows.append([
            star,
            s.asin,
            (s.product or "")[:26],
            (s.brand or "")[:16],
            _price(s),
            _bsr_cell(s),
            f"{s.rating}★" if s.rating is not None else "—",
            f"{s.reviews:,}" if s.reviews is not None else "—",
            _cell(s.coupon),
            "yes" if s.in_stock else "no",
            _cell(s.stp),
            _cell(s.ltd),
        ])
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(headers))]
    lines = []
    for ri, r in enumerate(rows):
        line = "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r))
        lines.append(line.rstrip())
        if ri == 0:
            lines.append("-" * len(line.rstrip()))
    return "\n".join(lines)


# ── block assembly ───────────────────────────────────────────────────────────

def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _product_link(s: Snapshot, domain: str) -> str:
    """Slack mrkdwn hyperlink: product name (or ASIN) pointing to the Amazon page."""
    name = s.product or s.asin
    url = f"https://{domain}/dp/{s.asin}?th=1"
    return f"<{url}|{name}>"



def build_blocks(cfg: Config, group: Group, snaps: list[Snapshot],
                 changes: list[Change]) -> list[dict]:
    today = date.today().isoformat()
    n = len(changes)
    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f"{cfg.slack.app_name} — {group.name} — "
                          f"{n} change{'s' if n != 1 else ''} detected ({today})"}},
        _section(f"Daily change digest for *{len(snaps)} tracked ASINs*. "
                 f"Comparing against yesterday's baseline."),
    ]

    my_set = set(group.my_asins)
    mines = [s for s in snaps if s.asin in my_set]
    for mine in mines:
        parts = [f"*★ My product:* {_product_link(mine, cfg.domain)} — {_price(mine)}"]
        if mine.bsr is not None:
            parts.append(f"BSR #{mine.bsr:,}")
        if mine.rating is not None:
            parts.append(f"{mine.rating}★")
        if mine.reviews is not None:
            parts.append(f"{mine.reviews:,} reviews")
        blocks.append(_section("  ·  ".join(parts)))

    # Changes
    if changes:
        body = "\n".join(f"{c.emoji} {c.text}" for c in changes)
    else:
        body = "_No changes vs yesterday._"
    blocks.append({"type": "divider"})
    blocks.append(_section(f"*Changes:*\n{body}"))

    # Current snapshot table
    table = render_table(snaps, group.my_asins)
    blocks.append(_section(f"*Current Snapshot:*\n```{table}```"))

    # Coupons — listed right below the table
    coupon_lines = [
        f"🎟️  {s.product or s.asin} — {s.coupon}"
        for s in snaps if s.coupon and s.error is None
    ]
    if coupon_lines:
        blocks.append(_section("*Coupons:*\n" + "\n".join(coupon_lines)))

    # Active STP (strike-through price discounts)
    stp_lines = [
        f"🔥  {s.product or s.asin} — {s.stp}"
        for s in snaps if s.stp and s.error is None
    ]
    if stp_lines:
        blocks.append(_section("*Active STP:*\n" + "\n".join(stp_lines)))

    # Active LTD (limited time deals)
    ltd_lines = [
        f"⚡  {s.product or s.asin} — {s.ltd}"
        for s in snaps if s.ltd and s.error is None
    ]
    if ltd_lines:
        blocks.append(_section("*Active LTD:*\n" + "\n".join(ltd_lines)))

    # Notes
    in_stock = sum(1 for s in snaps if s.in_stock and s.error is None)
    promo_count = sum(1 for s in snaps if s.stp or s.ltd or s.coupon)
    failed = [s.asin for s in snaps if s.error is not None]
    notes = [f"• {in_stock}/{len(snaps)} products in stock",
             f"• {promo_count}/{len(snaps)} products have active promotions"]
    if failed:
        notes.append(f"• ⚠️ failed to fetch: {', '.join(failed)}")
    blocks.append(_section("*Notes:*\n" + "\n".join(notes)))

    return blocks


# ── sending ──────────────────────────────────────────────────────────────────

def send_separator(cfg: Config) -> None:
    """Post a visual divider between group messages."""
    send(cfg, [_section("─" * 40)], "─" * 40)


def send(cfg: Config, blocks: list[dict], fallback_text: str) -> None:
    token = os.getenv("SLACK_BOT_TOKEN")
    webhook = os.getenv("SLACK_WEBHOOK_URL")

    if token:
        resp = requests.post(
            SLACK_POST_URL,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json; charset=utf-8"},
            json={"channel": cfg.slack.channel, "blocks": blocks,
                  "text": fallback_text},
            timeout=20,
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API error: {data.get('error')}")
        print(f"[slack] posted to {cfg.slack.channel}")
    elif webhook:
        resp = requests.post(webhook, json={"blocks": blocks, "text": fallback_text},
                             timeout=20)
        resp.raise_for_status()
        print("[slack] posted via webhook")
    else:
        print("[slack] no SLACK_BOT_TOKEN / SLACK_WEBHOOK_URL — printing instead:\n")
        print(fallback_text)
