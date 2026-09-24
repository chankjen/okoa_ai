"""Phase 1 test suite — WhatsApp gateway & anonymous identity."""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from tests.conftest import sign_body, wa_message_payload

PHONE = "254712345678"


async def _post_webhook(client, text: str, msg_id: str | None = None, phone: str = PHONE):
    body = wa_message_payload(phone, text, msg_id)
    resp = await client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": sign_body(body), "Content-Type": "application/json"},
    )
    return resp


async def _drain(client):
    """Wait until the pipeline queue is fully processed."""
    await client.pipeline.queue.join() if hasattr(client.pipeline.queue, "join") else None
    for _ in range(50):
        if client.pipeline.queue.empty():
            await asyncio.sleep(0.02)  # let worker finish DB commit
            break
        await asyncio.sleep(0.02)


# ---------------------------------------------------------------- webhook auth
@pytest.mark.asyncio
async def test_url_verification_challenge(client):
    resp = await client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "test-verify-token", "hub.challenge": "42"},
    )
    assert resp.status_code == 200
    assert resp.json() == "42"


@pytest.mark.asyncio
async def test_url_verification_rejects_bad_token(client):
    resp = await client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_missing_signature_rejected(client):
    body = wa_message_payload(PHONE, "hello")
    resp = await client.post("/webhooks/whatsapp", content=body)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tampered_signature_rejected(client):
    body = wa_message_payload(PHONE, "hello")
    resp = await client.post(
        "/webhooks/whatsapp", content=body,
        headers={"X-Hub-Signature-256": sign_body(b"different-body")},
    )
    assert resp.status_code == 401


# ------------------------------------------------------------- happy pathway
@pytest.mark.asyncio
async def test_first_message_gets_consent_prompt_and_no_plaintext_phone_in_db(client, db_session_factory):
    resp = await _post_webhook(client, "Habari")
    assert resp.status_code == 200
    await _drain(client)

    # Canned consent prompt sent back to the right number (fake WA captured it).
    assert len(client.wa.sent) == 1
    assert "AGREE" in client.wa.sent[0][1]

    async with db_session_factory() as db:
        from app.db.models import IdentityVaultRow, Message, User

        users = (await db.scalars(select(User))).all()
        assert len(users) == 1
        msgs = (await db.scalars(select(Message))).all()
        bodies = " ".join(m.body for m in msgs)
        # Exit criterion: no plaintext phone anywhere outside the vault ciphertext.
        assert PHONE not in bodies
        rows = (await db.scalars(select(IdentityVaultRow))).all()
        assert len(rows) == 1
        assert PHONE.encode() not in rows[0].ciphertext  # encrypted!


@pytest.mark.asyncio
async def test_consent_flow_then_canned_reply(client):
    await _post_webhook(client, "Habari yangu")
    await _drain(client)
    await _post_webhook(client, "AGREE", msg_id="wamid.agree.1")
    await _drain(client)
    await _post_webhook(client, "Nimechoka na maisha ya Nairobi", msg_id="wamid.m1")
    await _drain(client)

    texts = [t for _, t in client.wa.sent]
    assert any("Asante kwa kushiriki" in t for t in texts)  # canned reply after consent


@pytest.mark.asyncio
async def test_duplicate_webhook_processed_once(client):
    fixed_id = "wamid.duplicate.1"
    await _post_webhook(client, "Hello", msg_id=fixed_id)
    await _drain(client)
    await _post_webhook(client, "Hello", msg_id=fixed_id)  # Meta retry
    await _drain(client)
    assert len(client.wa.sent) == 1  # only first delivery answered

    # DB-level safety net: exactly one inbound row for that wa_message_id.
    async with client.pipeline.session_factory() as db:
        from app.db.models import Message

        rows = (
            await db.scalars(select(Message).where(Message.wa_message_id == fixed_id))
        ).all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_crisis_keyword_intercept_sends_helpline(client):
    # Consent first
    await _post_webhook(client, "Hi")
    await _drain(client)
    await _post_webhook(client, "AGREE", msg_id="wamid.a2")
    await _drain(client)
    client.wa.sent.clear()

    await _post_webhook(client, "Sina nguvu tena, nataka kuisha maisha yangu", msg_id="wamid.c1")
    await _drain(client)

    assert len(client.wa.sent) == 1
    reply = client.wa.sent[0][1]
    assert "1199" in reply
    assert "paused" in reply.lower() or "counselor" in reply.lower()


