import unittest

from helpers import ASSERT_SCRIPT, BRIDGE, SWAP, replay

from n3trace.codec import b64d, hash_to_le
from n3trace.script import CallResult, decode_script
from n3trace.vm import BY_NAME, emit_contract_call, emit_push, syscall_id

GAS = "0xd2a4cff31913016155e38e474a2c06d08be276cf"
NEO = "0xef4073a0f2b305a38ec4050e4d3d28bc40ea63f5"


def entry_script(fixture: str, txid: str) -> bytes:
    return b64d(replay(fixture).call("getrawtransaction", [txid, True])["script"])


class RecordedScripts(unittest.TestCase):
    def test_swap(self):
        d = decode_script(entry_script("tx-swap.json", SWAP))
        self.assertTrue(d.exact)
        self.assertEqual(len(d.calls), 1)
        c = d.calls[0]
        self.assertEqual((c.contract, c.method, c.flags), ("0x41ee590749816ef96a2adde02c375b8f64cb26af",
                                                           "swapTokenInForTokenOut", 15))
        self.assertEqual(c.args[1], 130000000)
        self.assertEqual(len(c.args[3]), 2)  # the swap path: GAS -> FTB

    def test_bridge_map_argument(self):
        d = decode_script(entry_script("tx-bridge.json", BRIDGE))
        self.assertTrue(d.exact)
        self.assertEqual(d.calls[0].method, "withdrawToken")
        self.assertTrue(any(isinstance(a, dict) for a in d.calls[0].args))

    def test_asserted_result(self):
        d = decode_script(bytes.fromhex(ASSERT_SCRIPT))
        self.assertTrue(d.exact)
        self.assertEqual(d.calls[0].result_use, "asserted")
        self.assertEqual(d.calls[0].meta["assert_at"], 94)


class BuiltScripts(unittest.TestCase):
    def test_dropped_and_kept_results(self):
        script = emit_contract_call(hash_to_le(GAS), "balanceOf", [b"\x11" * 20]) + bytes([BY_NAME["DROP"]])
        script += emit_contract_call(hash_to_le(NEO), "symbol", [])
        d = decode_script(script)
        self.assertTrue(d.exact)
        self.assertEqual([c.method for c in d.calls], ["balanceOf", "symbol"])
        self.assertEqual(d.calls[0].result_use, "dropped")
        self.assertEqual(d.calls[1].result_use, "kept")

    def test_result_as_argument(self):
        # [ balanceOf(...) ] PACK as the args of a second call
        script = emit_contract_call(hash_to_le(GAS), "balanceOf", [b"\x11" * 20])
        script += emit_push(1) + bytes([BY_NAME["PACK"]]) + emit_push(15) + emit_push("totalSupply")
        script += emit_push(hash_to_le(NEO)) + bytes([BY_NAME["SYSCALL"]]) + syscall_id("System.Contract.Call")
        d = decode_script(script)
        self.assertEqual(len(d.calls), 2)
        self.assertIsInstance(d.calls[1].args[0], CallResult)
        self.assertEqual(d.calls[1].args[0].index, 0)

    def test_loop_is_not_exact(self):
        script = emit_contract_call(hash_to_le(GAS), "symbol", []) + bytes([BY_NAME["JMP"], 0xFE])
        d = decode_script(script)
        self.assertFalse(d.exact)
        self.assertTrue(d.notes)

    def test_other_syscalls_recorded(self):
        script = bytes([BY_NAME["SYSCALL"]]) + syscall_id("System.Runtime.GetTime") + bytes([BY_NAME["DROP"]])
        d = decode_script(script)
        self.assertIn("System.Runtime.GetTime", d.other_syscalls)


if __name__ == "__main__":
    unittest.main()
