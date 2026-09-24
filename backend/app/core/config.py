"""OKOA AI backend configuration (Pydantic Settings).

All secrets come from environment variables / .env — never commit real values.
Phase 1 additions: WhatsApp Cloud API credentials + vault master key.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Application -------------------------------------------------------
    app_name: str = "okoa-backend"
    environment: str = Field(default="development")  # development | staging | production
    debug: bool = False

    # --- Database / Redis --------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://okoa:okoa@localhost:5432/okoa"
    )
    redis_url: str = Field(default="redis://localhost:6379/0")
    session_ttl_seconds: int = 60 * 60 * 24  # 24h idle window for chat sessions

    # --- Identity vault (TRD §5) --------------------------------------------
    # AES-256-GCM key (base64 of 32 random bytes) used to encrypt the
    # phone <-> UUID mapping at rest. Generate with:
    #   python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
    vault_master_key: str = Field(default="", repr=False)

    # --- WhatsApp Business Cloud API (Phase 1) ------------------------------
    whatsapp_verify_token: str = Field(default="", repr=False)
    whatsapp_app_secret: str = Field(default="", repr=False)
    whatsapp_access_token: str = Field(default="", repr=False)
    whatsapp_phone_number_id: str = Field(default="")
    whatsapp_api_base: str = "https://graph.facebook.com/v20.0"
    webhook_signature_tolerance_seconds: int = 600  # replay protection window

    # --- Behaviour flags -----------------------------------------------------
    # While the risk engine (Phase 2) is not live, inbound messages get a
    # canned reply. Crisis keyword fallback ships early as belt-and-braces.
    enable_crisis_keyword_fallback: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
