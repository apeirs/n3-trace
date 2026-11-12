"""Run a transaction that is not on chain yet and say what signing it would do.

Input is one of: the raw transaction bytes a wallet or dApp is about to sign (signed or not), an entry script
plus signers, or a single contract call written as `contract method args...` (arguments are checked against the
contract's ABI). Nothing is signed or sent: the node only runs the script in a test invocation.
"""

from __future__ import annotations

import json
from typing import Any

from . import storage as storage_mod
from .chain import ContractBook
from .codec import address_to_le, b64e, hash_to_le, parse_amount, to_hash
from .rpc import RpcError
from .script import decode_script
from .trace import (
    TraceError, balance_deltas, call_tree, calls_view, fault_hint, gas, notifications_view, scope_notes,
    last_entered, transfers_view, tree_count, tree_depth,
)
from .tx import Transaction, parse_scopes, rpc_signer, signer_view
from .values import item_value, render
from .vm import emit_contract_call

ALIASES = {"gas": "GasToken", "neo": "NeoToken", "policy": "PolicyContract", "management": "ContractManagement",
           "ledger": "LedgerContract", "oracle": "OracleContract", "roles": "RoleManagement", "stdlib": "StdLib",
           "crypto": "CryptoLib", "notary": "Notary"}

VALID_SCOPES = {"None", "CalledByEntry", "CustomContracts", "CustomGroups", "WitnessRules", "Global"}


class PreviewError(ValueError):
    """The input could not be turned into something to run."""


# -- inputs -------------------------------------------------------------------------------------------------------
def parse_signer(spec: str, allowed: list[str] | None = None) -> dict[str, Any]:
    """'NAddr' or 'NAddr:CalledByEntry' or '0xhash:CustomContracts' -> internal signer dict."""
    acct, _, scopes = spec.partition(":")
    try:
        h = to_hash(acct)
    except ValueError as exc:
        raise PreviewError(str(exc)) from exc
    names = parse_scopes(scopes) if scopes else ["CalledByEntry"]
    bad = [n for n in names if n not in VALID_SCOPES]
    if bad:
        raise PreviewError(f"unknown scope {bad[0]!r}; use one of {', '.join(sorted(VALID_SCOPES))}")
    if "WitnessRules" in names or "CustomGroups" in names:
        raise PreviewError("WitnessRules and CustomGroups signers can only be previewed from raw transaction bytes")
    s: dict[str, Any] = {"account": h, "scopes": names}
    if "CustomContracts" in names:
        if not allowed:
            raise PreviewError("a CustomContracts signer needs --allow CONTRACT")
        s["allowedcontracts"] = [to_hash(a) for a in allowed]
    return s


def resolve_contract(ref: str, book: ContractBook) -> str:
    book.load_natives()
    name = ALIASES.get(ref.lower(), ref)
    for h, c in list(book._by_hash.items()):
        if c is not None and c.native and c.name.lower() == name.lower():
            return h
    try:
        h = to_hash(ref)
    except ValueError as exc:
        raise PreviewError(f"{ref!r} is not a contract hash or a native contract name") from exc
    if book.get(h) is None:
        raise PreviewError(f"no contract {h} on this network")
    return h


def _loose(v: Any) -> Any:
    """A JSON value as a stack value: N-addresses and 0x+40 hex become Hash160, other 0x hex bytes, text UTF-8."""
    if isinstance(v, str):
        if v.startswith("N") and len(v) == 34:
            return address_to_le(v)
        if v.lower().startswith("0x"):
            h = v[2:]
            return hash_to_le(v) if len(h) == 40 else bytes.fromhex(h)
        return v.encode()
    if isinstance(v, list):
        return [_loose(x) for x in v]
    if isinstance(v, dict):
        raise PreviewError("maps are not supported as call arguments; pass the raw script with --script")
    return v


