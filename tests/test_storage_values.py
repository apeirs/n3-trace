import unittest

from n3trace.codec import address_to_le, le_to_hash
from n3trace.storage import _delta, key_text, split_key
from n3trace.values import decode_storage_value, item_value, leading_int, render, storage_value_text, typed_json

ADDR = "NLV8G6yrNsroQwmzTzrA9GSNJcv1BRiZtq"


class KeyTests(unittest.TestCase):
    def test_split(self):
        cid, key = split_key((-6).to_bytes(4, "little", signed=True) + b"\x14abc")
        self.assertEqual((cid, key), (-6, b"\x14abc"))
        with self.assertRaises(ValueError):
            split_key(b"\x01\x02")

    def test_native_balance(self):
        self.assertEqual(key_text(b"\x14" + address_to_le(ADDR), "GasToken"), f"balance[{ADDR}]")
        self.assertEqual(key_text(b"\x0b", "GasToken"), "totalSupply")

    def test_native_candidate(self):
        pub = bytes([3]) + b"\x01" * 32
        self.assertEqual(key_text(b"\x21" + pub, "NeoToken"), f"candidate (registered, votes)[{pub.hex()}]")

    def test_text_and_prefix_account(self):
        self.assertEqual(key_text(b"ReservePair"), "'ReservePair'")
        self.assertEqual(key_text(b"\x01" + address_to_le(ADDR)), f"0x01 + {ADDR}")
        self.assertEqual(key_text(b"bal" + address_to_le(ADDR) + b"x"), f"'bal' + {ADDR} + 'x'")

    def test_control_byte_prefix_is_hex(self):
        self.assertEqual(key_text(b"\t" + address_to_le(ADDR)), f"0x09 + {ADDR}")

    def test_long_key_is_cut(self):
        self.assertTrue(key_text(b"\xff" * 60).endswith("(60 bytes)"))


class DeltaTests(unittest.TestCase):
    def test_integer(self):
        self.assertEqual(_delta(b"\x21\x01\x0a", b"\x21\x01\x0f", "Changed"), 5)

    def test_added_counts_from_zero(self):
        self.assertEqual(_delta(None, b"\x21\x01\x0a", "Added"), 10)

    def test_deleted_goes_to_zero(self):
        self.assertEqual(_delta(b"\x21\x01\x0a", None, "Deleted"), -10)

    def test_struct_fields(self):
        # Struct[70, 7] -> Struct[120, 7]: only field 0 moved
        before = bytes([0x41, 2, 0x21, 1, 70, 0x21, 1, 7])
        after = bytes([0x41, 2, 0x21, 1, 120, 0x21, 1, 7])
        self.assertEqual(_delta(before, after, "Changed"), {"0": 50})

    def test_unchanged(self):
        self.assertIsNone(_delta(b"\x21\x01\x0a", b"\x21\x01\x0a", "Changed"))


class ValueTests(unittest.TestCase):
    def test_decode_and_text(self):
        self.assertEqual(decode_storage_value(b"\x21\x02\x00\x01"), 256)
        self.assertEqual(storage_value_text(b"\x21\x02\x00\x01"), "256")
        self.assertEqual(storage_value_text(address_to_le(ADDR)), f"{ADDR} / {le_to_hash(address_to_le(ADDR))}")
        self.assertEqual(storage_value_text(b"\x05\x00"), "0x0500 (int 5)")

    def test_leading_int(self):
        self.assertEqual(leading_int(b"\x41\x02\x21\x01\x09\x21\x01\x01"), 9)
        self.assertIsNone(leading_int(b"\x00" * 32))  # a hash is not a number

    def test_item_value_map(self):
        item = {"type": "Map", "value": [{"key": {"type": "ByteString", "value": "YQ=="},
                                          "value": {"type": "Integer", "value": "3"}}]}
        self.assertEqual(item_value(item), {b"a": 3})
        self.assertEqual(item_value({"type": "InteropInterface"}), "<iterator>")

    def test_typed_json(self):
        le = address_to_le(ADDR)
        self.assertEqual(typed_json(le, "Hash160"), le_to_hash(le))
        self.assertEqual(typed_json(b"\x01" + b"\x00" * 31, "Hash256"), "0x" + "00" * 31 + "01")
        self.assertEqual(typed_json(b"\x05", "Integer"), 5)
        self.assertEqual(typed_json(b"ab", None), "0x6162")

    def test_render_amount(self):
        self.assertEqual(render(150000000, "Integer", None, 8, "GAS"), "1.5 GAS (150000000)")
        self.assertEqual(render(address_to_le(ADDR), "Hash160"), ADDR)
        self.assertEqual(render(None), "null")


if __name__ == "__main__":
    unittest.main()
