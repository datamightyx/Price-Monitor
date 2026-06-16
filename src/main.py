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
from .slack import build_blocks, send, send_separator
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
    groups = cfg.groups
    all_changes: dict = {}
    for gi, group in enumerate(groups):
        snaps = results.get(group.name, [])
        if not snaps:
            continue
        changes = diff_group(snaps, baseline)
        all_changes[group.name] = changes
        blocks = build_blocks(cfg, group, snaps, changes)
        fallback = f"{cfg.slack.app_name} — {group.name}: {len(changes)} changes ({today})"
        if dry_run or demo:
            print(f"\n===== {group.name} ({len(changes)} changes) =====")
            from .slack import render_table
            print(render_table(snaps, group.my_asins))
            coupons = [(s.product or s.asin, s.coupon) for s in snaps if s.coupon and s.error is None]
            if coupons:
                print("\nCoupons:")
                for name, coupon in coupons:
                    print(f"  🎟️  {name} — {coupon}")
            stp_list = [(s.product or s.asin, s.stp) for s in snaps if s.stp and s.error is None]
            if stp_list:
                print("\nActive STP:")
                for name, stp in stp_list:
                    print(f"  🔥  {name} — {stp}")
            ltd_list = [(s.product or s.asin, s.ltd) for s in snaps if s.ltd and s.error is None]
            if ltd_list:
                print("\nActive LTD:")
                for name, ltd in ltd_list:
                    print(f"  ⚡  {name} — {ltd}")
            if changes:
                print("\nChanges:")
            for c in changes:
                print(f"  {c.emoji} {c.text}")
        else:
            send(cfg, blocks, fallback)
            if gi < len(groups) - 1:
                send_separator(cfg)

    # 5. PDF report — generated once across all groups
    from .report import generate_pdf, send_pdf
    try:
        print("[report] generating PDF...")
        pdf_bytes = generate_pdf(cfg, results, all_changes, today)
        send_pdf(cfg, pdf_bytes, today)   # always saves to disk; uploads if token has files:write
    except Exception as e:  # noqa: BLE001
        print(f"[report] PDF failed (continuing): {e}")

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
