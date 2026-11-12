"""The command line on recorded fixtures: text output, --json, and exit codes (0 ok, 2 error, 3 FAULT/REJECT)."""

import json
import unittest

from helpers import ASSERT_SCRIPT, DEX, GLOBAL, OVERPAID, PAIR, SENDER, SWAP, fixture_path, run_cli


def fx(name):
    return ["--replay", fixture_path(name)]


class TxCommand(unittest.TestCase):
    def test_text(self):
        code, out, _ = run_cli("tx", SWAP, *fx("tx-swap.json"))
        self.assertEqual(code, 0)
        for part in ("FlamingoSwapRouter.swapTokenInForTokenOut", "Token movements (2)", "same as on chain",
                     "Call tree (19 contract calls, depth 4)", "Storage writes (7, 1 rewrote", "1793.30380795 FTB"):
            self.assertIn(part, out)

    def test_json(self):
        code, out, _ = run_cli("tx", SWAP, "--json", *fx("tx-swap.json"))
        self.assertEqual(code, 0)
        m = json.loads(out)
        self.assertEqual((m["tool"].split()[0], m["network"], m["kind"]), ("n3-trace", "mainnet", "transaction"))
        self.assertTrue(m["replay"]["matches"])

    def test_fault_at_latest_exits_3(self):
        code, out, _ = run_cli("tx", SWAP, "--at", "latest", *fx("tx-swap-latest.json"))
        self.assertEqual(code, 3)
        self.assertIn("FAULT", out)
        self.assertIn("FlamingoSwapRouter.swapTokenInForTokenOut", out)

    def test_warnings_printed(self):
        self.assertIn("!!", run_cli("tx", GLOBAL, *fx("tx-global.json"))[1])
        self.assertIn("25x", run_cli("tx", OVERPAID, *fx("tx-overpaid.json"))[1])
        self.assertIn("+47,822 datoshi", run_cli("tx", DEX, *fx("tx-dex.json"))[1])

    def test_limits(self):
        _, out, _ = run_cli("tx", DEX, "--tree-lines", "5", "--storage-rows", "3", *fx("tx-dex.json"))
        self.assertIn("more", out)

    def test_unknown_request_is_an_error(self):
        code, _, err = run_cli("tx", "0x" + "ab" * 32, *fx("tx-swap.json"))
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("error:"))


class PreviewCommand(unittest.TestCase):
    def test_call(self):
        code, out, _ = run_cli("preview", "--call", "gas", "transfer", SENDER, PAIR, "0.5", "null",
                               "--signer", SENDER, *fx("preview-call.json"))
        self.assertEqual(code, 0)
        self.assertIn("-0.5 GAS", out)

    def test_assert_script(self):
        code, out, _ = run_cli("preview", "--script", ASSERT_SCRIPT, "--signer", SENDER, *fx("preview-assert.json"))
        self.assertEqual(code, 3)
        self.assertIn("returned false", out)

    def test_raw_file_reject(self):
        code, out, _ = run_cli("preview", "@" + fixture_path("tx-gas-transfer.raw"), *fx("preview-raw.json"))
        self.assertEqual(code, 3)
        self.assertIn("validUntilBlock", out)

    def test_input_errors(self):
        self.assertEqual(run_cli("preview", *fx("preview-call.json"))[0], 2)
        code, _, err = run_cli("preview", "--call", "gas", *fx("preview-call.json"))
        self.assertEqual(code, 2)
        self.assertIn("CONTRACT METHOD", err)
        code, _, err = run_cli("preview", "@" + fixture_path("tx-gas-transfer.raw"), "--signer", SENDER,
                               *fx("preview-raw.json"))
        self.assertEqual(code, 2)
        self.assertIn("drop --signer", err)


class OtherCommands(unittest.TestCase):
    def test_block(self):
        code, out, _ = run_cli("block", "14831181", *fx("block.json"))
        self.assertEqual(code, 0)
        self.assertIn(SWAP, out)

    def test_disasm_tx(self):
        code, out, _ = run_cli("disasm", SWAP, *fx("disasm-tx.json"))
        self.assertEqual(code, 0)
        self.assertIn("System.Contract.Call", out)
        self.assertIn("swapTokenInForTokenOut", out)

    def test_disasm_offline_script(self):
        code, out, _ = run_cli("disasm", ASSERT_SCRIPT, "--json")
        self.assertEqual(code, 0)
        m = json.loads(out)
        self.assertEqual(m["calls"][0]["method"], "transfer")
        self.assertEqual(m["calls"][0]["result"], "asserted")
        self.assertTrue(m["listing"][-1].split()[1] == "ASSERT")

    def test_bad_script(self):
        self.assertEqual(run_cli("disasm", "0x0c05")[0], 2)


if __name__ == "__main__":
    unittest.main()
