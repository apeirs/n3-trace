"""Turn raw stack values into readable text, using the contract's ABI types when they are known."""

from __future__ import annotations

from typing import Any

from .codec import DecodeError, b64d, deserialize, format_amount, int_from_le, le_to_address, le_to_hash


def _printable(b: bytes) -> bool:
    if not b:
        return False
    try:
        s = b.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return all(ch.isprintable() or ch in "\n\t" for ch in s)


def hash160_text(b: bytes, book: Any = None) -> str:
    """20 bytes -> 'Name (0x…)' for a known contract, else the N… address."""
    h = le_to_hash(b)
    c = book.known(h) if book is not None else None
    if c is not None:
        return f"{c.name} ({h})"
    return le_to_address(b)


def jsonable(v: Any) -> Any:
    """A JSON-safe copy of a decoded value: bytes become hex strings."""
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex()
    if isinstance(v, dict):
        return [[jsonable(k), jsonable(x)] for k, x in v.items()]
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, (int, bool, str)) or v is None:
        return v
    return repr(v)


def render(v: Any, typ: str | None = None, book: Any = None, decimals: int | None = None, symbol: str | None = None,
           depth: int = 0) -> str:
    """Readable text for one value. `typ` is an ABI parameter type such as Hash160 or Integer."""
    if depth > 4:
        return "…"
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if typ == "Integer" or (typ in (None, "Any") and isinstance(v, int)):
        n = v if isinstance(v, int) else int_from_le(v) if isinstance(v, (bytes, bytearray)) else None
        if n is None:
            return repr(v)
        if decimals is not None:
            return f"{format_amount(n, decimals)}{' ' + symbol if symbol else ''} ({n})" if decimals else f"{n}{' ' + symbol if symbol else ''}"
        return str(n)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, dict):
        items = list(v.items())[:8]
        body = ", ".join(f"{render(k, None, book, depth=depth + 1)}: {render(x, None, book, depth=depth + 1)}" for k, x in items)
        return "{" + body + (", …" if len(v) > 8 else "") + "}"
    if isinstance(v, (list, tuple)):
        if v and all(isinstance(x, tuple) and len(x) == 2 for x in v) and typ == "Map":
            return render(dict(v), "Map", book, depth=depth)
        items = [render(x, None, book, depth=depth + 1) for x in v[:8]]
        return "[" + ", ".join(items) + (", …" if len(v) > 8 else "") + "]"
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
        if typ == "Hash160" and len(b) == 20:
            return hash160_text(b, book)
        if typ == "Hash256" and len(b) == 32:
            return "0x" + b[::-1].hex()
        if typ == "PublicKey" and len(b) == 33:
            return b.hex()
        if typ == "String":
            return repr(b.decode("utf-8", "replace"))
        if typ == "Integer":
            return str(int_from_le(b))
        if typ == "Boolean":
            return "true" if any(b) else "false"
        if typ in (None, "Any", "ByteArray"):
            if len(b) == 20 and not _printable(b):
                return hash160_text(b, book)
            if _printable(b) and typ != "ByteArray":
                return repr(b.decode())
        if not b:
            return "0x (empty)"
        if len(b) > 64:
            return f"0x{b[:32].hex()}… ({len(b)} bytes)"
        return "0x" + b.hex()
    return repr(v)


def decode_storage_value(raw: bytes | None) -> Any:
    """Best-effort decode of a stored value: serialized StackItem, else the raw bytes."""
    if raw is None:
        return None
    if raw and raw[0] in (0x00, 0x20, 0x21, 0x28, 0x30, 0x40, 0x41, 0x48):
        try:
            return deserialize(raw)
        except DecodeError:
            pass
    return raw


def storage_value_text(raw: bytes | None, book: Any = None) -> str:
    if raw is None:
        return "-"
    v = decode_storage_value(raw)
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
        if len(b) == 20:
            return f"{le_to_address(b)} / {le_to_hash(b)}"
        if 0 < len(b) <= 16 and not _printable(b):
            return f"0x{b.hex()} (int {int_from_le(b)})"
        if _printable(b):
            return repr(b.decode())
        return render(b, "ByteArray")
    return render(v, None, book)


def leading_int(raw: bytes | None) -> int | None:
    """The number a stored value carries: an Integer, the first field of a Struct, or short raw bytes."""
    if raw is None:
        return 0
    v = decode_storage_value(raw)
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, list) and v and isinstance(v[0], int) and not isinstance(v[0], bool):
        return v[0]
    if isinstance(v, (bytes, bytearray)) and 0 < len(v) <= 16 and not _printable(bytes(v)):
        return int_from_le(bytes(v))
    return None


def item_value(item: Any) -> Any:
    """An RPC stack item (from a stack or a notification) as a Python value. Maps keep their key order."""
    if not isinstance(item, dict):
        return item
    t = item.get("type")
    v = item.get("value")
    if t == "Integer":
        return int(v)
    if t == "Boolean":
        return bool(v)
    if t in ("ByteString", "Buffer"):
        return b64d(v or "")
    if t in ("Array", "Struct"):
        return [item_value(x) for x in v or []]
    if t == "Map":
        pairs = [(item_value(p.get("key")), item_value(p.get("value"))) for p in v or []]
        if all(isinstance(k, (bytes, int, bool, str)) for k, _ in pairs):
            return dict(pairs)
        return pairs
    if t == "Any":
        return None
    if t == "InteropInterface":
        return "<iterator>" if not v else f"<interop {v}>"
    if t == "Pointer":
        return f"<pointer {v}>"
    return v


def typed_json(v: Any, typ: str | None) -> Any:
    """JSON form of a value of a known ABI type: hashes in their usual big-endian 0x form, the rest as jsonable."""
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
        if typ == "Hash160" and len(b) == 20:
            return le_to_hash(b)
        if typ == "Hash256" and len(b) == 32:
            return "0x" + b[::-1].hex()
        if typ == "String":
            return b.decode("utf-8", "replace")
        if typ == "Integer":
            return int_from_le(b)
    return jsonable(v)
