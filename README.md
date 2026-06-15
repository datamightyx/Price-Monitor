# Amazon Competitor Monitor

Daily monitor for Amazon competitor products. Scrapes each ASIN, stores a daily
snapshot in Google Sheets for analysis, and posts a Slack digest per product
group: **changes vs yesterday → current snapshot table → active promotions → notes**.

No Keepa, no paid API — direct scraping with Playwright.

```
config.yaml ─▶ scraper (Playwright) ─▶ diff vs yesterday ─▶ Google Sheets (history)
                                                         └▶ Slack digest (per group)
            scheduled daily by GitHub Actions cron
```

## Project layout

```
config.yaml                 groups + ASINs + settings (edit this, no code changes)
src/
  config.py                 load/validate config, marketplace→domain map
  models.py                 Snapshot dataclass + Sheets row mapping
  scraper.py                Playwright driver + defensive HTML parsers
  storage.py                Google Sheets append + diff baseline reader
  diff.py                   day-over-day change detection
  slack.py                  Block Kit message + sender
  main.py                   orchestration (--dry-run / --demo)
  demo_data.py              mock data for offline testing
.github/workflows/monitor.yml   daily cron
```

## Setup

```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium
cp .env.example .env        # fill in tokens
```

**Slack:** create an app, add the `chat:write` scope, install to the workspace,
invite the bot to your channel, and put the bot token in `SLACK_BOT_TOKEN`.
(Or use an incoming webhook via `SLACK_WEBHOOK_URL`.)

**Google Sheets:** create a service account, download its JSON key, point
`GOOGLE_SERVICE_ACCOUNT_JSON` at it, create a spreadsheet, share it with the
service account's `client_email` as Editor, and paste the spreadsheet key into
`config.yaml` (`google_sheet.spreadsheet`).

**Proxy:** Amazon blocks datacenter IPs fast. Set `PROXY_SERVER` (+ user/pass)
to a residential/rotating proxy. Without it, expect CAPTCHA pages.

## Run

```bash
python -m src.main --demo      # no scraping; mock data → prints table + changes
python -m src.main --dry-run   # real scrape; prints only, no Sheets/Slack writes
python -m src.main             # full run: scrape → Sheets → Slack
```

## Adding products

Edit `config.yaml`. Each group becomes its own Slack digest; `my_asin` is starred
in the table. Nothing else to touch.

```yaml
groups:
  - name: "Protein Bars"
    my_asin: B0B17L29N8
    asins: [B07Y7H3FHB, B0178ENI4K, B077QK3NX4]
```

## Scheduling

`.github/workflows/monitor.yml` runs daily via cron (UTC — convert from your
local time; Moldova is UTC+2 in winter, UTC+3 in summer). GitHub may start the
job a few minutes late; if you need exact timing, run the same command from a
`cron`/`systemd` timer on a VPS instead.

## How the diff stays correct in CI

The pipeline is stateless between runs: it reads the **latest row per ASIN dated
before today** from the Google Sheet as the "yesterday" baseline, then appends
today's rows. No state file to persist. (A local `state.json` fallback only
matters if Sheets is disabled.)

## Seeing the CAPTCHA / debugging a block

Two ways to see exactly what Amazon returns:

**Watch it live.** Open a single ASIN in a real browser window:
```bash
python inspect_asin.py B07Y7H3FHB
```
The window stays open until you press Enter — you'll see either the product page
or the "Robot Check" CAPTCHA. It also prints whether a CAPTCHA was detected and
saves `debug/inspect_<asin>_<time>.png` + `.html`. Add `--headless` to skip the
window and just save the files.

**Auto-capture during normal runs.** The scraper now saves a screenshot + HTML
to `debug/` automatically whenever it hits a CAPTCHA (so you get artifacts even
from a headless CI run). To dump artifacts for *every* fetch (not only blocks),
set `DEBUG_SAVE=1`. Change the folder with `DEBUG_DIR=...`.

For local debugging it also helps to flip `scraper.headless: false` in
`config.yaml` so the full run uses a visible browser.

## Honest caveats (this is direct scraping)

- **Selectors drift.** Amazon changes markup and runs A/B layouts. Every parser
  in `scraper.py` tries multiple selectors + a regex fallback, but if a field
  starts returning `—`, open a live page and update the selector. `bsr`,
  `coupon` and `deal` are the most fragile.
- **CAPTCHA / blocking** is expected without good proxies. The scraper detects
  "Robot Check" pages and retries with backoff, but it can't solve CAPTCHAs.
- **Terms of Service.** Scraping Amazon is against their ToS. Keep request rates
  low (`scraper.min_delay_sec`) and use responsibly.
- A failed ASIN is reported in the digest's Notes (`⚠️ failed to fetch: ...`)
  rather than crashing the run.
```