def parse_arg(text: str, typ: str, decimals: int | None = None) -> Any:
    """One command-line argument as the ABI type asks for it."""
    try:
        if typ == "Hash160":
            return hash_to_le(to_hash(text))
        if typ == "Integer":
            t = text.replace("_", "")
            if decimals is not None and "." in t:
                return parse_amount(t, decimals)
            return int(t, 0)
        if typ == "Boolean":
            if text.lower() not in ("true", "false", "1", "0"):
                raise ValueError("expected true or false")
            return text.lower() in ("true", "1")
        if typ == "String":
            return text.encode()
        if typ == "Hash256":
            h = text[2:] if text.lower().startswith("0x") else text
            if len(h) != 64:
                raise ValueError("expected 32 bytes of hex")
            return bytes.fromhex(h)[::-1]
        if typ == "PublicKey":
            b = bytes.fromhex(text[2:] if text.lower().startswith("0x") else text)
            if len(b) != 33:
                raise ValueError("expected a 33-byte compressed key")
            return b
        if typ == "ByteArray":
            return bytes.fromhex(text[2:]) if text.lower().startswith("0x") else text.encode()
        if typ in ("Array", "Any"):
            if typ == "Any" and text == "null":
                return None
            try:
                v = json.loads(text)
            except json.JSONDecodeError:
                if typ == "Array":
                    raise ValueError("expected a JSON list, e.g. '[\"NXV…\", 5]'") from None
                return _loose(text)
            return _loose(v)
    except ValueError as exc:
        raise PreviewError(f"argument {text!r} is not a valid {typ}: {exc}") from exc
    raise PreviewError(f"arguments of type {typ} are not supported on the command line; pass --script")


def build_call(book: ContractBook, contract_ref: str, method: str, args: list[str]) -> bytes:
    h = resolve_contract(contract_ref, book)
    c = book.get(h)
    assert c is not None
    meta = c.method(method, len(args))
    if meta is None:
        same = sorted({f"{n}/{k}" for (n, k) in c.methods if n == method})
        if same:
            raise PreviewError(f"{c.name}.{method} takes {' or '.join(s.split('/')[1] for s in same)} argument(s), got {len(args)}")
        raise PreviewError(f"{c.name} has no method {method!r}")
    dec = None
    tok = book.token(h)
    if tok is not None and tok.standard == "NEP-17":
        dec = tok.decimals
    values = []
    for text, p in zip(args, meta["params"]):
        d = dec if p["type"] == "Integer" and p["name"] in ("amount", "value") else None
        values.append(parse_arg(text, p["type"], d))
    return emit_contract_call(hash_to_le(h), method, values)


