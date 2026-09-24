"""Shared pytest fixtures: in-memory-ish SQLite DB, fake Redis, signed webhook helper."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

# --- Environment must be set BEFORE importing app modules -------------------
TEST_VAULT_KEY = base64.b64encode(os.urandom(32)).decode()

os.environ.update(
    ENVIRONMENT="development",
    DATABASE_URL=f"sqlite:///{uuid.uuid4().hex}.test.db",
    REDIS_URL="redis://localhost:6399/0",  # deliberately unreachable → degraded mode path
    VAULT_MASTER_KEY=TEST_VAULT_KEY,
    WHATSAPP_VERIFY_TOKEN="test-verify-token",
    WHATSAPP_APP_SECRET="test-app-secret",
    WHATSAPP_ACCESS_TOKEN="test-access-token",
    WHATSAPP_PHONE_NUMBER_ID="1234567890",
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def db_session_factory() -> AsyncIterator:
    from app.db.session import dispose_engine, get_session_factory, init_models

    await init_models()
    yield get_session_factory()
    await dispose_engine()


class FakeWhatsAppClient:
    """Captures outbound sends; never touches the network."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send_text(self, to_wa_id: str, body: str, *, preview_url: bool = False) -> str:
        self.sent.append((to_wa_id, body))
        return f"wamid.fake.{len(self.sent)}"


@pytest_asyncio.fixture(autouse=True)
async def clean_db(db_session_factory):
    """Truncate all tables between tests (fresh identity space per test)."""
    from app.db.models import Base

    from app.db.session import get_engine

    eng = get_engine()
    async with eng.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    yield


@pytest_asyncio.fixture
async def client(db_session_factory, clean_db):
    """App + httpx AsyncClient with pipeline wired to fakes (fakeredis-backed)."""
    from httpx import ASGITransport, AsyncClient

    import app.main as main_mod
    from app.services.identity_service import IdentityService
    from app.services.pipeline import MessagePipeline
    from app.services.session_store import SessionStore, reset_for_tests
    from app.vault.crypto import IdentityVault

    try:
        import fakeredis.aioredis

        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    except Exception:  # pragma: no cover - fakeredis is a dev dependency
        fake_redis = None
    reset_for_tests(fake_redis)

    settings = main_mod.get_settings()
    vault = IdentityVault(settings.vault_master_key)
    fake_wa = FakeWhatsAppClient()

    transport = ASGITransport(app=main_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Fresh pipeline per test; DB shared within the test's SQLite file.
        identity = IdentityService(vault)
        sessions = SessionStore()
        pipeline = MessagePipeline(db_session_factory, identity, sessions, fake_wa)
        pipeline.start()
        main_mod.app.state.pipeline = pipeline
        ac.wa = fake_wa  # type: ignore[attr-defined]
        ac.pipeline = pipeline  # type: ignore[attr-defined]
        yield ac
        await pipeline.stop()


def sign_body(body: bytes, secret: str | None = None) -> str:
    secret = secret or os.environ["WHATSAPP_APP_SECRET"]
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def wa_message_payload(wa_user_id: str, text: str, msg_id: str | None = None) -> bytes:
    msg_id = msg_id or f"wamid.{uuid.uuid4().hex}"
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "111",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "1555000", "phone_number_id": "1234567890"},
                            "contacts": [{"wa_id": wa_user_id, "profile": {"name": "X"}}],
                            "messages": [
                                {"from": wa_user_id, "id": msg_id, "timestamp": "1700000000",
                                 "type": "text", "text": {"body": text}}
                            ],
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(payload).encode()
