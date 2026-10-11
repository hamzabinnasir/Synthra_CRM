"""Application settings, loaded from environment variables (and an optional backend/.env file)."""
from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _check_key(value: str, name: str) -> str:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{name} must be base64-encoded") from exc
    if len(raw) != 32:
        raise ValueError(f"{name} must decode to exactly 32 bytes (AES-256)")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    env: str = Field("development", alias="CRM_ENV")
    database_url: str = Field(..., alias="CRM_DATABASE_URL")
    auto_migrate: bool = Field(False, alias="CRM_AUTO_MIGRATE")
    # Allow tenants to connect to databases on private/loopback addresses. Keep false when hosting for
    # other people (it would let them probe your internal network); true is fine for self-hosting alone.
    allow_private_db_hosts: bool = Field(False, alias="CRM_ALLOW_PRIVATE_DB_HOSTS")

    # Secrets. Generate with:  python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
    master_key: str = Field(..., alias="CRM_MASTER_KEY")
    master_key_previous: Optional[str] = Field(None, alias="CRM_MASTER_KEY_PREVIOUS")
    jwt_secret: str = Field(..., alias="CRM_JWT_SECRET")

    # Web
    public_url: str = Field("http://localhost:3000", alias="CRM_PUBLIC_URL")
    allowed_origins: List[str] = Field(default_factory=list, alias="CRM_ALLOWED_ORIGINS")
    trusted_proxy_hops: int = Field(0, alias="CRM_TRUSTED_PROXY_HOPS", ge=0, le=5)
    # Public base URL of THIS API as Meta reaches it; webhook URLs shown in the dashboard are built from it.
    api_public_url: str = Field("http://localhost:8000", alias="CRM_API_PUBLIC_URL")

    # Meta Graph API
    graph_version: str = Field("v21.0", alias="CRM_GRAPH_VERSION", pattern=r"^v\d+\.\d+$")
    instagram_graph_version: str = Field("v25.0", alias="CRM_INSTAGRAM_GRAPH_VERSION", pattern=r"^v\d+\.\d+$")

    # Webhook intake and background worker
    webhook_max_body_bytes: int = Field(1_000_000, alias="CRM_WEBHOOK_MAX_BODY_BYTES", ge=10_000)
    webhook_rate_per_minute: int = Field(1200, alias="CRM_WEBHOOK_RATE_PER_MINUTE", ge=10)
    run_worker: bool = Field(True, alias="CRM_RUN_WORKER")   # set false on API-only replicas
    worker_concurrency: int = Field(8, alias="CRM_WORKER_CONCURRENCY", ge=1, le=64)
    # Bot engine, LLM and knowledge base
    # Each workspace brings its own LLM key; this cap only protects the key (and the user's bill) from loops/abuse.
    llm_daily_calls: int = Field(2000, alias="CRM_LLM_DAILY_CALLS", ge=1)
    llm_timeout_seconds: float = Field(25.0, alias="CRM_LLM_TIMEOUT_SECONDS", ge=3, le=120)
    # fastembed = real semantic embeddings (downloads a ~90 MB model once); hash = offline/dev fallback.
    embedder: str = Field("fastembed", alias="CRM_EMBEDDER", pattern=r"^(fastembed|hash)$")
    max_upload_bytes: int = Field(5_000_000, alias="CRM_MAX_UPLOAD_BYTES", ge=10_000)
    max_media_bytes: int = Field(16_000_000, alias="CRM_MAX_MEDIA_BYTES", ge=100_000)
    max_kb_documents: int = Field(200, alias="CRM_MAX_KB_DOCUMENTS", ge=1)

    # Auth policy
    access_ttl_minutes: int = Field(10, alias="CRM_ACCESS_TTL_MINUTES", ge=1, le=60)
    refresh_ttl_days: int = Field(30, alias="CRM_REFRESH_TTL_DAYS", ge=1, le=90)
    refresh_reuse_grace_seconds: int = Field(10, alias="CRM_REFRESH_REUSE_GRACE_SECONDS", ge=0, le=60)
    verify_ttl_hours: int = Field(24, alias="CRM_VERIFY_TTL_HOURS", ge=1)
    reset_ttl_minutes: int = Field(60, alias="CRM_RESET_TTL_MINUTES", ge=5)
    require_email_verification: bool = Field(True, alias="CRM_REQUIRE_EMAIL_VERIFICATION")
    login_max_failures: int = Field(5, alias="CRM_LOGIN_MAX_FAILURES", ge=1)
    login_window_minutes: int = Field(15, alias="CRM_LOGIN_WINDOW_MINUTES", ge=1)
    login_ip_max_attempts: int = Field(40, alias="CRM_LOGIN_IP_MAX_ATTEMPTS", ge=1)

    # Mail (SMTP). Without SMTP_HOST, development falls back to printing links to the server log.
    smtp_host: Optional[str] = Field(None, alias="CRM_SMTP_HOST")
    smtp_port: int = Field(587, alias="CRM_SMTP_PORT")
    smtp_user: Optional[str] = Field(None, alias="CRM_SMTP_USER")
    smtp_password: Optional[str] = Field(None, alias="CRM_SMTP_PASSWORD")
    smtp_from: str = Field("Synthra CRM <no-reply@localhost>", alias="CRM_SMTP_FROM")

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if isinstance(value, str):
            return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]
        return value

    @field_validator("master_key")
    @classmethod
    def _master_key_ok(cls, value: str) -> str:
        return _check_key(value, "CRM_MASTER_KEY")

    @field_validator("master_key_previous")
    @classmethod
    def _previous_key_ok(cls, value: Optional[str]) -> Optional[str]:
        return _check_key(value, "CRM_MASTER_KEY_PREVIOUS") if value else value

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_ok(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("CRM_JWT_SECRET must be at least 32 characters")
        return value

    @model_validator(mode="after")
    def _production_rules(self):
        if self.env == "production":
            if not self.public_url.startswith("https://"):
                raise ValueError("CRM_PUBLIC_URL must be https:// in production")
            if self.require_email_verification and not self.smtp_host:
                raise ValueError("CRM_SMTP_HOST is required in production when email verification is on")
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cookie_secure(self) -> bool:
        return self.env != "development"

    @property
    def origins(self) -> List[str]:
        return sorted({self.public_url.rstrip("/"), *self.allowed_origins})


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
