import unittest

from n3trace.codec import (
    DecodeError,
    address_to_le,
    as_int,
    deserialize,
    format_amount,
    hash_to_le,
    int_from_le,
    int_to_le,
    le_to_address,
    le_to_hash,
    parse_amount,
    stack_value,
    to_hash,
)

# GhostMarket on mainnet, as listed by the Neoverse snapshot fixture.
GM_ADDR = "Ndr9zZP9R2bzHN62skQMEpQufLYuGXBucV"


class Addresses(unittest.TestCase):
    def test_round_trip(self):
        le = address_to_le(GM_ADDR)
        self.assertEqual(len(le), 20)
        self.assertEqual(le_to_address(le), GM_ADDR)
        self.assertEqual(hash_to_le(le_to_hash(le)), le)

    def test_hash_is_big_endian(self):
        le = bytes(range(20))
        self.assertEqual(le_to_hash(le), "0x" + bytes(range(19, -1, -1)).hex())

    def test_bad_checksum(self):
        bad = GM_ADDR[:-1] + ("a" if GM_ADDR[-1] != "a" else "b")
        with self.assertRaises(ValueError):
            address_to_le(bad)

    def test_to_hash_accepts_all_forms(self):
        h = le_to_hash(address_to_le(GM_ADDR))
        self.assertEqual(to_hash(GM_ADDR), h)
        self.assertEqual(to_hash(h.upper().replace("0X", "0x")), h)
        self.assertEqual(to_hash(h[2:]), h)
        with self.assertRaises(ValueError):
            to_hash("0x1234")


class Integers(unittest.TestCase):
    def test_neovm_encoding(self):
        cases = {0: b"", 1: b"\x01", -1: b"\xff", 127: b"\x7f", 128: b"\x80\x00", 255: b"\xff\x00",
                 -128: b"\x80", -129: b"\x7f\xff", 100_000_000: (100_000_000).to_bytes(4, "little")}
        for n, raw in cases.items():
            self.assertEqual(int_to_le(n), raw, n)
            self.assertEqual(int_from_le(raw), n, n)

    def test_round_trip_wide(self):
        for n in [2**63, -(2**63), 10**30 + 7, -(10**30) - 7, 9_233_177_661_621_512]:
            self.assertEqual(int_from_le(int_to_le(n)), n)


class Deserialize(unittest.TestCase):
    def test_struct_like_neo_account_state(self):
        # Struct[balance=5, height=10, voteTo=33-byte key, lastGasPerVote=0]
        key = b"\x03" + bytes(32)
        raw = (b"\x41\x04" + b"\x21\x01\x05" + b"\x21\x01\x0a" + b"\x28\x21" + key + b"\x21\x00")
        v = deserialize(raw)
        self.assertEqual(v[0], 5)
        self.assertEqual(v[1], 10)
        self.assertEqual(v[2], key)
        self.assertEqual(v[3], 0)

    def test_null_vote_and_bool(self):
        v = deserialize(b"\x41\x03\x21\x01\x07\x00\x20\x01")
        self.assertEqual(v, [7, None, True])

    def test_map(self):
        v = deserialize(b"\x48\x01\x28\x01a\x21\x01\x02")
        self.assertEqual(v, [(b"a", 2)])  # pairs: map keys may be unhashable

    def test_truncated_input_fails_cleanly(self):
        with self.assertRaises(DecodeError):
            deserialize(b"\x41\x02\x21\x01\x05")
        with self.assertRaises(DecodeError):
            deserialize(b"\x99")

    def test_item_limit(self):
        with self.assertRaises(DecodeError):
            deserialize(b"\x40\xfd\xff\xff", max_items=16)


class Amounts(unittest.TestCase):
    def test_format_exact(self):
        self.assertEqual(format_amount(9233177661621512, 8), "92331776.61621512")
        self.assertEqual(format_amount(100000000, 8), "1")
        self.assertEqual(format_amount(1, 8), "0.00000001")
        self.assertEqual(format_amount(-150, 2), "-1.5")
        self.assertEqual(format_amount(13496, 0), "13496")

    def test_parse(self):
        self.assertEqual(parse_amount("1.5", 8), 150_000_000)
        self.assertEqual(parse_amount("0.00000001", 8), 1)
        self.assertEqual(parse_amount("10", 0), 10)
        self.assertEqual(parse_amount(".5", 1), 5)
        for bad in ("1.234", "abc", "", "1e5"):
            with self.assertRaises(ValueError, msg=bad):
                parse_amount(bad, 2)

    def test_round_trip(self):
        for raw in (0, 1, 7, 10**8, 123456789012345678):
            self.assertEqual(parse_amount(format_amount(raw, 8), 8), raw)


class Stack(unittest.TestCase):
    def test_values(self):
        self.assertEqual(stack_value({"type": "Integer", "value": "42"}), 42)
        self.assertEqual(stack_value({"type": "ByteString", "value": "TkVP"}), b"NEO")
        self.assertEqual(stack_value({"type": "Array", "value": [{"type": "Boolean", "value": True}]}), [True])
        self.assertEqual(as_int(b"\x2a"), 42)
        self.assertEqual(as_int(True), 1)


if __name__ == "__main__":
    unittest.main()
