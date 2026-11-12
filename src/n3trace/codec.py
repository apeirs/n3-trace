"""Encoding helpers for Neo N3: addresses, script hashes, integers and StackItem binary serialization.

Everything here is pure and offline, so it is easy to test.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

ADDRESS_VERSION = 0x35  # Neo N3 mainnet and testnet

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


# -- base58check addresses -----------------------------------------------------
def _b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(data) - len(data.lstrip(b"\0"))
    return "1" * pad + out


def _b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        i = _B58.find(ch)
        if i < 0:
            raise ValueError(f"invalid base58 character {ch!r}")
        n = n * 58 + i
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\0" * pad + body


def _checksum(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()[:4]


def le_to_hash(le: bytes) -> str:
    """20 little-endian bytes (as stored on chain) -> '0x…' big-endian script hash."""
    if len(le) != 20:
        raise ValueError("a script hash is 20 bytes")
    return "0x" + le[::-1].hex()


def hash_to_le(h: str) -> bytes:
    raw = bytes.fromhex(h[2:] if h.startswith("0x") else h)
    if len(raw) != 20:
        raise ValueError(f"not a 20-byte script hash: {h}")
    return raw[::-1]


def le_to_address(le: bytes, version: int = ADDRESS_VERSION) -> str:
    body = bytes([version]) + le
    return _b58encode(body + _checksum(body))


def address_to_le(addr: str, version: int = ADDRESS_VERSION) -> bytes:
    raw = _b58decode(addr)
    if len(raw) != 25 or raw[0] != version or _checksum(raw[:21]) != raw[21:]:
        raise ValueError(f"not a valid Neo N3 address: {addr}")
    return raw[1:21]


def to_hash(ref: str) -> str:
    """Accept '0x…' (big-endian) hash, bare hex, or an N… address; return '0x…'."""
    ref = ref.strip()
    if ref.startswith("N") and len(ref) == 34:
        return le_to_hash(address_to_le(ref))
    h = ref[2:] if ref.lower().startswith("0x") else ref
    if len(h) == 40:
        bytes.fromhex(h)
        return "0x" + h.lower()
    raise ValueError(f"not a script hash or address: {ref}")


# -- integers ----------------------------------------------------------------------
def int_from_le(b: bytes) -> int:
    """NeoVM BigInteger encoding: little-endian two's complement, empty = 0."""
    return int.from_bytes(b, "little", signed=True) if b else 0


def int_to_le(n: int) -> bytes:
    if n == 0:
        return b""
    length = (n.bit_length() + 8) // 8  # room for the sign bit
    out = n.to_bytes(length, "little", signed=True)
    # Trim redundant sign bytes, as NeoVM does.
    while len(out) > 1 and ((out[-1] == 0 and out[-2] < 0x80) or (out[-1] == 0xFF and out[-2] >= 0x80)):
        out = out[:-1]
    return out


# -- StackItem binary serialization (StdLib.serialize / native storage) --------------
ANY, BOOLEAN, INTEGER, BYTESTRING, BUFFER, ARRAY, STRUCT, MAP = 0x00, 0x20, 0x21, 0x28, 0x30, 0x40, 0x41, 0x48


class DecodeError(ValueError):
    pass


def _varint(b: bytes, i: int) -> tuple[int, int]:
    if i >= len(b):
        raise DecodeError("truncated varint")
    f = b[i]
    if f < 0xFD:
        return f, i + 1
    size = {0xFD: 2, 0xFE: 4, 0xFF: 8}[f]
    if i + 1 + size > len(b):
        raise DecodeError("truncated varint")
    return int.from_bytes(b[i + 1 : i + 1 + size], "little"), i + 1 + size


def deserialize(b: bytes, max_items: int = 2048) -> Any:
    """Decode a serialized StackItem into Python values.

    Integer -> int, Boolean -> bool, ByteString/Buffer -> bytes, Array/Struct -> list,
    Map -> list of (key, value) pairs, Any -> None. Raises DecodeError on anything else.
    """
    count = [0]

    def item(i: int) -> tuple[Any, int]:
        count[0] += 1
        if count[0] > max_items:
            raise DecodeError("too many items")
        if i >= len(b):
            raise DecodeError("truncated item")
        t = b[i]
        i += 1
        if t == ANY:
            return None, i
        if t == BOOLEAN:
            if i >= len(b):
                raise DecodeError("truncated boolean")
            return b[i] != 0, i + 1
        if t in (INTEGER, BYTESTRING, BUFFER):
            n, i = _varint(b, i)
            if i + n > len(b) or (t == INTEGER and n > 32):
                raise DecodeError("bad length")
            raw = b[i : i + n]
            return (int_from_le(raw) if t == INTEGER else raw), i + n
        if t in (ARRAY, STRUCT):
            n, i = _varint(b, i)
            out = []
            for _ in range(n):
                v, i = item(i)
                out.append(v)
            return out, i
        if t == MAP:
            n, i = _varint(b, i)
            pairs = []
            for _ in range(n):
                k, i = item(i)
                v, i = item(i)
                pairs.append((k, v))
            return pairs, i
        raise DecodeError(f"unknown stack item type 0x{t:02x}")

    val, end = item(0)
    if end != len(b):
        raise DecodeError("trailing bytes")
    return val


def b64d(s: str) -> bytes:
    return base64.b64decode(s)


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


# -- RPC stack results ---------------------------------------------------------------
def stack_value(item: dict[str, Any]) -> Any:
    """Turn one RPC stack item into a Python value (enough for balanceOf/symbol/decimals)."""
    t = item.get("type")
    v = item.get("value")
    if t == "Integer":
        return int(v)
    if t == "Boolean":
        return bool(v)
    if t in ("ByteString", "Buffer"):
        return b64d(v or "")
    if t in ("Array", "Struct"):
        return [stack_value(x) for x in v or []]
    if t == "Any":
        return None
    return v


def as_int(v: Any) -> int:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, (bytes, bytearray)):
        return int_from_le(bytes(v))
    raise ValueError(f"not an integer: {v!r}")


def as_text(v: Any) -> str:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", "replace")
    return str(v)


def format_amount(raw: int, decimals: int) -> str:
    """Exact decimal string, no float rounding: 123456789, 8 -> '1.23456789'."""
    sign = "-" if raw < 0 else ""
    raw = abs(raw)
    if decimals <= 0:
        return f"{sign}{raw}"
    whole, frac = divmod(raw, 10**decimals)
    frac_s = str(frac).rjust(decimals, "0").rstrip("0")
    return f"{sign}{whole}" + (f".{frac_s}" if frac_s else "")


def parse_amount(s: str, decimals: int) -> int:
    """'1.5', 8 -> 150000000. Refuses more fractional digits than the token has."""
    s = s.strip()
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    whole, _, frac = s.partition(".")
    if not (whole or frac) or not (whole or "0").isdigit() or (frac and not frac.isdigit()):
        raise ValueError(f"not an amount: {s!r}")
    if len(frac) > decimals:
        raise ValueError(f"{s} has more than {decimals} decimal places")
    raw = int(whole or "0") * 10**decimals + int((frac or "0").ljust(decimals, "0") or "0")
    return -raw if neg else raw
