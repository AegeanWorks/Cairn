"""x86-64 emitter: the instruction subset Cairn lowers to, written as bytes.

This is the whole "own compiler" backend. It supports the encodings the code generator needs
and nothing else, so every method is a fixed byte pattern an agent can verify by eye. Labels
and 32-bit relative fixups are resolved later, when section addresses are known.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

REGISTERS = {
    "rax": 0, "rcx": 1, "rdx": 2, "rbx": 3,
    "rsp": 4, "rbp": 5, "rsi": 6, "rdi": 7,
    "r8": 8, "r9": 9, "r10": 10, "r11": 11,
    "r12": 12, "r13": 13, "r14": 14, "r15": 15,
}

EIGHT_BIT = {
    "al": 0, "cl": 1, "dl": 2, "bl": 3, "spl": 4, "bpl": 5, "sil": 6, "dil": 7,
    "r8b": 8, "r9b": 9, "r10b": 10, "r11b": 11,
}

ARGUMENT_REGISTERS = ("rcx", "rdx", "r8", "r9")

CONDITIONS = {
    "e": 0x4, "ne": 0x5, "l": 0xC, "le": 0xE, "g": 0xF, "ge": 0xD,
    "z": 0x4, "nz": 0x5, "s": 0x8, "ns": 0x9,
}


def _rex(w: bool, r: int, x: int, b: int) -> int:
    """The REX prefix byte from the four bits x86-64 adds to a legacy encoding."""
    return 0x40 | (0x08 if w else 0) | (0x04 if r else 0) | (0x02 if x else 0) | (0x01 if b else 0)


def _modrm(mod: int, reg: int, rm: int) -> Tuple[int, ...]:
    """ModRM with an optional SIB and displacement handled by the caller."""
    return ((mod << 6) | ((reg & 7) << 3) | (rm & 7),)


def _rbp_displacement(reg: int, offset: int) -> bytes:
    """`[rbp+disp8]` (mod 01) when it fits, otherwise `[rbp+disp32]` (mod 10)."""
    if -128 <= offset <= 127:
        return bytes([0x40 | ((reg & 7) << 3) | 5]) + bytes([offset & 0xFF])
    return bytes([0x80 | ((reg & 7) << 3) | 5]) + (offset & 0xFFFFFFFF).to_bytes(4, "little")


@dataclass
class Assembler:
    """A byte buffer with named labels and forward-reference fixups.

    `fixups` holds `(offset, label)` pairs where a signed 32-bit displacement belongs, relative
    to the end of the instruction at `offset` (the RIP-relative and `call rel32` rule).
    """

    code: bytearray = field(default_factory=bytearray)
    labels: Dict[str, int] = field(default_factory=dict)
    fixups: List[Tuple[int, str]] = field(default_factory=list)

    def here(self) -> int:
        """The current offset in the code buffer."""
        return len(self.code)

    def emit(self, *parts: bytes) -> None:
        """Append raw bytes."""
        for part in parts:
            self.code.extend(part)

    def label(self, name: str) -> None:
        """Bind `name` to the current offset; a duplicate name is a compiler bug."""
        self.labels[name] = self.here()

    def rel32(self, target: str) -> None:
        """A placeholder displacement patched when the image is linked."""
        self.fixups.append((self.here(), target))
        self.code.extend(b"\x00\x00\x00\x00")

    def call(self, target: str) -> None:
        """`call rel32` to a label in this image."""
        self.emit(b"\xE8")
        self.rel32(target)

    def call_indirect(self, target: str) -> None:
        """`call qword ptr [rip+disp32]`, used for imported functions through the IAT."""
        self.emit(b"\xFF\x15")
        self.rel32(target)

    def jmp(self, target: str) -> None:
        """`jmp rel32`."""
        self.emit(b"\xE9")
        self.rel32(target)

    def jcc(self, condition: str, target: str) -> None:
        """`jcc rel32` for one of the named conditions."""
        self.emit(b"\x0F", bytes([0x80 | CONDITIONS[condition]]))
        self.rel32(target)

    def push(self, reg: str) -> None:
        """`push r64`."""
        code = REGISTERS[reg]
        if code >= 8:
            self.emit(b"\x41")
        self.emit(bytes([0x50 + (code & 7)]))

    def pop(self, reg: str) -> None:
        """`pop r64`."""
        code = REGISTERS[reg]
        if code >= 8:
            self.emit(b"\x41")
        self.emit(bytes([0x58 + (code & 7)]))

    def mov_reg_reg(self, destination: str, source: str) -> None:
        """`mov r64, r64`."""
        dst, src = REGISTERS[destination], REGISTERS[source]
        self.emit(bytes([_rex(True, src >> 3, 0, dst >> 3), 0x89, *_modrm(3, src, dst)]))

    def mov_imm64(self, reg: str, value: int) -> None:
        """`mov r64, imm64`, used for every integer literal."""
        code = REGISTERS[reg]
        prefix = [_rex(True, 0, 0, code >> 3)] if code >= 8 else [0x48]
        self.emit(bytes(prefix), bytes([0xB8 + (code & 7)]),
                  (value & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little"))

    def mov_imm32(self, reg: str, value: int) -> None:
        """`mov r32, imm32`; writes zero-extend into the 64-bit register."""
        code = REGISTERS[reg]
        prefix = bytes([_rex(False, 0, 0, code >> 3)]) if code >= 8 else b""
        self.emit(prefix, bytes([0xB8 + (code & 7)]), (value & 0xFFFFFFFF).to_bytes(4, "little"))

    def mov_load_rbp(self, destination: str, offset: int) -> None:
        """`mov r64, [rbp+disp]` for a frame slot; disp8 shrinks the encoding where it fits."""
        dst = REGISTERS[destination]
        self.emit(bytes([_rex(True, dst >> 3, 0, 0), 0x8B]), _rbp_displacement(dst, offset))

    def mov_store_rbp(self, offset: int, source: str) -> None:
        """`mov [rbp+disp], r64` for a frame slot."""
        src = REGISTERS[source]
        self.emit(bytes([_rex(True, src >> 3, 0, 0), 0x89]), _rbp_displacement(src, offset))

    def mov_store_rsp_imm32(self, offset: int, value: int) -> None:
        """`mov qword ptr [rsp+disp32], imm32` for outgoing ABI slots."""
        self.emit(b"\x48\xC7\x84\x24", offset.to_bytes(4, "little"),
                  (value & 0xFFFFFFFF).to_bytes(4, "little"))

    def lea_rbp(self, destination: str, offset: int) -> None:
        """`lea r64, [rbp+disp]` for a frame address."""
        dst = REGISTERS[destination]
        self.emit(bytes([_rex(True, dst >> 3, 0, 0), 0x8D]), _rbp_displacement(dst, offset))

    def lea_rip(self, destination: str, target: str) -> None:
        """`lea r64, [rip+disp32]`, used for read-only data addresses."""
        dst = REGISTERS[destination]
        self.emit(bytes([_rex(True, dst >> 3, 0, 0), 0x8D, 0x00 | ((dst & 7) << 3) | 5]))
        self.rel32(target)

    def xor_zero(self, reg: str) -> None:
        """`xor r32, r32`: the zero idiom, which also clears the upper 32 bits."""
        code = REGISTERS[reg]
        self.emit(bytes([_rex(False, code >> 3, 0, code >> 3), 0x31] + list(_modrm(3, code, code))))

    def binary(self, opcode: int, destination: str, source: str) -> None:
        """A 64-bit `op r/m64, r64` form: add, sub, and, or, xor and cmp share it."""
        dst, src = REGISTERS[destination], REGISTERS[source]
        self.emit(bytes([_rex(True, src >> 3, 0, dst >> 3), opcode, *_modrm(3, src, dst)]))

    def _group1_imm(self, extension: int, reg: str, value: int) -> None:
        """`op r/m64, imm8` (83 /n) or `op r/m64, imm32` (81 /n), sign-extended.

        Group 1 holds add (0), or (1), and (4), sub (5) and cmp (7); only the extensions the
        generator uses are reachable, and the immediate must fit in 32 signed bits.
        """
        if not -0x80000000 <= value <= 0x7FFFFFFF:
            raise ValueError("immediate does not fit in 32 signed bits")
        code = REGISTERS[reg]
        rex = _rex(True, 0, 0, code >> 3)
        modrm = 0xC0 | (extension << 3) | (code & 7)
        if -128 <= value <= 127:
            self.emit(bytes([rex, 0x83, modrm, value & 0xFF]))
        else:
            self.emit(bytes([rex, 0x81, modrm]), (value & 0xFFFFFFFF).to_bytes(4, "little"))

    def add_imm(self, reg: str, value: int) -> None:
        """`add r64, imm`, used for `x = x + constant` without a register copy."""
        self._group1_imm(0, reg, value)

    def sub_imm(self, reg: str, value: int) -> None:
        """`sub r64, imm`, used for `x = x - constant` without a register copy."""
        self._group1_imm(5, reg, value)

    def cmp_imm(self, reg: str, value: int) -> None:
        """`cmp r64, imm`, so a comparison with a small literal needs no second register."""
        self._group1_imm(7, reg, value)

    def imul(self, destination: str, source: str) -> None:
        """`imul r64, r/m64`: two-operand signed multiply."""
        dst, src = REGISTERS[destination], REGISTERS[source]
        self.emit(bytes([_rex(True, dst >> 3, 0, src >> 3), 0x0F, 0xAF,
                         *_modrm(3, dst, src)]))

    def test(self, reg: str) -> None:
        """`test r64, r64`, setting flags from a value without changing it."""
        code = REGISTERS[reg]
        self.emit(bytes([_rex(True, code >> 3, 0, code >> 3), 0x85] + list(_modrm(3, code, code))))

    def setcc(self, condition: str) -> None:
        """`setcc al` followed by `movzx rax, al`: a comparison result as 0 or 1 in rax."""
        self.emit(b"\x0F", bytes([0x90 | CONDITIONS[condition], 0xC0]), b"\x48\x0F\xB6\xC0")

    def cqo(self) -> None:
        """Sign-extend rax into rdx:rax before a signed division."""
        self.emit(b"\x48\x99")

    def divide(self, reg: str, signed: bool) -> None:
        """`idiv`/`div r64`; the caller sets rdx and rax."""
        code = REGISTERS[reg]
        extension = 0x7 if signed else 0x6
        self.emit(bytes([_rex(True, 0, 0, code >> 3), 0xF7,
                         0xC0 | (extension << 3) | (code & 7)]))

    def unary(self, extension: int, reg: str) -> None:
        """`F7 /n` forms: not (2), neg (3)."""
        code = REGISTERS[reg]
        self.emit(bytes([_rex(True, 0, 0, code >> 3), 0xF7, 0xC0 | (extension << 3) | (code & 7)]))

    def shift_cl(self, extension: int) -> None:
        """`shl` (4), shr (5) or sar (7) of rax by cl."""
        self.emit(b"\x48\xD3", bytes([0xC0 | (extension << 3)]))

    def add_imm8(self, reg: str, value: int) -> None:
        """`add r8, imm8`, used to turn a digit into ASCII."""
        code = EIGHT_BIT[reg]
        self.emit(bytes([_rex(False, 0, 0, code >> 3), 0x80, 0xC0 | (code & 7),
                          value & 0xFF]))

    def store_byte(self, base: str, source: str) -> None:
        """`mov [base], r8`."""
        base_code, src_code = REGISTERS[base], EIGHT_BIT[source]
        self.emit(bytes([_rex(False, src_code >> 3, 0, base_code >> 3), 0x88,
                         *_modrm(0, src_code, base_code)]))

    def store_byte_imm(self, base: str, value: int) -> None:
        """`mov byte ptr [base], imm8`."""
        base_code = REGISTERS[base]
        self.emit(bytes([_rex(False, 0, 0, base_code >> 3), 0xC6,
                         *_modrm(0, 0, base_code), value & 0xFF]))

    def store_qword(self, base: str, source: str) -> None:
        """`mov [base], r64`."""
        base_code, src_code = REGISTERS[base], REGISTERS[source]
        self.emit(bytes([_rex(True, src_code >> 3, 0, base_code >> 3), 0x89,
                         *_modrm(0, src_code, base_code)]))

    def step(self, reg: str, decrement: bool) -> None:
        """`inc` (FF /0) or `dec` (FF /1) of a 64-bit register."""
        code = REGISTERS[reg]
        extension = 1 if decrement else 0
        self.emit(bytes([_rex(True, 0, 0, code >> 3), 0xFF,
                         0xC0 | (extension << 3) | (code & 7)]))

    def add_rsp(self, amount: int) -> None:
        """`add rsp, imm32`."""
        self.emit(b"\x48\x81\xC4", (amount & 0xFFFFFFFF).to_bytes(4, "little"))

    def sub_rsp(self, amount: int) -> None:
        """`sub rsp, imm32`."""
        self.emit(b"\x48\x81\xEC", (amount & 0xFFFFFFFF).to_bytes(4, "little"))

    def align_rsp(self) -> None:
        """`and rsp, -16`, for a helper reached by a jump from an arbitrary stack depth."""
        self.emit(b"\x48\x83\xE4\xF0")

    def leave_ret(self) -> None:
        """`leave; ret`: restore the caller's frame and return."""
        self.emit(b"\xC9\xC3")

    def ret(self) -> None:
        """A bare `ret`."""
        self.emit(b"\xC3")

    def hlt(self) -> None:
        """`hlt`, used only on unreachable paths such as after ExitProcess."""
        self.emit(b"\xF4")


__all__ = ["ARGUMENT_REGISTERS", "Assembler", "CONDITIONS", "EIGHT_BIT", "REGISTERS"]
