"""Tamper-evident append-only audit trail (roadmap 3.3, TRD §5 accountability).

Each entry's ``entry_hash`` = SHA-256(prev_hash | canonical fields). Any
edit, reorder or deletion breaks the chain, which verify_chain() detects.
Writes go through audit_append() only — there is deliberately no update or
delete helper in this module.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditAction, AuditLogEntry

logger = logging.getLogger("okoa.audit")


def compute_entry_hash(
    prev_hash: str, timestamp: dt.datetime, actor_type: str, actor_id: str | None,
    action: str, subject_uuid: str | None, details_json: str,
) -> str:
    ts = timestamp.isoformat()
    material = "|".join([prev_hash, ts, actor_type, actor_id or "", action,
                         subject_uuid or "", details_json])
    return hashlib.sha256(material.encode()).hexdigest()


async def _last_entry(db: AsyncSession) -> AuditLogEntry | None:
    return await db.scalar(
        select(AuditLogEntry).order_by(AuditLogEntry.seq.desc()).limit(1)
    )


async def audit_append(
    db: AsyncSession,
    *,
    action: AuditAction,
    actor_type: str,
    actor_id: str | None = None,
    subject_uuid: str | None = None,
    details: dict | None = None,
) -> AuditLogEntry:
    """Append one chained entry. Never call db.commit() here — participates
    in the caller's transaction so DB write + audit are atomic."""

    now = dt.datetime.now(dt.timezone.utc)
    prev = await _last_entry(db)
    prev_hash = prev.entry_hash if prev else "GENESIS"
    details_json = json.dumps(details or {}, sort_keys=True, ensure_ascii=False)

    entry = AuditLogEntry(
        timestamp=now,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        subject_uuid=subject_uuid,
        details_json=details_json,
        prev_hash=prev_hash,
    )
    entry.entry_hash = compute_entry_hash(
        prev_hash, now, actor_type, actor_id, action.value, subject_uuid, details_json
    )
    db.add(entry)
    await db.flush()
    return entry


async def verify_chain(db: AsyncSession) -> dict:
    """Walk the whole log recomputing hashes; report first breakage."""
    entries = (await db.scalars(select(AuditLogEntry).order_by(AuditLogEntry.seq))).all()
    expected_prev = "GENESIS"
    for e in entries:
        recomputed = compute_entry_hash(
            e.prev_hash, e.timestamp, e.actor_type, e.actor_id,
            e.action.value, e.subject_uuid, e.details_json,
        )
        if e.prev_hash != expected_prev:
            return {"valid": False, "broken_at_seq": e.seq, "reason": "prev_hash mismatch"}
        if recomputed != e.entry_hash:
            return {"valid": False, "broken_at_seq": e.seq, "reason": "entry_hash mismatch"}
        expected_prev = e.entry_hash
    return {"valid": True, "length": len(entries)}


async def export_audit_csv(db: AsyncSession) -> str:
    """CSV export of the full trail (exit criterion: 'audit export works')."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["seq", "timestamp", "actor_type", "actor_id", "action",
                "subject_uuid", "details", "prev_hash", "entry_hash"])
    entries = (await db.scalars(select(AuditLogEntry).order_by(AuditLogEntry.seq))).all()
    for e in entries:
        w.writerow([e.seq, e.timestamp.isoformat(), e.actor_type, e.actor_id or "",
                    e.action.value, e.subject_uuid or "", e.details_json,
                    e.prev_hash, e.entry_hash])
    return buf.getvalue()
