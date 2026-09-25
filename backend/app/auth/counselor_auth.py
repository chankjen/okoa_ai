"""Counselor authentication (roadmap 3.2 prerequisite for the dashboard API).

* bcrypt password hashes (passlib) — plaintext never stored;
* stateless JWT bearer tokens, TTL from settings.jwt_ttl_minutes;
* FastAPI dependency ``require_counselor`` guarding every /counselor route;
* bootstrap helper to create the first counselor accounts (CLI-ish script at
  scripts/create_counselor.py).
"""
from __future__ import annotations

import datetime as dt
import logging
import secrets

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Counselor
from app.db.session import get_db

logger = logging.getLogger("okoa.auth")
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd.verify(plain, hashed)
    except ValueError:
        return False


def generate_strong_password(length: int = 14) -> str:
    return secrets.token_urlsafe(length)


def create_token(counselor: Counselor) -> tuple[str, int]:
    s = get_settings()
    if not s.jwt_secret:
        raise RuntimeError("JWT_SECRET is required for the counselor dashboard")
    ttl_seconds = s.jwt_ttl_minutes * 60
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": counselor.id,
        "username": counselor.username,
        "name": counselor.display_name,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256"), ttl_seconds


def decode_token(token: str) -> dict:
    s = get_settings()
    try:
        return jwt.decode(token, s.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")


async def authenticate(db: AsyncSession, username: str, password: str) -> Counselor | None:
    counselor = await db.scalar(
        select(Counselor).where(Counselor.username == username, Counselor.is_active.is_(True))
    )
    if counselor is None or not verify_password(password, counselor.password_hash):
        return None
    return counselor


async def require_counselor(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> Counselor:
    """Guard for all dashboard endpoints; returns the live Counselor row."""
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    payload = decode_token(creds.credentials)
    counselor = await db.get(Counselor, payload["sub"])
    if counselor is None or not counselor.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account disabled")
    return counselor
