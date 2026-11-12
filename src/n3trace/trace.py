"""Explain one execution: what the script calls, what it emits, what it moves, what it writes, and what it costs.

`trace_tx` explains a transaction already on chain and replays it on the state it ran on (block H-1).
`preview` (in preview.py) runs a transaction that is not on chain yet and reuses the same pieces.
"""

from __future__ import annotations

import re
from typing import Any

from . import storage as storage_mod
from .chain import ContractBook
from .codec import b64d, format_amount, hash_to_le, le_to_address, le_to_hash
from .rpc import RpcError
from .script import CallResult, DecodedScript, Unknown, decode_script
from .tx import signer_view
from .values import _printable, item_value, jsonable, render, typed_json

GAS_DECIMALS = 8


class TraceError(Exception):
    """The transaction or block could not be found or explained."""


def gas(n: int | str | None) -> str:
    if n is None:
        return "?"
    return f"{format_amount(int(n), GAS_DECIMALS)} GAS"


# -- entry script calls ---------------------------------------------------------------------------------------
def _arg_text(v: Any, typ: str | None, name: str | None, book: ContractBook, contract: Any) -> str:
    if isinstance(v, CallResult):
        return repr(v)
    if isinstance(v, Unknown):
        return "? (computed)"
    dec = sym = None
    if typ == "Integer" and contract is not None and name in ("amount", "value", "quantity") and contract.is_token:
        tok = book.token(contract.hash)
        if tok is not None and tok.standard == "NEP-17":
            dec, sym = tok.decimals, tok.symbol
    return render(v, typ, book, dec, sym)


def calls_view(decoded: DecodedScript, book: ContractBook) -> list[dict[str, Any]]:
    book.prefetch(c.contract for c in decoded.calls if c.contract)
    book.tokens(c.contract for c in decoded.calls if c.contract)
    out = []
    for c in decoded.calls:
        contract = book.get(c.contract)
        meta = contract.method(c.method, len(c.args) if c.args is not None else None) if contract else None
        params = (meta or {}).get("params") or []
        args = []
        for i, v in enumerate(c.args or []):
            p = params[i] if i < len(params) and len(params) == len(c.args or []) else {}
            args.append(
                {
                    "name": p.get("name"),
                    "type": p.get("type"),
                    "value": typed_json(v, p.get("type")) if not isinstance(v, (CallResult, Unknown)) else repr(v),
                    "text": _arg_text(v, p.get("type"), p.get("name"), book, contract),
                }
            )
        out.append(
            {
                "contract": c.contract,
                "name": contract.name if contract else ("unknown contract" if c.contract else "?"),
                "method": c.method,
                "args": args if c.args is not None else None,
                "flags": c.flags,
                "result": c.result_use,
                "in_abi": meta is not None,
                "safe": bool(meta and meta.get("safe")),
                "returns": (meta or {}).get("returntype"),
            }
        )
    return out


# -- notifications and token movements -------------------------------------------------------------------------
def notifications_view(notifs: list[dict[str, Any]], book: ContractBook) -> list[dict[str, Any]]:
    book.prefetch(n.get("contract") for n in notifs)
    book.tokens(n.get("contract") for n in notifs if n.get("eventname") == "Transfer")
    out = []
    for n in notifs or []:
        c = book.get(n.get("contract"))
        values = item_value(n.get("state")) or []
        if not isinstance(values, list):
            values = [values]
        params = (c.events.get(n.get("eventname")) if c else None) or []
        tok = book.token(c.hash) if c is not None and n.get("eventname") == "Transfer" else None
        args = []
        for i, v in enumerate(values):
            p = params[i] if len(params) == len(values) else {}
            dec = sym = None
            if tok is not None and tok.standard == "NEP-17" and i == 2:
                dec, sym = tok.decimals, tok.symbol
            args.append({"name": p.get("name"), "type": p.get("type"), "value": typed_json(v, p.get("type")),
                         "text": render(v, p.get("type"), book, dec, sym)})
        out.append({"contract": n.get("contract"), "name": c.name if c else "unknown contract",
                    "event": n.get("eventname"), "args": args})
    return out


