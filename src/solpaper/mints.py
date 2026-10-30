"""Mint address validation. Tokens are identified by mint address only."""

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}


def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        if c not in _INDEX:
            raise ValueError(f"invalid base58 character {c!r}")
        n = n * 58 + _INDEX[c]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body


def is_valid_mint(s: object) -> bool:
    if not isinstance(s, str) or not 32 <= len(s) <= 44:
        return False
    try:
        return len(b58decode(s)) == 32
    except ValueError:
        return False
