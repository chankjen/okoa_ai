"""Message pipeline — the brain of roadmap 1.5 / Phase 1 deliverable.

inbound WhatsApp text -> [dedupe] -> identity resolve (UUID) -> consent &
opt-out gates -> crisis keyword fallback -> canned reply via Cloud API.

Async ingestion: the webhook handler enqueues parsed events onto an
asyncio worker queue and returns 200 immediately (Meta requires < a few
seconds; heavy work happens in the background task started on lifespan).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import ChatSession, ConsentStatus, Message, MessageDirection, MessageKind, User
from app.services.crisis_keywords import CRISIS_RESPONSE_TEXT, detect_crisis
from app.services.webhook_security import verify_signature  # noqa: F401 (re-export for tests)
from app.services.identity_service import IdentityService
from app.services.session_store import SessionStore
from app.services.whatsapp_client import WhatsAppClient

logger = logging.getLogger("okoa.pipeline")

WELCOME_TEXT = (
    "Karibu OKOA 🤍 — your anonymous, judgment-free support space.\n\n"
    "• Hakuna jina, hakuna nambari — tutumia *UUID* pekee.\n"
    "• Unganisha *AGREE* kukubali, au *STOP* kusimama.\n"
    "• Dharura: piga **1199** (suicide hotline, free 24/7).\n\n"
    "Tuko nawe. Andaa? Type AGREE to begin."
)
CONSENT_NUDGE = (
    "Tafadhali thibitisha: andika *AGREE* (nakubali) au *STOP* (situshegerezi)."
)
OPT_OUT_CONFIRM = (
    "Sawa, tumesimamisha ujumbe wote. Data yako itafutwa (tombstone). "
    "Ukitaka kurudi baadaye, tuandikie 'Karibu'. 🤍"
)
CANNED_REPLY = (
    "Asante kwa kushiriki hili nami. 🤍 Niko hapa kusikia.\n\n"
    "(Kamili ya AI inajengwa — kwa sasa niko katika hali ya awali. "
    "Kama unahitaji msaada wa haraka wa binadamu, pigia 1199.)"
)


@dataclass
class InboundEvent:
    wa_user_id: str          # phone digits from webhook — consumed once, then dropped
    text: str
    wa_message_id: str
    raw: dict = field(default_factory=dict)


class MessagePipeline:
    def __init__(
        self,
        session_factory,
        identity: IdentityService,
        sessions: SessionStore,
        wa: WhatsAppClient,
    ):
        self.session_factory = session_factory
        self.identity = identity
        self.sessions = sessions
        self.wa = wa
        self.queue: asyncio.Queue[InboundEvent] = asyncio.Queue(maxsize=10_000)
        self._worker: asyncio.Task | None = None
        self.settings = get_settings()

    # ---------------------------------------------------------- queue control
    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="pipeline-worker")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def enqueue(self, event: InboundEvent) -> bool:
        """Non-blocking put; False ⇒ backpressure (webhook should return 5xx so Meta retries)."""
        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            logger.error("pipeline queue full — dropping fast for retry")
            return False

    async def _run(self) -> None:
        while True:
            event = await self.queue.get()
            try:
                await self.handle(event)
            except Exception:
                logger.exception("pipeline error processing event")
            finally:
                self.queue.task_done()

    # ------------------------------------------------------------ core logic
    async def handle(self, event: InboundEvent) -> None:
        db: AsyncSession
        async with self.session_factory() as db:
            # 1. Dedupe webhook retries (Redis SETNX + DB unique constraint).
            if await self.sessions.seen_message(event.wa_message_id):
                logger.info("duplicate webhook ignored", extra={"event": "dedupe"})
                return

            # 2. Anonymous identity resolution — plaintext phone discarded after.
            user_uuid, is_new = await self.identity.resolve_or_enrol(db, event.wa_user_id)
            user = await db.get(User, user_uuid)
            await self.identity.mark_seen(db, user_uuid)

            # 3. Opt-out gate: STOP/FUTA handling (roadmap 1.5).
            stripped = event.text.strip().lower()
            if stripped in {"stop", "futa", "kusimama", "unsibscribe"}:
                was_opted_out = user is not None and user.opt_out
                await self.identity.opt_out(db, user_uuid)
                await db.commit()
                await self.sessions.clear(user_uuid)
                if not was_opted_out:
                    await self._send(db, user_uuid, event, OPT_OUT_CONFIRM, MessageKind.system)
                return

            if user is not None and user.opt_out:
                logger.info("message from opted-out user ignored", extra={"user_uuid": user_uuid})
                await db.commit()
                return

            # 4. Consent gate.
            if user is not None and user.consent_status == ConsentStatus.pending:
                if stripped in {"agree", "ndipo", "nikubali", "yes", "ndiyo"}:
                    await self.identity.set_consent(db, user_uuid, granted=True)
                    await db.commit()
                    await self._send(db, user_uuid, event, CANNED_REPLY, MessageKind.canned_reply)
                    return
                # New user's very first message doubles as the Karibu/consent
                # screen; later messages just re-prompt until they decide.
                already_prompted = (
                    await db.scalar(
                        select(Message.id)
                        .join(ChatSession, Message.session_id == ChatSession.id)
                        .where(
                            ChatSession.user_uuid == user_uuid,
                            Message.kind == MessageKind.consent_prompt,
                        )
                        .limit(1)
                    )
                    is not None
                )
                body = CONSENT_NUDGE if already_prompted else WELCOME_TEXT
                await self._send(db, user_uuid, event, body, MessageKind.consent_prompt)
                await db.commit()
                return

            # 5. Crisis keyword fallback (until Phase 2 model replaces it).
            crisis = self.settings.enable_crisis_keyword_fallback and detect_crisis(event.text)
            kind = MessageKind.crisis_response if crisis else MessageKind.canned_reply
            body = CRISIS_RESPONSE_TEXT if crisis else CANNED_REPLY
            await self._send(db, user_uuid, event, body, kind, risk_label="crisis" if crisis else None)
            await db.commit()

    async def _send(
        self,
        db: AsyncSession,
        user_uuid: str,
        event: InboundEvent,
        reply_body: str,
        kind: MessageKind,
        *,
        risk_label: str | None = None,
    ) -> None:
        # Persist inbound message + active session window.
        session_id = await self.sessions.get_active_session_id(user_uuid)
        session: ChatSession | None = await db.get(ChatSession, session_id) if session_id else None
        if session is None:
            session = ChatSession(user_uuid=user_uuid)
            db.add(session)
            await db.flush()
            await self.sessions.touch(user_uuid, session.id)

        db.add(
            Message(
                session_id=session.id,
                direction=MessageDirection.inbound,
                kind=MessageKind.user_text,
                wa_message_id=event.wa_message_id,
                body=event.text[:4000],
                risk_label=risk_label,
            )
        )
        db.add(
            Message(
                session_id=session.id,
                direction=MessageDirection.outbound,
                kind=kind,
                body=reply_body,
            )
        )
        session.last_activity_at = dt.datetime.now(dt.timezone.utc)
        await db.flush()

        try:
            await self.wa.send_text(event.wa_user_id, reply_body)
        except Exception as exc:
            # Outbound failure must not lose the inbound record; log & continue.
            logger.error("outbound send failed err=%s", type(exc).__name__)
