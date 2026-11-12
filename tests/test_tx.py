import unittest

from helpers import GAS_TRANSFER, fixture_path

from n3trace.codec import b64e
from n3trace.preview import _witness_bytes
from n3trace.tx import (
    TxFormatError, count_signatures, decode_input, describe_verification, parse_scopes, parse_transaction, rpc_signer,
    scope_names, signer_view,
)
from n3trace.vm import BY_NAME, emit_push, syscall_id


def raw_gas_transfer() -> bytes:
    with open(fixture_path("tx-gas-transfer.raw"), encoding="utf-8") as fh:
        return decode_input(fh.read())


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.raw = raw_gas_transfer()
        self.tx = parse_transaction(self.raw)

    def test_hash_matches_chain(self):
        self.assertEqual(self.tx.hash, GAS_TRANSFER)

    def test_fields(self):
        self.assertEqual(self.tx.sysfee, 215925)
        self.assertEqual(self.tx.netfee, 37824)
        self.assertEqual(self.tx.size, 252)
        self.assertEqual(len(self.tx.signers), 1)
        self.assertEqual(self.tx.signers[0]["scopes"], ["CalledByEntry"])
        self.assertEqual(len(self.tx.witnesses), 1)

    def test_witness_roundtrip(self):
        # unsigned part + rebuilt witness section gives back the exact bytes
        self.assertEqual(self.tx.unsigned + _witness_bytes(self.tx), self.raw)

    def test_signer_view_classifies_witness(self):
        v = signer_view(self.tx.signers[0], self.tx.witnesses[0])
        self.assertEqual(v["address"], "NdJbpKzsxT3UtHDDQnfgMWemsvuh7Tj64J")
        self.assertEqual(v["witness"]["type"], "standard")
        self.assertEqual(v["witness"]["text"], "single signature")
        self.assertEqual(v["witness"]["signatures"], 1)

    def test_unsigned_parses(self):
        tx = parse_transaction(self.tx.unsigned)
        self.assertEqual(tx.hash, GAS_TRANSFER)
        self.assertEqual(tx.witnesses, [])

    def test_truncated(self):
        with self.assertRaises(TxFormatError):
            parse_transaction(self.raw[:40])

    def test_trailing_bytes(self):
        with self.assertRaises(TxFormatError):
            parse_transaction(self.raw + b"\x00")

    def test_hex_and_base64_inputs(self):
        self.assertEqual(decode_input("0x" + self.raw.hex()), self.raw)
        self.assertEqual(decode_input(b64e(self.raw)), self.raw)
        with self.assertRaises(ValueError):
            decode_input("not!base64")


class VerificationTests(unittest.TestCase):
    def test_multisig(self):
        keys = [bytes([2]) + bytes([i]) * 32 for i in (1, 2, 3)]
        script = emit_push(2) + b"".join(emit_push(k) for k in keys) + emit_push(3) + bytes([BY_NAME["SYSCALL"]])
        script += syscall_id("System.Crypto.CheckMultisig")
        v = describe_verification(script)
        self.assertEqual((v["type"], v["m"], v["n"]), ("multisig", 2, 3))
        self.assertEqual(v["text"], "2-of-3 multisig")

    def test_contract_and_custom(self):
        self.assertEqual(describe_verification(b"")["type"], "contract")
        self.assertEqual(describe_verification(b"\x11\x40")["type"], "custom")

    def test_signature_count(self):
        inv = emit_push(b"\x01" * 64) + emit_push(b"\x02" * 64)
        self.assertEqual(count_signatures(inv), 2)


class ScopeTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(scope_names(0), ["None"])
        self.assertEqual(scope_names(0x11), ["CalledByEntry", "CustomContracts"])
        self.assertEqual(scope_names(0x80), ["Global"])
        with self.assertRaises(TxFormatError):
            scope_names(0x02)

    def test_rpc_form(self):
        self.assertEqual(parse_scopes("CalledByEntry, CustomContracts"), ["CalledByEntry", "CustomContracts"])
        s = rpc_signer({"account": "0x" + "11" * 20, "scopes": ["CalledByEntry", "CustomContracts"],
                        "allowedcontracts": ["0x" + "22" * 20]})
        self.assertEqual(s["scopes"], "CalledByEntry, CustomContracts")
        self.assertEqual(s["allowedcontracts"], ["0x" + "22" * 20])

    def test_witness_rules_parse(self):
        # A hand-built signer with one WitnessRule: Allow if CalledByContract(0x22..22)
        tx = parse_transaction(raw_gas_transfer())
        acct = bytes.fromhex(tx.signers[0]["account"][2:])[::-1]
        rule = bytes([0x01, 0x28]) + b"\x22" * 20
        signer = acct + bytes([0x40]) + b"\x01" + rule
        raw = bytearray(tx.unsigned)
        # header is 25 bytes, then the signer count (1) and the 21-byte signer that is replaced
        start = 26
        rebuilt = bytes(raw[:start]) + signer + bytes(raw[start + 21:])
        t2 = parse_transaction(rebuilt)
        self.assertEqual(t2.signers[0]["scopes"], ["WitnessRules"])
        rule0 = t2.signers[0]["rules"][0]
        self.assertEqual(rule0["action"], "Allow")
        self.assertEqual(rule0["condition"]["type"], "CalledByContract")


if __name__ == "__main__":
    unittest.main()