def _account(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)) and len(v) == 20:
        return le_to_address(bytes(v))
    return "?"


def account_label(addr: str | None, book: ContractBook) -> str:
    """'N… (ContractName)' when the address is a contract this run has seen, else the address; null is 'null'."""
    if addr is None:
        return "null (mint/burn)"
    if addr == "?":
        return "?"
    from .codec import address_to_le

    try:
        c = book.known(le_to_hash(address_to_le(addr)))
    except ValueError:
        c = None
    return f"{addr} ({c.name})" if c is not None else addr


def transfers_view(notifs: list[dict[str, Any]], book: ContractBook) -> list[dict[str, Any]]:
    """NEP-17 and NEP-11 Transfer events, with symbols and decimals applied."""
    out = []
    for n in notifs or []:
        if n.get("eventname") != "Transfer":
            continue
        tok = book.token(n.get("contract"))
        if tok is None:
            continue
        vals = item_value(n.get("state")) or []
        if not isinstance(vals, list) or len(vals) not in (3, 4) or not isinstance(vals[2], int):
            continue
        row: dict[str, Any] = {
            "token": n.get("contract"),
            "symbol": tok.symbol,
            "standard": tok.standard,
            "decimals": tok.decimals,
            "from": _account(vals[0]),
            "to": _account(vals[1]),
            "amount": vals[2],
            "amount_text": f"{format_amount(vals[2], tok.decimals)} {tok.symbol}",
        }
        row["from_label"] = account_label(row["from"], book)
        row["to_label"] = account_label(row["to"], book)
        if len(vals) == 4:
            tid = vals[3]
            row["tokenid"] = jsonable(tid)
            row["tokenid_text"] = repr(tid.decode()) if isinstance(tid, bytes) and _printable(tid) else render(tid, "ByteArray")
        out.append(row)
    return out


def balance_deltas(transfers: list[dict[str, Any]], book: ContractBook | None = None) -> list[dict[str, Any]]:
    """Net change per (account, token). Mints come from null and burns go to null; those are left out."""
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for t in transfers:
        for who, sign in ((t["from"], -1), (t["to"], 1)):
            if who is None or who == "?":
                continue
            k = (who, t["token"])
            row = acc.setdefault(k, {"account": who, "token": t["token"], "symbol": t["symbol"],
                                     "decimals": t["decimals"], "delta": 0})
            row["delta"] += sign * t["amount"]
    rows = [r for r in acc.values() if r["delta"] != 0]
    for r in rows:
        txt = format_amount(abs(r["delta"]), r["decimals"])
        r["delta_text"] = f"{'+' if r['delta'] > 0 else '-'}{txt} {r['symbol']}"
        r["label"] = account_label(r["account"], book) if book is not None else r["account"]
    return rows


# -- call tree ---------------------------------------------------------------------------------------------------
def _signature(node: dict[str, Any]) -> str:
    return node.get("hash", "?") + "(" + ",".join(_signature(c) for c in node.get("call") or []) + ")"


