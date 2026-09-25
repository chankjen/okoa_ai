"""Counselor dashboard API — Phase 3 human-in-the-loop (roadmap 3.1–3.5).

All routes except /login require a Bearer JWT from /counselor/login.
Privacy: conversation views expose user_uuid only — never phones, names or
contact details (TRD §5). Every state transition lands in the chained audit
log via the services; SLA timers are computed server-side for drill reports.
"""
from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.counselor_auth import (
    authenticate,
    create_token,
    require_counselor,
)
from app.db.models import (
    AuditAction,
    Counselor,
    Escalation,
    EscalationOutcome,
    EscalationStatus,
    HandoverMode,
    Message,
    MessageDirection,
    MessageKind,
    ResponseDrill,
    RiskAssessment,
    SessionControl,
)
from app.db.session import get_db
from app.safety.audit import audit_append, export_audit_csv, verify_chain

logger = logging.getLogger("okoa.counselor_api")

router = APIRouter(prefix="/counselor", tags=["counselor"])


# ---------------------------------------------------------------------- auth
class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    counselor = await authenticate(db, body.username, body.password)
    if counselor is None:
        # Log failed attempts to the audit trail too (accountability).
        await audit_append(
            db, action=AuditAction.login_failure, actor_type="system",
            details={"username": body.username},
        )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    token, ttl = create_token(counselor)
    await audit_append(
        db, action=AuditAction.login_success, actor_type="counselor",
        actor_id=counselor.id, details={"username": counselor.username},
    )
    await db.commit()
    return {"access_token": token, "expires_in": ttl,
            "display_name": counselor.display_name}


# ------------------------------------------------------------------ queue 3.1
def _esc_dict(e: Escalation) -> dict:
    return {
        "id": e.id,
        "user_uuid": e.user_uuid,
        "session_id": e.session_id,
        "risk_score": e.risk_score,
        "risk_label": e.risk_label.value,
        "status": e.status.value,
        "claimed_by": e.claimed_by,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "first_response_at": e.first_response_at.isoformat() if e.first_response_at else None,
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
        "outcome": e.outcome.value if e.outcome else None,
        "notes": e.notes,
    }


@router.get("/escalations")
async def list_escalations(
    request: Request,
    include_closed: bool = False,
    counselor: Counselor = Depends(require_counselor),
    db: AsyncSession = Depends(get_db),
):
    """Priority-ordered queue (highest risk first) — roadmap 3.1."""
    q = select(Escalation)
    if not include_closed:
        q = q.where(Escalation.status.in_(
            [EscalationStatus.open, EscalationStatus.claimed, EscalationStatus.handed_over]))
    q = q.order_by(Escalation.risk_score.desc(), Escalation.created_at.asc())
    rows = (await db.scalars(q)).all()
    sla = request.app.state.settings.escalation_sla_seconds if hasattr(
        request.app.state, "settings") else 120
    out = []
    now = dt.datetime.now(dt.timezone.utc)
    for e in rows:
        d = _esc_dict(e)
        if e.created_at:
            created = e.created_at.replace(tzinfo=dt.timezone.utc) \
                if e.created_at.tzinfo is None else e.created_at
            d["age_seconds"] = round((now - created).total_seconds())
            d["sla_breached"] = (
                e.status is EscalationStatus.open and d["age_seconds"] > sla
            )
        out.append(d)
    return {"queue": out, "open_count": sum(
        1 for e in rows if e.status is EscalationStatus.open)}


@router.post("/escalations/{escalation_id}/claim")
async def claim(escalation_id: str, request: Request,
                counselor: Counselor = Depends(require_counselor),
                db: AsyncSession = Depends(get_db)):
    esc = await db.get(Escalation, escalation_id)
    if esc is None:
        raise HTTPException(404, "escalation not found")
    if esc.status not in (EscalationStatus.open, EscalationStatus.claimed):
        raise HTTPException(409, f"cannot claim from status {esc.status.value}")
    esc.status = EscalationStatus.claimed
    esc.claimed_by = counselor.id
    esc.claimed_at = dt.datetime.now(dt.timezone.utc)
    await audit_append(
        db, action=AuditAction.escalation_claimed, actor_type="counselor",
        actor_id=counselor.id, subject_uuid=esc.user_uuid,
        details={"escalation_id": esc.id},
    )
    await db.commit()
    await _broadcast(payload={"type": "escalation_update", **_esc_dict(esc)})
    return _esc_dict(esc)


