"""Agent configuration: a TOML file, overridden by `REGISTA_*` environment variables.

The file is `agent.toml` in the agent's home: `%ProgramData%\\Regista` on Windows and
`/etc/regista` elsewhere. `REGISTA_HOME` points the whole agent (config, key, logs) somewhere
else, which is how tests and development run without touching the system folders.

Nothing here is specific to Windows except where the home is.
"""

import os
import sys
import tomllib
import uuid
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from regista_agent.errors import AgentError

CONFIG_NAME = "agent.toml"
Mode = Literal["service", "session", "oneshot"]

# The account a Windows service runs as, when the machine is registered in `service` mode. A
# virtual service account: no password, and its SID follows from the service name alone.
DEFAULT_SERVICE_ACCOUNT = r"NT SERVICE\RegistaAgent"


def default_home() -> Path:
    override = os.environ.get("REGISTA_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Regista"
    return Path("/etc/regista")


class _TomlSource(PydanticBaseSettingsSource):
    """The file under the home, read every time settings are built (so tests can move it)."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        path = default_home() / CONFIG_NAME
        self._data: dict[str, Any] = (
            tomllib.loads(path.read_text("utf-8")) if path.is_file() else {}
        )

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._data)


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REGISTA_", extra="ignore")

    server_url: str | None = None
    machine_id: uuid.UUID | None = None
    mode: Mode | None = None
    # The account that runs the agent: the only one besides SYSTEM and Administrators that may
    # read the private key (see keystore.py).
    agent_account: str | None = None
    proxy: str | None = None
    ca_bundle: Path | None = None
    log_level: str = Field(default="INFO")

    # --- running robots (M3) ---------------------------------------------------------------
    # `prod` unless said otherwise: forgetting to set it can only make the agent stricter.
    environment: Literal["dev", "prod"] = "prod"
    # M3 only, before signed packages exist (M4): run a robot from a local folder. Refused in
    # production (`check_dev_unsigned`), so it cannot be switched on by accident on a customer's
    # machine.
    dev_unsigned: bool = False
    dev_bots_dir: Path | None = None
    dev_python: Path | None = None
    # Development only: where the browsers are (a dev machine keeps them in the user's profile).
    dev_browsers_path: Path | None = None
    # M4: the robots this machine may run (`regista-agent allow <pacote>`). Empty runs nothing.
    allowed_bots: list[str] = Field(default_factory=list)
    job_priority: Literal["below_normal", "normal"] = "below_normal"
    cancel_grace_seconds: int = Field(default=15, ge=0, le=300)
    log_flush_seconds: float = Field(default=2.0, gt=0, le=60)
    poll_wait_seconds: int = Field(default=30, ge=0, le=30)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Highest priority first: arguments, then the environment, then the file.
        return (init_settings, env_settings, _TomlSource(settings_cls))

    @property
    def home(self) -> Path:
        return default_home()

    @property
    def config_path(self) -> Path:
        return self.home / CONFIG_NAME

    @property
    def keys_dir(self) -> Path:
        return self.home / "keys"

    @property
    def key_path(self) -> Path:
        return self.keys_dir / "machine.key"

    @property
    def python_dir(self) -> Path:
        return self.home / "python"

    @property
    def browsers_dir(self) -> Path:
        return self.home / "browsers"

    @property
    def packages_dir(self) -> Path:
        return self.home / "packages"

    @property
    def envs_dir(self) -> Path:
        return self.home / "envs"

    @property
    def uv_cache_dir(self) -> Path:
        return self.home / "uv-cache"

    @property
    def log_path(self) -> Path:
        return self.home / "logs" / "agent.log"

    @property
    def enrolled(self) -> bool:
        return self.machine_id is not None and self.server_url is not None

    def check_dev_unsigned(self) -> None:
        """Running a robot that nobody signed is a development tool, and only that."""
        if not self.dev_unsigned:
            return
        if self.environment != "dev":
            raise AgentError(
                "REGISTA_DEV_UNSIGNED só pode ser usado com REGISTA_ENVIRONMENT=dev. Em produção "
                "o agente só roda pacotes assinados pela Artemisys."
            )
        if self.dev_bots_dir is None or not self.dev_bots_dir.is_dir():
            raise AgentError(
                "REGISTA_DEV_UNSIGNED pede REGISTA_DEV_BOTS_DIR apontando para a pasta dos robôs "
                "(no repositório, a pasta `bots`)."
            )


def save_identity(
    home: Path, *, server_url: str, machine_id: uuid.UUID, mode: str, agent_account: str | None
) -> Path:
    """Write what enrollment learned into `agent.toml`, keeping any other setting already there."""
    path = home / CONFIG_NAME
    current: dict[str, Any] = tomllib.loads(path.read_text("utf-8")) if path.is_file() else {}
    current.update({"server_url": server_url, "machine_id": str(machine_id), "mode": mode})
    if agent_account:
        current["agent_account"] = agent_account
    home.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".toml.new")
    temporary.write_text(tomli_w.dumps(current), encoding="utf-8")
    os.replace(temporary, path)
    return path
