from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Startup configuration is unusable; the message says how to fix it."""


class Settings(BaseSettings):
    """Application settings, read from REGISTA_* environment variables (or .env)."""

    model_config = SettingsConfigDict(env_prefix="REGISTA_", env_file=".env", extra="ignore")

    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # Runtime connection: role regista_app (no BYPASSRLS).
    database_url: str = Field(repr=False)
    # Migrations connection: role regista_owner (owns the tables).
    database_owner_url: str = Field(repr=False)

    # LocalKeyProvider master key (base64, 32 bytes). Required to start the API.
    master_key: str = Field(default="", repr=False)

    # Base URL of the panel, used to build invitation links.
    public_url: str = "http://localhost:3000"
    email_backend: Literal["console", "memory"] = "console"

    # Sessions and invitations (docs/STATUS.md, "Prazos padrão").
    invitation_days: int = 7
    session_idle_hours: int = 12
    session_absolute_days: int = 7
    partial_session_minutes: int = 10

    # Progressive lockout per account: after `lockout_threshold` consecutive failures the
    # account is locked for `lockout_base_seconds`, doubling on each further failure.
    lockout_threshold: int = 5
    lockout_base_seconds: int = 60
    lockout_max_seconds: int = 900

    # Rate limits (fixed windows in PostgreSQL, ADR 0003).
    rate_login_ip_per_minute: int = 20
    rate_login_email_per_15min: int = 10
    rate_mfa_per_5min: int = 10
    rate_invite_ip_per_minute: int = 20

    # Peers allowed to set X-Forwarded-For (comma separated). Dev: the Next.js proxy.
    trusted_proxies: str = "127.0.0.1,::1"

    @property
    def cookie_secure(self) -> bool:
        return self.environment == "prod"

    @property
    def trusted_proxy_set(self) -> frozenset[str]:
        return frozenset(p.strip() for p in self.trusted_proxies.split(",") if p.strip())

    def validate_for_runtime(self) -> None:
        if not self.master_key:
            raise ConfigurationError(
                "REGISTA_MASTER_KEY is not set. Generate one with: "
                'uv run python -c "from regista_api.core.keys import LocalKeyProvider; '
                'print(LocalKeyProvider.generate_key())" and put it in .env'
            )
        if self.environment == "prod":
            # Both backends keep invitation links (which carry tokens) out of real mailboxes.
            raise ConfigurationError(
                f"REGISTA_EMAIL_BACKEND={self.email_backend} is for dev and tests only. "
                "A real e-mail backend arrives in M7/M8."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
