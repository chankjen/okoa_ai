"""WhatsApp webhook router (roadmap 1.1).

GET  /webhooks/whatsapp — Meta URL verification echo challenge.
POST /webhooks/whatsapp — signed message/status events → async ingestion queue.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request

from app.core.config import get_settings
from app.services.pipeline import InboundEvent
from app.services.webhook_security import verify_signature

logger = logging.getLogger("okoa.webhook")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/whatsapp")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    settings = get_settings()
    if hub_mode == "subscribe" and settings.whatsapp_verify_token and \
            hub_verify_token == settings.whatsapp_verify_token:
        logger.info("webhook verification OK")
        return hub_challenge
    raise HTTPException(status_code=403, detail="verification failed")


def _extract_events(payload: dict) -> list[InboundEvent]:
    """Flatten a Cloud API messages webhook payload into InboundEvents."""
    events: list[InboundEvent] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                if msg.get("type") != "text":
                    # Phase 5 adds interactive/button parsing; ignore others now.
                    continue
                text = (msg.get("text") or {}).get("body", "")
                wa_id = msg.get("from", "")
                wa_msg_id = msg.get("id", "")
                if wa_id and wa_msg_id:
                    events.append(InboundEvent(wa_user_id=wa_id, text=text, wa_message_id=wa_msg_id))
    return events


@router.post("/whatsapp")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(None),
):
    settings = get_settings()
    raw = await request.body()

    # 1. Signature verification over RAW bytes, constant-time compare.
    if not verify_signature(
        settings.whatsapp_app_secret, x_hub_signature_256, raw,
        settings.webhook_signature_tolerance_seconds,
    ):
        logger.warning("webhook rejected: bad signature")
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="bad json")

    # 2. Statuses (delivered/read) — log only for now (roadmap 1.4 delivery status).
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            statuses = change.get("value", {}).get("statuses")
            if statuses:
                logger.info("delivery status", extra={"event": "status", "count": len(statuses)})

    # 3. Enqueue message events for the async pipeline; respond 200 immediately.
    pipeline = request.app.state.pipeline
    events = _extract_events(payload)
    for ev in events:
        if not await pipeline.enqueue(ev):
            # Backpressure: 5xx makes Meta retry the webhook later.
            raise HTTPException(status_code=503, detail="queue full, retry")
    logger.info("webhook accepted", extra={"event": "accepted", "count": len(events)})
    return {"status": "ready", "accepted": len(events)}
