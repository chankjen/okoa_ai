"""Out-of-band crisis notifications (roadmap 3.4).

Belt-and-braces beyond the dashboard: when a Crisis item enters the queue,
alert every on-duty counselor via WhatsApp (primary in Kenya context) and
email (secondary). Channels are pluggable; ``notification_channel`` selects:

  log       — records intent only (default/dev/tests)
  whatsapp  — real Cloud API sends to counselors' stored phone_e164
  email     — SMTP send to counselors' stored email
  both      — whatsapp + email

Every attempt is written to the audit trail (notification_sent /
notification_failed) so drill reviews can prove alerts fired. No message
content leaves the system — alerts carry UUID + risk score only.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import AuditAction, Counselor, Escalation

logger = logging.getLogger("okoa.notifications")

ALERT_TEMPLATE = (
    "🚨 OKOA CRISIS ALERT\n"
    "A session has been flagged CRITICAL (risk {score}/100).\n"
    "Session ref: {uuid}\n"
    "Open the counselor dashboard and claim it now. Target: first response < {sla} s.\n"
    "(Anonymous by design — no user details in this alert.)"
)


class Notifier:
    def __init__(self, wa_client=None):
        self.wa = wa_client  # WhatsAppClient for counselor phones (not users!)
        self.settings = get_settings()
        self.sent_log: list[dict] = []  # inspectable in tests / drills

    async def notify_crisis(self, db: AsyncSession, escalation: Escalation) -> None:
        channel = self.settings.notification_channel
        on_duty = (
            await db.scalars(
                select(Counselor).where(
                    Counselor.is_on_duty.is_(True), Counselor.is_active.is_(True)
                )
            )
        ).all()
        if not on_duty:
            logger.warning("crisis alert with NO on-duty counselors", extra={
                "event": "notification_gap", "escalation_id": escalation.id})
        text = ALERT_TEMPLATE.format(
            score=round(escalation.risk_score),
            uuid=escalation.user_uuid[:8],
            sla=self.settings.escalation_sla_seconds,
        )
        for c in on_duty:
            targets = []
            if channel in {"whatsapp", "both"} and c.phone_e164:
                targets.append(("whatsapp", c.phone_e164))
            if channel in {"email", "both"} and c.email:
                targets.append(("email", c.email))
            if channel == "log":
                targets = [("log", c.username)]
            for kind, target in targets:
                ok = await self._send(kind, target, text)
                from app.safety.audit import audit_append

                await audit_append(
                    db,
                    action=AuditAction.notification_sent if ok
                    else AuditAction.notification_failed,
                    actor_type="system",
                    subject_uuid=escalation.user_uuid,
                    details={"escalation_id": escalation.id, "channel": kind,
                             "counselor_id": c.id},
                )

    async def _send(self, kind: str, target: str, text: str) -> bool:
        record = {"channel": kind, "ok": False}
        try:
            if kind == "log":
                logger.info("crisis alert (log channel) to counselor=%s", target,
                            extra={"event": "notification_log"})
                record["ok"] = True
            elif kind == "whatsapp" and self.wa is not None:
                await self.wa.send_text(target.lstrip("+"), text)
                record["ok"] = True
            elif kind == "email":
                self._smtp_send(target, text)
                record["ok"] = True
            else:
                logger.error("notification channel %s unavailable", kind)
                return False
        except Exception as exc:
            logger.error("notification failed channel=%s err=%s", kind, type(exc).__name__)
            return False
        finally:
            self.sent_log.append(record)
        return record["ok"]

    def _smtp_send(self, to_addr: str, text: str) -> None:
        s = self.settings
        msg = EmailMessage()
        msg["Subject"] = "OKOA crisis alert"
        msg["From"] = s.smtp_from
        msg["To"] = to_addr
        msg.set_content(text)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
