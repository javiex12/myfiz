# Finbot — Personal Finance Assistant for BCP

A Telegram bot that automatically logs your BCP bank expenses by reading your Gmail notifications and saving everything to Google Sheets. You can also log expenses manually by chatting with the bot.

```
BCP sends email → Gmail → Pub/Sub → Cloud Run → Google Sheets + Telegram notification
                                         ↑
                              Manual message from Telegram
```

**Cost**: essentially free. Cloud Run scales to zero, Sheets is free, Pub/Sub is free at this volume.

---

## What it does

- **Auto-logging**: detects BCP emails (debit, credit card, Yape, transfers) and logs them to Google Sheets in under 30 seconds.
- **Manual logging**: send `Almuerzo 25 PEN yape` to the bot and it saves it.
- **Commands**: `/hoy` (today), `/ultimo` (last 5), `/resumen` (monthly summary), `/categoria_<name>` (search by concept).
- **Deduplication**: if Gmail re-delivers a notification, the same expense is never saved twice.
- **Parse failure alerts**: if an email can't be parsed, the bot messages you immediately so you can log it manually.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11 |
| Framework | FastAPI |
| Deploy | Google Cloud Run (scale-to-zero) |
| Database | Google Sheets |
| Email push | Gmail API + Cloud Pub/Sub |
| Notifications | Telegram Bot API |
| Package manager | uv |

---

## Prerequisites