def call_tree(invoked: list[dict[str, Any]] | None, book: ContractBook, decoded: DecodedScript | None) -> list[dict[str, Any]]:
    """NeoGo's `invokedcontracts`, with contract names, entry-call method names, and runs of identical calls folded."""
    if not invoked:
        return []
    hashes: set[str] = set()

    def walk(ns: list[dict[str, Any]]) -> None:
        for n in ns:
            hashes.add(n.get("hash"))
            walk(n.get("call") or [])

    walk(invoked)
    book.prefetch(hashes)

    def build(ns: list[dict[str, Any]], methods: list[str | None] | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, n in enumerate(ns):
            sig = _signature(n)
            method = methods[i] if methods else None
            if out and out[-1]["_sig"] == sig and out[-1].get("method") == method:
                out[-1]["times"] += 1
                continue
            c = book.get(n.get("hash"))
            out.append({"hash": n.get("hash"), "name": c.name if c else "unknown contract", "method": method,
                        "times": 1, "calls": build(n.get("call") or []), "_sig": sig})
        return out

    def strip(ns: list[dict[str, Any]]) -> None:
        for n in ns:
            n.pop("_sig", None)
            if n.get("method") is None:
                n.pop("method", None)
            strip(n["calls"])

    roots = []
    for root in invoked:
        kids = root.get("call") or []
        methods = None
        if decoded is not None and decoded.exact and len(kids) == len(decoded.calls) and all(
            k.get("hash") == c.contract for k, c in zip(kids, decoded.calls)
        ):
            methods = [c.method for c in decoded.calls]
        roots.append({"hash": root.get("hash"), "name": "entry script", "times": 1, "calls": build(kids, methods)})
    strip(roots)
    return roots


def tree_depth(nodes: list[dict[str, Any]]) -> int:
    return 1 + max((tree_depth(n["calls"]) for n in nodes), default=0) if nodes else 0


def last_entered(nodes: list[dict[str, Any]]) -> list[str]:
    """Path to the last contract entered: where a FAULT happened, or the contract that returned just before it."""
    path: list[str] = []
    while nodes:
        n = nodes[-1]
        if n["name"] != "entry script":
            path.append(n["name"] + (f".{n['method']}" if n.get("method") else ""))
        nodes = n["calls"]
    return path


def tree_count(nodes: list[dict[str, Any]]) -> int:
    return sum(n["times"] * (1 + tree_count(n["calls"])) for n in nodes)


# -- signers and their scopes ------------------------------------------------------------------------------------
def scope_notes(signers: list[dict[str, Any]], calls: list[dict[str, Any]], exact: bool, book: ContractBook) -> list[dict[str, str]]:
    """What each signer's witness scope lets the contracts in this transaction do."""
    notes = []
    direct = sorted({c["name"] for c in calls if c.get("contract")})
    for i, s in enumerate(signers):
        who = s["address"]
        scopes = s["scopes"]
        if "Global" in scopes:
            notes.append({"level": "high", "signer": who, "text": (
                "Global scope: every contract this transaction reaches, directly or through other contracts, "
                "can act with this signature (move its tokens, change its votes). Wallets should never need it.")})
            continue
        if scopes == ["None"]:
            notes.append({"level": "info", "signer": who, "text": "scope None: the signature only pays the fees"})
            continue
        if "CalledByEntry" in scopes:
            names = ", ".join(direct) or "no contract"
            txt = f"CalledByEntry: only contracts the script calls directly can use the signature ({names})"
            if not exact:
                txt += "; the script is not a plain call list, so check it by hand"
            notes.append({"level": "info" if exact else "warn", "signer": who, "text": txt})
        if "CustomContracts" in scopes:
            allowed = s.get("allowedcontracts") or []
            book.prefetch(allowed)
            names = ", ".join(f"{book.name(h)} ({h})" for h in allowed)
            notes.append({"level": "info", "signer": who, "text": f"CustomContracts: {names}"})
        if "CustomGroups" in scopes:
            notes.append({"level": "warn", "signer": who, "text": (
                f"CustomGroups: any contract whose manifest is signed by {len(s.get('allowedgroups') or [])} group key(s) "
                "can use the signature, including contracts deployed later by the same group")})
        if "WitnessRules" in scopes:
            for r in s.get("rules") or []:
                cond = r.get("condition") or {}
                txt = f"rule {r.get('action')}: {condition_text(cond, book)}"
                level = "high" if r.get("action") == "Allow" and cond == {"type": "Boolean", "expression": True} else "info"
                if level == "high":
                    txt += " (allows everything, same as Global)"
                notes.append({"level": level, "signer": who, "text": txt})
    return notes


def condition_text(c: dict[str, Any], book: ContractBook) -> str:
    t = c.get("type")
    if t == "Boolean":
        e = c.get("expression")
        return "always" if e in (True, "true") else "never"
    if t == "Not":
        return f"not ({condition_text(c.get('expression') or {}, book)})"
    if t in ("And", "Or"):
        return "(" + f" {t.lower()} ".join(condition_text(x, book) for x in c.get("expressions") or []) + ")"
    if t in ("ScriptHash", "CalledByContract"):
        h = c.get("hash")
        return f"{t} {book.name(h)} ({h})"
    if t in ("Group", "CalledByGroup"):
        return f"{t} {c.get('group')}"
    return str(t)


# -- faults --------------------------------------------------------------------------------------------------------
def fault_hint(exception: str | None, gasconsumed: int | None, sysfee: int | None,
               decoded: DecodedScript | None = None, path: list[str] | None = None) -> str | None:
    if not exception:
        return None
    e = exception.lower()
    m = re.search(r"at instruction (\d+) \((assert|assertmsg)\)", e)
    if m and decoded is not None:
        off = int(m.group(1))
        for c in decoded.calls:
            if c.meta.get("assert_at") == off and path is not None and len(path) <= 1:
                return (f"the entry script asserts that {c.method or 'call'} #{c.index + 1} returned true, and it returned "
                        "false (for a NEP-17 transfer: balance too low, or the sender did not sign)")
    if "gas limit" in e or "insufficient gas" in e or (sysfee is not None and gasconsumed is not None and gasconsumed >= sysfee):
        return "the system fee ran out: raise sysfee to at least the gas a test invoke reports"
    if "assert" in e:
        return "a contract (or the entry script) ASSERTed on a false value: a check in the contract failed"
    if "abort" in e:
        return "a contract called ABORT: it rejected the call on purpose"
    if "method not found" in e or "not found" in e and "method" in e:
        return "the called method or argument count is not in the contract's ABI"
    if "callflags" in e or "call flags" in e:
        return "the call was made with flags that do not allow what the callee does (e.g. ReadOnly)"
    if "witness" in e:
        return "a CheckWitness failed: a signer is missing or its scope does not reach this contract"
    if "insufficient" in e or "balance" in e:
        return "a balance was too low for the transfer"
    return None


def _same_notifs(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    def norm(ns: list[dict[str, Any]]) -> list[Any]:
        return [(n.get("contract"), n.get("eventname"), jsonable(item_value(n.get("state")))) for n in ns or []]
    return norm(a) == norm(b)


# -- one transaction ------------------------------------------------------------------------------------------------
def _native(book: ContractBook, name: str) -> str | None:
    book.load_natives()
    for h, c in list(book._by_hash.items()):
        if c is not None and c.native and c.name == name:
            return h
    return None


def trace_tx(rpc: Any, txid: str, at: int | str | None = None, with_storage: bool = True) -> dict[str, Any]:
    """Everything about one on-chain transaction, plus a replay on the state before it (or at `at`)."""
    txid = txid.strip().lower()
    if not txid.startswith("0x"):
        txid = "0x" + txid
    if len(txid) != 66:
        raise TraceError("a transaction hash is 32 bytes (64 hex digits)")
    tx_res, log_res = rpc.batch([("getrawtransaction", [txid, True]), ("getapplicationlog", [txid])])
    if isinstance(tx_res, RpcError):
        raise TraceError(f"transaction {txid} not found on this network ({tx_res})")
    tx = tx_res
    if not tx.get("blockhash"):
        raise TraceError(f"transaction {txid} is still in the mempool; use `n3-trace preview` on its bytes")
    execu = None
    if not isinstance(log_res, RpcError):
        for e in log_res.get("executions") or []:
            if e.get("trigger") == "Application":
                execu = e
    book = ContractBook(rpc)
    book.load_natives()
    policy = _native(book, "PolicyContract")

    block = rpc.call("getblock", [tx["blockhash"], True])
    height = int(block["index"])
    hashes = [t.get("hash") if isinstance(t, dict) else t for t in block.get("tx") or []]
    position = hashes.index(txid) if txid in hashes else None

    script = b64d(tx["script"])
    decoded = decode_script(script)

    if at is None:
        start = height - 1
    elif at == "latest":
        start = None
    else:
        start = int(at)
    signers_rpc = [{k: v for k, v in s.items()} for s in tx.get("signers") or []]

    reqs: list[tuple[str, list[Any]]] = []
    if start is None:
        reqs.append(("invokescript", [tx["script"], signers_rpc, True]))
    else:
        reqs.append(("invokescripthistoric", [start, tx["script"], signers_rpc, True]))
    if policy:
        reqs.append(("invokefunctionhistoric", [height - 1, policy, "getFeePerByte", []]))
    res = rpc.batch(reqs)
    replay_raw = res[0]
    fee_per_byte = None
    if policy and not isinstance(res[1], RpcError) and res[1].get("state") == "HALT":
        try:
            fee_per_byte = int(res[1]["stack"][0]["value"])
        except (KeyError, IndexError, TypeError, ValueError):
            fee_per_byte = None

    witnesses = tx.get("witnesses") or []
    signers = [signer_view(s, witnesses[i] if i < len(witnesses) else None) for i, s in enumerate(tx.get("signers") or [])]
    calls = calls_view(decoded, book)

    onchain_notifs = (execu or {}).get("notifications") or []
    onchain = None
    if execu is not None:
        onchain = {
            "state": execu.get("vmstate"),
            "gasconsumed": int(execu.get("gasconsumed") or 0),
            "exception": execu.get("exception"),
            "stack": [{"type": s.get("type"), "text": render(item_value(s), None, book)} for s in execu.get("stack") or []],
            "notifications": notifications_view(onchain_notifs, book),
        }
    transfers = transfers_view(onchain_notifs, book)

    sysfee, netfee, size = int(tx["sysfee"]), int(tx["netfee"]), int(tx["size"])
    fees: dict[str, Any] = {"sysfee": sysfee, "netfee": netfee, "total": sysfee + netfee, "size": size}
    if onchain is not None:
        fees["gasconsumed"] = onchain["gasconsumed"]
        fees["unused_sysfee"] = sysfee - onchain["gasconsumed"]
    if fee_per_byte is not None:
        fees["feeperbyte"] = fee_per_byte
        fees["size_fee"] = size * fee_per_byte
        fees["verification_fee"] = netfee - size * fee_per_byte

    replay: dict[str, Any] | None = None
    if isinstance(replay_raw, RpcError):
        replay = {"error": str(replay_raw), "height": start}
    else:
        diag = replay_raw.get("diagnostics") or {}
        rnotifs = replay_raw.get("notifications") or []
        tree = call_tree(diag.get("invokedcontracts"), book, decoded)
        replay = {
            "height": start,
            "state": replay_raw.get("state"),
            "gasconsumed": int(replay_raw.get("gasconsumed") or 0),
            "exception": replay_raw.get("exception"),
            "tree": tree,
            "contract_calls": max(tree_count(tree) - len(tree), 0),
            "depth": max(tree_depth(tree) - 1, 0),
            "storage": storage_mod.collect(rpc, book, diag.get("storagechanges") or [], start) if with_storage else None,
        }
        if replay["state"] != "HALT":
            replay["fault_path"] = last_entered(tree)
            replay["fault_hint"] = fault_hint(replay["exception"], replay["gasconsumed"], sysfee if at is None else None,
                                              decoded, replay["fault_path"])
        if at is not None:
            replay["notifications"] = notifications_view(rnotifs, book)
            replay["transfers"] = transfers_view(rnotifs, book)
            replay["deltas"] = balance_deltas(replay["transfers"], book)
        if onchain is not None:
            same_notifs = _same_notifs(rnotifs, onchain_notifs)
            replay["match"] = {
                "state": replay["state"] == onchain["state"],
                "gas": replay["gasconsumed"] == onchain["gasconsumed"],
                "notifications": same_notifs,
            }
            replay["matches"] = all(replay["match"].values())

    warnings: list[str] = []
    notes = scope_notes(signers, calls, decoded.exact, book)
    if onchain is not None and onchain["state"] == "HALT" and fees.get("unused_sysfee", 0) > max(onchain["gasconsumed"], 10**6):
        warnings.append(
            f"system fee {gas(sysfee)} was {sysfee / max(onchain['gasconsumed'], 1):.0f}x the gas used ({gas(onchain['gasconsumed'])}); "
            f"the unused {gas(fees['unused_sysfee'])} was burned, N3 does not refund it")
    if replay is not None and "matches" in replay and not replay["matches"] and at is None:
        why = []
        if position:
            why.append(f"{position} earlier transaction(s) in block {height} ran first on chain but not in the replay")
        other = set(decoded.other_syscalls)
        if other & {"System.Runtime.GetRandom", "System.Runtime.GetTime"}:
            why.append("the script reads the block time or randomness")
        if not why:
            why.append("contracts that read the block time, randomness or the transaction itself can take another path")
        diff = [k for k, ok in replay["match"].items() if not ok]
        warnings.append(f"the replay differs from the chain in {', '.join(diff)}: " + "; ".join(why))
    elif position and at is None:
        warnings.append(f"replayed on the state after block {height - 1}; {position} earlier transaction(s) in this block are not applied")

    model: dict[str, Any] = {
        "kind": "transaction",
        "hash": tx["hash"],
        "block": {"index": height, "hash": tx["blockhash"], "time": int(block.get("time") or tx.get("blocktime") or 0),
                  "position": position, "count": len(hashes)},
        "confirmations": tx.get("confirmations"),
        "sender": tx.get("sender"),
        "size": size,
        "nonce": tx.get("nonce"),
        "validuntilblock": tx.get("validuntilblock"),
        "attributes": tx.get("attributes") or [],
        "signers": signers,
        "scope_notes": notes,
        "script": {"bytes": len(script), "exact": decoded.exact, "notes": decoded.notes,
                   "syscalls": sorted(set(decoded.other_syscalls))},
        "calls": calls,
        "onchain": onchain,
        "transfers": transfers,
        "deltas": balance_deltas(transfers, book),
        "fees": fees,
        "replay": replay,
        "warnings": warnings,
    }
    if onchain is not None and onchain["state"] != "HALT":
        path = (replay or {}).get("fault_path")
        model["fault_hint"] = fault_hint(onchain["exception"], onchain["gasconsumed"], sysfee, decoded, path)
        if path:
            model["fault_path"] = path
    return model


def block_summary(rpc: Any, ref: str) -> dict[str, Any]:
    """One line per transaction in a block: sender, calls, state, gas."""
    ref = ref.strip()
    param: Any = int(ref) if ref.isdigit() else ref
    try:
        block = rpc.call("getblock", [param, True])
    except RpcError as exc:
        raise TraceError(f"block {ref} not found ({exc})") from exc
    hashes = [t.get("hash") if isinstance(t, dict) else t for t in block.get("tx") or []]
    rows = []
    if hashes:
        res = rpc.batch([(m, [h, True] if m == "getrawtransaction" else [h]) for h in hashes for m in ("getrawtransaction", "getapplicationlog")])
        book = ContractBook(rpc)
        decs = []
        for i, h in enumerate(hashes):
            tx = res[2 * i]
            decs.append(decode_script(b64d(tx["script"])) if isinstance(tx, dict) else None)
        book.prefetch(c.contract for d in decs if d for c in d.calls if c.contract)
        for i, h in enumerate(hashes):
            tx, log = res[2 * i], res[2 * i + 1]
            ex = next((e for e in (log.get("executions") or []) if e.get("trigger") == "Application"), {}) if isinstance(log, dict) else {}
            d = decs[i]
            calls = [f"{book.name(c.contract) if c.contract else '?'}.{c.method or '?'}" for c in (d.calls if d else [])]
            rows.append({
                "hash": h,
                "sender": tx.get("sender") if isinstance(tx, dict) else None,
                "calls": calls,
                "state": ex.get("vmstate"),
                "gasconsumed": int(ex.get("gasconsumed") or 0),
                "sysfee": int(tx.get("sysfee") or 0) if isinstance(tx, dict) else None,
                "netfee": int(tx.get("netfee") or 0) if isinstance(tx, dict) else None,
                "events": len(ex.get("notifications") or []),
                "exception": ex.get("exception"),
            })
    return {"kind": "block", "index": int(block["index"]), "hash": block["hash"], "time": int(block.get("time") or 0),
            "primary": block.get("primary"), "transactions": rows}


__all__ = [
    "TraceError", "trace_tx", "block_summary", "calls_view", "notifications_view", "transfers_view",
    "balance_deltas", "call_tree", "scope_notes", "fault_hint", "gas", "le_to_hash", "hash_to_le",
]
