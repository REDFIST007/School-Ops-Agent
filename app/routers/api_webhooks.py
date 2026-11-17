from typing import Any, Dict, Optional
from fastapi import APIRouter, Header, HTTPException, Query, Request

from app.config import settings
from app.database import get_conn
from app.models import CanonicalMessageEnvelope
from app.services.channel_adapter import (
    normalize_telegram_payload,
    normalize_whatsapp_payload,
    process_incoming_message,
)

router = APIRouter(tags=["webhooks"])


@router.post("/api/webhooks/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    if settings.telegram_webhook_secret and settings.telegram_webhook_secret != "change-me-telegram-secret":
        if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
            raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret.")
    payload = await request.json()
    envelope = normalize_telegram_payload(payload)
    with get_conn() as conn:
        return process_incoming_message(conn, envelope)


@router.get("/api/webhooks/whatsapp")
def whatsapp_verify(
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return int(hub_challenge) if (hub_challenge and hub_challenge.isdigit()) else (hub_challenge or "ok")
    raise HTTPException(status_code=403, detail="WhatsApp verification failed.")


@router.post("/api/webhooks/whatsapp")
async def whatsapp_webhook(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    envelope = normalize_whatsapp_payload(payload)
    with get_conn() as conn:
        return process_incoming_message(conn, envelope)


@router.post("/api/simulator/message")
def simulator_message(envelope: CanonicalMessageEnvelope) -> Dict[str, Any]:
    with get_conn() as conn:
        return process_incoming_message(conn, envelope)
