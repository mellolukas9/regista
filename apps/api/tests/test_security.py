import uuid

import pytest
import structlog

from regista_api.core.keys import KeyProviderError, LocalKeyProvider
from regista_api.core.logging import REDACTED, redact_sensitive
from regista_api.core.security import (
    hash_password,
    hash_token,
    needs_rehash,
    new_recovery_code,
    new_token,
    normalize_recovery_code,
    password_problem,
    verify_password,
)

GOOD = "correct horse battery staple"


def test_password_hash_is_argon2id_and_verifies() -> None:
    hashed = hash_password(GOOD)
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, GOOD)
    assert not verify_password(hashed, GOOD + "x")
    assert not needs_rehash(hashed)


def test_missing_hash_never_verifies() -> None:
    assert not verify_password(None, GOOD)


def test_tokens_are_unique_and_hash_is_stable() -> None:
    a, b = new_token(), new_token()
    assert a != b
    assert len(hash_token(a)) == 32
    assert hash_token(a) == hash_token(a)
    assert hash_token(a) != hash_token(b)


@pytest.mark.parametrize(
    ("password", "problem"),
    [
        ("short1!", "too_short"),
        ("password1234", "too_common"),
        ("Password123456", "too_common"),
        ("12345678abcd", None),
        (GOOD, None),
    ],
)
def test_password_policy(password: str, problem: str | None) -> None:
    got = password_problem(password)
    if problem is None:
        assert got != "too_short"
    else:
        assert got == problem


def test_password_must_differ_from_current() -> None:
    assert password_problem(GOOD, current_hash=hash_password(GOOD)) == "same_as_current"
    assert password_problem(GOOD + "!", current_hash=hash_password(GOOD)) is None


def test_recovery_codes_have_the_expected_shape() -> None:
    codes = {new_recovery_code() for _ in range(50)}
    assert len(codes) == 50
    for code in codes:
        assert len(code) == 9
        assert code[4] == "-"
    assert normalize_recovery_code("ab12-cd34 ") == "AB12CD34"


def test_key_provider_round_trip_and_tenant_binding() -> None:
    provider = LocalKeyProvider(LocalKeyProvider.generate_key())
    tenant = uuid.uuid4()
    ciphertext, key_id = provider.encrypt(tenant, b"totp-secret", aad=b"user-1")
    assert b"totp-secret" not in ciphertext
    assert provider.decrypt(tenant, ciphertext, key_id, aad=b"user-1") == b"totp-secret"
    with pytest.raises(KeyProviderError):
        provider.decrypt(uuid.uuid4(), ciphertext, key_id, aad=b"user-1")
    with pytest.raises(KeyProviderError):
        provider.decrypt(tenant, ciphertext, key_id, aad=b"user-2")
    with pytest.raises(KeyProviderError):
        provider.decrypt(tenant, ciphertext, "other-key", aad=b"user-1")


def test_key_provider_rejects_bad_master_keys() -> None:
    with pytest.raises(KeyProviderError):
        LocalKeyProvider("not base64!!")
    with pytest.raises(KeyProviderError):
        LocalKeyProvider("c2hvcnQ=")


def test_log_redaction_masks_sensitive_keys_recursively() -> None:
    event = {
        "event": "login",
        "password": "hunter2",
        "session_token": "abc",
        "csrf_token": "def",
        "recovery_code": "AAAA-BBBB",
        "code": "123456",
        "status_code": 401,
        "error_code": "x",
        "nested": {"Authorization": "Bearer z", "ok": 1},
        "items": [{"cookie": "c"}],
    }
    out = redact_sensitive(structlog.get_logger(), "info", event)
    for key in ("password", "session_token", "csrf_token", "recovery_code", "code"):
        assert out[key] == REDACTED
    assert out["status_code"] == 401
    assert out["error_code"] == "x"
    assert out["nested"] == {"Authorization": REDACTED, "ok": 1}
    assert out["items"] == [{"cookie": REDACTED}]
    assert out["event"] == "login"
