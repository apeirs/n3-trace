"""What the chain knows about contracts: names, ABIs, token symbols and decimals. Cached and batched."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .codec import as_int, as_text, stack_value
from .rpc import RpcError


@dataclass
class Contract:
    hash: str
    id: int | None
    name: str
    standards: list[str] = field(default_factory=list)
    methods: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict)
    events: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    native: bool = False

    @classmethod
    def from_state(cls, state: dict[str, Any], native: bool = False) -> "Contract":
        m = state.get("manifest") or {}
        abi = m.get("abi") or {}
        methods = {}
        for meth in abi.get("methods") or []:
            params = meth.get("parameters") or []
            methods[(meth.get("name"), len(params))] = {
                "params": [{"name": p.get("name"), "type": p.get("type")} for p in params],
                "returntype": meth.get("returntype"),
                "safe": bool(meth.get("safe")),
            }
        events = {}
        for ev in abi.get("events") or []:
            events.setdefault(ev.get("name"), [{"name": p.get("name"), "type": p.get("type")} for p in ev.get("parameters") or []])
        return cls(
            hash=state.get("hash"),
            id=state.get("id"),
            name=m.get("name") or "?",
            standards=[str(s).upper() for s in m.get("supportedstandards") or []],
            methods=methods,
            events=events,
            native=native,
        )

    def method(self, name: str | None, nargs: int | None) -> dict[str, Any] | None:
        if name is None:
            return None
        if nargs is not None and (name, nargs) in self.methods:
            return self.methods[(name, nargs)]
        same = [v for (n, _k), v in self.methods.items() if n == name]
        return same[0] if len(same) == 1 else None

    @property
    def is_token(self) -> bool:
        return any(s in ("NEP-17", "NEP17", "NEP-11", "NEP11") for s in self.standards) or (
            self.method("decimals", 0) is not None and self.method("symbol", 0) is not None
        )


@dataclass
class Token:
    symbol: str
    decimals: int
    standard: str  # NEP-17 | NEP-11


class ContractBook:
    """Looks up contracts once per run. Unknown or destroyed contracts are remembered as missing."""

    def __init__(self, rpc: Any):
        self.rpc = rpc
        self._by_hash: dict[str, Contract | None] = {}
        self._by_id: dict[int, Contract] = {}
        self._tokens: dict[str, Token | None] = {}
        self._natives_loaded = False

    def _add(self, c: Contract) -> None:
        self._by_hash[c.hash] = c
        if c.id is not None:
            self._by_id[c.id] = c

    def load_natives(self) -> None:
        if self._natives_loaded:
            return
        self._natives_loaded = True
        try:
            natives = self.rpc.call("getnativecontracts")
        except RpcError:
            return
        for st in natives or []:
            self._add(Contract.from_state(st, native=True))

    def prefetch(self, hashes: Iterable[str]) -> None:
        self.load_natives()
        todo = sorted({h for h in hashes if h and h not in self._by_hash})
        if not todo:
            return
        res = self.rpc.batch([("getcontractstate", [h]) for h in todo])
        for h, r in zip(todo, res):
            if isinstance(r, RpcError) or not isinstance(r, dict):
                self._by_hash[h] = None
            else:
                self._add(Contract.from_state(r))

    def get(self, h: str | None) -> Contract | None:
        if not h:
            return None
        if h not in self._by_hash:
            self.prefetch([h])
        return self._by_hash.get(h)

    def known(self, h: str) -> Contract | None:
        """A contract already looked up, without a new RPC call."""
        return self._by_hash.get(h)

    def by_id(self, cid: int) -> Contract | None:
        self.load_natives()
        if cid in self._by_id:
            return self._by_id[cid]
        try:
            st = self.rpc.call("getcontractstate", [cid])
        except RpcError:
            return None
        c = Contract.from_state(st)
        self._add(c)
        return c

    def name(self, h: str | None) -> str:
        c = self.get(h)
        if c is not None:
            return c.name
        return "unknown contract" if h else "?"

    def tokens(self, hashes: Iterable[str]) -> None:
        """Fetch symbol and decimals for every token contract among `hashes`, in one batch."""
        todo = []
        for h in sorted(set(hashes)):
            if h in self._tokens:
                continue
            c = self.get(h)
            if c is None or not c.is_token:
                self._tokens[h] = None
                continue
            todo.append(c)
        if not todo:
            return
        reqs = []
        for c in todo:
            reqs.append(("invokefunction", [c.hash, "symbol", []]))
            reqs.append(("invokefunction", [c.hash, "decimals", []]))
        res = self.rpc.batch(reqs)
        for i, c in enumerate(todo):
            sym, dec = res[2 * i], res[2 * i + 1]
            try:
                if isinstance(sym, RpcError) or isinstance(dec, RpcError) or sym.get("state") != "HALT" or dec.get("state") != "HALT":
                    raise ValueError
                symbol = as_text(stack_value(sym["stack"][0]))
                decimals = as_int(stack_value(dec["stack"][0]))
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                self._tokens[c.hash] = None
                continue
            nep11 = any(s in ("NEP-11", "NEP11") for s in c.standards) or c.method("ownerOf", 1) is not None
            self._tokens[c.hash] = Token(symbol, decimals, "NEP-11" if nep11 else "NEP-17")

    def token(self, h: str | None) -> Token | None:
        if not h:
            return None
        if h not in self._tokens:
            self.tokens([h])
        return self._tokens.get(h)
