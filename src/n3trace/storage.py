"""Storage changes of an execution: label keys, fetch the value before, decode both sides."""

from __future__ import annotations

from typing import Any

from .codec import b64d, b64e, le_to_address
from .rpc import RpcError
from .values import _printable, decode_storage_value, leading_int, storage_value_text

# Key prefixes of native contracts (neo-project/neo src/Neo/SmartContract/Native/*.cs).
NATIVE_PREFIXES: dict[str, dict[int, str]] = {
    "GasToken": {0x14: "balance", 0x0B: "totalSupply"},
    "NeoToken": {
        0x14: "account (balance, height, vote, lastGasPerVote)",
        0x0B: "totalSupply",
        0x01: "votersCount",
        0x0D: "registerPrice",
        0x0E: "committee",
        0x17: "voterRewardPerCommittee",
        0x1D: "gasPerBlock",
        0x21: "candidate (registered, votes)",
    },
    "ContractManagement": {0x08: "contract", 0x0C: "contractHash", 0x0F: "nextAvailableId", 0x14: "minimumDeploymentFee"},
    "PolicyContract": {0x0A: "feePerByte", 0x0F: "blockedAccount", 0x12: "execFeeFactor", 0x13: "storagePrice", 0x14: "attributeFee"},
    "Notary": {0x01: "deposit", 0x0A: "maxNotValidBeforeDelta"},
    "OracleContract": {0x05: "price", 0x07: "request", 0x06: "requestId", 0x09: "idList"},
    "RoleManagement": {},
    "LedgerContract": {0x05: "block", 0x09: "blockHash", 0x0B: "transaction", 0x0C: "currentBlock"},
}


def split_key(full: bytes) -> tuple[int, bytes]:
    """NeoGo diagnostics keys start with the contract id (int32 little-endian)."""
    if len(full) < 4:
        raise ValueError("storage key shorter than a contract id")
    return int.from_bytes(full[:4], "little", signed=True), full[4:]


def key_text(key: bytes, contract_name: str | None = None) -> str:
    """Readable key: native prefix names, 'prefix + address', text, or hex."""
    if not key:
        return "(empty key)"
    label = NATIVE_PREFIXES.get(contract_name or "", {}).get(key[0])
    rest = key[1:]
    if label:
        if len(rest) == 20:
            return f"{label}[{le_to_address(rest)}]"
        if len(rest) == 33 and rest[0] in (2, 3):
            return f"{label}[{rest.hex()}]"
        if len(rest) == 4 and label in ("contractHash", "request"):
            return f"{label}[{int.from_bytes(rest, 'big' if label == 'contractHash' else 'little')}]"
        return f"{label}{'[0x' + rest.hex() + ']' if rest else ''}"
    if _printable(key):
        return repr(key.decode())
    # Common layouts: <prefix bytes><20-byte account>[<suffix>]
    for plen in (1, 2, 3, 4):
        if len(key) >= plen + 20:
            prefix, acct, suffix = key[:plen], key[plen : plen + 20], key[plen + 20 :]
            if _printable(prefix) or plen == 1:
                p = repr(prefix.decode()) if _printable(prefix) else "0x" + prefix.hex()
                tail = ""
                if suffix:
                    tail = " + " + (repr(suffix.decode()) if _printable(suffix) else "0x" + suffix.hex())
                if len(suffix) <= 33:
                    return f"{p} + {le_to_address(acct)}{tail}"
    if len(key) > 1 and _printable(key[1:]):
        return f"0x{key[:1].hex()} + {key[1:].decode()!r}"
    if len(key) > 48:
        return f"0x{key[:24].hex()}… ({len(key)} bytes)"
    return "0x" + key.hex()


def _delta(before: bytes | None, after: bytes | None, state: str | None) -> Any:
    """How a numeric value moved: an int for a number or a one-field struct, {field: change} for a wider struct."""
    if state == "Added":
        before = None
    vb, va = decode_storage_value(before), decode_storage_value(after)
    if isinstance(vb, list) and isinstance(va, list) and len(vb) == len(va) and len(va) > 1:
        out = {}
        for i, (x, y) in enumerate(zip(vb, va)):
            if isinstance(x, int) and isinstance(y, int) and not isinstance(x, bool) and x != y:
                out[str(i)] = y - x
        return out or None
    nb = leading_int(before) if (before is not None or state == "Added") else None
    na = leading_int(after) if (after is not None or state == "Deleted") else None
    if isinstance(va, list) and len(va) > 1 or isinstance(vb, list) and len(vb) > 1:
        return None
    return na - nb if nb is not None and na is not None and na != nb else None


def collect(rpc: Any, book: Any, changes: list[dict[str, Any]], before_height: int | None) -> list[dict[str, Any]]:
    """Enrich NeoGo `storagechanges` with contract names, readable keys and the value before the change.

    `before_height` is the block whose state the execution started from (H-1 for a replayed tx). When it is None
    (a preview against the latest state) the previous value is read from the current state instead.
    """
    rows: list[dict[str, Any]] = []
    for ch in changes or []:
        full = b64d(ch.get("key") or "")
        try:
            cid, key = split_key(full)
        except ValueError:
            continue
        c = book.by_id(cid)
        after = b64d(ch["value"]) if ch.get("value") is not None and ch.get("state") != "Deleted" else None
        rows.append(
            {
                "contract": c.hash if c else None,
                "contract_id": cid,
                "name": c.name if c else f"contract #{cid}",
                "state": ch.get("state"),
                "key": "0x" + key.hex(),
                "key_text": key_text(key, c.name if c and c.native else None),
                "_key": key,
                "_after": after,
            }
        )
    rows.sort(key=lambda r: (r["name"].lower(), r["contract_id"], r["_key"]))
    # Previous values for Changed/Deleted keys, read from the starting state.
    need = [r for r in rows if r["state"] in ("Changed", "Deleted") and r["contract"]]
    before: dict[int, bytes | None] = {}
    if need:
        root = None
        if before_height is not None:
            try:
                root = rpc.call("getstateroot", [before_height])["roothash"]
            except (RpcError, KeyError, TypeError):
                root = None
        if root is not None:
            reqs = [("getstate", [root, r["contract"], b64e(r["_key"])]) for r in need]
        else:
            reqs = [("getstorage", [r["contract"], b64e(r["_key"])]) for r in need]
        try:
            res = rpc.batch(reqs)
        except RpcError:
            res = [None] * len(need)
        for r, v in zip(need, res):
            before[id(r)] = b64d(v) if isinstance(v, str) else None
    for r in rows:
        b = before.get(id(r)) if r["state"] in ("Changed", "Deleted") else None
        a = r.pop("_after")
        r.pop("_key")
        r["before"] = "0x" + b.hex() if b is not None else None
        r["after"] = "0x" + a.hex() if a is not None else None
        r["before_text"] = storage_value_text(b, book) if b is not None else ("-" if r["state"] == "Added" else "?")
        r["after_text"] = storage_value_text(a, book) if a is not None else "-"
        r["delta"] = _delta(b, a, r["state"])
        r["same"] = r["state"] == "Changed" and b is not None and a is not None and a == b
    return rows


__all__ = ["collect", "key_text", "split_key", "NATIVE_PREFIXES"]
