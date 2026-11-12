import unittest

from n3trace.codec import hash_to_le
from n3trace.vm import (
    BY_NAME, SYSCALLS, DisassemblyError, disassemble, emit_contract_call, emit_push, format_listing, syscall_id,
)

GAS = "0xd2a4cff31913016155e38e474a2c06d08be276cf"


class SyscallTests(unittest.TestCase):
    def test_contract_call_id(self):
        # The id every wallet-built entry script ends with.
        self.assertEqual(syscall_id("System.Contract.Call").hex(), "627d5b52")
        self.assertEqual(SYSCALLS[bytes.fromhex("627d5b52")], "System.Contract.Call")

    def test_checksig_id(self):
        self.assertEqual(syscall_id("System.Crypto.CheckSig").hex(), "56e7b327")


class EmitTests(unittest.TestCase):
    def test_small_ints(self):
        self.assertEqual(emit_push(0), bytes([BY_NAME["PUSH0"]]))
        self.assertEqual(emit_push(16), bytes([BY_NAME["PUSH16"]]))
        self.assertEqual(emit_push(-1), bytes([BY_NAME["PUSHM1"]]))

    def test_int_widths(self):
        self.assertEqual(emit_push(17)[0], BY_NAME["PUSHINT8"])
        self.assertEqual(emit_push(128)[0], BY_NAME["PUSHINT16"])
        self.assertEqual(emit_push(10**12), bytes([BY_NAME["PUSHINT64"]]) + (10**12).to_bytes(8, "little"))
        with self.assertRaises(ValueError):
            emit_push(1 << 256)

    def test_bytes_and_null(self):
        self.assertEqual(emit_push(None), bytes([BY_NAME["PUSHNULL"]]))
        self.assertEqual(emit_push("ab"), bytes([BY_NAME["PUSHDATA1"], 2]) + b"ab")
        self.assertEqual(emit_push(b"x" * 300)[:3], bytes([BY_NAME["PUSHDATA2"]]) + (300).to_bytes(2, "little"))

    def test_contract_call_shape(self):
        script = emit_contract_call(hash_to_le(GAS), "symbol", [])
        names = [i.name for i in disassemble(script)]
        self.assertEqual(names, ["NEWARRAY0", "PUSH15", "PUSHDATA1", "PUSHDATA1", "SYSCALL"])


class ListingTests(unittest.TestCase):
    def test_listing_comments(self):
        listing = format_listing(emit_contract_call(hash_to_le(GAS), "symbol", []))
        self.assertIn("'symbol'", listing)
        self.assertIn("System.Contract.Call", listing)
        self.assertIn(GAS + " / NepwUjd9GhqgNkrfXaxj9mmsFhFzGoFuWM", listing)  # hash and address form of the GAS hash, so the reader can search it

    def test_truncated_operand(self):
        with self.assertRaises(DisassemblyError):
            disassemble(bytes([BY_NAME["PUSHDATA1"], 5, 1, 2]))

    def test_unknown_opcode(self):
        with self.assertRaises(DisassemblyError):
            disassemble(b"\xff")


if __name__ == "__main__":
    unittest.main()
