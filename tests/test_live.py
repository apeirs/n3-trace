"""Live checks against public RPC nodes. Skipped unless N3_LIVE=1 (they need the network and a NeoGo node)."""

import os
import unittest

from helpers import SWAP

from n3trace.chain import ContractBook
from n3trace.preview import build_call, parse_signer, preview
from n3trace.rpc import Rpc
from n3trace.trace import trace_tx

LIVE = os.environ.get("N3_LIVE") == "1"


@unittest.skipUnless(LIVE, "set N3_LIVE=1 to run live checks")
class Mainnet(unittest.TestCase):
    def setUp(self):
        self.rpc = Rpc.for_network("mainnet", [], timeout=30)

    def test_swap_replays_exactly(self):
        m = trace_tx(self.rpc, SWAP)
        self.assertEqual(m["onchain"]["state"], "HALT")
        self.assertTrue(m["replay"]["matches"], m["warnings"])
        self.assertEqual(m["replay"]["contract_calls"], 19)

    def test_preview_balance_read(self):
        book = ContractBook(self.rpc)
        script = build_call(book, "gas", "balanceOf", ["NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq"])
        m = preview(self.rpc, script, [], book=book)
        self.assertEqual(m["verdict"], "HALT")
        self.assertEqual(m["storage"], [])


@unittest.skipUnless(LIVE, "set N3_LIVE=1 to run live checks")
class Testnet(unittest.TestCase):
    def test_latest_block_traces(self):
        rpc = Rpc.for_network("testnet", [], timeout=30)
        height = int(rpc.call("getblockcount", [])) - 1
        # testnet is quiet: find the newest block with transactions (an empty block has a zero merkle root)
        empty = "0x" + "00" * 32
        h = None
        for start in range(height, height - 30000, -500):
            headers = rpc.batch([("getblockheader", [i, True]) for i in range(start, start - 500, -1)])
            h = next((x["index"] for x in headers if isinstance(x, dict) and x.get("merkleroot") != empty), None)
            if h is not None:
                break
        if h is None:
            self.skipTest("no testnet transaction in the last 30000 blocks")
        txid = rpc.call("getblock", [h, True])["tx"][0]["hash"]
        m = trace_tx(rpc, txid)
        self.assertIn(m["replay"]["state"], ("HALT", "FAULT"))
        self.assertEqual(m["block"]["index"], h)


if __name__ == "__main__":
    unittest.main()
