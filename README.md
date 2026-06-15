# Amazon Competitor Monitor

Щоденний моніторинг товарів конкурентів на Amazon. Скрейпить кожен ASIN, зберігає щоденний знімок у Google Sheets і надсилає дайджест у Slack: **зміни vs вчора → поточна таблиця → купони → акції → нотатки**.

Без Keepa, без платних API — пряме скрейпінг через Playwright.

```
config.yaml ──▶ scraper (Playwright) ──▶ diff vs yesterday ──▶ Google Sheets (history)
                                                            └──▶ Slack digest (per group)
             scheduled daily via GitHub Actions + cron-job.org
```

---

## Як це працює

1. **Scraper** (`src/scraper.py`) відкриває сторінку кожного ASIN у headless Chromium, встановлює US zip-код для отримання правильної ціни buy-box, і парсить: назву, ціну, BSR (обидва ранки), рейтинг, кількість відгуків, купон, акцію, наявність.
2. **Storage** (`src/storage.py`) читає з Google Sheets останній рядок по кожному ASIN до сьогодні — це "вчорашній baseline". Потім дописує сьогоднішні дані.
3. **Diff** (`src/diff.py`) порівнює сьогоднішні знімки з baseline і формує список змін (ціна впала/зросла, з'явився купон тощо).
4. **Slack** (`src/slack.py`) будує Block Kit повідомлення і надсилає в канал.

Пайплайн **stateless**: між запусками не зберігається жодних файлів — вся histórica в Google Sheets.

---

## Структура проекту

```
config.yaml                       групи, ASIN-и, налаштування (тільки цей файл потрібно редагувати)
.env                              токени (Slack, Google, Proxy) — не комітити в git
service_account.json              ключ Google Service Account — не комітити в git
src/
  config.py                       завантаження та валідація config.yaml
  models.py                       Snapshot dataclass + маппінг на рядок Sheets
  scraper.py                      Playwright драйвер + парсери HTML
  storage.py                      Google Sheets: запис + читання baseline
  diff.py                         порівняння день-до-дня
  slack.py                        Block Kit повідомлення + відправка
  main.py                         оркестрація (--dry-run / --demo)
  demo_data.py                    мок-дані для офлайн тестування
.github/workflows/monitor.yml     GitHub Actions workflow (cron + ручний запуск)
inspect_asin.py                   утиліта для відлагодження одного ASIN
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
python -m src.main --dry-run    # реальний скрейп, виводить у термінал
python -m src.main --demo       # без скрейпу, мок-дані
```

### Повний запуск

```bash
python -m src.main              # скрейп → Sheets → Slack
```

---

## Налаштування

### 1. config.yaml

```yaml
marketplace: US

groups:
  - name: "Blotting Sheets"       # назва групи — заголовок у Slack
    my_asin: B086692GCS           # ТВІЙ товар (виділяється * в таблиці)
    asins:
      - B0DQQGR9KR                # конкуренти
      - B0FQ21P4J2

scraper:
  min_delay_sec: 4
  max_delay_sec: 9
  retries: 3
  headless: true
  timeout_ms: 30000
  zip_code: "10001"               # US zip для правильних цін

google_sheet:
  enabled: true
  spreadsheet: "ID_ТАБЛИЦІ"      # довгий ID з URL таблиці
  worksheet: "history"

slack:
  channel: "#amazon-monitor"
  app_name: "Amazon Monitor"
```

Щоб додати другу групу товарів — просто додай ще один блок `- name:`. Кожна група отримає окреме повідомлення в Slack.

### 2. Slack

**Варіант A — Bot Token (рекомендовано):**

1. [api.slack.com/apps](https://api.slack.com/apps) → Create New App → From scratch
2. OAuth & Permissions → Bot Token Scopes → додай `chat:write`
3. Install App → Install to Workspace
4. Скопіюй **Bot User OAuth Token** (`xoxb-...`)
5. У Slack: `/invite @Amazon Monitor` в канал `#amazon-monitor`
6. `.env`: `SLACK_BOT_TOKEN=xoxb-...`

**Варіант B — Incoming Webhook:**

1. [api.slack.com/apps](https://api.slack.com/apps) → Create New App → From scratch
2. Incoming Webhooks → увімкни → Add New Webhook → вибери канал
3. `.env`: `SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...`

### 3. Google Sheets

1. Створи таблицю на [sheets.google.com](https://sheets.google.com)
2. Скопіюй ID з URL: `docs.google.com/spreadsheets/d/**ID**/edit`
3. Встав ID в `config.yaml` → `google_sheet.spreadsheet`

**Service Account:**

1. [console.cloud.google.com](https://console.cloud.google.com) → вибери або створи проект
2. APIs & Services → Enable APIs → увімкни **Google Sheets API**
3. IAM & Admin → Service Accounts → Create Service Account (name: `amazon-monitor`)
4. Натисни на акаунт → Keys → Add Key → Create new key → JSON → завантажиться файл
5. Перейменуй файл на `service_account.json`, поклади в корінь проекту
6. Відкрий файл, знайди `client_email` → поділись таблицею з цим email (права Editor)
7. `.env`: `GOOGLE_SERVICE_ACCOUNT_JSON=./service_account.json`

### 4. .env файл

```env
SLACK_BOT_TOKEN=xoxb-...
# або
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

GOOGLE_SERVICE_ACCOUNT_JSON=./service_account.json

# Проксі (рекомендовано для стабільної роботи)
# PROXY_SERVER=http://gate.provider.com:7777
# PROXY_USERNAME=your_user
# PROXY_PASSWORD=your_pass
```

---

## Автоматичний запуск (GitHub Actions + cron-job.org)

GitHub Actions запускає скрейпер на серверах GitHub — не потрібен увімкнений комп'ютер.

### Крок 1 — Запуш код на GitHub

```bash
git init
git add .
git commit -m "init"
git remote add origin https://github.com/YOUR_USER/Price-Monitor.git
git push -u origin main
```

> `.env` і `service_account.json` вже в `.gitignore` — вони не потраплять в репозиторій.

### Крок 2 — Додай Secrets у GitHub

Репозиторій → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Значення |
|---|---|
| `SLACK_BOT_TOKEN` | `xoxb-...` |
| `SLACK_WEBHOOK_URL` | `https://hooks.slack.com/...` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | **Весь вміст** файлу `service_account.json` (скопіюй JSON як текст) |
| `PROXY_SERVER` | `http://gate.provider.com:7777` (якщо є) |
| `PROXY_USERNAME` | логін проксі (якщо є) |
| `PROXY_PASSWORD` | пароль проксі (якщо є) |

### Крок 3 — Налаштуй час у workflow

Відкрий `.github/workflows/monitor.yml`, змін час запуску (формат UTC):

```yaml
on:
  schedule:
    - cron: "25 8 * * *"   # 08:25 UTC = 11:25 Київ (UTC+3 влітку)
  workflow_dispatch: {}     # дозволяє запускати вручну з Actions
```

**Таблиця переведення часу:**

| Київ (EEST, UTC+3) | UTC (для cron) |
|---|---|
| 09:00 | `0 6 * * *` |
| 10:00 | `0 7 * * *` |
| 11:25 | `25 8 * * *` |
| 12:00 | `0 9 * * *` |

### Крок 4 — Автоматичний тригер через cron-job.org

GitHub cron може запізнюватися на 10-30 хвилин або пропускати запуск. Для надійного запуску у точний час використовуй [cron-job.org](https://cron-job.org) — він дзвонить у GitHub API і запускає workflow.

**4.1 — Створи GitHub токен:**

github.com → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token  
Scope: постав галочку **workflow** → Generate token → скопіюй

**4.2 — Налаштуй cron-job.org:**

1. Зареєструйся на [cron-job.org](https://cron-job.org)
2. **Create cronjob**
3. **URL:**
   ```
   https://api.github.com/repos/YOUR_USER/Price-Monitor/actions/workflows/monitor.yml/dispatches
   ```
4. **Execution schedule:** вибери потрібний час (зверни увагу — cron-job.org використовує UTC)
5. Розгорни **Advanced → Headers**, додай три заголовки:

   | Header | Value |
   |---|---|
   | `Authorization` | `Bearer ТВІЙ_ТОКЕН` |
   | `Accept` | `application/vnd.github.v3+json` |
   | `Content-Type` | `application/json` |

6. **Request method:** `POST`
7. **Request body:**
   ```json
   {"ref":"main"}
   ```
8. **Save**

Тепер cron-job.org щодня у вказаний час надсилає запит до GitHub API і запускає workflow — незалежно від того, увімкнений комп'ютер чи ні.

**4.3 — Перевір що все працює:**

Репозиторій → вкладка **Actions** → має з'явитися запуск. Клікни на нього — побачиш логи в реальному часі. Через 2-3 хвилини прийде повідомлення в Slack.

---

## Відлагодження

### Перевірити один ASIN

```bash
python inspect_asin.py B0DQQGR9KR
```

Відкриває браузер, зберігає `debug/inspect_<asin>_<time>.png` і `.html`. Додай `--headless` щоб без вікна.

### Змінити CAPTCHA / блокування

Без проксі Amazon швидко блокує запити з датацентрів. Ознаки блокування:
- Поле ціни порожнє (`—`)
- В `debug/` скріншот показує "Robot Check"

Рішення: додай residential/rotating проксі в `.env`.

### Якщо парсер перестав знаходити дані

Amazon змінює HTML розмітку. Відкрий `inspect_asin.py` для ASIN-у що не парситься, знайди у збереженому `.html` потрібний елемент і оновіть селектор у `src/scraper.py`. Найбільш нестабільні поля: `bsr`, `coupon`, `deal`.

---

## Важливі застереження

- **Дрейф селекторів.** Amazon регулярно змінює верстку і проводить A/B тести. Якщо поле починає повертати `—` — відкрий живу сторінку і оновіть селектор.
- **CAPTCHA / блокування** неминуче без хороших проксі. Скрейпер визначає сторінки "Robot Check" і робить retry з backoff, але не може вирішити CAPTCHA.
- **Terms of Service.** Скрейпінг Amazon суперечить їхнім ToS. Тримай затримки між запитами (`scraper.min_delay_sec`) і використовуй відповідально.
- Невдалий ASIN відображається в Notes дайджесту (`⚠️ failed to fetch: ...`) — не ламає весь запуск.
