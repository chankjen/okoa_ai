"""Message pipeline — the brain of roadmap 1.5 / Phase 1 deliverable,
with the Phase 2 risk gate and Phase 3 human-in-the-loop wired in.

inbound WhatsApp text -> [dedupe] -> identity resolve (UUID) -> consent &
opt-out gates -> RISK PRE-SCREENING (roadmap 2.3: every message scored
before any generative/canned reply) -> crisis interception + escalation
(2.4/3.1) OR handover check (bot paused? counselor replies instead) ->
reply via Cloud API.

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
from app.db.models import (
    ChatSession,
    ConsentStatus,
    HandoverMode,
    Message,
    MessageDirection,
    MessageKind,
    RiskLabel,
    SessionControl,
    User,
)
from app.services.crisis_keywords import CRISIS_RESPONSE_TEXT, detect_crisis
from app.services.webhook_security import verify_signature  # noqa: F401 (re-export for tests)
from app.services.identity_service import IdentityService
from app.services.session_store import SessionStore
from app.services.whatsapp_client import WhatsAppClient

logger = logging.getLogger("okoa.pipeline")

DISTRESS_NUDGE = (
    "Asante kwa kuwa na ujasiri wa kushiriki hili. 🤍 Nimesikia — unapita "
    "katika kipindi kigumu.\n\n"
    "Ungependa kuzungumza na mfanyikazi wetu wa kibinafsi (bila malipo, "
    "siri kamili)? Andika *COUNSELOR* na tutakuunganisha. Kama ni dharura, "
    "pigia **1199** sasa."
)

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
        escalation=None,
    ):
        self.session_factory = session_factory
        self.identity = identity
        self.sessions = sessions
        self.wa = wa
        # Phase 2/3: EscalationService (risk persistence + crisis routing).
        # Optional so the pipeline degrades to Phase-1 behaviour if unwired.
        self.escalation = escalation
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
                # Safety first (TRD §3.2): even pre-consent messages are risk
                # scored — a crisis never waits on a consent form.
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
                pre_crisis = await self._score_risk(db, user_uuid, event, session_id=None)
                if pre_crisis:
                    await db.commit()
                    return
                body = CONSENT_NUDGE if already_prompted else WELCOME_TEXT
                await self._send(db, user_uuid, event, body, MessageKind.consent_prompt)
                await db.commit()
                return

            # 4b. Handover gate (roadmap 3.2): counselor took over ⇒ bot silent.
            control = await db.get(SessionControl, user_uuid)
            if control is not None and control.mode is HandoverMode.counselor_active:
                session_id = await self.sessions.get_active_session_id(user_uuid)
                if session_id:
                    db.add(Message(
                        session_id=session_id,
                        direction=MessageDirection.inbound,
                        kind=MessageKind.user_text,
                        wa_message_id=event.wa_message_id,
                        body=event.text[:4000],
                    ))
                await db.commit()
                logger.info("bot paused (counselor handover)", extra={
                    "event": "handover_hold", "user_uuid": user_uuid})
                return

            # Explicit human request keyword → create escalation + notify.
            if stripped in {"counselor", "mtu", "human", "msaidizi"}:
                await self._request_human(db, user_uuid, event)
                await db.commit()
                return

            # 5. Risk pre-screening gate (roadmap 2.3) with crisis routing
            #    (2.4) + escalation queue (3.1). Falls back to Phase-1 keyword
            #    detection when the engine is disabled or unwired.
            result = await self._assess_risk(db, user_uuid, event)
            if result is not None and result.is_crisis:
                await self._send(db, user_uuid, event, CRISIS_RESPONSE_TEXT,
                                 MessageKind.crisis_response, risk_label="crisis")
                await db.commit()
                return
            if result is not None and result.label is RiskLabel.distressed:
                await self._send(db, user_uuid, event, DISTRESS_NUDGE,
                                 MessageKind.canned_reply, risk_label="distressed")
                await db.commit()
                return

            crisis_fallback = (
                self.settings.enable_crisis_keyword_fallback
                and detect_crisis(event.text)
            )
            kind = MessageKind.crisis_response if crisis_fallback else MessageKind.canned_reply
            body = CRISIS_RESPONSE_TEXT if crisis_fallback else CANNED_REPLY
            await self._send(db, user_uuid, event, body, kind,
                             risk_label="crisis" if crisis_fallback else None)
            await db.commit()

    # ------------------------------------------------------ risk helpers
    async def _recent_distress_count(self, db: AsyncSession, user_uuid: str) -> int:
        """How many of the last 5 assessments were distressed/crisis."""
        from app.db.models import RiskAssessment

        rows = (await db.scalars(
            select(RiskAssessment.label)
            .where(RiskAssessment.user_uuid == user_uuid)
            .order_by(RiskAssessment.created_at.desc())
            .limit(5)
        )).all()
        return sum(1 for r in rows if r in (RiskLabel.distressed, RiskLabel.crisis))

    async def _assess_risk(self, db: AsyncSession, user_uuid: str,
                           event: InboundEvent):
        """Score + persist via EscalationService. Returns RiskResult|None."""
        if self.escalation is None or not self.settings.enable_risk_engine:
            return None
        from app.safety.risk_engine import score_message

        t0 = asyncio.get_running_loop().time()
        recent = await self._recent_distress_count(db, user_uuid)
        result = score_message(
            event.text,
            crisis_threshold=self.settings.crisis_threshold_pct,
            distress_threshold=self.settings.distress_threshold_pct,
            recent_distressed_count=recent,
        )
        result.latency_ms = round((asyncio.get_running_loop().time() - t0) * 1000, 2)
        session_id = await self.sessions.get_active_session_id(user_uuid)
        await self.escalation.process_risk(
            db, user_uuid=user_uuid, session_id=session_id or "",
            result=result, trigger_message_id=None,
        )
        return result

    async def _score_risk(self, db: AsyncSession, user_uuid: str,
                          event: InboundEvent, session_id: str | None) -> bool:
        """Pre-consent safety screen: intercept ONLY crises, no other reply."""
        result = await self._assess_risk(db, user_uuid, event)
        if result is not None and result.is_crisis:
            await self._send(db, user_uuid, event, CRISIS_RESPONSE_TEXT,
                             MessageKind.crisis_response, risk_label="crisis")
            return True
        return False

    async def _request_human(self, db: AsyncSession, user_uuid: str,
                             event: InboundEvent) -> None:
        """User typed COUNSELOR — open an escalation & promise contact."""
        if self.escalation is None:
            await self._send(db, user_uuid, event, CANNED_REPLY, MessageKind.canned_reply)
            return
        from app.safety.risk_engine import RiskResult, score_message
        from app.db.models import EscalationStatus, Escalation

        existing = await db.scalar(
            select(Escalation).where(
                Escalation.user_uuid == user_uuid,
                Escalation.status.in_([EscalationStatus.open, EscalationStatus.claimed,
                                       EscalationStatus.handed_over]),
            ).limit(1))
        if existing is None:
            base = score_message(event.text)
            forced = RiskResult(
                label=RiskLabel.crisis,
                score=max(base.score, 86.0),
                crisis_prob=max(base.crisis_prob, 0.86),
                distress_prob=base.distress_prob,
                features={**base.features, "requested_by_user": True},
            )
            session_id = await self.sessions.get_active_session_id(user_uuid) or ""
            await self.escalation.process_risk(
                db, user_uuid=user_uuid, session_id=session_id, result=forced)
        await self._send(
            db, user_uuid, event,
            "Karibu. Nimekuweka kwenye foleni ya washauri wetu 🤍 — mtu wa "
            "binadamu atakujibu hivi karibuni (lengo: chini ya dakika 2). "
            "Kwa sasa simu imepumzishwa kwa bot.",
            MessageKind.system)

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
