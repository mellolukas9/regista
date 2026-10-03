"""Small-order Ed25519 public keys are refused (docs/adr/0018)."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_api.machines.ed25519 import NEUTRAL, P, _decode, _double, is_weak_public_key


def _le(hex_bytes: str) -> int:
    """Ed25519 writes the coordinate least significant byte first."""
    return int.from_bytes(bytes.fromhex(hex_bytes), "little")


# The y coordinate of every point of small order (the identity, -1, and the 4 of order 8 / 4).
SMALL_ORDER_Y = [
    0,  # order 4: (+-sqrt(-1), 0)
    1,  # the identity
    P - 1,  # order 2
    _le("26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05"),  # order 8
    _le("c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a"),  # order 8
]
# x = 0 for these, and an x of 0 has no encoding with the sign bit set.
SIGN_BIT_IMPOSSIBLE = {1, P - 1}


def _encode(y: int, sign: int) -> bytes:
    return (y | (sign << 255)).to_bytes(32, "little")


def _times8(encoded: bytes) -> tuple[int, int]:
    point = _decode(encoded)
    assert point is not None, "the test vector is not a point of the curve"
    for _ in range(3):
        point = _double(point)
    return point


def test_real_keys_are_not_weak() -> None:
    for _ in range(50):
        raw = (
            Ed25519PrivateKey.generate()
            .public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        )
        assert _decode(raw) is not None
        assert not is_weak_public_key(raw)


def test_every_small_order_point_is_weak_in_both_sign_encodings() -> None:
    checked = 0
    for y in SMALL_ORDER_Y:
        for sign in (0, 1):
            encoded = _encode(y, sign)
            if sign == 1 and y in SIGN_BIT_IMPOSSIBLE:
                assert _decode(encoded) is None
                assert is_weak_public_key(encoded)
                continue
            # The vector really is of small order: 8 * A is the identity.
            assert _times8(encoded) == NEUTRAL, f"y={y} sign={sign}"
            assert is_weak_public_key(encoded), f"y={y} sign={sign}"
            checked += 1
    assert checked == 8  # the full torsion subgroup: 1 + 1 + 2 + 2 + 2


def test_the_all_zero_key_is_weak() -> None:
    assert is_weak_public_key(bytes(32))


def test_non_canonical_and_malformed_encodings_are_refused() -> None:
    # y >= p: the same point written a second way. libsodium and others blacklist these too.
    for y in (P, P + 1, P + 2):
        assert is_weak_public_key(_encode(y, 0))
    assert is_weak_public_key(b"")
    assert is_weak_public_key(bytes(31))
    assert is_weak_public_key(bytes(33))
    # Not a point of the curve at all (no square root for x^2).
    candidates = [bytes([i]) + bytes(31) for i in range(2, 40)]
    assert any(_decode(c) is None for c in candidates)
    assert all(is_weak_public_key(c) for c in candidates if _decode(c) is None)


@pytest.mark.parametrize("key", [bytes(32), _encode(1, 0), _encode(P - 1, 0)])
def test_openssl_would_have_accepted_these_so_the_server_must_not(key: bytes) -> None:
    """Why the check exists: the library itself does not refuse the key at load time."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    Ed25519PublicKey.from_public_bytes(key)  # loads without complaint
    assert is_weak_public_key(key)
