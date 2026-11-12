# Tutorial: replay a Neo N3 transaction and see every contract call and storage write, with curl

`n3-trace` is a convenience over a handful of RPC calls that any NeoGo node answers. This walks through them by
hand on one real mainnet transaction, a plain GAS transfer:
[`0x1e8700a6…f29d`](https://dora.coz.io/transaction/neo3/mainnet/0x1e8700a6f80de6b59849617709d79f0ea21a3e173a8fec3e39f5ff7952b1f29d)
in block 14,832,243. Every value below is what the public COZ node returned on 2026-11-12.

```bash
RPC=https://mainnet1.neo.coz.io:443
rpc() { curl -s --max-time 30 -H 'Content-Type: application/json' "$RPC" \
          -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$1\",\"params\":$2}"; }
TX=0x1e8700a6f80de6b59849617709d79f0ea21a3e173a8fec3e39f5ff7952b1f29d
```

(From Python, send a `User-Agent` header: the COZ nodes answer 403 to the default `Python-urllib` one.)

## 1. The transaction and what it did

```bash
rpc getrawtransaction "[\"$TX\", true]"   # script, signers, sysfee 215925, netfee 37824, blockhash
rpc getapplicationlog "[\"$TX\"]"         # vmstate HALT, gasconsumed 215925, one GAS Transfer event
```

That is all an explorer has: the result and the events. It does not say which contracts ran, in what order, or
what they wrote.

## 2. Replay it on the state it ran on

`invokescripthistoric(N, script, signers, diagnostics)` runs a script on the state **after** block N. A transaction
in block H ran on the state after block H-1, so:

```bash
rpc invokescripthistoric '[14832242,
  "CwNZRw2eAAAAAAwU/sAhTTsO0KOCocbz0fKybs6k2hkMFL7B3fyDE5eVbrQVBDNSFKtN+SOwFMAfDAh0cmFuc2ZlcgwUz3bii9AGLEpHjuNVYQETGfPPpNJBYn1bUg==",
  [{"account": "0xb023f94dab1452330415b46e95971383fcddc1be", "scopes": "CalledByEntry"}],
  true]'
```

```json
"state": "HALT", "gasconsumed": "215925",
"diagnostics": {
  "invokedcontracts": [
    {"hash": "0xcfc0626c6b9fcd31b7a2799865399e5da8b21ede",
     "call": [{"hash": "0xd2a4cff31913016155e38e474a2c06d08be276cf"}]}
  ],
  "storagechanges": [
    {"state": "Added",   "key": "+v///xT+wCFNOw7Qo4KhxvPR8rJuzqTaGQ==", "value": "QQEhBVlHDZ4A"},
    {"state": "Changed", "key": "+v///xS+wd38gxOXlW60FQQzUhSrTfkjsA==", "value": "QQEhBqs60zT0Cw=="}
  ]
}
```

**Check the replay before you trust it.** Here the state and the gas (215925) match the application log to the
datoshi, and so does the Transfer event, so this tree and these writes are the transaction's own. If they did
not match, the usual reason is an earlier transaction in the same block that changed the state first: the replay
does not include it. `n3-trace` compares state, gas and notifications every time and says DIFFERENT when one
of them differs.

## 3. Read the call tree

The root of `invokedcontracts` is the entry script itself: `0xcfc0626c…` is the script hash of the bytes above.
Its children are the contracts it called, and their children the contracts those called. NeoGo gives hashes only,
so names come from `getcontractstate` (`manifest.name`), and method names come from decoding the entry script
(step 6). Here there is one call: GasToken (`0xd2a4cff3…`).

On a DEX swap the same field holds 19 nested calls (router → factory → pair → tokens → StdLib…). That nesting is
what a debugger shows and an explorer does not.

## 4. Decode the storage writes

Each key is the **contract id** (int32, little-endian) followed by the contract's own key:

```python
import base64
k = base64.b64decode("+v///xS+wd38gxOXlW60FQQzUhSrTfkjsA==")
int.from_bytes(k[:4], "little", signed=True)   # -6 = GasToken (getnativecontracts lists the ids)
k[4:5].hex(), k[5:].hex()                      # '14', 'bec1ddfc…23b0' = prefix 0x14 (balance) + account (LE)
```

`0x14` + account is the GasToken balance map (`Prefix_Account` in `FungibleToken.cs`). The account bytes are
a script hash in little-endian order, `0xb023f94d…c1be` as a hash, `NdJbpKzsxT3UtHDDQnfgMWemsvuh7Tj64J` as an
address. The value `QQEhBqs60zT0Cw==` is a serialized stack item:
`41 01 | 21 06 ab3ad334f40b` = Struct of 1 field, Integer 6 bytes = 13,143,486,184,107 (131,434.86 GAS).

Two cautions:
- The order of `storagechanges` is not stable between calls. Sort before comparing two runs.
- A `Changed` entry can hold the value that was already there. It still costs storage gas. `n3-trace` marks
  these rows `unchanged:`; on one Flamingo trade 25 of 53 writes were like that.

## 5. The value before each write

Diagnostics give only the new value. The old one is in the state of block H-1, which every NeoGo node with
StateRoot enabled serves:

```bash
rpc getstateroot '[14832242]'      # roothash 0x85f62717…ac65
rpc getstate '["0x85f627170184aa8c69aeb3ebce8e8fed24200460c6fbba71fe38b26196c1ac65",
               "0xd2a4cff31913016155e38e474a2c06d08be276cf", "FL7B3fyDE5eVbrQVBDNSFKtN+SOw"]'
# -> "QQEhBgSC4NL0Cw=="  = Struct[13,146,137,854,468]
```

The key passed to `getstate` is the contract's own key, without the 4-byte id. Before 13,146,137,854,468, after
13,143,486,184,107: the sender lost exactly the 26.51670361 GAS of the Transfer event. The `Added` key had no
value before, and its new value (`QQEhBVlHDZ4A` = 2,651,670,361) is the recipient's first GAS balance.

`getstate` takes one key per call, so `n3-trace` sends all of them in one JSON-RPC batch.

## 6. What the entry script calls

The script is ordinary NeoVM code. Wallets and SDKs all build the same shape:

```
PUSHNULL                          data = null
PUSHINT64 2651670361              amount
PUSHDATA1 fec0214d…               to   (20 bytes, little-endian)
PUSHDATA1 bec1ddfc…               from
PUSH4 PACK                        the 4 arguments as an array, last argument pushed first
PUSH15                            CallFlags.All
PUSHDATA1 'transfer'              method
PUSHDATA1 cf76e28b…               GasToken hash (little-endian)
SYSCALL System.Contract.Call
```

`n3-trace disasm` prints this listing, and `n3-trace tx` decodes it against the contract's ABI, so the
arguments come out as `from`, `to` and `amount = 26.51670361 GAS`. Scripts that loop or compute their
arguments are marked `partly computed` rather than guessed.

## 7. Fees: where the GAS went

```
sysfee 215925  = gas the script may burn; it used 215925. Any unused part is burned too, it is NOT refunded
netfee 37824   = size 252 B x feePerByte 20 = 5040, plus 32784 for running the signature check
```

`feePerByte` at that block comes from `invokefunctionhistoric(14832242, "0xcc5e4edd9f5f8dba8bb65734541df7a1c081c67b",
"getFeePerByte", [])`. One NEO transfer in our fixtures set a system fee of 0.055 GAS and used 0.00215925, so
0.0528 GAS was burned for nothing. `n3-trace tx` warns when this happens.

## 8. Before you sign: the same, on the latest state

For a transaction that is not on chain yet, use `invokescript(script, signers, true)` instead. It is the same
call on the current state, and it returns the same diagnostics. Add:

- `calculatenetworkfee(<base64 of the signed tx>)`, the network fee the node will ask for;
- `getblockcount`, to check the transaction's `validUntilBlock` has not passed;
- `getstorage(contract, key)` for the before-values, since there is no older root to read.

A FAULT comes with `exception` and the tree of contracts entered up to that point. The last contract on the
deepest path is where it stopped. If the entry script ASSERTs on a call's result (`… SYSCALL; ASSERT`), the
exception says only `ASSERT failed` at an offset in the entry script. `n3-trace preview` maps that offset back to
the call: "the entry script asserts that transfer #1 returned true, and it returned false".

## 9. Limits of the technique

- `invokescripthistoric` is a NeoGo extension. A C# node with RpcServer has `invokescript` with diagnostics, but
  no historic variant.
- History reaches back as far as the node keeps state (`MaxTraceableBlocks`, 2,102,400 blocks on mainnet).
- The replay is the transaction alone. Witness verification is not part of it, so the gas compared is
  application gas only, as in the log.
