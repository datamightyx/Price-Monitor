# Price Monitor

Щотижневий моніторинг твоїх товарів і конкурентів на Amazon. Скрейпить кожен ASIN, зберігає знімок у Google Sheets, надсилає дайджест у Slack і генерує PDF-звіт.

Без Keepa, без платних API — пряме скрейпінг через Playwright.

```
config.yaml ──▶ scraper (Playwright) ──▶ diff vs попередній запуск ──▶ Google Sheets
                                                                    ├──▶ Slack digest (per group)
                                                                    └──▶ PDF report → Cloudflare R2 → Slack
             запускається щотижня через GitHub Actions
```

---

## Що моніториться

Для кожного ASIN збирається: назва, бренд, ціна, два BSR (основний + підкатегорія), рейтинг, відгуки, купон, STP (strike-through price), LTD (limited time deal), наявність.

**Зміни що відстежуються:** ціна впала/зросла, з'явився/зник купон, STP, LTD, зникнення з наявності.

---

## Структура проекту

```
config.yaml                       групи, ASINи, налаштування (тільки цей файл потрібно редагувати)
.env                              токени (Slack, Google, R2, Proxy) — не комітити в git
src/
  config.py                       завантаження та валідація config.yaml
  models.py                       Snapshot dataclass + маппінг на рядок Sheets
  scraper.py                      Playwright драйвер + HTML парсери
  storage.py                      Google Sheets: запис + читання baseline
  diff.py                         порівняння зі попереднім запуском
  slack.py                        Block Kit повідомлення + відправка
  report.py                       генерація PDF-звіту (Playwright → PDF)
  main.py                         оркестрація (--dry-run / --demo)
  demo_data.py                    мок-дані для офлайн тестування
.github/workflows/monitor.yml     GitHub Actions workflow (cron + ручний запуск)
```

---

## Швидкий старт

```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium
cp .env.example .env        # заповни токени
```

### Тест без запису

```bash
python -m src.main --demo       # без скрейпу, мок-дані, генерує PDF
python -m src.main --dry-run    # реальний скрейп, виводить у термінал, без запису в Sheets/Slack
```

### Повний запуск

```bash
python -m src.main              # скрейп → diff → Sheets → Slack → PDF → R2
```

---

## Налаштування

### 1. config.yaml

```yaml
marketplace: US          # US | UK | DE | FR | IT | ES | CA | JP
language: en_US

groups:
  - name: "Blotting Sheets"
    my_asins:            # твої ASINи (позначаються ★ в таблиці та PDF)
      - B086692GCS
      - B0ASIN2HERE      # можна один або кілька
    asins:               # ASINи конкурентів
      - B0DQQGR9KR
      - B0FQ21P4J2
      - B0DBY7CJC8

  - name: "Cast Covers"  # кожна група — окремий Slack-дайджест і секція в PDF
    my_asins:
      - B078Q32FXQ
    asins:
      - B0FMF5GRWG

scraper:
  min_delay_sec: 4
  max_delay_sec: 9
  retries: 3
  headless: true
  timeout_ms: 30000
  zip_code: "10001"      # US zip для правильних цін buy-box (важливо для не-US IP)

google_sheet:
  enabled: true
  spreadsheet: "ID_ТАБЛИЦІ"   # довгий ID з URL таблиці
  worksheet: "history"

google_drive:
  folder_id: ""          # ID папки Google Drive для PDF (опційно)

cloudflare_r2:
  bucket: "price-monitor"
  public_url: "https://pub-xxx.r2.dev"   # публічний URL бакету

slack:
  channel: "#price-monitor"
  channel_id: "C0XXXXXXX"   # ID каналу (правий клік на канал → View details)
  app_name: "Price Monitor"
```

Щоб додати нову групу — додай ще один блок `- name:`. Кожна група отримає окремий розділ у PDF і Slack.

---

### 2. Slack

**Варіант A — Bot Token (рекомендовано):**

