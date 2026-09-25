"""Crisis interception & escalation workflow (roadmap 2.4 / 3.1 glue).

On a Crisis-route score:
  1. pause the AI path (static helpline response is sent by the pipeline),
  2. create/refresh an Escalation for the counselor queue,
  3. append to the tamper-evident audit log,
  4. push a live alert to connected counselors (WebSocket hub),
  5. notify on-duty counselors out-of-band (roadmap 3.4 — belt & braces).

Duplicate suppression: if the user already has an open escalation AND a
crisis response was delivered within the cooldown window, we bump the risk
score but do not spam a second helpline message or duplicate notification.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditAction,
    Escalation,
    EscalationStatus,
    HandoverMode,
    Message,
    MessageDirection,
    MessageKind,
    RiskAssessment,
    RiskLabel,
    SessionControl,
)
from app.safety.risk_engine import RiskResult

logger = logging.getLogger("okoa.escalation")

# Don't re-send the helpline script to the same user within this window while
# an escalation is still open (they've already been told; counselor is coming).
REINTERCEPT_COOLDOWN_SECONDS = 60 * 30


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class EscalationService:
    def __init__(self, notifier=None, hub=None):
        # notifier: services.notifications.Notifier (roadmap 3.4)
        # hub:      api.ws.EscalationHub        (roadmap 3.1 WebSocket push)
        self.notifier = notifier
        self.hub = hub

    async def process_risk(
        self,
        db: AsyncSession,
        *,
        user_uuid: str,
        session_id: str,
        result: RiskResult,
        trigger_message_id: str | None = None,
    ) -> RiskAssessment:
        """Persist the assessment; if crisis ⇒ intercept + escalate.

        Returns the stored RiskAssessment. The caller (pipeline) uses
        ``result.is_crisis`` to decide what text to send. Non-crisis scores
        are persisted for analytics only — the queue trail records actions
        that require human attention (created/claimed/resolved/etc.).
        """
        assessment = RiskAssessment(
            user_uuid=user_uuid,
            session_id=session_id,
            message_id=trigger_message_id,
            label=result.label,
            score=result.score,
            crisis_prob=result.crisis_prob,
            distress_prob=result.distress_prob,
            model_version=result.model_version,
            features_json=result.features_json(),
            latency_ms=result.latency_ms,
            intercepted=result.is_crisis,
        )
        db.add(assessment)
        await db.flush()

        if result.is_crisis:
            await self._escalate(db, user_uuid, session_id, result, trigger_message_id)
        return assessment

    async def _escalate(
        self,
        db: AsyncSession,
        user_uuid: str,
        session_id: str,
        result: RiskResult,
        trigger_message_id: str | None,
    ) -> Escalation:
        from app.safety.audit import audit_append

        existing = await db.scalar(
            select(Escalation)
            .where(
                Escalation.user_uuid == user_uuid,
                Escalation.status.in_([
                    EscalationStatus.open, EscalationStatus.claimed,
                    EscalationStatus.handed_over,
                ]),
            )
            .order_by(Escalation.created_at.desc())
            .limit(1)
        )
        in_cooldown = False
        if existing is not None:
            last_crisis_msg = await db.scalar(
                select(Message.created_at)
                .where(
                    Message.session_id == existing.session_id,
                    Message.kind == MessageKind.crisis_response,
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            if last_crisis_msg is not None:
                if last_crisis_msg.tzinfo is None:
                    last_crisis_msg = last_crisis_msg.replace(tzinfo=dt.timezone.utc)
                in_cooldown = (
                    _now() - last_crisis_msg
                ).total_seconds() < REINTERCEPT_COOLDOWN_SECONDS

        if existing is not None and in_cooldown:
            # Refresh priority, keep single open item per user.
            if result.score > existing.risk_score:
                existing.risk_score = result.score
            logger.info(
                "crisis re-detected within cooldown; escalation %s refreshed",
                existing.id, extra={"event": "escalation_refresh"},
            )
            return existing

        esc = existing if (existing is not None and existing.status is EscalationStatus.muted) else None
        if esc is not None:
            # Muted earlier (false positive) but crisis again ⇒ reopen.
            esc.status = EscalationStatus.open
            esc.risk_score = max(esc.risk_score, result.score)
            esc.trigger_message_id = trigger_message_id
            await db.flush()
            await audit_append(
                db, action=AuditAction.escalation_created, actor_type="system",
                subject_uuid=user_uuid,
                details={"escalation_id": esc.id, "reopened": True,
                         "risk_score": esc.risk_score},
            )
            await self._dispatch(db, esc, result)
            return esc

        escalation = Escalation(
            user_uuid=user_uuid,
            session_id=session_id,
            trigger_message_id=trigger_message_id,
            risk_score=result.score,
            risk_label=RiskLabel(result.label.value),
            status=EscalationStatus.open,
        )
        db.add(escalation)
        await db.flush()

        await audit_append(
            db, action=AuditAction.escalation_created, actor_type="system",
            subject_uuid=user_uuid,
            details={"escalation_id": escalation.id,
                     "risk_score": escalation.risk_score,
                     "trigger": "crisis_route"},
        )
        await self._dispatch(db, escalation, result)
        return escalation

    async def _dispatch(self, db: AsyncSession, esc: Escalation, result: RiskResult) -> None:
        """Live push + out-of-band notification (3.1 + 3.4)."""
        payload = {
            "type": "escalation_new",
            "id": esc.id,
            "user_uuid": esc.user_uuid,
            "session_id": esc.session_id,
            "risk_score": esc.risk_score,
            "risk_label": esc.risk_label.value,
            "status": esc.status.value,
            "created_at": esc.created_at.isoformat() if esc.created_at else None,
        }
        if self.hub is not None:
            try:
                await self.hub.broadcast(payload)
            except Exception:  # pragma: no cover - push failure must not block
                logger.exception("ws broadcast failed (non-fatal)")
        if self.notifier is not None:
            try:
                await self.notifier.notify_crisis(db, esc)
            except Exception:  # pragma: no cover
                logger.exception("crisis notification failed (non-fatal)")

    # ------------------------------------------------- handover controls (3.2)
    async def set_handover(
        self,
        db: AsyncSession,
        user_uuid: str,
        mode: HandoverMode,
        *,
        counselor_id: str | None = None,
        escalation_id: str | None = None,
    ) -> SessionControl:
        from app.safety.audit import audit_append

        control = await db.get(SessionControl, user_uuid)
        if control is None:
            control = SessionControl(user_uuid=user_uuid, mode=mode)
            db.add(control)
        control.mode = mode
        control.active_escalation_id = escalation_id
        control.changed_by = counselor_id
        await db.flush()
        await audit_append(
            db,
            action=AuditAction.handover_started if mode is HandoverMode.counselor_active
            else AuditAction.handover_ended,
            actor_type="counselor", actor_id=counselor_id, subject_uuid=user_uuid,
            details={"mode": mode.value, "escalation_id": escalation_id},
        )
        return control

    async def get_mode(self, db: AsyncSession, user_uuid: str) -> HandoverMode:
        control = await db.get(SessionControl, user_uuid)
        return control.mode if control else HandoverMode.bot_active
