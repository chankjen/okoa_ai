"""SQLAlchemy models — Phase 1 schema.

Privacy invariant (TRD §5): only ``identity_vault`` ever touches a phone number.
Every other table references users exclusively via ``user_uuid``.
"""
from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid_str() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class ConsentStatus(str, enum.Enum):
    pending = "pending"      # first contact, consent screen not yet accepted
    granted = "granted"      # user replied AGREE / NDIPO
    withdrawn = "withdrawn"  # STOP / FUTA — bot silent, data purge scheduled


class OptOutStatus(str, enum.Enum):
    active = "active"
    opted_out = "opted_out"


class User(Base):
    """Anonymous user — UUID is the ONLY identifier."""

    __tablename__ = "users"

    user_uuid: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    language: Mapped[str | None] = mapped_column(String(10))  # sw | en | sheng (Phase 4.6 refines)
    consent_status: Mapped[ConsentStatus] = mapped_column(
        Enum(ConsentStatus, name="consent_status"), default=ConsentStatus.pending
    )
    opt_out: Mapped[bool] = mapped_column(Boolean, default=False)
    data_purged: Mapped[bool] = mapped_column(Boolean, default=False)

    sessions: Mapped[list["ChatSession"]] = relationship(back_populates="user")


class ChatSession(Base):
    """Conversation window; Redis holds hot state, this is durable record."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    user_uuid: Mapped[str] = mapped_column(ForeignKey("users.user_uuid"), index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_activity_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(back_populates="session")


class MessageDirection(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"


class MessageKind(str, enum.Enum):
    user_text = "user_text"
    canned_reply = "canned_reply"
    crisis_response = "crisis_response"   # Phase 2 intercept (keyword fallback now)
    consent_prompt = "consent_prompt"
    system = "system"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    direction: Mapped[MessageDirection] = mapped_column(Enum(MessageDirection, name="msg_direction"))
    kind: Mapped[MessageKind] = mapped_column(Enum(MessageKind, name="msg_kind"), default=MessageKind.user_text)
    # WhatsApp provider message id — enables dedupe of webhook retries.
    wa_message_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Phase 2 placeholder: risk score written by the sentiment engine.
    risk_label: Mapped[str | None] = mapped_column(String(16), nullable=True)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class IdentityVaultRow(Base):
    """The ONLY place PII lives — encrypted at rest (AES-256-GCM)."""

    __tablename__ = "identity_vault"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    blind_index: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    cipher_nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    purged_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_messages_session_created", Message.session_id, Message.created_at)
