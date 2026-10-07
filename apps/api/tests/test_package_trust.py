"""The API trusts the compiled-in keys, plus a development file that production refuses."""

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_api.core.config import ConfigurationError
from regista_pkg import key_id_of, load_trusted_keys

from .conftest import DbUrls, make_settings


def _key_file(tmp_path: Path) -> tuple[Path, str]:
    raw = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    kid = key_id_of(raw)
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({kid: base64.b64encode(raw).decode()}), encoding="utf-8")
    return path, kid


def test_the_api_and_the_agent_read_one_list(db_urls: DbUrls) -> None:
    """Both import `regista_pkg.trusted_keys`, so they cannot disagree about who is trusted."""
    assert set(make_settings(db_urls).package_trusted_keys()) == set(load_trusted_keys())


def test_outside_production_the_dev_file_adds_a_key(db_urls: DbUrls, tmp_path: Path) -> None:
    path, kid = _key_file(tmp_path)
    for environment in ("dev", "test"):
        settings = make_settings(db_urls, environment=environment, dev_trusted_keys=str(path))
        assert kid in settings.package_trusted_keys()


def test_production_refuses_the_dev_file_instead_of_ignoring_it(
    db_urls: DbUrls, tmp_path: Path
) -> None:
    path, _ = _key_file(tmp_path)
    settings = make_settings(db_urls, environment="prod", dev_trusted_keys=str(path))
    with pytest.raises(ConfigurationError, match="REGISTA_DEV_TRUSTED_KEYS"):
        settings.validate_signing()
    with pytest.raises(ConfigurationError, match="REGISTA_DEV_TRUSTED_KEYS"):
        settings.package_trusted_keys()
    with pytest.raises(ConfigurationError, match="REGISTA_DEV_TRUSTED_KEYS"):
        settings.validate_for_runtime()


def test_a_broken_dev_file_is_a_configuration_error(db_urls: DbUrls, tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("nope", encoding="utf-8")
    settings = make_settings(db_urls, environment="dev", dev_trusted_keys=str(broken))
    with pytest.raises(ConfigurationError, match="REGISTA_DEV_TRUSTED_KEYS"):
        settings.package_trusted_keys()