1. [api.slack.com/apps](https://api.slack.com/apps) → Create New App → From scratch
2. OAuth & Permissions → Bot Token Scopes → додай `chat:write` і `files:write`
3. Install App → Install to Workspace
4. Скопіюй **Bot User OAuth Token** (`xoxb-...`)
5. У Slack: `/invite @Price Monitor` в канал `#price-monitor`
6. `.env`: `SLACK_BOT_TOKEN=xoxb-...`

**Варіант B — Incoming Webhook:**

1. [api.slack.com/apps](https://api.slack.com/apps) → Incoming Webhooks → Add New Webhook → вибери канал
2. `.env`: `SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...`

---

### 3. Google Sheets

1. Створи таблицю на [sheets.google.com](https://sheets.google.com)
2. Скопіюй ID з URL: `docs.google.com/spreadsheets/d/**ID**/edit`
3. Встав ID в `config.yaml` → `google_sheet.spreadsheet`

**Service Account:**

1. [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → увімкни **Google Sheets API**
2. IAM & Admin → Service Accounts → Create → завантаж JSON-ключ
3. Відкрий JSON, знайди `client_email` → поділись таблицею з цим email (права Editor)
4. `.env`: `GOOGLE_SERVICE_ACCOUNT_JSON=./service_account.json`
   або GitHub Secret: вміст усього JSON-файлу як текст

---

### 4. Cloudflare R2 (зберігання PDF)

1. Cloudflare Dashboard → R2 → Create bucket (`price-monitor`)
2. Увімкни **Public access** на бакеті → скопіюй `r2.dev` URL → встав в `config.yaml`
3. R2 → Manage R2 API tokens → Create token (Object Read & Write)
4. `.env`:
   ```env
   R2_ACCOUNT_ID=...
   R2_ACCESS_KEY_ID=...
   R2_SECRET_ACCESS_KEY=...
   ```

---

### 5. .env файл

```env
# Slack (один з двох)
SLACK_BOT_TOKEN=xoxb-...
# SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# Google Sheets
GOOGLE_SERVICE_ACCOUNT_JSON=./service_account.json

# Cloudflare R2
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...

# Проксі (рекомендовано — без нього Amazon блокує датацентри)
# PROXY_SERVER=http://gate.provider.com:7777
# PROXY_USERNAME=user
# PROXY_PASSWORD=pass
```

---

## Автоматичний запуск (GitHub Actions)

### Крок 1 — Додай Secrets у GitHub

Репозиторій → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Що це |
|--------|-------|
| `SLACK_BOT_TOKEN` | `xoxb-...` з OAuth & Permissions |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | повний вміст JSON-файлу service account |
| `R2_ACCOUNT_ID` | Cloudflare → правий верхній кут |
| `R2_ACCESS_KEY_ID` | R2 API token |
| `R2_SECRET_ACCESS_KEY` | R2 API token secret |
| `PROXY_SERVER` | `http://host:port` (якщо є) |
| `PROXY_USERNAME` | логін проксі (якщо є) |
| `PROXY_PASSWORD` | пароль проксі (якщо є) |

### Крок 2 — Налаштуй розклад

Відкрий `.github/workflows/monitor.yml`, змін час (формат UTC):

```yaml
on:
  schedule:
    - cron: "0 8 * * 1"   # щопонеділка о 11:00 Київ/Молдова (EEST = UTC+3)
  workflow_dispatch: {}    # ручний запуск з вкладки Actions
```

| Київ/Молдова (EEST, UTC+3) | cron UTC |
|---|---|
| Пн 09:00 | `0 6 * * 1` |
| Пн 11:00 | `0 8 * * 1` |
| Пн 12:00 | `0 9 * * 1` |

### Крок 3 — Надійний тригер через cron-job.org

GitHub cron може запізнюватися або пропускати запуск. Для точного часу використовуй [cron-job.org](https://cron-job.org):

1. Create cronjob → **URL:**
   ```
   https://api.github.com/repos/YOUR_USER/YOUR_REPO/actions/workflows/monitor.yml/dispatches
   ```
2. Method: `POST`, Body: `{"ref":"main"}`
3. Headers:
   ```
   Authorization: Bearer GITHUB_TOKEN
   Accept: application/vnd.github.v3+json
   Content-Type: application/json
   ```
   *(GitHub token: Settings → Developer settings → Personal access tokens → scope: `workflow`)*

### PDF як артефакт

Кожен запуск зберігає PDF у GitHub Actions Artifacts (30 днів):
Репозиторій → **Actions** → вибери запуск → розділ **Artifacts**.

---

## Відлагодження

### Перевірити один ASIN

```bash
python inspect_asin.py B0DQQGR9KR
```

Зберігає `debug/inspect_<asin>_<time>.png` і `.html`.

### Зберегти дебаг-артефакти

```bash
DEBUG_SAVE=1 python -m src.main --dry-run
```

### CAPTCHA / блокування

Без проксі Amazon блокує запити з датацентрів. Ознака: ціна `—`, у `debug/` скріншот "Robot Check". Рішення: додай residential/rotating проксі в `.env`.

### Якщо парсер перестав знаходити дані

Amazon регулярно змінює верстку. Відкрий `inspect_asin.py` для проблемного ASIN, знайди елемент у `.html` і оновіть селектор у `src/scraper.py`. Найбільш нестабільні поля: `bsr`, `coupon`, `deal`.

---

## Важливі застереження

- **Terms of Service.** Скрейпінг Amazon суперечить їхнім ToS. Тримай затримки між запитами і використовуй відповідально.
- **Дрейф селекторів.** Amazon проводить A/B тести і змінює верстку. Якщо поле повертає `—` — оновіть селектор.
- **CAPTCHA** неминуча без хороших проксі. Скрейпер робить retry з backoff, але не вирішує CAPTCHA.
- Невдалий ASIN відображається як `⚠️ failed to fetch` в Slack і PDF — не ламає весь запуск.
