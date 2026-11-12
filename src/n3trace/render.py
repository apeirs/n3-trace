"""Plain-text reports for the terminal. The --json output carries the same model without this layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .codec import format_amount

MAX_TEXT = 110


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def when(ms: int | None) -> str:
    if not ms:
        return "?"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def g(n: int | None) -> str:
    return "?" if n is None else format_amount(int(n), 8)


def cut(s: str, n: int = MAX_TEXT) -> str:
    s = str(s).replace("\n", "\\n")
    return s if len(s) <= n else s[: n - 1] + "…"


def delta_text(d: Any) -> str:
    if isinstance(d, dict):
        return ", ".join(f"[{k}] {v:+d}" for k, v in d.items())
    return f"{d:+d}" if isinstance(d, int) else ""


def short(h: str | None) -> str:
    return h if not h or len(h) < 20 else f"{h[:10]}…{h[-4:]}"


class Out:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, s: str = "") -> None:
        self.lines.append(s)

    def head(self, title: str) -> None:
        if self.lines and self.lines[-1] != "":
            self.lines.append("")
        self.lines.append(title)

    def text(self) -> str:
        return "\n".join(self.lines).rstrip() + "\n"


# -- shared sections --------------------------------------------------------------------------------------------
def _signers(o: Out, signers: list[dict[str, Any]], notes: list[dict[str, str]]) -> None:
    o.head(f"Signers ({len(signers)})")
    for i, s in enumerate(signers, 1):
        w = s.get("witness")
        wtxt = f"  {w['text']}" + (f", {w['signatures']} signature(s)" if w and w["type"] == "multisig" else "") if w else ""
        o(f"  {i}. {s['address']}  {', '.join(s['scopes'])}{wtxt}")
        for n in notes:
            if n["signer"] == s["address"]:
                mark = {"high": "!!", "warn": " !", "info": "  "}[n["level"]]
                o(f"     {mark} {cut(n['text'], 400)}")


def _calls(o: Out, calls: list[dict[str, Any]], script: dict[str, Any]) -> None:
    how = "decoded exactly" if script["exact"] else "decoded in part"
    o.head(f"Calls in the entry script ({script['bytes']} bytes, {how})")
    if not calls:
        o("  (no contract calls)")
    for i, c in enumerate(calls, 1):
        tag = "" if c["in_abi"] or not c.get("contract") else "  (not in the ABI)"
        o(f"  {i}. {c['name']}.{c['method'] or '?'}  {short(c['contract'])}  result {c['result']}{tag}")
        if c["args"] is None:
            o("       (arguments are computed at run time)")
            continue
        width = max((len(a["name"] or "") for a in c["args"]), default=0)
        for j, a in enumerate(c["args"]):
            name = a["name"] or f"arg{j}"
            o(f"       {name:<{max(width, 4)}}  {(a['type'] or ''):<9} {cut(a['text'], 90)}")
    if not script["exact"]:
        for n in script["notes"][:3]:
            o(f"     note: {n}")
    if script.get("syscalls"):
        o(f"     other syscalls: {', '.join(script['syscalls'])}")


def _events(o: Out, notifs: list[dict[str, Any]], title: str = "Events") -> None:
    o.head(f"{title} ({len(notifs)})")
    if not notifs:
        o("  (none)")
    for n in notifs:
        args = ", ".join(f"{a['name']}={a['text']}" if a["name"] else a["text"] for a in n["args"])
        o(f"  {n['name']}.{n['event']}({cut(args, 160)})")


def _moves(o: Out, transfers: list[dict[str, Any]], deltas: list[dict[str, Any]]) -> None:
    if not transfers:
        return
    o.head(f"Token movements ({len(transfers)})")
    for t in transfers:
        tid = f" token {cut(t['tokenid_text'], 40)}" if "tokenid_text" in t else ""
        o(f"  {t['amount_text']:>26}{tid}  {t['from_label']} -> {t['to_label']}")
    if deltas:
        o.head("Balance changes")
        by: dict[str, list[str]] = {}
        labels: dict[str, str] = {}
        for d in deltas:
            by.setdefault(d["account"], []).append(d["delta_text"])
            labels[d["account"]] = d.get("label", d["account"])
        width = max(len(v) for v in labels.values())
        for acct, items in by.items():
            o(f"  {labels[acct]:<{width}}  {'   '.join(items)}")


def _tree(o: Out, tree: list[dict[str, Any]], limit: int) -> None:
    lines: list[str] = []

    def walk(nodes: list[dict[str, Any]], prefix: str) -> None:
        for i, n in enumerate(nodes):
            last = i == len(nodes) - 1
            label = n["name"] + (f".{n['method']}" if n.get("method") else "")
            if n["name"] != "entry script":
                label += f" {short(n['hash'])}"
            if n["times"] > 1:
                label += f"  x{n['times']}"
            lines.append(f"{prefix}{'└─ ' if last else '├─ '}{label}")
            walk(n["calls"], prefix + ("   " if last else "│  "))

    for root in tree:
        lines.append("  entry script")
        walk(root["calls"], "  ")
    shown = lines if limit <= 0 else lines[:limit]
    for line in shown:
        o(line)
    if len(shown) < len(lines):
        o(f"  … {len(lines) - len(shown)} more lines (--tree-lines 0 shows all)")


def _storage(o: Out, rows: list[dict[str, Any]] | None, limit: int, halted: bool = True) -> None:
    if rows is None:
        return
    same = sum(1 for r in rows if r.get("same"))
    o.head(f"Storage writes ({len(rows)}" + (f", {same} rewrote the value already there)" if same else ")")
           + ("" if halted else ", all discarded because the run FAULTed"))
    if not rows:
        o("  (none)")
        return
    nw = min(max(len(r["name"]) for r in rows), 26)
    for r in rows[: limit if limit > 0 else None]:
        state = {"Added": "+", "Deleted": "-", "Changed": "~"}.get(r["state"], "?")
        o(f"  {state} {cut(r['name'], 26):<{nw}}  {cut(r['key_text'], 70)}")
        if r["state"] == "Added":
            o(f"      = {cut(r['after_text'], 100)}")
        elif r["state"] == "Deleted":
            o(f"      was {cut(r['before_text'], 100)}")
        elif r.get("same"):
            o(f"      unchanged: {cut(r['after_text'], 90)}")
        else:
            delta = f"  ({delta_text(r.get('delta'))})" if r.get("delta") else ""
            o(f"      {cut(r['before_text'], 50)} -> {cut(r['after_text'], 50)}{delta}")
    if limit > 0 and len(rows) > limit:
        o(f"  … {len(rows) - limit} more (--json has all)")


# -- transaction ----------------------------------------------------------------------------------------------------
def transaction(m: dict[str, Any], tree_lines: int = 60, storage_rows: int = 40) -> str:
    o = Out()
    b = m["block"]
    on = m["onchain"] or {}
    pos = f"tx {b['position'] + 1} of {b['count']}" if b["position"] is not None else "position ?"
    o(f"Transaction {m['hash']}")
    o(f"  {on.get('state', '?')}  block {b['index']:,} ({pos})  {when(b['time'])}")
    o(f"  sender {m['sender']}   size {m['size']} B   valid until block {m['validuntilblock']:,}")
    f = m["fees"]
    fee = f"  fees {g(f['total'])} GAS = system {g(f['sysfee'])}"
    if "gasconsumed" in f:
        fee += f" (used {g(f['gasconsumed'])})"
    fee += f" + network {g(f['netfee'])}"
    if "size_fee" in f:
        fee += f" (size {f['size']} B x {f['feeperbyte']} = {g(f['size_fee'])}, verification {g(f['verification_fee'])})"
    o(fee)
    for a in m["attributes"]:
        o(f"  attribute {a.get('type')} {cut({k: v for k, v in a.items() if k != 'type'}, 80) if len(a) > 1 else ''}")
    for w in m["warnings"]:
        o(f"  ! {w}")

    _signers(o, m["signers"], m["scope_notes"])
    _calls(o, m["calls"], m["script"])

    if on:
        o.head(f"Result: {on['state']}, gas {g(on['gasconsumed'])}")
        if on.get("exception"):
            o(f"  exception: {cut(on['exception'], 200)}")
            if m.get("fault_path"):
                o(f"  last contract entered: {' > '.join(m['fault_path'])}")
            if m.get("fault_hint"):
                o(f"  likely cause: {m['fault_hint']}")
        if on["stack"]:
            o("  stack: " + cut(", ".join(s["text"] for s in on["stack"]), 150))
        _events(o, on["notifications"])
    _moves(o, m["transfers"], m["deltas"])

    r = m.get("replay")
    if r is not None:
        if "error" in r:
            o.head(f"Replay: not available ({cut(r['error'], 140)})")
        else:
            where = "the latest state" if r["height"] is None else f"the state after block {r['height']:,}"
            line = f"Replay on {where}: {r['state']}, gas {g(r['gasconsumed'])}"
            if "matches" in r:
                line += ", same as on chain" if r["matches"] else ", DIFFERENT from the chain (" + ", ".join(
                    k for k, ok in r["match"].items() if not ok) + ")"
            o.head(line)
            if r.get("exception"):
                o(f"  exception: {cut(r['exception'], 200)}")
                if r.get("fault_path"):
                    o(f"  last contract entered: {' > '.join(r['fault_path'])}")
                if r.get("fault_hint"):
                    o(f"  likely cause: {r['fault_hint']}")
            if "notifications" in r:
                _events(o, r["notifications"], "Replay events")
                _moves(o, r["transfers"], r["deltas"])
            o.head(f"Call tree ({plural(r['contract_calls'], 'contract call')}, depth {r['depth']})")
            _tree(o, r["tree"], tree_lines)
            _storage(o, r["storage"], storage_rows, r["state"] == "HALT")
    return o.text()


# -- preview -----------------------------------------------------------------------------------------------------------
def preview(m: dict[str, Any], tree_lines: int = 60, storage_rows: int = 40) -> str:
    o = Out()
    res = m["result"]
    verdict = {"HALT": "would succeed", "FAULT": "would FAIL", "REJECT": "would be REJECTED"}[m["verdict"]]
    o(f"Preview on the state after block {m['height']:,}: {verdict}")
    if m.get("hash"):
        o(f"  transaction {m['hash']}  size {m['size']} B  valid until block {m['validuntilblock']:,}")
    f = m["fees"]
    line = f"  gas needed {g(f['gas_needed'])}"
    if f.get("sysfee") is not None:
        line += f"   system fee set {g(f['sysfee'])}"
    if f.get("netfee") is not None:
        line += f"   network fee set {g(f['netfee'])}"
        if f.get("netfee_needed") is not None:
            line += f" (node asks {g(f['netfee_needed'])})"
    o(line)
    for e in m["errors"]:
        o(f"  X {e}")
    for w in m["warnings"]:
        o(f"  ! {w}")
    if m.get("fault_path"):
        o(f"  last contract entered: {' > '.join(m['fault_path'])}")
    if m.get("fault_hint"):
        o(f"  likely cause: {m['fault_hint']}")

    if m["signer_deltas"]:
        o.head("What the signers' balances do")
        for d in m["signer_deltas"]:
            o(f"  {d['account']}  {d['delta_text']}")
    elif m["signers"]:
        o.head("What the signers' balances do")
        o("  no token leaves or reaches a signer (fees aside)")

    _signers(o, m["signers"], m["scope_notes"])
    _calls(o, m["calls"], m["script"])
    o.head(f"Result: {res['state']}, gas {g(res['gasconsumed'])}")
    if res.get("exception"):
        o(f"  exception: {cut(res['exception'], 200)}")
    if res["stack"]:
        o("  stack: " + cut(", ".join(s["text"] for s in res["stack"]), 150))
    _events(o, res["notifications"])
    _moves(o, m["transfers"], m["deltas"])
    o.head(f"Call tree ({plural(m['contract_calls'], 'contract call')}, depth {m['depth']})")
    _tree(o, m["tree"], tree_lines)
    _storage(o, m["storage"], storage_rows, res["state"] == "HALT")
    return o.text()


# -- block -------------------------------------------------------------------------------------------------------------
def block(m: dict[str, Any]) -> str:
    o = Out()
    o(f"Block {m['index']:,}  {m['hash']}  {when(m['time'])}  {plural(len(m['transactions']), 'transaction')}")
    for i, t in enumerate(m["transactions"], 1):
        calls = ", ".join(t["calls"]) or "(no calls)"
        o(f"  {i:>3}. {t['hash']}  {t['state'] or '?':<5} gas {g(t['gasconsumed'])}  {t['sender']}")
        o(f"       {cut(calls, 120)}  ({t['events']} events)")
        if t.get("exception"):
            o(f"       exception: {cut(t['exception'], 120)}")
    return o.text()
