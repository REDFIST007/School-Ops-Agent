# Operational Runbook

## 1. Local Startup
```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

## 2. Live Telegram & WhatsApp Setup
1. Expose local port 8000 via `cloudflared tunnel --url http://localhost:8000` (or deploy to Render/Railway/Fly.io).
2. **Telegram**: Create a bot with `@BotFather`, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` in `.env`, then register webhook:
   ```bash
   curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
     -d "url=https://<PUBLIC_HTTPS_URL>/api/webhooks/telegram" \
     -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
   ```
3. **WhatsApp**: Set `WHATSAPP_VERIFY_TOKEN` in `.env` and configure Meta Cloud API Webhook URL to `https://<PUBLIC_HTTPS_URL>/api/webhooks/whatsapp`.

## 3. Secret Rotation Assumptions
- `SECRET_KEY` signs/hashes session tokens via HMAC-SHA256. Rotating `SECRET_KEY` invalidates active sessions cleanly (users re-login).
- `TELEGRAM_WEBHOOK_SECRET` and `WHATSAPP_VERIFY_TOKEN` can be rotated in `.env` + provider dashboard with zero DB changes.

## 4. Recovery & Debugging
- Use `X-Correlation-ID` from any HTTP response or Audit Timeline row to trace `channel -> intent -> action -> notification` in `/audit`.
- Reminder deduplication keys (`rem:<sa_id>:<kind>:<date>`) ensure restarting the server or re-running `/api/reminders/run` never double-sends reminders.
