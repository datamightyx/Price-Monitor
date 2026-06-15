"""
Entry point: scrape → diff vs yesterday → store → Slack digest (per group).

Run:
    python -m src.main                 # full run
    python -m src.main --dry-run       # scrape + print, no Sheets write, no Slack post
    python -m src.main --demo          # no scraping; uses mock data (for testing)
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

# Load .env from project root automatically
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# Ensure Unicode output works on Windows terminals that default to cp1251
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from .config import load_config
from .diff import diff_group
from .slack import build_blocks, send
from .storage import make_store


def run(config_path: str, dry_run: bool, demo: bool) -> int:
    cfg = load_config(config_path)
    today = date.today().isoformat()

    # 1. baseline (yesterday) — read BEFORE writing today's rows
    store = None if (dry_run or demo) else make_store(cfg.google_sheet)
    baseline = store.latest_before(today) if store else {}

    # 2. collect today's snapshots
    if demo:
        from .demo_data import demo_snapshots, demo_baseline
        results = demo_snapshots(cfg)
        baseline = demo_baseline()
    else:
        from .scraper import scrape_groups
        results = scrape_groups(cfg)

    # 3. persist today's snapshots
    if store:
        all_snaps = [s for snaps in results.values() for s in snaps]
        try:
            store.append(all_snaps)
        except Exception as e:  # noqa: BLE001
            print(f"[sheets] append failed (continuing): {e}")

    # 4. per-group diff + Slack digest
    for group in cfg.groups:
        snaps = results.get(group.name, [])
        if not snaps:
            continue
        changes = diff_group(snaps, baseline)
        blocks = build_blocks(cfg, group, snaps, changes)
        fallback = f"{cfg.slack.app_name} — {group.name}: {len(changes)} changes ({today})"
        if dry_run or demo:
            print(f"\n===== {group.name} ({len(changes)} changes) =====")
            from .slack import render_table
            print(render_table(snaps, group.my_asin))
            coupons = [(s.product or s.asin, s.coupon) for s in snaps if s.coupon and s.error is None]
            if coupons:
                print("\nCoupons:")
                for name, coupon in coupons:
                    print(f"  🎟️  {name} — {coupon}")
            deals = [(s.product or s.asin, s.deal) for s in snaps if s.deal and s.error is None]
            if deals:
                print("\nActive Deals:")
                for name, deal in deals:
                    print(f"  🔥  {name} — {deal}")
            if changes:
                print("\nChanges:")
            for c in changes:
                print(f"  {c.emoji} {c.text}")
        else:
            send(cfg, blocks, fallback)

    return 0


def main():
    ap = argparse.ArgumentParser(description="Amazon competitor monitor")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true",
                    help="scrape + print; no Sheets write, no Slack post")
    ap.add_argument("--demo", action="store_true",
                    help="no scraping; use mock data to exercise diff/table/slack")
    args = ap.parse_args()
    sys.exit(run(args.config, args.dry_run, args.demo))


if __name__ == "__main__":
    main()