@pytest.mark.asyncio
async def test_crisis_english_variant(client):
    await _post_webhook(client, "Hi")
    await _drain(client)
    await _post_webhook(client, "agree", msg_id="wamid.a3")
    await _drain(client)
    client.wa.sent.clear()
    await _post_webhook(client, "I want to end my life tonight", msg_id="wamid.c2")
    await _drain(client)
    assert "1199" in client.wa.sent[-1][1]


@pytest.mark.asyncio
async def test_opt_out_stop(client, ):
    await _post_webhook(client, "Hi")
    await _drain(client)
    await _post_webhook(client, "STOP", msg_id="wamid.s1")
    await _drain(client)
    assert any("tumesimamisha" in t for _, t in client.wa.sent)

    # Subsequent messages ignored entirely
    client.wa.sent.clear()
    await _post_webhook(client, "Ukopo?", msg_id="wamid.s2")
    await _drain(client)
    assert client.wa.sent == []


@pytest.mark.asyncio
async def test_same_number_maps_to_same_uuid_new_uuid_per_number(client):
    await _post_webhook(client, "Hi", msg_id="wamid.u1")
    await _drain(client)
    await _post_webhook(client, "Hello again", msg_id="wamid.u2")
    await _drain(client)
    await _post_webhook(client, "Different person", msg_id="wamid.u3", phone="254799888777")
    await _drain(client)

    async with client.pipeline.session_factory() as db:
        from app.db.models import IdentityVaultRow

        rows = (await db.scalars(select(IdentityVaultRow))).all()
        assert len(rows) == 2
        uuids = {r.user_uuid for r in rows}
        assert len(uuids) == 2


# ------------------------------------------------------------------- vault unit
class TestVaultCrypto:
    def test_roundtrip(self):
        from app.vault.crypto import IdentityVault
        from tests.conftest import TEST_VAULT_KEY

        v = IdentityVault(TEST_VAULT_KEY)
        uid, row = v.enrol("0712345678")
        assert v.resolve(row) == "+254712345678"
        assert uid == row["user_uuid"]

    def test_normalisation_equivalence(self):
        from app.vault.crypto import IdentityVault, normalise_msisdn
        from tests.conftest import TEST_VAULT_KEY

        forms = ["0712345678", "+254712345678", "254712345678", "0712 345 678"]
        norm = {normalise_msisdn(f) for f in forms}
        assert norm == {"+254712345678"}
        v = IdentityVault(TEST_VAULT_KEY)
        assert len({v.blind_index(f) for f in forms}) == 1

    def test_aad_binds_uuid(self):
        """Ciphertext must not decrypt under a different user UUID."""
        import pytest as _pytest
        from cryptography.exceptions import InvalidTag

        from app.vault.crypto import IdentityVault
        from tests.conftest import TEST_VAULT_KEY

        v = IdentityVault(TEST_VAULT_KEY)
        _, row = v.enrol("+254700000111")
        with _pytest.raises(InvalidTag):
            v.decrypt_phone(row["cipher_nonce"], row["ciphertext"], "someone-elses-uuid")

    def test_bad_key_length_rejected(self):
        import base64 as b64

        import pytest as _pytest

        from app.vault.crypto import IdentityVault

        with _pytest.raises(ValueError):
            IdentityVault(b64.b64encode(b"x" * 16).decode())


# ------------------------------------------------------------------ log scrubber
class TestPIIScrubbing:
    def test_scrubs_kenyan_numbers(self):
        from app.core.logging import scrub

        assert scrub("namba yangu ni 0712345678") == "namba yangu ni <REDACTED_PHONE>"
        assert scrub("call +254712345678 now") == "call <REDACTED_PHONE> now"

    def test_leaves_non_pii(self):
        from app.core.logging import scrub

        assert scrub("mzee habari 12:30 pm") == "mzee habari 12:30 pm"


# ------------------------------------------------------------------- crisis kw
class TestCrisisKeywords:
    @pytest.mark.parametrize(
        "text",
        [
            "nataka kuisha",
            "Sit want to kill myself",
            "maisha yamekwisha mbona",
            "I'm so suicidal right now",
            "nitajiua kesho",
            "siwezi kuendelea",
        ],
    )
    def test_positive(self, text):
        from app.services.crisis_keywords import detect_crisis

        assert detect_crisis(text)

    @pytest.mark.parametrize(
        "text",
        [
            "nimechoka na kazi leo",          # tired, not suicidal
            "kuishana na marafiki",           # 'ignite each other' style false positive
            "habari gani",
            "I want to eat ugali",
        ],
    )
    def test_negative(self, text):
        from app.services.crisis_keywords import detect_crisis

        assert not detect_crisis(text)