@router.post("/escalations/{escalation_id}/unclaim")
async def unclaim(escalation_id: str,
                  counselor: Counselor = Depends(require_counselor),
                  db: AsyncSession = Depends(get_db)):
    esc = await db.get(Escalation, escalation_id)
    if esc is None:
        raise HTTPException(404, "escalation not found")
    if esc.claimed_by != counselor.id:
        raise HTTPException(403, "only the owner can unclaim")
    esc.status = EscalationStatus.open
    esc.claimed_by = None
    esc.claimed_at = None
    await audit_append(
        db, action=AuditAction.escalation_unclaimed, actor_type="counselor",
        actor_id=counselor.id, subject_uuid=esc.user_uuid,
        details={"escalation_id": esc.id},
    )
    await db.commit()
    return _esc_dict(esc)


class ResolveRequest(BaseModel):
    outcome: EscalationOutcome
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/escalations/{escalation_id}/resolve")
async def resolve(escalation_id: str, body: ResolveRequest,
                  counselor: Counselor = Depends(require_counselor),
                  db: AsyncSession = Depends(get_db)):
    esc = await db.get(Escalation, escalation_id)
    if esc is None:
        raise HTTPException(404, "escalation not found")
    if esc.status in (EscalationStatus.resolved, EscalationStatus.muted):
        raise HTTPException(409, "already closed")
    was_open_like = esc.status in (
        EscalationStatus.open, EscalationStatus.claimed, EscalationStatus.handed_over)
    esc.status = EscalationStatus.resolved
    esc.outcome = body.outcome
    esc.notes = body.notes
    esc.resolved_at = dt.datetime.now(dt.timezone.utc)
    # Return bot control to automation.
    control = await db.get(SessionControl, esc.user_uuid)
    if control and control.active_escalation_id == esc.id:
        control.mode = HandoverMode.bot_active
        control.active_escalation_id = None
        control.changed_by = counselor.id
    await audit_append(
        db, action=AuditAction.escalation_resolved, actor_type="counselor",
        actor_id=counselor.id, subject_uuid=esc.user_uuid,
        details={"escalation_id": esc.id, "outcome": body.outcome.value},
    )
    await db.commit()
    logger.info("escalation resolved", extra={"event": "resolved",
                                              "was_open_like": was_open_like})
    return _esc_dict(esc)


@router.post("/escalations/{escalation_id}/mute")
async def mute(escalation_id: str, body: ResolveRequest,
               counselor: Counselor = Depends(require_counselor),
               db: AsyncSession = Depends(get_db)):
    """Mark false-positive / suppress duplicates (still audited)."""
    esc = await db.get(Escalation, escalation_id)
    if esc is None:
        raise HTTPException(404, "escalation not found")
    esc.status = EscalationStatus.muted
    esc.outcome = body.outcome
    esc.notes = body.notes
    esc.resolved_at = dt.datetime.now(dt.timezone.utc)
    control = await db.get(SessionControl, esc.user_uuid)
    if control and control.active_escalation_id == esc.id:
        control.mode = HandoverMode.bot_active
        control.active_escalation_id = None
    await audit_append(
        db, action=AuditAction.escalation_muted, actor_type="counselor",
        actor_id=counselor.id, subject_uuid=esc.user_uuid,
        details={"escalation_id": esc.id, "outcome": body.outcome.value},
    )
    await db.commit()
    return _esc_dict(esc)


