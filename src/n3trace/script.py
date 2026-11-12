"""Recover the contract calls an entry script makes, with their arguments.

Wallets and SDKs (neon-js, neo-go, neow3j, mamba) build entry scripts from a few patterns:
push the arguments, PACK them, push call flags, method and contract hash, SYSCALL System.Contract.Call,
then maybe ASSERT or DROP the result. This module runs a small symbolic stack machine over those patterns.
Anything it cannot model (loops, arithmetic on call results, ...) marks the decoding as approximate instead of
guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .codec import le_to_hash
from .vm import DisassemblyError, iter_instructions

CALL = "System.Contract.Call"


@dataclass
class CallResult:
    """Placeholder for the value returned by call number `index`."""

    index: int

    def __repr__(self) -> str:
        return f"<result of call #{self.index + 1}>"


class Unknown:
    def __repr__(self) -> str:
        return "?"


UNKNOWN = Unknown()


@dataclass
class Call:
    index: int
    offset: int
    contract: str | None  # '0x…' or None when not a constant
    method: str | None
    args: list[Any] | None  # None when the argument array is not a constant
    flags: int | None
    result_use: str = "kept"  # kept | dropped | asserted
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class DecodedScript:
    calls: list[Call]
    exact: bool
    notes: list[str]
    other_syscalls: list[str]


# Syscalls an entry script may use besides Contract.Call, with (pops, pushes).
_SYSCALL_EFFECT = {
    "System.Runtime.GetTime": (0, 1),
    "System.Runtime.GetNetwork": (0, 1),
    "System.Runtime.GetTrigger": (0, 1),
    "System.Runtime.Platform": (0, 1),
    "System.Runtime.GetRandom": (0, 1),
    "System.Runtime.GetScriptContainer": (0, 1),
    "System.Runtime.GetExecutingScriptHash": (0, 1),
    "System.Runtime.GetEntryScriptHash": (0, 1),
    "System.Runtime.GetCallingScriptHash": (0, 1),
    "System.Runtime.GasLeft": (0, 1),
    "System.Runtime.CheckWitness": (1, 1),
    "System.Runtime.Log": (1, 0),
    "System.Runtime.Notify": (2, 0),
    "System.Runtime.BurnGas": (1, 0),
    "System.Runtime.CurrentSigners": (0, 1),
    "System.Runtime.GetInvocationCounter": (0, 1),
    "System.Iterator.Next": (1, 1),
    "System.Iterator.Value": (1, 1),
    "System.Contract.GetCallFlags": (0, 1),
}


def _pop(stack: list[Any]) -> Any:
    return stack.pop() if stack else UNKNOWN


def decode_script(script: bytes) -> DecodedScript:
    from .vm import SYSCALLS

    calls: list[Call] = []
    notes: list[str] = []
    other: list[str] = []
    exact = True
    stack: list[Any] = []

    def inexact(why: str) -> None:
        nonlocal exact
        if exact or len(notes) < 5:
            notes.append(why)
        exact = False

    try:
        instrs = list(iter_instructions(script))
    except DisassemblyError as exc:
        return DecodedScript([], False, [f"script does not disassemble: {exc}"], [])

    for ins in instrs:
        n = ins.name
        try:
            stack.append(ins.push_value())
            continue
        except ValueError:
            pass
        if n == "NOP":
            continue
        if n in ("NEWARRAY0", "NEWSTRUCT0"):
            stack.append([])
        elif n == "NEWMAP":
            stack.append({})
        elif n in ("PACK", "PACKSTRUCT"):
            count = _pop(stack)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0 or count > len(stack):
                inexact(f"{n} at {ins.offset} with a non-constant size")
                stack = []
                stack.append(UNKNOWN)
                continue
            items = [stack.pop() for _ in range(count)]
            stack.append(items)
        elif n == "PACKMAP":
            count = _pop(stack)
            if not isinstance(count, int) or isinstance(count, bool) or count * 2 > len(stack):
                inexact(f"PACKMAP at {ins.offset} with a non-constant size")
                stack = [UNKNOWN]
                continue
            pairs = []
            for _ in range(count):
                k = stack.pop()
                v = stack.pop()
                pairs.append((k, v))
            stack.append(dict(pairs) if all(isinstance(k, (int, bytes, bool)) for k, _ in pairs) else pairs)
        elif n == "SYSCALL":
            name = SYSCALLS.get(ins.operand, "0x" + ins.operand.hex())
            if name == CALL:
                h = _pop(stack)
                method = _pop(stack)
                flags = _pop(stack)
                args = _pop(stack)
                call = Call(
                    index=len(calls),
                    offset=ins.offset,
                    contract=le_to_hash(h) if isinstance(h, bytes) and len(h) == 20 else None,
                    method=method.decode("utf-8", "replace") if isinstance(method, bytes) else None,
                    args=args if isinstance(args, list) else None,
                    flags=flags if isinstance(flags, int) and not isinstance(flags, bool) else None,
                )
                if call.contract is None or call.method is None or call.args is None:
                    inexact(f"call at {ins.offset} has a computed contract, method or argument list")
                calls.append(call)
                stack.append(CallResult(call.index))
            elif name in _SYSCALL_EFFECT:
                other.append(name)
                pops, pushes = _SYSCALL_EFFECT[name]
                for _ in range(pops):
                    _pop(stack)
                stack.extend([UNKNOWN] * pushes)
            else:
                other.append(name)
                inexact(f"syscall {name} at {ins.offset}")
                stack = []
        elif n in ("DROP", "ASSERT"):
            v = _pop(stack)
            if isinstance(v, CallResult):
                calls[v.index].result_use = "dropped" if n == "DROP" else "asserted"
                if n == "ASSERT":
                    calls[v.index].meta["assert_at"] = ins.offset
        elif n == "ASSERTMSG":
            _pop(stack)
            v = _pop(stack)
            if isinstance(v, CallResult):
                calls[v.index].result_use = "asserted"
                calls[v.index].meta["assert_at"] = ins.offset
        elif n == "DUP":
            stack.append(stack[-1] if stack else UNKNOWN)
        elif n == "SWAP":
            if len(stack) >= 2:
                stack[-1], stack[-2] = stack[-2], stack[-1]
        elif n == "CONVERT":
            pass  # keeps the value; the type tag does not matter for display
        elif n in ("RET",):
            break
        else:
            inexact(f"{n} at {ins.offset}")
            stack = []
    return DecodedScript(calls, exact, notes, other)
