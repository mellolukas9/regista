from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from regista_pkg import load_trusted_keys, parse_key_file


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

    # Enrollment key of a machine: single use, valid for this long (docs/specs/agent.md).
    enrollment_key_hours: int = 24

    # Machine access token (docs/adr/0018). Renewal is the agent's job, on its own monotonic clock.
    agent_token_minutes: int = 15
    # Public URL of this API, as the agent knows it. It is part of what the agent signs (the
    # "audience"), so a signature made for another environment is worthless here.
    api_public_url: str = "http://127.0.0.1:8000"
    heartbeat_seconds: int = 30
    # "Sem sinal" (docs/STATUS.md): an online machine silent for this long becomes offline. The
    # sweep that does it runs on this cron (6 fields, the last one is the second).
    machine_offline_after_seconds: int = 120
    machine_sweep_cron: str = "* * * * * */30"
    # Windows of the rate limit table older than this are purged (hourly).
    rate_limit_retention_seconds: int = 86_400
    # Rate limits of the agent routes (fixed windows in PostgreSQL, ADR 0003).
    rate_agent_enroll_ip_per_minute: int = 20
    rate_agent_auth_ip_per_minute: int = 120
    rate_agent_auth_machine_per_minute: int = 30

    # Execution limits (docs/STATUS.md, M3). The agent keeps the same numbers in its own settings.
    agent_poll_wait_seconds: int = 30
    agent_max_waiters: int = 2000
    job_timeout_seconds: int = 7200
    log_line_max_bytes: int = 4096
    log_job_max_lines: int = 20_000
    log_job_max_bytes: int = 5_000_000
    artifact_max_bytes: int = 5_000_000
    artifact_max_per_job: int = 20
    artifact_upload_seconds: int = 120
    artifact_view_seconds: int = 60

    # Object storage for screenshots (ADR 0019). Dev and tests: the SeaweedFS of the compose, with
    # static keys. Production: AWS S3 through the instance role, so no keys and no endpoint.
    s3_endpoint_url: str = ""
    # The address the agent and the browser reach the storage at, when it differs from the one the
    # API uses (a container network, for instance). Empty = the same.
    s3_public_endpoint_url: str = ""
    s3_bucket: str = "regista-artifacts"
    s3_region: str = "us-east-1"
    s3_access_key_id: str = Field(default="", repr=False)
    s3_secret_access_key: str = Field(default="", repr=False)

    # Signed robot packages (ADR 0021). The size is a hard ceiling for one package; the URLs are
    # pre-signed for this many seconds (upload from the panel, download by the agent).
    package_max_bytes: int = 200 * 1024 * 1024
    package_upload_seconds: int = 300
    package_download_seconds: int = 120
    # Development and tests only: a file of extra trusted public keys (so tests can sign with a key
    # made on the spot). Refused in production, like the agent's `REGISTA_DEV_TRUSTED_KEYS`.
    dev_trusted_keys: str = ""

    # Peers allowed to set X-Forwarded-For (comma separated). Dev: the Next.js proxy.
    trusted_proxies: str = "127.0.0.1,::1"

    @property
    def cookie_secure(self) -> bool:
        return self.environment == "prod"

    @property
    def trusted_proxy_set(self) -> frozenset[str]:
        return frozenset(p.strip() for p in self.trusted_proxies.split(",") if p.strip())

    def validate_storage(self) -> None:
        """Production reaches S3 through an IAM role only: no static keys, no dev endpoint.

        Run by the API and by the worker, so neither starts with a key that could leak.
        """
        if self.environment != "prod":
            return
        if self.s3_access_key_id or self.s3_secret_access_key:
            raise ConfigurationError(
                "REGISTA_S3_ACCESS_KEY_ID / REGISTA_S3_SECRET_ACCESS_KEY are for dev and tests "
                "only. In production the storage is reached with an IAM role."
            )
        for url in (self.s3_endpoint_url, self.s3_public_endpoint_url):
            if not url:
                continue
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or host in ("localhost", "::1") or host.startswith("127."):
                raise ConfigurationError(
                    f"REGISTA_S3_ENDPOINT_URL={url} is the dev storage. In production leave it "
                    "empty (AWS S3) or use an https address that is not local."
                )

    def validate_signing(self) -> None:
        if self.environment == "prod" and self.dev_trusted_keys:
            raise ConfigurationError(
                "REGISTA_DEV_TRUSTED_KEYS is for development and tests only. In production the "
                "API trusts only the keys compiled into regista_pkg."
            )

    def package_trusted_keys(self) -> dict[str, Ed25519PublicKey]:
        """The keys a package signature is checked against: the compiled-in ones, plus the
        development file outside production."""
        self.validate_signing()
        extra: dict[str, Ed25519PublicKey] = {}
        if self.dev_trusted_keys:
            try:
                extra = parse_key_file(Path(self.dev_trusted_keys).read_text("utf-8"))
            except (OSError, ValueError) as exc:
                raise ConfigurationError(f"REGISTA_DEV_TRUSTED_KEYS: {exc}") from exc
        return load_trusted_keys(extra)

    def validate_for_runtime(self) -> None:
        self.validate_storage()
        self.validate_signing()
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