# ------------------------------------------------------- conversation 3.2
@router.get("/sessions/{user_uuid}/messages")
async def session_messages(user_uuid: str, limit: int = 50,
                           _: Counselor = Depends(require_counselor),
                           db: AsyncSession = Depends(get_db)):
    """Anonymized conversation view — UUID only, no PII anywhere."""
    rows = (await db.scalars(
        select(Message)
        .where(Message.session_id.in_(
            select(Escalation.session_id).where(Escalation.user_uuid == user_uuid)))
        .order_by(Message.created_at.desc())
        .limit(min(limit, 200))
    )).all()
    return {
        "user_uuid": user_uuid,
        "messages": [
            {
                "id": m.id,
                "direction": m.direction.value,
                "kind": m.kind.value,
                "body": m.body,
                "risk_label": m.risk_label,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in reversed(rows)
        ],
    }


# ---------------------------------------------------------- handover 3.2
class HandoverRequest(BaseModel):
    mode: HandoverMode


@router.post("/escalations/{escalation_id}/handover")
async def handover(escalation_id: str, body: HandoverRequest, request: Request,
                   counselor: Counselor = Depends(require_counselor),
                   db: AsyncSession = Depends(get_db)):
    """One-click: bot pauses (counselor_active) or resumes (bot_active)."""
    esc = await db.get(Escalation, escalation_id)
    if esc is None:
        raise HTTPException(404, "escalation not found")
    service = _service_from_request(request)
    await service.set_handover(
        db, esc.user_uuid, body.mode,
        counselor_id=counselor.id,
        escalation_id=esc.id if body.mode is HandoverMode.counselor_active else None,
    )
    if body.mode is HandoverMode.counselor_active:
        esc.status = EscalationStatus.handed_over
        if esc.claimed_by is None:
            esc.claimed_by = counselor.id
            esc.claimed_at = dt.datetime.now(dt.timezone.utc)
    elif esc.status is EscalationStatus.handed_over:
        esc.status = EscalationStatus.claimed
    await db.commit()
    return {"escalation": _esc_dict(esc), "mode": body.mode.value}


class CounselorReplyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/sessions/{user_uuid}/reply")
async def counselor_reply(user_uuid: str, body: CounselorReplyRequest,
                          request: Request,
                          counselor: Counselor = Depends(require_counselor),
                          db: AsyncSession = Depends(get_db)):
    """Counselor types directly to the user (requires handover mode)."""
    control = await db.get(SessionControl, user_uuid)
    if control is None or control.mode is not HandoverMode.counselor_active:
        raise HTTPException(409, "handover mode not active for this user")
    wa = request.app.state.wa
    vault = request.app.state.identity
    msisdn = await vault.reveal_msisdn(db, user_uuid)
    if msisdn is None:
        raise HTTPException(410, "identity purged/opted-out; cannot deliver")
    await wa.send_text(msisdn, body.text)

    session_id = await db.scalar(
        select(Escalation.session_id).where(Escalation.user_uuid == user_uuid)
        .order_by(Escalation.created_at.desc()).limit(1))
    db.add(Message(session_id=session_id or "direct",
                   direction=MessageDirection.outbound,
                   kind=MessageKind.system, body=body.text[:4000]))
    # SLA clock: first counselor message marks first response.
    esc = await db.scalar(
        select(Escalation).where(
            Escalation.user_uuid == user_uuid,
            Escalation.status.in_([EscalationStatus.claimed, EscalationStatus.handed_over]),
        ).order_by(Escalation.created_at.desc()).limit(1))
    if esc is not None and esc.first_response_at is None:
        esc.first_response_at = dt.datetime.now(dt.timezone.utc)
    await audit_append(
        db, action=AuditAction.counselor_replied, actor_type="counselor",
        actor_id=counselor.id, subject_uuid=user_uuid,
        details={"escalation_id": esc.id if esc else None,
                 "length": len(body.text)},
    )
    await db.commit()
    return {"sent": True}


# ------------------------------------------------------------ audit 3.3
@router.get("/audit")
async def read_audit(limit: int = 200,
                     _: Counselor = Depends(require_counselor),
                     db: AsyncSession = Depends(get_db)):
    from app.db.models import AuditLogEntry

    rows = (await db.scalars(
        select(AuditLogEntry).order_by(AuditLogEntry.seq.desc()).limit(min(limit, 1000))
    )).all()
    return [{
        "seq": r.seq, "timestamp": r.timestamp.isoformat(),
        "actor_type": r.actor_type, "actor_id": r.actor_id,
        "action": r.action.value, "subject_uuid": r.subject_uuid,
        "details": r.details_json, "entry_hash": r.entry_hash,
    } for r in rows]


@router.get("/audit/verify")
async def audit_verify(_: Counselor = Depends(require_counselor),
                       db: AsyncSession = Depends(get_db)):
    return await verify_chain(db)


@router.get("/audit/export", response_class=PlainTextResponse)
async def audit_export(_: Counselor = Depends(require_counselor),
                       db: AsyncSession = Depends(get_db)):
    csv_text = await export_audit_csv(db)
    return PlainTextResponse(
        csv_text, media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="okoa_audit.csv"'},
    )


# ------------------------------------------------------- metrics & drills
@router.get("/metrics/sla")
async def sla_metrics(_: Counselor = Depends(require_counselor),
                      db: AsyncSession = Depends(get_db)):
    """Median/percentile time-to-first-response vs the < 2 min PRD KPI."""
    rows = (await db.scalars(
        select(Escalation).where(Escalation.first_response_at.is_not(None))
    )).all()
    deltas = sorted(
        (e.first_response_at - (e.created_at if e.created_at.tzinfo else
                                e.created_at.replace(tzinfo=dt.timezone.utc))).total_seconds()
        for e in rows if e.created_at and e.first_response_at
    )
    open_rows = (await db.scalars(
        select(func.count()).select_from(Escalation)
        .where(Escalation.status.in_([EscalationStatus.open, EscalationStatus.claimed,
                                      EscalationStatus.handed_over]))
    )).one()
    def pct(p: float) -> float | None:
        if not deltas:
            return None
        idx = min(len(deltas) - 1, int(p * (len(deltas) - 1)))
        return round(deltas[idx], 1)
    return {
        "count_with_response": len(deltas),
        "median_seconds": pct(0.5),
        "p90_seconds": pct(0.9),
        "max_seconds": round(max(deltas), 1) if deltas else None,
        "currently_open": int(open_rows or 0),
    }


class DrillRequest(BaseModel):
    scenario: str
    seconds_to_first_response: float
    participants: str | None = None
    notes: str | None = None


@router.post("/drills")
async def record_drill(body: DrillRequest, request: Request,
                       counselor: Counselor = Depends(require_counselor),
                       db: AsyncSession = Depends(get_db)):
    """SOP dry-run record (roadmap 3.5); pass/fail vs configured SLA."""
    sla = getattr(request.app.state, "settings", None)
    threshold = sla.escalation_sla_seconds if sla else 120
    drill = ResponseDrill(
        scenario=body.scenario,
        seconds_to_first_response=body.seconds_to_first_response,
        participants=body.participants,
        passed=body.seconds_to_first_response <= threshold,
        notes=body.notes,
    )
    db.add(drill)
    await audit_append(
        db, action=AuditAction.drill_recorded, actor_type="counselor",
        actor_id=counselor.id,
        details={"scenario": body.scenario,
                 "seconds": body.seconds_to_first_response,
                 "passed": drill.passed},
    )
    await db.commit()
    return {"id": drill.id, "passed": drill.passed}


@router.get("/drills/summary")
async def drill_summary(_: Counselor = Depends(require_counselor),
                        db: AsyncSession = Depends(get_db)):
    """Weekly rollup used for the exit criterion: two consecutive weeks < 2 min median."""
    rows = (await db.scalars(select(ResponseDrill).order_by(ResponseDrill.performed_at))).all()
    by_week: dict[str, list[float]] = {}
    for r in rows:
        wk = r.performed_at.strftime("%G-W%V") if r.performed_at else "?"
        by_week.setdefault(wk, []).append(r.seconds_to_first_response)
    weeks = [{"week": w, "n": len(v), "median_seconds": round(sorted(v)[len(v) // 2], 1)}
             for w, v in sorted(by_week.items())]
    ok = [w for w in weeks if w["median_seconds"] < 120]
    return {"weeks": weeks, "weeks_passing_120s": len(ok)}


# ------------------------------------------------------------- on-duty 3.4
class DutyRequest(BaseModel):
    on_duty: bool


@router.post("/me/duty")
async def set_duty(body: DutyRequest,
                   counselor: Counselor = Depends(require_counselor),
                   db: AsyncSession = Depends(get_db)):
    counselor.is_on_duty = body.on_duty
    await db.commit()
    return {"is_on_duty": counselor.is_on_duty}


# ----------------------------------------------------------------- helpers
async def _broadcast(payload: dict) -> None:
    from app.api.ws import hub

    try:
        await hub.broadcast(payload)
    except Exception:  # pragma: no cover
        logger.exception("ws broadcast failed")


def _service_from_request(request: Request):
    # Reuse the app-wide escalation service.
    return request.app.state.escalation
