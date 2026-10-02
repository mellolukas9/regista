from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from REGISTA_* environment variables (or .env)."""

    model_config = SettingsConfigDict(env_prefix="REGISTA_", env_file=".env", extra="ignore")

    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # Runtime connection: role regista_app (no BYPASSRLS).
    database_url: str = Field(repr=False)
    # Migrations connection: role regista_owner (owns the tables).
    database_owner_url: str = Field(repr=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
