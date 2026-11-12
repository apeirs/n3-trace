"""Command line interface: n3-trace tx | preview | block | disasm."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import __version__, render
from .chain import ContractBook
from .codec import b64d, b64e, to_hash
from .preview import PreviewError, build_call, parse_signer, preview, resolve_contract
from .rpc import RecordingRpc, ReplayRpc, Rpc, RpcError
from .script import decode_script
from .trace import TraceError, block_summary, trace_tx
from .tx import TxFormatError, decode_input, parse_transaction
from .vm import DisassemblyError, format_listing

EXIT_OK, EXIT_ERROR, EXIT_FAULT = 0, 2, 3


def _rpc(args: argparse.Namespace) -> Any:
    if getattr(args, "replay", None):
        return ReplayRpc.load(args.replay)
    inner = Rpc.for_network(args.network, args.rpc or [], timeout=args.timeout)
    return RecordingRpc(inner) if getattr(args, "record", None) else inner


def _save(rpc: Any, args: argparse.Namespace) -> None:
    if getattr(args, "record", None) and isinstance(rpc, RecordingRpc):
        rpc.save(args.record)


def _emit(model: dict[str, Any], args: argparse.Namespace, text: str) -> None:
    if args.json:
        model = {"tool": f"n3-trace {__version__}", "network": args.network if not args.rpc else "custom", **model}
        json.dump(model, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(text)


def _read_input(text: str) -> str:
    if text == "-":
        return sys.stdin.read()
    if text.startswith("@"):
        with open(text[1:], encoding="utf-8") as fh:
            return fh.read()
    return text


# -- commands -----------------------------------------------------------------------------------------------------------
def cmd_tx(args: argparse.Namespace) -> int:
    rpc = _rpc(args)
    at: int | str | None = None
    if args.at is not None:
        at = "latest" if args.at == "latest" else int(args.at)
    try:
        m = trace_tx(rpc, args.txid, at=at, with_storage=not args.no_storage)
    finally:
        _save(rpc, args)
    _emit(m, args, render.transaction(m, args.tree_lines, args.storage_rows))
    state = (m["replay"] or {}).get("state") if at is not None else (m["onchain"] or {}).get("state")
    return EXIT_OK if state == "HALT" else EXIT_FAULT


def cmd_preview(args: argparse.Namespace) -> int:
    given = [x for x in (args.raw, args.script, args.call) if x]
    if len(given) != 1:
        raise PreviewError("give exactly one of: raw transaction bytes, --script, or --call CONTRACT METHOD [ARG...]")
    rpc = _rpc(args)
    try:
        book = ContractBook(rpc)
        tx = None
        if args.raw:
            if args.signer:
                raise PreviewError("a raw transaction carries its own signers; drop --signer")
            tx = parse_transaction(decode_input(_read_input(args.raw)))
            script, signers = tx.script, tx.signers
        else:
            signers = [parse_signer(s, args.allow) for s in args.signer or []]
            if args.script:
                script = decode_input(_read_input(args.script))
            else:
                if len(args.call) < 2:
                    raise PreviewError("--call needs CONTRACT METHOD [ARG...]")
                contract, method, *rest = args.call
                script = build_call(book, contract, method, rest)
        m = preview(rpc, script, signers, tx=tx, book=book, with_storage=not args.no_storage)
    finally:
        _save(rpc, args)
    _emit(m, args, render.preview(m, args.tree_lines, args.storage_rows))
    return EXIT_OK if m["verdict"] == "HALT" else EXIT_FAULT


def cmd_block(args: argparse.Namespace) -> int:
    rpc = _rpc(args)
    try:
        m = block_summary(rpc, args.block)
    finally:
        _save(rpc, args)
    _emit(m, args, render.block(m))
    return EXIT_OK


def cmd_disasm(args: argparse.Namespace) -> int:
    ref = _read_input(args.input).strip()
    source = "script"
    script: bytes | None = None
    h = ref[2:] if ref.lower().startswith("0x") else ref
    is_hash = len(h) == 64 and all(c in "0123456789abcdefABCDEF" for c in h)
    if is_hash or len(h) == 40 or (ref.startswith("N") and len(ref) == 34) or ref.isalpha():
        rpc = _rpc(args)
        try:
            if is_hash:
                tx = rpc.call("getrawtransaction", [("0x" + h).lower(), True])
                script, source = b64d(tx["script"]), f"entry script of {tx['hash']}"
            else:
                book = ContractBook(rpc)
                ch = resolve_contract(ref, book) if ref.isalpha() else to_hash(ref)
                st = rpc.call("getcontractstate", [ch])
                script = b64d(st["nef"]["script"])
                source = f"{st['manifest']['name']} {ch} (NEF script)"
        finally:
            _save(rpc, args)
    else:
        script = decode_input(ref)
    listing = format_listing(script)
    decoded = decode_script(script)
    if args.json:
        model = {
            "source": source,
            "bytes": len(script),
            "base64": b64e(script),
            "listing": listing.splitlines(),
            "calls": [{"contract": c.contract, "method": c.method, "args": len(c.args) if c.args is not None else None,
                       "result": c.result_use} for c in decoded.calls],
            "exact": decoded.exact,
        }
        json.dump(model, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return EXIT_OK
    print(f"; {source}, {len(script)} bytes")
    print(listing)
    if decoded.calls:
        print(f"; {len(decoded.calls)} contract call(s){'' if decoded.exact else ' (partly computed)'}: "
              + ", ".join(f"{c.contract}.{c.method}" for c in decoded.calls))
    return EXIT_OK


# -- parser -------------------------------------------------------------------------------------------------------------
def _common(p: argparse.ArgumentParser, output: bool = True) -> None:
    p.add_argument("--network", default="mainnet", help="mainnet (default) or testnet")
    p.add_argument("--rpc", action="append", metavar="URL", help="RPC endpoint (repeat for failover); replays need a NeoGo node")
    p.add_argument("--timeout", type=float, default=30.0, help="seconds per HTTP request (default 30)")
    p.add_argument("--json", action="store_true", help="JSON output")
    if output:
        p.add_argument("--no-storage", action="store_true", help="skip storage writes (saves a few RPC calls)")
        p.add_argument("--tree-lines", type=int, default=60, metavar="N", help="call tree lines to print (default 60, 0 = all)")
        p.add_argument("--storage-rows", type=int, default=40, metavar="N", help="storage writes to print (default 40, 0 = all)")
    p.add_argument("--record", metavar="FILE", help=argparse.SUPPRESS)
    p.add_argument("--replay", metavar="FILE", help=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="n3-trace",
        description="Read any Neo N3 transaction like a debugger, and preview one before you sign it. Read-only: "
        "nothing is signed or sent.",
    )
    ap.add_argument("--version", action="version", version=f"n3-trace {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tx", help="explain a transaction on chain and replay it on the state it ran on")
    p.add_argument("txid", help="transaction hash")
    p.add_argument("--at", metavar="HEIGHT|latest", help="replay on the state after this block instead of the block before it")
    _common(p)
    p.set_defaults(func=cmd_tx)

    p = sub.add_parser("preview", help="run a transaction that is not on chain yet: what would signing it do?")
    p.add_argument("raw", nargs="?", help="raw transaction, hex or base64, signed or not ('-' = stdin, @FILE = file)")
    p.add_argument("--script", metavar="SCRIPT", help="entry script, hex or base64, instead of a whole transaction")
    p.add_argument("--call", nargs="+", metavar="ARG", help="CONTRACT METHOD [ARG...]: one call, arguments checked against the ABI")
    p.add_argument("--signer", action="append", metavar="ACCOUNT[:SCOPES]",
                   help="signer for --script/--call (repeat; default scope CalledByEntry)")
    p.add_argument("--allow", action="append", metavar="CONTRACT", help="allowed contract for a CustomContracts signer")
    _common(p)
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("block", help="one line per transaction in a block")
    p.add_argument("block", help="block index or hash")
    _common(p, output=False)
    p.set_defaults(func=cmd_block)

    p = sub.add_parser("disasm", help="NeoVM listing of a script, a transaction's entry script, or a contract")
    p.add_argument("input", help="script (hex/base64), transaction hash, contract hash/address, or native name like GasToken")
    _common(p, output=False)
    p.set_defaults(func=cmd_disasm)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (TraceError, PreviewError, TxFormatError, DisassemblyError, RpcError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyError as exc:
        # Only a replayed fixture raises KeyError for a request it does not hold.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
