"""Neo N3 transaction binary format: parse a raw (signed or unsigned) transaction, describe signers and witnesses.

Used by `preview`, which takes the bytes a wallet or dApp is about to sign, and by `tx` to classify witnesses.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .codec import b64d, b64e, le_to_address, le_to_hash
from .vm import DisassemblyError, iter_instructions, syscall_id

SCOPE_FLAGS = [
    (0x01, "CalledByEntry"),
    (0x10, "CustomContracts"),
    (0x20, "CustomGroups"),
    (0x40, "WitnessRules"),
    (0x80, "Global"),
]

ATTR_HIGH_PRIORITY, ATTR_ORACLE_RESPONSE, ATTR_NOT_VALID_BEFORE, ATTR_CONFLICTS, ATTR_NOTARY_ASSISTED = (
    0x01, 0x11, 0x20, 0x21, 0x22,
)

COND_NAMES = {
    0x00: "Boolean", 0x01: "Not", 0x02: "And", 0x03: "Or", 0x18: "ScriptHash", 0x19: "Group",
    0x20: "CalledByEntry", 0x28: "CalledByContract", 0x29: "CalledByGroup",
}


class TxFormatError(ValueError):
    pass


def scope_names(flags: int) -> list[str]:
    if flags == 0:
        return ["None"]
    names = [n for bit, n in SCOPE_FLAGS if flags & bit]
    if flags & ~sum(b for b, _ in SCOPE_FLAGS):
        raise TxFormatError(f"unknown witness scope bits 0x{flags:02x}")
    return names


def parse_scopes(text: str) -> list[str]:
    """'CalledByEntry, CustomContracts' (RPC form) -> ['CalledByEntry', 'CustomContracts']."""
    return [s.strip() for s in str(text).split(",") if s.strip()]


class _Reader:
    def __init__(self, b: bytes):
        self.b = b
        self.i = 0

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.b):
            raise TxFormatError("transaction is truncated")
        out = self.b[self.i : self.i + n]
        self.i += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def uint(self, n: int) -> int:
        return int.from_bytes(self.take(n), "little")

    def var(self) -> int:
        f = self.u8()
        if f < 0xFD:
            return f
        return self.uint({0xFD: 2, 0xFE: 4, 0xFF: 8}[f])

    def varbytes(self, limit: int = 1 << 20) -> bytes:
        n = self.var()
        if n > limit:
            raise TxFormatError("length out of range")
        return self.take(n)


def _condition(r: _Reader, depth: int = 0) -> dict[str, Any]:
    if depth > 3:
        raise TxFormatError("witness condition nested too deep")
    t = r.u8()
    name = COND_NAMES.get(t)
    if name is None:
        raise TxFormatError(f"unknown witness condition type 0x{t:02x}")
    if name == "Boolean":
        return {"type": name, "expression": r.u8() != 0}
    if name == "Not":
        return {"type": name, "expression": _condition(r, depth + 1)}
    if name in ("And", "Or"):
        n = r.var()
        if n > 16:
            raise TxFormatError("too many sub-conditions")
        return {"type": name, "expressions": [_condition(r, depth + 1) for _ in range(n)]}
    if name in ("ScriptHash", "CalledByContract"):
        return {"type": name, "hash": le_to_hash(r.take(20))}
    if name in ("Group", "CalledByGroup"):
        return {"type": name, "group": r.take(33).hex()}
    return {"type": name}


@dataclass
class Transaction:
    version: int
    nonce: int
    sysfee: int
    netfee: int
    validuntilblock: int
    signers: list[dict[str, Any]]
    attributes: list[dict[str, Any]]
    script: bytes
    witnesses: list[dict[str, str]] = field(default_factory=list)
    unsigned: bytes = b""
    size: int = 0

    @property
    def hash(self) -> str:
        return "0x" + hashlib.sha256(self.unsigned).digest()[::-1].hex()

    @property
    def sender(self) -> str | None:
        return self.signers[0]["account"] if self.signers else None

    def rpc_signers(self) -> list[dict[str, Any]]:
        return [rpc_signer(s) for s in self.signers]


def rpc_signer(s: dict[str, Any]) -> dict[str, Any]:
    """A signer in the shape invokescript / invokefunction accept."""
    out: dict[str, Any] = {"account": s["account"], "scopes": ", ".join(s["scopes"])}
    if s.get("allowedcontracts"):
        out["allowedcontracts"] = list(s["allowedcontracts"])
    if s.get("allowedgroups"):
        out["allowedgroups"] = list(s["allowedgroups"])
    if s.get("rules"):
        out["rules"] = list(s["rules"])
    return out


def parse_transaction(raw: bytes) -> Transaction:
    r = _Reader(raw)
    version = r.u8()
    if version != 0:
        raise TxFormatError(f"unsupported transaction version {version}")
    nonce = r.uint(4)
    sysfee = int.from_bytes(r.take(8), "little", signed=True)
    netfee = int.from_bytes(r.take(8), "little", signed=True)
    vub = r.uint(4)
    signers = []
    for _ in range(r.var()):
        acct = r.take(20)
        flags = r.u8()
        s: dict[str, Any] = {"account": le_to_hash(acct), "scopes": scope_names(flags)}
        if flags & 0x10:
            s["allowedcontracts"] = [le_to_hash(r.take(20)) for _ in range(r.var())]
        if flags & 0x20:
            s["allowedgroups"] = [r.take(33).hex() for _ in range(r.var())]
        if flags & 0x40:
            rules = []
            for _ in range(r.var()):
                action = r.u8()
                rules.append({"action": {0: "Deny", 1: "Allow"}.get(action, f"0x{action:02x}"), "condition": _condition(r)})
            s["rules"] = rules
        signers.append(s)
    if not signers:
        raise TxFormatError("a transaction needs at least one signer")
    attributes = []
    for _ in range(r.var()):
        t = r.u8()
        if t == ATTR_HIGH_PRIORITY:
            attributes.append({"type": "HighPriority"})
        elif t == ATTR_ORACLE_RESPONSE:
            oid = r.uint(8)
            code = r.u8()
            attributes.append({"type": "OracleResponse", "id": oid, "code": code, "result": b64e(r.varbytes())})
        elif t == ATTR_NOT_VALID_BEFORE:
            attributes.append({"type": "NotValidBefore", "height": r.uint(4)})
        elif t == ATTR_CONFLICTS:
            attributes.append({"type": "Conflicts", "hash": "0x" + r.take(32)[::-1].hex()})
        elif t == ATTR_NOTARY_ASSISTED:
            attributes.append({"type": "NotaryAssisted", "nkeys": r.u8()})
        else:
            raise TxFormatError(f"unknown transaction attribute 0x{t:02x}")
    script = r.varbytes(0xFFFF)
    if not script:
        raise TxFormatError("empty script")
    unsigned = raw[: r.i]
    witnesses = []
    if r.i < len(raw):
        for _ in range(r.var()):
            inv = r.varbytes(1024)
            ver = r.varbytes(1024)
            witnesses.append({"invocation": b64e(inv), "verification": b64e(ver)})
    if r.i != len(raw):
        raise TxFormatError("trailing bytes after the transaction")
    return Transaction(version, nonce, sysfee, netfee, vub, signers, attributes, script, witnesses, unsigned, len(raw))


def decode_input(text: str) -> bytes:
    """Raw bytes from base64 or hex (with or without 0x)."""
    t = text.strip()
    h = t[2:] if t.lower().startswith("0x") else t
    if h and len(h) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in h):
        return bytes.fromhex(h)
    try:
        return b64d(t)
    except ValueError as exc:
        raise ValueError("input is neither hex nor base64") from exc


# -- witnesses -----------------------------------------------------------------------
_CHECKSIG = syscall_id("System.Crypto.CheckSig")
_CHECKMULTISIG = syscall_id("System.Crypto.CheckMultisig")


def describe_verification(script: bytes) -> dict[str, Any]:
    """Classify a witness verification script: standard, m-of-n multisig, contract-based or custom."""
    if not script:
        return {"type": "contract", "text": "contract witness (the account's verify method)"}
    try:
        ins = list(iter_instructions(script))
    except DisassemblyError:
        return {"type": "custom", "text": f"custom verification script ({len(script)} bytes)"}
    if (
        len(ins) == 2
        and ins[0].name == "PUSHDATA1"
        and len(ins[0].operand) == 33
        and ins[1].name == "SYSCALL"
        and ins[1].operand == _CHECKSIG
    ):
        return {"type": "standard", "pubkey": ins[0].operand.hex(), "text": "single signature"}
    if len(ins) >= 4 and ins[-1].name == "SYSCALL" and ins[-1].operand == _CHECKMULTISIG:
        try:
            m = ins[0].push_value()
            n = ins[-2].push_value()
        except ValueError:
            m = n = None
        keys = [i.operand.hex() for i in ins[1:-2] if i.name == "PUSHDATA1" and len(i.operand) == 33]
        if isinstance(m, int) and isinstance(n, int) and n == len(keys):
            return {"type": "multisig", "m": m, "n": n, "pubkeys": keys, "text": f"{m}-of-{n} multisig"}
    return {"type": "custom", "text": f"custom verification script ({len(script)} bytes)"}


def count_signatures(invocation: bytes) -> int:
    """How many 64-byte signatures an invocation script pushes."""
    try:
        return sum(1 for i in iter_instructions(invocation) if i.name == "PUSHDATA1" and len(i.operand) == 64)
    except DisassemblyError:
        return 0


def signer_view(s: dict[str, Any], witness: dict[str, str] | None) -> dict[str, Any]:
    """Signer + its witness, in a form the renderers and --json share."""
    from .codec import hash_to_le

    out: dict[str, Any] = {
        "account": s["account"],
        "address": le_to_address(hash_to_le(s["account"])),
        "scopes": s["scopes"] if isinstance(s["scopes"], list) else parse_scopes(s["scopes"]),
    }
    for k in ("allowedcontracts", "allowedgroups", "rules"):
        if s.get(k):
            out[k] = s[k]
    if witness is not None:
        ver = b64d(witness.get("verification") or "")
        inv = b64d(witness.get("invocation") or "")
        w = describe_verification(ver)
        w["signatures"] = count_signatures(inv)
        out["witness"] = w
    return out
