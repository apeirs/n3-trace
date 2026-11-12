"""Minimal JSON-RPC client for Neo N3 nodes (stdlib only).

Features: endpoint failover, retries with backoff, JSON-RPC batching, and a
record/replay layer so tests can run offline against responses captured from
mainnet.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Iterable, Sequence

from . import __version__

NETWORKS = {
    "mainnet": [f"https://mainnet{i}.neo.coz.io:443" for i in (1, 2, 3, 4, 5)],
    "testnet": ["https://testnet1.neo.coz.io:443", "https://testnet2.neo.coz.io:443"],
}

USER_AGENT = f"n3-trace/{__version__} (+https://github.com/apeirs/n3-trace)"

# Keep batches small enough that public nodes answer them quickly.
MAX_BATCH = 40


class RpcError(Exception):
    """The node answered with a JSON-RPC error, or no endpoint answered at all."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def _key(method: str, params: Sequence[Any]) -> str:
    return json.dumps([method, list(params)], sort_keys=True, separators=(",", ":"))


class Rpc:
    """JSON-RPC over HTTPS with failover across several endpoints."""

    def __init__(self, urls: Iterable[str], timeout: float = 30.0, retries: int = 3):
        self.urls = list(urls)
        if not self.urls:
            raise ValueError("at least one RPC URL is required")
        self.timeout = timeout
        self.retries = retries
        self._preferred = 0
        self.calls = 0  # number of HTTP round trips, for progress output

    @classmethod
    def for_network(cls, network: str, extra: Sequence[str] = (), **kw: Any) -> "Rpc":
        if extra:
            return cls(extra, **kw)
        if network not in NETWORKS:
            raise ValueError(f"unknown network {network!r} (use mainnet, testnet or --rpc URL)")
        return cls(NETWORKS[network], **kw)

    # -- transport -----------------------------------------------------------
    def _post(self, body: Any, lane: int = 0) -> Any:
        """POST to the preferred endpoint; `lane` > 0 starts at another one, to spread parallel work."""
        data = json.dumps(body).encode()
        last: Exception | None = None
        base = self._preferred + lane
        for attempt in range(self.retries):
            server_errors = 0
            for i in range(len(self.urls)):
                url = self.urls[(base + i) % len(self.urls)]
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"content-type": "application/json", "user-agent": USER_AGENT},
                )
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        out = json.load(resp)
                    if lane == 0:
                        self._preferred = (base + i) % len(self.urls)
                    self.calls += 1
                    return out
                except urllib.error.HTTPError as exc:
                    last = exc
                    if exc.code >= 500:
                        server_errors += 1
                except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as exc:
                    last = exc
            if server_errors == len(self.urls):
                # Every node failed on this exact request: retrying will not help.
                break
            time.sleep(min(2.0**attempt, 8.0))
        raise RpcError(f"no RPC endpoint answered ({', '.join(self.urls)}): {last}")

    # -- API -----------------------------------------------------------------
    def call(self, method: str, params: Sequence[Any] = ()) -> Any:
        out = self._post({"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)})
        if out.get("error"):
            err = out["error"]
            raise RpcError(f"{method}: {err.get('message')} {err.get('data') or ''}".strip(), err.get("code"))
        return out["result"]

    @property
    def lanes(self) -> int:
        """How many endpoints parallel work can be spread over."""
        return len(self.urls)

    def batch(self, requests: Sequence[tuple[str, Sequence[Any]]], lane: int = 0) -> list[Any]:
        """Run many calls in as few round trips as possible. Errors become RpcError objects in the list."""
        results: list[Any] = []
        for start in range(0, len(requests), MAX_BATCH):
            chunk = requests[start : start + MAX_BATCH]
            body = [
                {"jsonrpc": "2.0", "id": i, "method": m, "params": list(p)} for i, (m, p) in enumerate(chunk)
            ]
            out = self._post(body, lane)
            if isinstance(out, dict):  # some nodes answer a batch with a single error object
                raise RpcError(f"batch refused: {out.get('error')}")
            by_id = {r.get("id"): r for r in out}
            for i, (m, _p) in enumerate(chunk):
                r = by_id.get(i)
                if r is None:
                    results.append(RpcError(f"{m}: missing from batch response"))
                elif r.get("error"):
                    results.append(RpcError(f"{m}: {r['error'].get('message')}", r["error"].get("code")))
                else:
                    results.append(r["result"])
        return results


def _contract_state(st: Any) -> Any:
    if not isinstance(st, dict):
        return st
    m = st.get("manifest") or {}
    return {"id": st.get("id"), "hash": st.get("hash"),
            "manifest": {k: m.get(k) for k in ("name", "supportedstandards", "abi")}}


def _slim(method: str, result: Any) -> Any:
    """Drop the bulky fields this tool never reads, to keep test fixtures small."""
    if method == "getcontractstate":
        return _contract_state(result)
    if method == "getnativecontracts" and isinstance(result, list):
        return [_contract_state(st) for st in result]
    if method == "getblock" and isinstance(result, dict):
        out = {k: result[k] for k in ("hash", "index", "time", "primary") if k in result}
        out["tx"] = [{"hash": t.get("hash")} if isinstance(t, dict) else t for t in result.get("tx") or []]
        return out
    if method == "getstateroot" and isinstance(result, dict):
        return {k: result[k] for k in ("index", "roothash") if k in result}
    if method == "getblockheader" and isinstance(result, dict):
        return {k: result[k] for k in ("hash", "index", "time") if k in result}
    return result


class RecordingRpc:
    """Wraps an Rpc and remembers every answer, so a session can be saved as a test fixture."""

    def __init__(self, inner: Rpc):
        self.inner = inner
        self.tape: dict[str, Any] = {}

    def call(self, method: str, params: Sequence[Any] = ()) -> Any:
        try:
            res = self.inner.call(method, params)
        except RpcError as exc:
            self.tape[_key(method, params)] = {"__error__": str(exc), "code": exc.code}
            raise
        self.tape[_key(method, params)] = _slim(method, res)
        return res

    @property
    def lanes(self) -> int:
        return self.inner.lanes

    def batch(self, requests: Sequence[tuple[str, Sequence[Any]]], lane: int = 0) -> list[Any]:
        try:
            res = self.inner.batch(requests, lane)
        except RpcError as exc:
            # Remember the failure per request; a later single call may overwrite it with a success.
            for m, p in requests:
                self.tape.setdefault(_key(m, p), {"__error__": str(exc), "code": exc.code})
            raise
        for (m, p), r in zip(requests, res):
            self.tape[_key(m, p)] = {"__error__": str(r), "code": r.code} if isinstance(r, RpcError) else _slim(m, r)
        return res

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.tape, fh, indent=0, sort_keys=True)
            fh.write("\n")


class ReplayRpc:
    """Answers from a recorded tape; raises if asked for something that was not recorded."""

    def __init__(self, tape: dict[str, Any]):
        self.tape = tape
        self.calls = 0

    @classmethod
    def load(cls, path: str) -> "ReplayRpc":
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def _get(self, method: str, params: Sequence[Any]) -> Any:
        key = _key(method, params)
        if key not in self.tape:
            raise KeyError(f"not in fixture: {key[:160]}")
        val = self.tape[key]
        if isinstance(val, dict) and "__error__" in val:
            return RpcError(val["__error__"], val.get("code"))
        return val

    def call(self, method: str, params: Sequence[Any] = ()) -> Any:
        self.calls += 1
        val = self._get(method, params)
        if isinstance(val, RpcError):
            raise val
        return val

    lanes = 1

    def batch(self, requests: Sequence[tuple[str, Sequence[Any]]], lane: int = 0) -> list[Any]:
        self.calls += 1
        out = [self._get(m, p) for m, p in requests]
        if len(out) > 1 and all(isinstance(r, RpcError) for r in out):
            raise out[0]  # the whole batch failed when it was recorded
        return out
