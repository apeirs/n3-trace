"""NeoVM N3 instruction set, a disassembler and the interop (SYSCALL) names.

The opcode table mirrors neo-project/neo-vm src/Neo.VM/OpCode.cs: value -> (name, fixed operand size,
operand length prefix size).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterator

from .codec import int_from_le, le_to_address, le_to_hash

OPCODES: dict[int, tuple[str, int, int]] = {
    0x00: ("PUSHINT8", 1, 0),
    0x01: ("PUSHINT16", 2, 0),
    0x02: ("PUSHINT32", 4, 0),
    0x03: ("PUSHINT64", 8, 0),
    0x04: ("PUSHINT128", 16, 0),
    0x05: ("PUSHINT256", 32, 0),
    0x08: ("PUSHT", 0, 0),
    0x09: ("PUSHF", 0, 0),
    0x0A: ("PUSHA", 4, 0),
    0x0B: ("PUSHNULL", 0, 0),
    0x0C: ("PUSHDATA1", 0, 1),
    0x0D: ("PUSHDATA2", 0, 2),
    0x0E: ("PUSHDATA4", 0, 4),
    0x0F: ("PUSHM1", 0, 0),
    0x10: ("PUSH0", 0, 0),
    0x11: ("PUSH1", 0, 0),
    0x12: ("PUSH2", 0, 0),
    0x13: ("PUSH3", 0, 0),
    0x14: ("PUSH4", 0, 0),
    0x15: ("PUSH5", 0, 0),
    0x16: ("PUSH6", 0, 0),
    0x17: ("PUSH7", 0, 0),
    0x18: ("PUSH8", 0, 0),
    0x19: ("PUSH9", 0, 0),
    0x1A: ("PUSH10", 0, 0),
    0x1B: ("PUSH11", 0, 0),
    0x1C: ("PUSH12", 0, 0),
    0x1D: ("PUSH13", 0, 0),
    0x1E: ("PUSH14", 0, 0),
    0x1F: ("PUSH15", 0, 0),
    0x20: ("PUSH16", 0, 0),
    0x21: ("NOP", 0, 0),
    0x22: ("JMP", 1, 0),
    0x23: ("JMP_L", 4, 0),
    0x24: ("JMPIF", 1, 0),
    0x25: ("JMPIF_L", 4, 0),
    0x26: ("JMPIFNOT", 1, 0),
    0x27: ("JMPIFNOT_L", 4, 0),
    0x28: ("JMPEQ", 1, 0),
    0x29: ("JMPEQ_L", 4, 0),
    0x2A: ("JMPNE", 1, 0),
    0x2B: ("JMPNE_L", 4, 0),
    0x2C: ("JMPGT", 1, 0),
    0x2D: ("JMPGT_L", 4, 0),
    0x2E: ("JMPGE", 1, 0),
    0x2F: ("JMPGE_L", 4, 0),
    0x30: ("JMPLT", 1, 0),
    0x31: ("JMPLT_L", 4, 0),
    0x32: ("JMPLE", 1, 0),
    0x33: ("JMPLE_L", 4, 0),
    0x34: ("CALL", 1, 0),
    0x35: ("CALL_L", 4, 0),
    0x36: ("CALLA", 0, 0),
    0x37: ("CALLT", 2, 0),
    0x38: ("ABORT", 0, 0),
    0x39: ("ASSERT", 0, 0),
    0x3A: ("THROW", 0, 0),
    0x3B: ("TRY", 2, 0),
    0x3C: ("TRY_L", 8, 0),
    0x3D: ("ENDTRY", 1, 0),
    0x3E: ("ENDTRY_L", 4, 0),
    0x3F: ("ENDFINALLY", 0, 0),
    0x40: ("RET", 0, 0),
    0x41: ("SYSCALL", 4, 0),
    0x43: ("DEPTH", 0, 0),
    0x45: ("DROP", 0, 0),
    0x46: ("NIP", 0, 0),
    0x48: ("XDROP", 0, 0),
    0x49: ("CLEAR", 0, 0),
    0x4A: ("DUP", 0, 0),
    0x4B: ("OVER", 0, 0),
    0x4D: ("PICK", 0, 0),
    0x4E: ("TUCK", 0, 0),
    0x50: ("SWAP", 0, 0),
    0x51: ("ROT", 0, 0),
    0x52: ("ROLL", 0, 0),
    0x53: ("REVERSE3", 0, 0),
    0x54: ("REVERSE4", 0, 0),
    0x55: ("REVERSEN", 0, 0),
    0x56: ("INITSSLOT", 1, 0),
    0x57: ("INITSLOT", 2, 0),
    0x58: ("LDSFLD0", 0, 0),
    0x59: ("LDSFLD1", 0, 0),
    0x5A: ("LDSFLD2", 0, 0),
    0x5B: ("LDSFLD3", 0, 0),
    0x5C: ("LDSFLD4", 0, 0),
    0x5D: ("LDSFLD5", 0, 0),
    0x5E: ("LDSFLD6", 0, 0),
    0x5F: ("LDSFLD", 1, 0),
    0x60: ("STSFLD0", 0, 0),
    0x61: ("STSFLD1", 0, 0),
    0x62: ("STSFLD2", 0, 0),
    0x63: ("STSFLD3", 0, 0),
    0x64: ("STSFLD4", 0, 0),
    0x65: ("STSFLD5", 0, 0),
    0x66: ("STSFLD6", 0, 0),
    0x67: ("STSFLD", 1, 0),
    0x68: ("LDLOC0", 0, 0),
    0x69: ("LDLOC1", 0, 0),
    0x6A: ("LDLOC2", 0, 0),
    0x6B: ("LDLOC3", 0, 0),
    0x6C: ("LDLOC4", 0, 0),
    0x6D: ("LDLOC5", 0, 0),
    0x6E: ("LDLOC6", 0, 0),
    0x6F: ("LDLOC", 1, 0),
    0x70: ("STLOC0", 0, 0),
    0x71: ("STLOC1", 0, 0),
    0x72: ("STLOC2", 0, 0),
    0x73: ("STLOC3", 0, 0),
    0x74: ("STLOC4", 0, 0),
    0x75: ("STLOC5", 0, 0),
    0x76: ("STLOC6", 0, 0),
    0x77: ("STLOC", 1, 0),
    0x78: ("LDARG0", 0, 0),
    0x79: ("LDARG1", 0, 0),
    0x7A: ("LDARG2", 0, 0),
    0x7B: ("LDARG3", 0, 0),
    0x7C: ("LDARG4", 0, 0),
    0x7D: ("LDARG5", 0, 0),
    0x7E: ("LDARG6", 0, 0),
    0x7F: ("LDARG", 1, 0),
    0x80: ("STARG0", 0, 0),
    0x81: ("STARG1", 0, 0),
    0x82: ("STARG2", 0, 0),
    0x83: ("STARG3", 0, 0),
    0x84: ("STARG4", 0, 0),
    0x85: ("STARG5", 0, 0),
    0x86: ("STARG6", 0, 0),
    0x87: ("STARG", 1, 0),
    0x88: ("NEWBUFFER", 0, 0),
    0x89: ("MEMCPY", 0, 0),
    0x8B: ("CAT", 0, 0),
    0x8C: ("SUBSTR", 0, 0),
    0x8D: ("LEFT", 0, 0),
    0x8E: ("RIGHT", 0, 0),
    0x90: ("INVERT", 0, 0),
    0x91: ("AND", 0, 0),
    0x92: ("OR", 0, 0),
    0x93: ("XOR", 0, 0),
    0x97: ("EQUAL", 0, 0),
    0x98: ("NOTEQUAL", 0, 0),
    0x99: ("SIGN", 0, 0),
    0x9A: ("ABS", 0, 0),
    0x9B: ("NEGATE", 0, 0),
    0x9C: ("INC", 0, 0),
    0x9D: ("DEC", 0, 0),
    0x9E: ("ADD", 0, 0),
    0x9F: ("SUB", 0, 0),
    0xA0: ("MUL", 0, 0),
    0xA1: ("DIV", 0, 0),
    0xA2: ("MOD", 0, 0),
    0xA3: ("POW", 0, 0),
    0xA4: ("SQRT", 0, 0),
    0xA5: ("MODMUL", 0, 0),
    0xA6: ("MODPOW", 0, 0),
    0xA8: ("SHL", 0, 0),
    0xA9: ("SHR", 0, 0),
    0xAA: ("NOT", 0, 0),
    0xAB: ("BOOLAND", 0, 0),
    0xAC: ("BOOLOR", 0, 0),
    0xB1: ("NZ", 0, 0),
    0xB3: ("NUMEQUAL", 0, 0),
    0xB4: ("NUMNOTEQUAL", 0, 0),
    0xB5: ("LT", 0, 0),
    0xB6: ("LE", 0, 0),
    0xB7: ("GT", 0, 0),
    0xB8: ("GE", 0, 0),
    0xB9: ("MIN", 0, 0),
    0xBA: ("MAX", 0, 0),
    0xBB: ("WITHIN", 0, 0),
    0xBE: ("PACKMAP", 0, 0),
    0xBF: ("PACKSTRUCT", 0, 0),
    0xC0: ("PACK", 0, 0),
    0xC1: ("UNPACK", 0, 0),
    0xC2: ("NEWARRAY0", 0, 0),
    0xC3: ("NEWARRAY", 0, 0),
    0xC4: ("NEWARRAY_T", 1, 0),
    0xC5: ("NEWSTRUCT0", 0, 0),
    0xC6: ("NEWSTRUCT", 0, 0),
    0xC8: ("NEWMAP", 0, 0),
    0xCA: ("SIZE", 0, 0),
    0xCB: ("HASKEY", 0, 0),
    0xCC: ("KEYS", 0, 0),
    0xCD: ("VALUES", 0, 0),
    0xCE: ("PICKITEM", 0, 0),
    0xCF: ("APPEND", 0, 0),
    0xD0: ("SETITEM", 0, 0),
    0xD1: ("REVERSEITEMS", 0, 0),
    0xD2: ("REMOVE", 0, 0),
    0xD3: ("CLEARITEMS", 0, 0),
    0xD4: ("POPITEM", 0, 0),
    0xD8: ("ISNULL", 0, 0),
    0xD9: ("ISTYPE", 1, 0),
    0xDB: ("CONVERT", 1, 0),
    0xE0: ("ABORTMSG", 0, 0),
    0xE1: ("ASSERTMSG", 0, 0),
}

BY_NAME = {name: code for code, (name, _s, _p) in OPCODES.items()}

# Interop service names. A SYSCALL operand is the first 4 bytes of sha256(name).
SYSCALL_NAMES = [
    "System.Contract.Call",
    "System.Contract.CallNative",
    "System.Contract.GetCallFlags",
    "System.Contract.CreateStandardAccount",
    "System.Contract.CreateMultisigAccount",
    "System.Contract.NativeOnPersist",
    "System.Contract.NativePostPersist",
    "System.Crypto.CheckSig",
    "System.Crypto.CheckMultisig",
    "System.Iterator.Next",
    "System.Iterator.Value",
    "System.Runtime.Platform",
    "System.Runtime.GetNetwork",
    "System.Runtime.GetAddressVersion",
    "System.Runtime.GetTrigger",
    "System.Runtime.GetTime",
    "System.Runtime.GetScriptContainer",
    "System.Runtime.GetExecutingScriptHash",
    "System.Runtime.GetCallingScriptHash",
    "System.Runtime.GetEntryScriptHash",
    "System.Runtime.LoadScript",
    "System.Runtime.CheckWitness",
    "System.Runtime.GetInvocationCounter",
    "System.Runtime.GetRandom",
    "System.Runtime.Log",
    "System.Runtime.Notify",
    "System.Runtime.GetNotifications",
    "System.Runtime.GasLeft",
    "System.Runtime.BurnGas",
    "System.Runtime.CurrentSigners",
    "System.Storage.GetContext",
    "System.Storage.GetReadOnlyContext",
    "System.Storage.AsReadOnly",
    "System.Storage.Get",
    "System.Storage.Find",
    "System.Storage.Put",
    "System.Storage.Delete",
    "System.Storage.Local.Get",
    "System.Storage.Local.Find",
    "System.Storage.Local.Put",
    "System.Storage.Local.Delete",
]


def syscall_id(name: str) -> bytes:
    """The 4 operand bytes of SYSCALL <name>."""
    return hashlib.sha256(name.encode()).digest()[:4]


SYSCALLS: dict[bytes, str] = {syscall_id(n): n for n in SYSCALL_NAMES}


class DisassemblyError(ValueError):
    pass


@dataclass(frozen=True)
class Instr:
    offset: int
    opcode: int
    name: str
    operand: bytes = b""

    @property
    def size(self) -> int:
        _n, fixed, prefix = OPCODES[self.opcode]
        return 1 + (fixed if fixed else prefix + len(self.operand))

    def jump_target(self) -> int | None:
        """Absolute target of a JMP*/CALL*/PUSHA/ENDTRY instruction, else None."""
        n = self.name
        if n.startswith(("JMP", "CALL", "ENDTRY")) and n not in ("CALLA", "CALLT") or n == "PUSHA":
            return self.offset + int_from_le(self.operand)
        return None

    def push_value(self) -> int | bytes | bool | None:
        """The constant a PUSH* instruction puts on the stack. Raises ValueError for any other opcode."""
        n = self.name
        if n.startswith("PUSHINT"):
            return int_from_le(self.operand)
        if n.startswith("PUSHDATA"):
            return self.operand
        if n == "PUSHT":
            return True
        if n == "PUSHF":
            return False
        if n == "PUSHNULL":
            return None
        if n == "PUSHM1":
            return -1
        if n.startswith("PUSH") and n[4:].isdigit():
            return int(n[4:])
        raise ValueError(f"{n} does not push a constant")

    def comment(self) -> str:
        """A short human note: syscall name, jump target, decoded push data."""
        n = self.name
        if n == "SYSCALL":
            return SYSCALLS.get(self.operand, "unknown interop 0x" + self.operand.hex())
        target = self.jump_target()
        if target is not None:
            return f"-> {target:04d}"
        if n.startswith("PUSHDATA"):
            return describe_bytes(self.operand)
        if n.startswith("PUSHINT"):
            return str(int_from_le(self.operand))
        if n == "CALLT":
            return f"token #{int.from_bytes(self.operand, 'little')}"
        if n in ("TRY", "TRY_L"):
            half = len(self.operand) // 2
            c = int_from_le(self.operand[:half])
            f = int_from_le(self.operand[half:])
            return f"catch {self.offset + c if c else '-'} finally {self.offset + f if f else '-'}"
        return ""


def describe_bytes(b: bytes) -> str:
    """Best guess at what a byte string is: script hash/address, text, or hex."""
    if len(b) == 20:
        return f"{le_to_hash(b)} / {le_to_address(b)}"
    if len(b) == 33 and b[0] in (2, 3):
        return f"public key {b.hex()}"
    if b and all(32 <= c < 127 for c in b):
        return repr(b.decode())
    if not b:
        return "empty"
    if len(b) <= 32:
        return f"0x{b.hex()} (int {int_from_le(b)})"
    return f"{len(b)} bytes"


def iter_instructions(script: bytes) -> Iterator[Instr]:
    i = 0
    while i < len(script):
        op = script[i]
        if op not in OPCODES:
            raise DisassemblyError(f"unknown opcode 0x{op:02x} at offset {i}")
        name, fixed, prefix = OPCODES[op]
        if prefix:
            if i + 1 + prefix > len(script):
                raise DisassemblyError(f"truncated {name} at offset {i}")
            n = int.from_bytes(script[i + 1 : i + 1 + prefix], "little")
            start = i + 1 + prefix
        else:
            n = fixed
            start = i + 1
        if start + n > len(script):
            raise DisassemblyError(f"truncated {name} at offset {i}")
        yield Instr(i, op, name, script[start : start + n])
        i = start + n


def disassemble(script: bytes) -> list[Instr]:
    return list(iter_instructions(script))


def format_listing(script: bytes) -> str:
    rows = []
    for ins in iter_instructions(script):
        operand = ins.operand.hex() if ins.operand and not ins.name.startswith("PUSHDATA") else ""
        note = ins.comment()
        line = f"{ins.offset:04d}  {ins.name:<12} {operand}".rstrip()
        rows.append(f"{line:<40} {('; ' + note) if note else ''}".rstrip())
    return "\n".join(rows)


# -- script builder (used by `preview --call`) ------------------------------------
def emit_push(value: object) -> bytes:
    """Encode one constant push. Accepts bool, int, bytes, str (UTF-8), None and lists (packed)."""
    if value is None:
        return bytes([BY_NAME["PUSHNULL"]])
    if isinstance(value, bool):
        return bytes([BY_NAME["PUSHT" if value else "PUSHF"]])
    if isinstance(value, int):
        if -1 <= value <= 16:
            return bytes([BY_NAME["PUSH0"] + value]) if value >= 0 else bytes([BY_NAME["PUSHM1"]])
        for size, name in ((1, "PUSHINT8"), (2, "PUSHINT16"), (4, "PUSHINT32"), (8, "PUSHINT64"),
                           (16, "PUSHINT128"), (32, "PUSHINT256")):
            lo, hi = -(1 << (size * 8 - 1)), (1 << (size * 8 - 1)) - 1
            if lo <= value <= hi:
                return bytes([BY_NAME[name]]) + value.to_bytes(size, "little", signed=True)
        raise ValueError("integer does not fit in 256 bits")
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, (bytes, bytearray)):
        b = bytes(value)
        if len(b) < 0x100:
            return bytes([BY_NAME["PUSHDATA1"], len(b)]) + b
        if len(b) < 0x10000:
            return bytes([BY_NAME["PUSHDATA2"]]) + len(b).to_bytes(2, "little") + b
        return bytes([BY_NAME["PUSHDATA4"]]) + len(b).to_bytes(4, "little") + b
    if isinstance(value, (list, tuple)):
        return emit_array(list(value))
    raise TypeError(f"cannot push {type(value).__name__}")


def emit_array(items: list) -> bytes:
    if not items:
        return bytes([BY_NAME["NEWARRAY0"]])
    out = b"".join(emit_push(v) for v in reversed(items))
    return out + emit_push(len(items)) + bytes([BY_NAME["PACK"]])


CALL_FLAGS_ALL = 0x0F


def emit_contract_call(contract_le: bytes, method: str, args: list, flags: int = CALL_FLAGS_ALL) -> bytes:
    """The standard System.Contract.Call sequence wallets build (neon-js / neo-go style)."""
    return (
        emit_array(args)
        + emit_push(flags)
        + emit_push(method)
        + emit_push(contract_le)
        + bytes([BY_NAME["SYSCALL"]])
        + syscall_id("System.Contract.Call")
    )
