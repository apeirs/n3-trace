"""Shared test helpers: recorded mainnet fixtures and the transactions they hold."""

from __future__ import annotations

import io
import os
from contextlib import redirect_stderr, redirect_stdout

from n3trace.cli import main
from n3trace.rpc import ReplayRpc

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")

# Mainnet transactions the fixtures were recorded from (November 2026).
GAS_TRANSFER = "0x1e8700a6f80de6b59849617709d79f0ea21a3e173a8fec3e39f5ff7952b1f29d"  # plain GAS transfer
SWAP = "0x45221017442f49751845059878b0ac3f08ba36c918510c181eae50fed14dd6ea"  # Flamingo GAS -> FTB swap
DEX = "0x5dee5c13fb8a79424b1424fa7d64e24794ec3952b1d895ff94216ab7ec728253"  # Flamingo order-book convert
TWO_SIGNERS = "0xa69312e794980a8549c1394a1e1dd618120d9607d8c2ef21538fa1359911e4d7"  # NEO transfer, 2 signers
GLOBAL = "0x27b9ce06871a0a150824f5a9ed17895f1f7a4dcc5358ab63dffa81c7e07257ea"  # signer with Global scope
OVERPAID = "0xc36b928eda0ba7b5be25f283f8515bd07c0289f539a234e114885a91f2cceafe"  # sysfee 25x the gas used
BRIDGE = "0x8f1125f200b046562f3473fc056567c8d66631ecce2d077b4603ee037db9971e"  # bridge withdrawToken with a map arg

SENDER = "NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq"  # sender of SWAP
PAIR = "NcpYQyfknkSrqW1ZW6ku4LRu9h5Tt3dqGv"  # FlamingoSwapPair GAS/FTB

# GasToken.transfer(SENDER, NdJbpK…, 10000 GAS, null) followed by ASSERT: more GAS than SENDER has.
ASSERT_SCRIPT = (
    "0b030010a5d4e80000000c14bec1ddfc831397956eb41504335214ab4df923b00c1406454d58f0dace9a8d1b0595353b4fe6f3f0de4114"
    "c01f0c087472616e736665720c14cf76e28bd0062c4a478ee35561011319f3cfa4d241627d5b5239"
)


def replay(name: str) -> ReplayRpc:
    return ReplayRpc.load(os.path.join(FIX, name))


def fixture_path(name: str) -> str:
    return os.path.join(FIX, name)


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()
