# n3-trace

Read any Neo N3 transaction like a debugger, and preview one before you sign it, from public RPC only.

```
pip install git+https://github.com/apeirs/n3-trace
n3-trace tx 0x45221017442f49751845059878b0ac3f08ba36c918510c181eae50fed14dd6ea
n3-trace preview --call gas transfer NXV… NYm… 0.5 null --signer NXV…
```

For one transaction, `n3-trace tx` gives you:

- the entry script's calls decoded against each contract's ABI;
- the events and token movements, with net balance changes;
- the fee split;
- a **replay on the exact state the transaction ran on**, which yields the full contract call tree and every
  storage write with its value before and after.

`n3-trace preview` does the same for a transaction that is not on chain yet: raw bytes from a wallet or dApp, an
entry script, or a single call typed on the command line. It says whether the transaction would succeed, be
rejected, or FAULT, and why.

No keys, nothing signed or sent, no indexer, no dependencies beyond the Python standard library (3.10+).

## Why

When a Neo N3 transaction surprises you, the tools stop at "what was emitted":

- Explorers show notifications, the result stack and the exception. They do not show which contracts called which,
  what storage changed, or what the entry script's arguments mean.
- [neo-express#708](https://github.com/neo-project/neo-express/issues/708) (open since June 2026): "Neo has had no
  working source-level debugger since neo-debugger went dormant". Its last release was v3.8.7 in May 2023.
- `neotrace` writes a binary trace that only that debugger can open, and it broke on mainnet four ways in 2025
  ([#484](https://github.com/neo-project/neo-express/issues/484)–[#487](https://github.com/neo-project/neo-express/issues/487)).
- Local-chain tools replay only their own chain. Nanvil's `--print-traces` is "reserved for future extended trace
  output".
- Before signing, wallets and dApps have `invokescript` and a gas number. Nothing turns that into "this moves
  0.5 GAS out of your account, through these contracts, and writes these keys"
  ([neon-js#757](https://github.com/CityOfZion/neon-js/issues/757),
  [neon-js#681](https://github.com/CityOfZion/neon-js/issues/681)).

NeoGo nodes, including the public COZ ones, already compute all of this: `invokescripthistoric` with diagnostics
returns the call tree and the storage changes. `n3-trace` asks for it on the right state, fetches the values from
before, decodes everything against the contracts' manifests, and checks the replay against the real execution.

## Output (real, mainnet, 2026-11-12)

### `tx`: a Flamingo swap, replayed

```
$ n3-trace tx 0x45221017442f49751845059878b0ac3f08ba36c918510c181eae50fed14dd6ea
Transaction 0x45221017442f49751845059878b0ac3f08ba36c918510c181eae50fed14dd6ea
  HALT  block 14,831,181 (tx 1 of 1)  2026-11-12 09:56:49Z
  sender NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq   size 364 B   valid until block 14,836,938
  fees 0.02507052 GAS = system 0.02466988 (used 0.02466988) + network 0.00040064 (size 364 B x 20 = 0.0000728, verification 0.00032784)

Signers (1)
  1. NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq  CustomContracts  single signature
        CustomContracts: FlamingoSwapRouter (0x41ee590749816ef96a2adde02c375b8f64cb26af), GasToken (0xd2a4cff31913016155e38e474a2c06d08be276cf), FreeTheBird (0xf85f93d672cbf79f7749fb50a2d46a48421d7e71)

Calls in the entry script (145 bytes, decoded exactly)
  1. FlamingoSwapRouter.swapTokenInForTokenOut  0x41ee5907…26af  result kept
       sender        Hash160   NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq
       amountIn      Integer   130000000
       amountOutMin  Integer   175743773179
       paths         Array     [GasToken (0xd2a4cff31913016155e38e474a2c06d08be276cf), FreeTheBird (0xf85f93d672cbf79f77…
       deadLine      Integer   1794477586950

Result: HALT, gas 0.02466988
  stack: true

Events (4)
  GasToken.Transfer(from=NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq, to=FlamingoSwapPair (0x683dc96420041cb70adbda64eff4fa8f3e6173b9), amount=1.3 GAS (130000000))
  FreeTheBird.Transfer(from=FlamingoSwapPair (0x683dc96420041cb70adbda64eff4fa8f3e6173b9), to=NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq, amount=1793.30380795 FTB (179330380795))
  FlamingoSwapPair.Synced(balance0=3032462667, balance1=4015891793220)
  FlamingoSwapPair.Swapped(caller=Nbt5upFxprqL331KWwyq1yBAR7ThywPFUN, amount0In=130000000, amount1In=0, amount0Out=0, amount1Out=179330380795, to=NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq)

Token movements (2)
                     1.3 GAS  NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq -> NcpYQyfknkSrqW1ZW6ku4LRu9h5Tt3dqGv (FlamingoSwapPair)
           1793.30380795 FTB  NcpYQyfknkSrqW1ZW6ku4LRu9h5Tt3dqGv (FlamingoSwapPair) -> NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq

Balance changes
  NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq                     -1.3 GAS   +1793.30380795 FTB
  NcpYQyfknkSrqW1ZW6ku4LRu9h5Tt3dqGv (FlamingoSwapPair)  +1.3 GAS   -1793.30380795 FTB

Replay on the state after block 14,831,180: HALT, gas 0.02466988, same as on chain

Call tree (19 contract calls, depth 4)
  entry script
  └─ FlamingoSwapRouter.swapTokenInForTokenOut 0x41ee5907…26af
     ├─ FlamingoSwapFactory 0xa809b0f5…970b
     ├─ FlamingoSwapPair 0x683dc964…73b9
     │  └─ StdLib 0xacce6fd8…efc0
     ├─ FlamingoSwapFactory 0xa809b0f5…970b
     ├─ GasToken 0xd2a4cff3…76cf
     │  └─ FlamingoSwapPair 0x683dc964…73b9
     ├─ FlamingoSwapFactory 0xa809b0f5…970b
     └─ FlamingoSwapPair 0x683dc964…73b9
        ├─ FlamingoSwapPairWhiteList 0xa66e8ba2…ba6a
        ├─ StdLib 0xacce6fd8…efc0
        ├─ FreeTheBird 0xf85f93d6…7e71
        │  └─ ContractManagement 0xfffdc937…a3fd
        ├─ GasToken 0xd2a4cff3…76cf
        ├─ FreeTheBird 0xf85f93d6…7e71
        └─ StdLib 0xacce6fd8…efc0  x4

Storage writes (7, 1 rewrote the value already there)
  ~ FlamingoSwapPair  'Cumulative'
      [7232505839096641852947276191, 448510165156247512… -> [7232506706337186590331145528, 448510165197758486…  ([0] +867240544737383869337, [1] +415109743409211, [2] +6)
  ~ FlamingoSwapPair  'ReservePair'
      [2902462667, 4195222174015] -> [3032462667, 4015891793220]  ([0] +130000000, [1] -179330380795)
  ~ FlamingoSwapPair  'enteredentered'
      unchanged: 0x (empty)
  ~ FreeTheBird       0x01 + NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq
      0xac7be86518 (int 104788949932) -> 0xa7efd42642 (int 284119330727)  (+179330380795)
  ~ FreeTheBird       0x01 + NcpYQyfknkSrqW1ZW6ku4LRu9h5Tt3dqGv
      0x3f39bac6d003 (int 4195222174015) -> 0x44c5cd05a703 (int 4015891793220)  (-179330380795)
  ~ GasToken          balance[NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq]
      [503172739] -> [373172739]  (-130000000)
  ~ GasToken          balance[NcpYQyfknkSrqW1ZW6ku4LRu9h5Tt3dqGv]
      [2902462667] -> [3032462667]  (+130000000)
```

Things to notice:

- `same as on chain` means the replay on the state after block H-1 produced the same VM state, the same gas to
  the datoshi and the same notifications as the real execution. Only then are the tree and the storage writes
  shown as the transaction's own.
- Storage keys are labelled: native prefixes by name (`balance[N…]`), text keys as text, `prefix + account`
  layouts as addresses.
- Struct values get a delta per field. `unchanged:` marks a write of the value that was already there, which
  costs storage gas for nothing.
- The fee line splits the network fee into the size part (`feePerByte` read at block H-1) and the
  verification part.

### `tx --at latest`: would it still work today?

The same swap, replayed on the current state. Its deadline argument has passed, so it FAULTs, and the tool says
where:

```
$ n3-trace tx 0x4522…d6ea --at latest      # exit code 3
…
Replay on the latest state: FAULT, gas 0.00097292, DIFFERENT from the chain (state, gas, notifications)
  exception: at instruction 268 (ASSERT): ASSERT failed
  last contract entered: FlamingoSwapRouter.swapTokenInForTokenOut
  likely cause: a contract (or the entry script) ASSERTed on a false value: a check in the contract failed

Replay events (1)
  FlamingoSwapRouter.Fault(message='Exceeded the deadline', paras=null)

Call tree (1 contract call, depth 1)
  entry script
  └─ FlamingoSwapRouter.swapTokenInForTokenOut 0x41ee5907…26af

Storage writes (0), all discarded because the run FAULTed
  (none)
```

`--at HEIGHT` replays on any state within the node's history, for example to find the block where a call
started failing.

### `preview`: before you sign

A call typed on the command line. Its arguments are checked against the ABI, and NEP-17 amounts are in whole
tokens:

```
$ n3-trace preview --call gas transfer NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq NcpYQyfknkSrqW1ZW6ku4LRu9h5Tt3dqGv 0.5 null \
    --signer NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq
Preview on the state after block 14,832,692: would succeed
  gas needed 0.00216088

What the signers' balances do
  NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq  -0.5 GAS

…
```

An entry script that would FAULT (a transfer of more GAS than the account holds, with the result ASSERTed):

```
$ n3-trace preview --script 0b0300…5239 --signer NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq      # exit code 3
Preview on the state after block 14,832,692: would FAIL
  gas needed 0.00215926
  X the script FAULTs: at instruction 94 (ASSERT): ASSERT failed
  last contract entered: GasToken.transfer
  likely cause: the entry script asserts that transfer #1 returned true, and it returned false (for a NEP-17 transfer: balance too low, or the sender did not sign)

What the signers' balances do
  no token leaves or reaches a signer (fees aside)

…
```

Raw transaction bytes, signed or not, as a wallet would send them (hex or base64, `-` for stdin, `@file`). Here
the transaction had already been mined, so the network would refuse it:

```
$ n3-trace preview @tx.raw      # exit code 3
Preview on the state after block 14,832,691: would be REJECTED
  transaction 0x1e8700a6f80de6b59849617709d79f0ea21a3e173a8fec3e39f5ff7952b1f29d  size 252 B  valid until block 14,832,441
  gas needed 0.00215925   system fee set 0.00215925   network fee set 0.00037824 (node asks 0.00037824)
  X validUntilBlock 14832441 has passed (chain is at 14832691): nodes will reject it

What the signers' balances do
  NdJbpKzsxT3UtHDDQnfgMWemsvuh7Tj64J  -26.51670361 GAS

…
```

`preview` checks:

- that the script HALTs;
- that the system fee covers the gas;
- that `validUntilBlock` has not passed;
- the network fee against `calculatenetworkfee` (signed transactions);
- that the signer and witness counts match;
- the signer scopes.

### Signer scopes, gas surprises

```
$ n3-trace tx 0x27b9ce06871a0a150824f5a9ed17895f1f7a4dcc5358ab63dffa81c7e07257ea
Transaction 0x27b9ce06871a0a150824f5a9ed17895f1f7a4dcc5358ab63dffa81c7e07257ea
  HALT  block 14,825,476 (tx 1 of 1)  2026-11-12 05:00:18Z
  sender NUWq8bh6HcctnSW4ag8JJKE7438AcstUMT   size 294 B   valid until block 14,831,233
  fees 0.21 GAS = system 0.2 (used 0.18836817) + network 0.01 (size 294 B x 20 = 0.0000588, verification 0.0099412)
  ! the replay differs from the chain in gas (+47,822 datoshi): contracts that read the block time, randomness or the transaction itself can take another path

Signers (1)
  1. NUWq8bh6HcctnSW4ag8JJKE7438AcstUMT  Global  single signature
     !! Global scope: every contract this transaction reaches, directly or through other contracts, can act with this signature (move its tokens, change its votes). Wallets should never need it.
```

A replay that does not match the chain is never passed off as the transaction's own trace:

```
$ n3-trace tx 0x5dee5c13fb8a79424b1424fa7d64e24794ec3952b1d895ff94216ab7ec728253
Transaction 0x5dee5c13fb8a79424b1424fa7d64e24794ec3952b1d895ff94216ab7ec728253
  HALT  block 14,829,212 (tx 1 of 1)  2026-11-12 08:14:24Z
  sender NUWq8bh6HcctnSW4ag8JJKE7438AcstUMT   size 345 B   valid until block 14,829,391
  fees 0.16417931 GAS = system 0.16378247 (used 0.16168264) + network 0.00039684 (size 345 B x 20 = 0.000069, verification 0.00032784)
  ! the replay differs from the chain in gas (+47,822 datoshi): contracts that read the block time, randomness or the transaction itself can take another path
…
Replay on the state after block 14,829,211: HALT, gas 0.16216086, DIFFERENT from the chain (gas)
```

A system fee far above the gas used gets a warning, because N3 burns the unused part:

```
  ! system fee 0.055 GAS was 25x the gas used (0.00215925 GAS); the unused 0.05284075 GAS was burned, N3 does not refund it
```

### `disasm` and `block`

```
$ n3-trace disasm 0x45221017442f49751845059878b0ac3f08ba36c918510c181eae50fed14dd6ea
; entry script of 0x45221017442f49751845059878b0ac3f08ba36c918510c181eae50fed14dd6ea, 145 bytes
0000  PUSHINT64    06fa32cfa1010000      ; 1794477586950
0009  PUSHDATA1                          ; 0xf85f93d672cbf79f7749fb50a2d46a48421d7e71 / NWG4d1PM1KhhWkZHyZK7bG3HpYz3Vp7XTX
0031  PUSHDATA1                          ; 0xd2a4cff31913016155e38e474a2c06d08be276cf / NepwUjd9GhqgNkrfXaxj9mmsFhFzGoFuWM
0053  PUSH2
0054  PACK
0055  PUSHINT64    fb2925eb28000000      ; 175743773179
0064  PUSHINT32    80a4bf07              ; 130000000
0069  PUSHDATA1                          ; 0x41def0f3e64f3b3595051b8d9acedaf0584d4506 / NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq
0091  PUSH5
0092  PACK
0093  PUSH15
0094  PUSHDATA1                          ; 'swapTokenInForTokenOut'
0118  PUSHDATA1                          ; 0x41ee590749816ef96a2adde02c375b8f64cb26af / Nbt5upFxprqL331KWwyq1yBAR7ThywPFUN
0140  SYSCALL      627d5b52              ; System.Contract.Call
; 1 contract call(s): 0x41ee590749816ef96a2adde02c375b8f64cb26af.swapTokenInForTokenOut
```

`disasm` also takes a raw script (offline), a contract hash or address (its NEF), or a native name such as
`GasToken`. `block 14831181` prints one line per transaction with its calls, state and gas.

## How it works

| Step | RPC |
|---|---|
| transaction, its block, its execution log | `getrawtransaction`, `getblock`, `getapplicationlog` |
| replay on the state the tx started from | `invokescripthistoric(H-1, script, signers, diagnostics=true)` |
| call tree and storage writes | `diagnostics.invokedcontracts`, `diagnostics.storagechanges` of that replay |
| values before each write | `getstateroot(H-1)` + `getstate(root, contract, key)` |
| names, ABIs, token symbols and decimals | `getnativecontracts`, `getcontractstate`, `symbol`/`decimals` via `invokefunction` |
| fee split | `Policy.getFeePerByte` at H-1 (`invokefunctionhistoric`) |
| preview | `invokescript(script, signers, diagnostics=true)`, `calculatenetworkfee`, `getstorage` |

The entry script is decoded by a small symbolic NeoVM pass. It covers the `PUSH… PACK PUSH15 PUSHDATA SYSCALL
System.Contract.Call` pattern that wallets and SDKs emit, results passed between calls, and ASSERTs on results.
Anything it cannot follow exactly (loops, computed arguments) is marked `partly computed` rather than guessed.
Requests are batched, so a full trace takes 8–10 HTTP round trips, 2–4 seconds on the public mainnet nodes.

[TUTORIAL.md](TUTORIAL.md) does the same by hand with curl on one GAS transfer, step by step, if you want the
technique without the tool.

## Options

| | |
|---|---|
| `--network mainnet\|testnet` | the public COZ NeoGo nodes (default mainnet) |
| `--rpc URL` | your own node; repeat for failover. `tx` needs a NeoGo node (`invokescripthistoric`) |
| `--at HEIGHT\|latest` | `tx`: replay on another state |
| `--signer ACCOUNT[:SCOPES]`, `--allow C` | `preview`: signers for `--script`/`--call` (default `CalledByEntry`) |
| `--json` | everything above as JSON (values typed: hashes big-endian, amounts as integers) |
| `--no-storage` | skip storage writes |
| `--tree-lines N`, `--storage-rows N` | how much to print (0 = all) |

Exit codes: `0` HALT, `3` FAULT or would be rejected, `2` bad input or RPC error. A CI step can run
`n3-trace preview` on the transaction a deploy script is about to send and stop on 3.

## Limits

- The replay runs the transaction alone on the state after block H-1. Earlier transactions in the same block are
  not applied; the output says so when there are any, and a gas or event mismatch is reported, never hidden.
  Some Flamingo order-book transactions replay with exactly 47,822 datoshi more gas than they used on chain, with
  the same events. The cause is not visible over RPC, so the output marks those replays DIFFERENT.
- Values before a write come from the node's state roots. If the node cannot serve them, `before` is `?`.
- History reaches back as far as the node keeps state (about 2.1M blocks on the public mainnet nodes).
- Storage values are decoded without the contract's source: integers, structs and addresses come out readable,
  other layouts are shown as hex.
- `preview` refuses `WitnessRules` and `CustomGroups` signers typed on the command line. Pass the raw
  transaction instead, which carries them.

## Development

```
PYTHONPATH=src:tests python -m unittest discover -s tests -t . -v              # offline, recorded mainnet fixtures
N3_LIVE=1 PYTHONPATH=src:tests python -m unittest tests.test_live -v           # live mainnet and testnet
```

The fixtures in `tests/fixtures/` are recorded RPC answers for eight mainnet transactions (a GAS transfer, a
swap, two order-book trades, a two-signer NEO transfer, an overpaid fee, a bridge withdrawal) and four previews.
The CLI can record new ones with the hidden `--record FILE` option and play them back with `--replay FILE`.

## License

Apache-2.0