# -- the run ---------------------------------------------------------------------------------------------------------
def preview(rpc: Any, script: bytes, signers: list[dict[str, Any]], tx: Transaction | None = None,
            book: ContractBook | None = None, with_storage: bool = True) -> dict[str, Any]:
    book = book or ContractBook(rpc)
    book.load_natives()
    decoded = decode_script(script)
    rsigners = [rpc_signer(s) for s in signers]
    reqs: list[tuple[str, list[Any]]] = [("getblockcount", []), ("invokescript", [b64e(script), rsigners, True])]
    if tx is not None and tx.witnesses:
        reqs.append(("calculatenetworkfee", [b64e(tx.unsigned + _witness_bytes(tx))]))
    res = rpc.batch(reqs)
    if isinstance(res[0], RpcError):
        raise TraceError(f"node did not answer getblockcount: {res[0]}")
    height = int(res[0]) - 1
    inv = res[1]
    if isinstance(inv, RpcError):
        raise TraceError(f"the node refused the test invocation: {inv}")
    netfee_needed = None
    netfee_error = None
    if len(res) > 2:
        if isinstance(res[2], RpcError):
            netfee_error = str(res[2])
        else:
            netfee_needed = int(res[2].get("networkfee"))

    witnesses = tx.witnesses if tx is not None else []
    sview = [signer_view(s, witnesses[i] if i < len(witnesses) else None) for i, s in enumerate(signers)]
    calls = calls_view(decoded, book)
    diag = inv.get("diagnostics") or {}
    notifs = inv.get("notifications") or []
    tree = call_tree(diag.get("invokedcontracts"), book, decoded)
    transfers = transfers_view(notifs, book)
    deltas = balance_deltas(transfers, book)
    state = inv.get("state")
    gasconsumed = int(inv.get("gasconsumed") or 0)

    errors: list[str] = []
    warnings: list[str] = []
    if state != "HALT":
        errors.append(f"the script FAULTs: {inv.get('exception')}")
    if tx is not None:
        if tx.sysfee < gasconsumed:
            errors.append(f"system fee {gas(tx.sysfee)} is below the {gas(gasconsumed)} the script needs: it will FAULT on chain "
                          "and the fees are still paid")
        elif gasconsumed and tx.sysfee > 2 * gasconsumed and tx.sysfee - gasconsumed > 10**6:
            warnings.append(f"system fee {gas(tx.sysfee)} is {tx.sysfee / gasconsumed:.0f}x the {gas(gasconsumed)} needed; "
                            "the unused part is burned, not refunded")
        if tx.validuntilblock <= height:
            errors.append(f"validUntilBlock {tx.validuntilblock} has passed (chain is at {height}): nodes will reject it")
        if netfee_needed is not None and tx.netfee < netfee_needed:
            errors.append(f"network fee {gas(tx.netfee)} is below the {gas(netfee_needed)} the node asks: nodes will reject it")
        if not tx.witnesses:
            warnings.append("the transaction is not signed yet, so the network fee cannot be checked")
        elif netfee_error:
            warnings.append(f"the node could not price the network fee: {netfee_error}")
        if len(tx.witnesses) not in (0, len(tx.signers)):
            errors.append(f"{len(tx.signers)} signers but {len(tx.witnesses)} witnesses")
    if not signers:
        warnings.append("no signer given: any CheckWitness in the call fails (use --signer)")
    if decoded.calls and not decoded.exact:
        warnings.append("the script does more than plain contract calls: " + "; ".join(decoded.notes[:3]))

    signer_accounts = {s["address"] for s in sview}
    yours = [d for d in deltas if d["account"] in signer_accounts]

    model: dict[str, Any] = {
        "kind": "preview",
        "height": height,
        "hash": tx.hash if tx is not None else None,
        "size": tx.size if tx is not None else None,
        "validuntilblock": tx.validuntilblock if tx is not None else None,
        "signers": sview,
        "scope_notes": scope_notes(sview, calls, decoded.exact, book),
        "script": {"bytes": len(script), "base64": b64e(script), "exact": decoded.exact, "notes": decoded.notes,
                   "syscalls": sorted(set(decoded.other_syscalls))},
        "calls": calls,
        "result": {
            "state": state,
            "gasconsumed": gasconsumed,
            "exception": inv.get("exception"),
            "stack": [{"type": s.get("type"), "text": render(item_value(s), None, book)} for s in inv.get("stack") or []],
            "notifications": notifications_view(notifs, book),
        },
        "transfers": transfers,
        "deltas": deltas,
        "signer_deltas": yours,
        "tree": tree,
        "contract_calls": max(tree_count(tree) - len(tree), 0),
        "depth": max(tree_depth(tree) - 1, 0),
        "storage": storage_mod.collect(rpc, book, diag.get("storagechanges") or [], None) if with_storage else None,
        "fees": {
            "gas_needed": gasconsumed,
            "sysfee": tx.sysfee if tx is not None else None,
            "netfee": tx.netfee if tx is not None else None,
            "netfee_needed": netfee_needed,
        },
        "errors": errors,
        "warnings": warnings,
        "verdict": "FAULT" if state != "HALT" else ("REJECT" if errors else "HALT"),
    }
    if state != "HALT":
        model["fault_path"] = last_entered(tree)
        model["fault_hint"] = fault_hint(inv.get("exception"), gasconsumed, tx.sysfee if tx is not None else None,
                                         decoded, model["fault_path"])
    return model


def _witness_bytes(tx: Transaction) -> bytes:
    from .codec import b64d

    def var(n: int) -> bytes:
        if n < 0xFD:
            return bytes([n])
        if n <= 0xFFFF:
            return b"\xfd" + n.to_bytes(2, "little")
        return b"\xfe" + n.to_bytes(4, "little")

    out = var(len(tx.witnesses))
    for w in tx.witnesses:
        inv, ver = b64d(w["invocation"]), b64d(w["verification"])
        out += var(len(inv)) + inv + var(len(ver)) + ver
    return out
