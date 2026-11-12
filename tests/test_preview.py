"""preview on recorded test invocations (mainnet, November 2026)."""

import unittest

from helpers import ASSERT_SCRIPT, GAS_TRANSFER, PAIR, SENDER, fixture_path, replay

from n3trace.chain import ContractBook
from n3trace.codec import address_to_le
from n3trace.preview import PreviewError, build_call, parse_arg, parse_signer, preview, resolve_contract
from n3trace.tx import decode_input, parse_transaction

GAS = "0xd2a4cff31913016155e38e474a2c06d08be276cf"


class Signers(unittest.TestCase):
    def test_default_scope(self):
        s = parse_signer(SENDER)
        self.assertEqual(s["scopes"], ["CalledByEntry"])
        self.assertTrue(s["account"].startswith("0x"))

    def test_custom_contracts_needs_allow(self):
        with self.assertRaises(PreviewError):
            parse_signer(SENDER + ":CustomContracts")
        s = parse_signer(SENDER + ":CalledByEntry,CustomContracts", [GAS])
        self.assertEqual(s["allowedcontracts"], [GAS])

    def test_refused_scopes(self):
        for bad in ("Everything", "WitnessRules", "CustomGroups"):
            with self.assertRaises(PreviewError):
                parse_signer(f"{SENDER}:{bad}")

    def test_bad_account(self):
        with self.assertRaises(PreviewError):
            parse_signer("Nnope")


class Args(unittest.TestCase):
    def test_types(self):
        self.assertEqual(parse_arg(SENDER, "Hash160"), address_to_le(SENDER))
        self.assertEqual(parse_arg("1.5", "Integer", 8), 150000000)
        self.assertEqual(parse_arg("1_000", "Integer"), 1000)
        self.assertEqual(parse_arg("0x10", "Integer"), 16)
        self.assertIs(parse_arg("true", "Boolean"), True)
        self.assertEqual(parse_arg("hi", "String"), b"hi")
        self.assertIsNone(parse_arg("null", "Any"))
        self.assertEqual(parse_arg(f'["{SENDER}", 5, "0xff"]', "Array"), [address_to_le(SENDER), 5, b"\xff"])

    def test_errors(self):
        for text, typ in (("1.5", "Integer"), ("maybe", "Boolean"), ("0x12", "Hash256"), ("[1,", "Array"),
                          ("x", "InteropInterface")):
            with self.assertRaises(PreviewError, msg=f"{text} {typ}"):
                parse_arg(text, typ)
        with self.assertRaises(PreviewError):
            parse_arg('{"a": 1}', "Array")


class CallBuilding(unittest.TestCase):
    def setUp(self):
        self.book = ContractBook(replay("preview-call.json"))

    def test_alias(self):
        self.assertEqual(resolve_contract("gas", self.book), GAS)
        self.assertEqual(resolve_contract("GasToken", self.book), GAS)

    def test_arg_count_checked(self):
        with self.assertRaises(PreviewError) as cm:
            build_call(self.book, "gas", "transfer", [SENDER, PAIR, "1"])
        self.assertIn("takes 4", str(cm.exception))
        with self.assertRaises(PreviewError):
            build_call(self.book, "gas", "mint", [])


class Runs(unittest.TestCase):
    def test_call_halts_and_shows_your_balance(self):
        rpc = replay("preview-call.json")
        book = ContractBook(rpc)
        script = build_call(book, "gas", "transfer", [SENDER, PAIR, "0.5", "null"])
        m = preview(rpc, script, [parse_signer(SENDER)], book=book)
        self.assertEqual(m["verdict"], "HALT")
        self.assertEqual(m["errors"], [])
        self.assertEqual([(d["symbol"], d["delta"]) for d in m["signer_deltas"]], [("GAS", -50000000)])
        self.assertEqual(m["calls"][0]["args"][2]["text"], "0.5 GAS (50000000)")

    def test_assert_is_explained(self):
        rpc = replay("preview-assert.json")
        m = preview(rpc, bytes.fromhex(ASSERT_SCRIPT), [parse_signer(SENDER)])
        self.assertEqual(m["verdict"], "FAULT")
        self.assertEqual(m["fault_path"], ["GasToken.transfer"])
        self.assertIn("returned false", m["fault_hint"])
        self.assertIn("balance too low", m["fault_hint"])

    def test_raw_transaction_checks(self):
        with open(fixture_path("tx-gas-transfer.raw"), encoding="utf-8") as fh:
            tx = parse_transaction(decode_input(fh.read()))
        m = preview(replay("preview-raw.json"), tx.script, tx.signers, tx=tx)
        self.assertEqual(m["hash"], GAS_TRANSFER)
        self.assertEqual(m["result"]["state"], "HALT")
        self.assertEqual(m["verdict"], "REJECT")  # it was already mined: validUntilBlock has passed
        self.assertTrue(any("validUntilBlock" in e for e in m["errors"]))
        self.assertEqual(m["fees"]["netfee_needed"], m["fees"]["netfee"])  # priced by calculatenetworkfee


if __name__ == "__main__":
    unittest.main()
