"""trace_tx on recorded mainnet transactions: every fact here was checked against the chain when recorded."""

import unittest

from helpers import BRIDGE, DEX, GAS_TRANSFER, GLOBAL, OVERPAID, PAIR, SENDER, SWAP, TWO_SIGNERS, replay

from n3trace.trace import TraceError, block_summary, gas, trace_tx

FTB = "0xf85f93d672cbf79f7749fb50a2d46a48421d7e71"
GAS = "0xd2a4cff31913016155e38e474a2c06d08be276cf"


class GasTransfer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = trace_tx(replay("tx-gas-transfer.json"), GAS_TRANSFER)

    def test_block_and_position(self):
        self.assertEqual(self.m["block"]["index"], 14832243)
        self.assertEqual((self.m["block"]["position"], self.m["block"]["count"]), (0, 1))

    def test_replay_matches(self):
        r = self.m["replay"]
        self.assertEqual(r["height"], 14832242)
        self.assertTrue(r["matches"])
        self.assertEqual(r["gasconsumed"], self.m["onchain"]["gasconsumed"])

    def test_fee_split(self):
        f = self.m["fees"]
        self.assertEqual(f["total"], f["sysfee"] + f["netfee"])
        self.assertEqual(f["size_fee"], 252 * 20)
        self.assertEqual(f["size_fee"] + f["verification_fee"], f["netfee"])
        self.assertEqual(f["unused_sysfee"], 0)

    def test_balances_net_to_zero(self):
        self.assertEqual(sum(d["delta"] for d in self.m["deltas"]), 0)
        self.assertEqual(len(self.m["transfers"]), 1)

    def test_storage_has_before_values(self):
        rows = self.m["replay"]["storage"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["key_text"].startswith("balance[") for r in rows))
        changed, added = rows
        self.assertEqual((changed["state"], added["state"]), ("Changed", "Added"))
        self.assertEqual(changed["delta"], -2651670361)  # read from the state root of block H-1
        self.assertIsNotNone(changed["before"])
        self.assertIsNone(added["before"])
        self.assertEqual(added["delta"], 2651670361)


class Swap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = trace_tx(replay("tx-swap.json"), SWAP)

    def test_entry_call(self):
        c = self.m["calls"][0]
        self.assertEqual((c["name"], c["method"]), ("FlamingoSwapRouter", "swapTokenInForTokenOut"))
        self.assertEqual(c["args"][0]["text"], SENDER)
        self.assertIn("FreeTheBird", c["args"][3]["text"])  # hashes inside the path array are named

    def test_replay_tree(self):
        r = self.m["replay"]
        self.assertTrue(r["matches"])
        self.assertEqual((r["contract_calls"], r["depth"]), (19, 4))
        self.assertEqual(r["tree"][0]["calls"][0]["name"], "FlamingoSwapRouter")
        self.assertEqual(r["tree"][0]["calls"][0]["method"], "swapTokenInForTokenOut")

    def test_token_moves(self):
        moves = {(t["symbol"], t["from"], t["to"]) for t in self.m["transfers"]}
        self.assertIn(("GAS", SENDER, PAIR), moves)
        self.assertIn(("FTB", PAIR, SENDER), moves)
        mine = {d["symbol"]: d["delta"] for d in self.m["deltas"] if d["account"] == SENDER}
        self.assertEqual(mine, {"GAS": -130000000, "FTB": 179330380795})

    def test_reserve_struct_delta(self):
        rows = {r["key_text"]: r for r in self.m["replay"]["storage"]}
        self.assertEqual(rows["'ReservePair'"]["delta"], {"0": 130000000, "1": -179330380795})

    def test_custom_contracts_note(self):
        notes = self.m["scope_notes"]
        self.assertEqual(notes[0]["level"], "info")
        self.assertIn("FreeTheBird", notes[0]["text"])

    def test_storage_sorted(self):
        keys = [(r["name"].lower(), r["contract_id"]) for r in self.m["replay"]["storage"]]
        self.assertEqual(keys, sorted(keys))


class SwapAtLatest(unittest.TestCase):
    def test_deadline_fault(self):
        m = trace_tx(replay("tx-swap-latest.json"), SWAP, at="latest")
        r = m["replay"]
        self.assertEqual(r["state"], "FAULT")
        self.assertIsNone(r["height"])
        self.assertFalse(r["matches"])
        self.assertEqual(r["fault_path"], ["FlamingoSwapRouter.swapTokenInForTokenOut"])
        self.assertIn("ASSERT", r["fault_hint"])
        self.assertEqual(r["transfers"], [])
        self.assertTrue(any("deadline" in str(n).lower() for n in r["notifications"]) or "deadline" in
                        (r["exception"] or "").lower())


class Divergent(unittest.TestCase):
    def test_gas_difference_is_reported(self):
        m = trace_tx(replay("tx-dex.json"), DEX)
        r = m["replay"]
        self.assertEqual(r["match"], {"state": True, "gas": False, "notifications": True})
        self.assertEqual(r["gasconsumed"] - m["onchain"]["gasconsumed"], 47822)
        self.assertTrue(any("+47,822 datoshi" in w for w in m["warnings"]))
        self.assertEqual(m["fees"]["unused_sysfee"], 209983)


class Scopes(unittest.TestCase):
    def test_global_is_high(self):
        m = trace_tx(replay("tx-global.json"), GLOBAL)
        self.assertEqual(m["scope_notes"][0]["level"], "high")
        self.assertTrue(m["scope_notes"][0]["text"].startswith("Global"))

    def test_two_signers(self):
        m = trace_tx(replay("tx-two-signers.json"), TWO_SIGNERS)
        self.assertEqual(len(m["signers"]), 2)
        self.assertEqual(m["block"]["count"], 2)
        self.assertTrue(m["replay"]["matches"])


class Fees(unittest.TestCase):
    def test_overpaid(self):
        m = trace_tx(replay("tx-overpaid.json"), OVERPAID)
        self.assertEqual(m["fees"]["unused_sysfee"], 5284075)
        self.assertTrue(any("25x" in w and "burned" in w for w in m["warnings"]))

    def test_gas_text(self):
        self.assertEqual(gas(215925), "0.00215925 GAS")
        self.assertEqual(gas(None), "?")


class Bridge(unittest.TestCase):
    def test_map_argument_and_moves(self):
        m = trace_tx(replay("tx-bridge.json"), BRIDGE)
        self.assertTrue(m["replay"]["matches"])
        self.assertEqual(m["calls"][0]["method"], "withdrawToken")
        self.assertEqual((m["replay"]["contract_calls"], m["replay"]["depth"]), (16, 3))
        self.assertEqual(len(m["transfers"]), 3)


class Errors(unittest.TestCase):
    def test_bad_hash(self):
        with self.assertRaises(TraceError):
            trace_tx(replay("tx-swap.json"), "0x1234")

    def test_block(self):
        b = block_summary(replay("block.json"), "14831181")
        self.assertEqual(b["index"], 14831181)
        self.assertEqual(b["transactions"][0]["hash"], SWAP)
        self.assertEqual(b["transactions"][0]["calls"], ["FlamingoSwapRouter.swapTokenInForTokenOut"])


if __name__ == "__main__":
    unittest.main()