- A Gmail account that receives BCP notifications (`notificaciones@bcp.com.pe`)
- A GCP account (free tier is enough)
- A Telegram account
- [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Python 3.11+

---

## Full setup guide

### 1. Clone and install

```bash
git clone <this-repo>
cd finbot
uv sync
```

### 2. Create GCP project

```bash
gcloud projects create <your-gcp-project> --name="Finbot"
gcloud config set project <your-gcp-project>
```

Enable the required APIs:

```bash
gcloud services enable \
  gmail.googleapis.com \
  sheets.googleapis.com \
  run.googleapis.com \
  pubsub.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com
```

### 3. Create the Telegram bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the steps — save the **bot token**
3. Send any message to your new bot, then call:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
   Find your `chat_id` in the response under `message.chat.id`

Generate a random webhook secret (any hex string works):
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 4. Create Google Sheet

1. Go to [Google Sheets](https://sheets.google.com) and create a new spreadsheet
2. Create three tabs with these **exact** names: `expenses`, `processed_emails`, `errors`
3. Add headers to each tab — **Row 1, exact lowercase, no spaces**:

**expenses** (columns A–G):
```
timestamp | concepto | monto | moneda | modalidad | fuente | message_id
```

**processed_emails** (columns A–B):
```
message_id | processed_at
```

**errors** (columns A–D):
```
timestamp | type | detail | raw
```

**config** (columns A–B):
```
key | value
```
The `config` sheet is created automatically on first run. It stores `last_history_id` to track Gmail history continuity across webhooks.

4. Copy the Sheet ID from the URL: `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`

### 5. Create Service Account for Sheets

```bash
gcloud iam service-accounts create finbot-sa \
  --display-name="Finbot Sheets SA" \
  --project=<your-gcp-project>

gcloud iam service-accounts keys create sa-key.json \
  --iam-account=finbot-sa@<your-gcp-project>.iam.gserviceaccount.com
```

Share your Google Sheet with `finbot-sa@<your-gcp-project>.iam.gserviceaccount.com` as **Editor**.

### 6. Generate Gmail OAuth token

Create an OAuth 2.0 Client in GCP:
1. GCP Console → APIs & Services → Credentials → Create Credentials → OAuth Client ID
2. Type: **Desktop app** — download the JSON and save it as `credentials.json` in the project root

Generate the token:
```bash
uv run python scripts/generate_token.py
```

A browser window opens — log in with the Gmail account that receives BCP emails. This creates `token.json`.

### 7. Create Pub/Sub topic

```bash
gcloud pubsub topics create gmail-notifications --project=<your-gcp-project>

# Grant Gmail permission to publish to this topic (required for Gmail Watch)
gcloud pubsub topics add-iam-policy-binding gmail-notifications \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
  --role="roles/pubsub.publisher" \
  --project=<your-gcp-project>
```

### 8. Upload secrets to Secret Manager

> **Windows users**: use `Out-File -Encoding ascii -NoNewline` when writing temp files for secrets. Secrets with trailing newlines or BOM will cause silent authentication failures.

```bash
# Telegram
gcloud secrets create TELEGRAM_BOT_TOKEN --data-file=- --project=<your-gcp-project> <<< "<your-bot-token>"
gcloud secrets create TELEGRAM_WEBHOOK_SECRET --data-file=- --project=<your-gcp-project> <<< "<your-webhook-secret>"
gcloud secrets create TELEGRAM_ALLOWED_CHAT_IDS --data-file=- --project=<your-gcp-project> <<< "[<your-chat-id>]"

# Google
gcloud secrets create GOOGLE_SERVICE_ACCOUNT_JSON --data-file=sa-key.json --project=<your-gcp-project>
gcloud secrets create GMAIL_OAUTH_CREDENTIALS --data-file=token.json --project=<your-gcp-project>
gcloud secrets create GMAIL_USER_EMAIL --data-file=- --project=<your-gcp-project> <<< "<your-gmail>"
gcloud secrets create SHEET_ID --data-file=- --project=<your-gcp-project> <<< "<your-sheet-id>"

# Pub/Sub
gcloud secrets create PUBSUB_TOPIC --data-file=- --project=<your-gcp-project> <<< "gmail-notifications"

# Placeholder — will update after first deploy
gcloud secrets create CLOUD_RUN_URL --data-file=- --project=<your-gcp-project> <<< "https://placeholder.run.app"
```

Delete local credentials after uploading:
```bash
rm sa-key.json token.json credentials.json
```

Grant Cloud Run access to read secrets and build from source:
```bash
PROJECT_NUMBER=$(gcloud projects describe <your-gcp-project> --format="value(projectNumber)")

gcloud projects add-iam-policy-binding <your-gcp-project> \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding <your-gcp-project> \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.builder"
```

### 9. Deploy to Cloud Run

```bash
gcloud run deploy finbot \
  --source . \
  --region=us-central1 \
  --allow-unauthenticated \
  --project=<your-gcp-project> \
  --set-secrets="TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest,TELEGRAM_ALLOWED_CHAT_IDS=TELEGRAM_ALLOWED_CHAT_IDS:latest,TELEGRAM_WEBHOOK_SECRET=TELEGRAM_WEBHOOK_SECRET:latest,GOOGLE_SERVICE_ACCOUNT_JSON=GOOGLE_SERVICE_ACCOUNT_JSON:latest,GMAIL_OAUTH_CREDENTIALS=GMAIL_OAUTH_CREDENTIALS:latest,GMAIL_USER_EMAIL=GMAIL_USER_EMAIL:latest,SHEET_ID=SHEET_ID:latest,PUBSUB_TOPIC=PUBSUB_TOPIC:latest,CLOUD_RUN_URL=CLOUD_RUN_URL:latest" \
  --set-env-vars="ENVIRONMENT=prod"
```

Copy the service URL from the output (e.g. `https://finbot-XXXXXX-uc.a.run.app`).

Update `CLOUD_RUN_URL` with the real URL:
```bash
# Linux/Mac
echo -n "https://finbot-XXXXXX-uc.a.run.app" | gcloud secrets versions add CLOUD_RUN_URL --data-file=- --project=<your-gcp-project>

# Windows PowerShell
"https://finbot-XXXXXX-uc.a.run.app" | Out-File -FilePath url.txt -Encoding ascii -NoNewline
gcloud secrets versions add CLOUD_RUN_URL --data-file=url.txt --project=<your-gcp-project>
Remove-Item url.txt
```

Verify the service is live:
```bash
curl https://finbot-XXXXXX-uc.a.run.app/health
# → {"status":"ok"}
```

### 10. Configure Telegram webhook

```bash
curl -X POST https://api.telegram.org/bot<TOKEN>/setWebhook \
  -H "Content-Type: application/json" \
  -d '{"url": "https://finbot-XXXXXX-uc.a.run.app/telegram-webhook", "secret_token": "<WEBHOOK_SECRET>"}'
```

Verify it registered correctly:
```bash
curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo
```

### 11. Create Pub/Sub push subscription

```bash
gcloud pubsub subscriptions create gmail-notifications-push \
  --topic=gmail-notifications \
  --push-endpoint=https://finbot-XXXXXX-uc.a.run.app/gmail-webhook \
  --project=<your-gcp-project>
```

### 12. Activate Gmail Watch

```bash
PYTHONPATH=. uv run python scripts/setup_watch.py
```

This starts Gmail's push notifications to your Pub/Sub topic. The watch expires every 7 days — Step 13 handles renewal automatically.

### 13. Set up auto-renewal with Cloud Scheduler

```bash
gcloud scheduler jobs create http renew-gmail-watch \
  --schedule="0 9 */6 * *" \
  --uri="https://finbot-XXXXXX-uc.a.run.app/renew-watch" \
  --http-method=POST \
  --location=us-central1 \
  --project=<your-gcp-project>
```

**You're done.** Make a card payment at BCP — it should appear in your Sheet and Telegram within 30 seconds.

---

## Local development

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

Run the server:
```bash
uv run uvicorn app.main:app --reload --port 8000
```

Test the Gmail webhook locally (get a fresh `historyId` first, then forward yourself a BCP email):
```bash
PYTHONPATH=. uv run python scripts/get_history_id.py
# update gmail_body.json with the new historyId, then:
curl -X POST http://localhost:8000/gmail-webhook \
  -H "Content-Type: application/json" \
  -d @gmail_body.json
```

Test the Telegram webhook locally:
```bash
curl -X POST http://localhost:8000/telegram-webhook \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: <WEBHOOK_SECRET>" \
  -d '{"message": {"chat": {"id": <CHAT_ID>}, "text": "Taxi 15 PEN efectivo"}}'
```

Run tests:
```bash
uv run pytest
```

---

## Usage

### Manual expenses

Send a message to the bot:

```
concepto monto [moneda] [modalidad]
```

| Field | Required | Values | Default |
|-------|----------|--------|---------|
| concepto | yes | any text | — |
| monto | yes | number (`15`, `3.50`) | — |
| moneda | no | `PEN`, `USD` | `PEN` |
| modalidad | no | `efectivo` `debito` `credito` `yape` `plin` `transferencia` | `efectivo` |

Examples:
```
Taxi 15
Almuerzo 25 PEN yape
Netflix 13.99 USD credito
Supermercado 120.50 PEN debito
```

### Commands

| Command | Description |
|---------|-------------|
| `/hoy` | All expenses today + total |
| `/ultimo` | Last 5 expenses |
| `/resumen` | Current month totals by modality |
| `/categoria_<name>` | Search by concept (e.g. `/categoria_Taxi`) |

---

## Supported BCP email types

| Type | Modality assigned |
|------|-------------------|
| Debit card payment | `debito` |
| Plin payment (via debit) | `plin` |
| Credit card consumption | `credito` |
| Transfer to third party | `transferencia` |
| Credit card payment | `transferencia` |
| Yape sent | `yape` |

If an email doesn't match any pattern, it's logged to the `errors` sheet and you get an immediate Telegram alert so you can log it manually.

---

## Project structure

```
app/
  main.py              # FastAPI + webhook routes + command handlers
  config.py            # env vars (pydantic-settings)
  models.py            # Expense, ParsedEmail dataclasses
  parsers/
    bcp.py             # BCP regex parsers → ParsedEmail | None
    telegram_msg.py    # manual message parser → Expense | None
  services/
    gmail.py           # Gmail API client
    sheets.py          # gspread: read/write expenses
    telegram.py        # send messages via Bot API
scripts/
  generate_token.py    # one-off: generate Gmail OAuth token
  get_history_id.py    # get current historyId for local testing
  setup_watch.py       # activate Gmail Watch
tests/
  fixtures/            # real anonymized BCP emails (.eml / .html)
docs/
  schema.md            # Google Sheets schema detail
  infra.md             # GCP resources state
```

---

## Adapting for other banks

The only bank-specific code lives in `app/parsers/bcp.py`. To add another bank:

1. Collect 5–10 real anonymized emails from that bank
2. Add `app/parsers/<bank>.py` following the same pattern (return `ParsedEmail | None`)
3. Call it from `gmail_webhook` in `main.py` before the parse failure path
4. Add fixtures to `tests/fixtures/<bank>/` and write unit tests against them

---

## Security notes

- `TELEGRAM_ALLOWED_CHAT_IDS` whitelist is **critical** — without it anyone who finds your bot can read your financial history.
- Pub/Sub JWT validation is active in `ENVIRONMENT=prod` — only accepts requests from Google's push service.
- Telegram webhook secret validates requests come from Telegram, not arbitrary HTTP clients.
- Service account credentials and OAuth tokens live only in Secret Manager, never in the image or repo.
