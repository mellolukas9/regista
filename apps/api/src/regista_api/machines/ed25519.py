"""Weak Ed25519 public keys (docs/adr/0018).

Ed25519 has eight points of small order. A public key that is one of them lets anyone produce a
signature that verifies, without knowing any private key, so a machine enrolled with such a key
could be impersonated by whoever knows its id. Proof of possession does not help: the forged
proof verifies too. The agent never generates one, so the server refuses them at enrollment.

A key is refused when it cannot be decoded, is not in canonical form, or has small order
(8 * A is the neutral element). Pure integer arithmetic on the curve of RFC 8032, used once per
enrollment, so clarity matters more than speed.
"""

P = 2**255 - 19
D = (-121665 * pow(121666, P - 2, P)) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)

Point = tuple[int, int]
NEUTRAL: Point = (0, 1)


def _decode(public_key: bytes) -> Point | None:
    """RFC 8032 section 5.1.3; None for anything that is not a canonical encoding of a point."""
    if len(public_key) != 32:
        return None
    value = int.from_bytes(public_key, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= P:
        return None
    y2 = y * y % P
    u = (y2 - 1) % P
    v = (D * y2 + 1) % P
    x = u * pow(v, 3, P) * pow(u * pow(v, 7, P), (P - 5) // 8, P) % P
    check = v * x * x % P
    if check == (-u) % P:
        x = x * SQRT_M1 % P
    elif check != u:
        return None
    if x == 0 and sign == 1:
        return None
    if x & 1 != sign:
        x = (-x) % P
    return x, y


def _double(point: Point) -> Point:
    """Twisted Edwards addition (a = -1) of a point with itself; the formula is complete."""
    x, y = point
    t = D * x * x % P * y % P * y % P
    x3 = 2 * x * y % P * pow(1 + t, P - 2, P) % P
    y3 = (y * y + x * x) % P * pow((1 - t) % P, P - 2, P) % P
    return x3, y3


def is_weak_public_key(public_key: bytes) -> bool:
    point = _decode(public_key)
    if point is None:
        return True
    for _ in range(3):  # multiply by the cofactor, 8
        point = _double(point)
    return point == NEUTRAL
