"""Identity service — glue between webhook phone ids and anonymous UUIDs.

Flow for every inbound message:
  wa_user_id (phone digits from webhook)
    -> blind index lookup in identity_vault
       * found   -> return user_uuid (plaintext phone discarded immediately)
       * missing -> enrol(): create UUID + encrypted vault row atomically

Downstream code receives ONLY the UUID. The raw phone never leaves this module
except transiently in memory during enrolment.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IdentityVaultRow, User
from app.vault.crypto import IdentityVault, normalise_msisdn

logger = logging.getLogger("okoa.identity")


class IdentityService:
    def __init__(self, vault: IdentityVault):
        self.vault = vault

    async def resolve_or_enrol(self, db: AsyncSession, wa_user_id: str) -> tuple[str, bool]:
        """Return (user_uuid, is_new_user)."""
        msisdn = normalise_msisdn(wa_user_id)
        bi = self.vault.blind_index(msisdn)

        row = await db.scalar(select(IdentityVaultRow).where(IdentityVaultRow.blind_index == bi))
        if row is not None:
            return row.user_uuid, False

        user_uuid, fields = self.vault.enrol(msisdn)
        vault_row = IdentityVaultRow(
            id=fields["id"],
            blind_index=fields["blind_index"],
            user_uuid=user_uuid,
            cipher_nonce=fields["cipher_nonce"],
            ciphertext=fields["ciphertext"],
            status="active",
        )
        user = User(user_uuid=user_uuid)
        db.add(vault_row)
        db.add(user)
        try:
            await db.flush()
        except IntegrityError:
            # Concurrent webhook for the same new number: fetch winner's UUID.
            await db.rollback()
            row = await db.scalar(
                select(IdentityVaultRow).where(IdentityVaultRow.blind_index == bi)
            )
            if row is None:
                raise
            return row.user_uuid, False
        logger.info("new anonymous identity created", extra={"event": "enrol", "user_uuid": user_uuid})
        return user_uuid, True

    async def mark_seen(self, db: AsyncSession, user_uuid: str) -> None:
        user = await db.get(User, user_uuid)
        if user is not None:
            user.last_seen_at = dt.datetime.now(dt.timezone.utc)

    async def set_consent(self, db: AsyncSession, user_uuid: str, granted: bool) -> None:
        from app.db.models import ConsentStatus

        user = await db.get(User, user_uuid)
        if user is not None:
            user.consent_status = ConsentStatus.granted if granted else ConsentStatus.withdrawn

    async def opt_out(self, db: AsyncSession, user_uuid: str) -> None:
        """STOP/FUTA handling stub (roadmap 1.5). Full wipe lands in Phase 6.3."""
        from app.db.models import ConsentStatus

        user = await db.get(User, user_uuid)
        if user is not None:
            user.opt_out = True
            user.consent_status = ConsentStatus.withdrawn
        vault_row = await db.scalar(
            select(IdentityVaultRow).where(
                IdentityVaultRow.user_uuid == user_uuid,
                IdentityVaultRow.status == "active",
            )
        )
        if vault_row is not None:
            vault_row.status = "tombstoned"
            vault_row.purged_at = dt.datetime.now(dt.timezone.utc)
            # Hard-delete ciphertext now; tombstone row kept for audit only.
            vault_row.ciphertext = b""
            vault_row.blind_index = "purged:" + vault_row.id
        logger.info("user opted out", extra={"event": "opt_out", "user_uuid": user_uuid})

    async def reveal_msisdn(self, db: AsyncSession, user_uuid: str) -> str | None:
        """Reverse lookup UUID -> phone, ONLY for outbound delivery.

        Privacy note: the message pipeline already holds the phone transiently
        from the webhook and never calls this. It exists for the counselor
        handover reply path (Phase 3), where the dashboard initiated contact
        without a webhook context. The returned value must never be logged —
        callers pass it straight to WhatsAppClient.send_text.
        Returns None when the identity was purged (opt-out tombstone).
        """
        row = await db.scalar(
            select(IdentityVaultRow).where(
                IdentityVaultRow.user_uuid == user_uuid,
                IdentityVaultRow.status == "active",
            )
        )
        if row is None or not row.ciphertext:
            return None
        return self.vault.decrypt_phone(
            bytes(row.cipher_nonce), bytes(row.ciphertext), user_uuid
        )
