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
    "z": 0x4, "nz": 0x5, "s": 0x8, "ns": 0x9, "c": 0x2, "nc": 0x3,
}
CMOV_CONDITIONS = {
    "e": 0x40, "ne": 0x45, "l": 0x4C, "le": 0x4E, "g": 0x4F, "ge": 0x4D,
    "s": 0x48, "ns": 0x49, "c": 0x42, "nc": 0x43,
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
        if name in self.labels:
            raise ValueError(f"duplicate label {name!r}: a label may be bound once")
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
        """`jmp rel32`; a bound backward target within a byte takes `jmp rel8` (EB)."""
        if target in self.labels:
            displacement = self.labels[target] - (self.here() + 2)
            if -128 <= displacement <= 127:
                self.emit(bytes([0xEB, displacement & 0xFF]))
                return
        self.emit(b"\xE9")
        self.rel32(target)

    def jcc(self, condition: str, target: str) -> None:
        """`jcc rel32`; a bound backward target within a byte takes `jcc rel8` (70+r).

        Hot loops latch backward, so their repeated conditional branch is the two-byte
        form whenever the distance allows.
        """
        code = CONDITIONS[condition]
        if target in self.labels:
            displacement = self.labels[target] - (self.here() + 2)
            if -128 <= displacement <= 127:
                self.emit(bytes([0x70 | code, displacement & 0xFF]))
                return
        self.emit(b"\x0F", bytes([0x80 | code]))
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

    def lea_imm(self, destination: str, base: str, offset: int) -> None:
        """`lea r64, [base+disp]`, computing `base + offset` without touching flags.

        The base must be an addressable register other than `rsp`; `r12` shares rsp's
        ModRM code, so it takes the SIB form the rsp base would use.
        """
        dst, base_code = REGISTERS[destination], REGISTERS[base]
        assert base_code != REGISTERS["rsp"]
        wide = -128 <= offset <= 127
        self.emit(bytes([_rex(True, dst >> 3, 0, base_code >> 3), 0x8D,
                         *_modrm(1 if wide else 2, dst, base_code)]))
        if (base_code & 7) == 4:
            self.emit(b"\x24")  # SIB: no index, base register only
        if wide:
            self.emit(bytes([offset & 0xFF]))
        else:
            self.emit((offset & 0xFFFFFFFF).to_bytes(4, "little"))

    def lea_scale(self, destination: str, base: str, index: str, scale_log2: int,
                  displacement: int) -> None:
        """`lea r64, [base + index*2**scale_log2 + displacement]` (SIB byte).

        Neither base nor index may be `rsp`; a caller picks the base+index form when
        the factor is one more than a power of two and the index-only form otherwise.
        """
        dst, base_code, index_code = REGISTERS[destination], REGISTERS[base], REGISTERS[index]
        assert base_code != REGISTERS["rsp"] and index_code != REGISTERS["rsp"]
        wide = not (-128 <= displacement <= 127)
        self.emit(bytes([_rex(True, dst >> 3, index_code >> 3, base_code >> 3), 0x8D,
                         *_modrm(2 if wide else 1, dst, 4)]))
        self.emit(bytes([(scale_log2 << 6) | (index_code & 7) << 3 | (base_code & 7)]))
        if wide:
            self.emit((displacement & 0xFFFFFFFF).to_bytes(4, "little"))
        else:
            self.emit(bytes([displacement & 0xFF]))

    def lea_index(self, destination: str, index: str, scale_log2: int,
                  displacement: int) -> None:
        """`lea r64, [index*2**scale_log2 + displacement]` with no base register.

        The base-less SIB form always carries a 32-bit displacement; the caller must
        keep it inside int32. `rbp` as the index needs a `mov` through rax instead.
        """
        dst, index_code = REGISTERS[destination], REGISTERS[index]
        assert index_code not in (REGISTERS["rsp"], REGISTERS["rbp"])
        self.emit(bytes([_rex(True, dst >> 3, index_code >> 3, 0), 0x8D,
                         *_modrm(0, dst, 4)]))
        self.emit(bytes([(scale_log2 << 6) | (index_code & 7) << 3 | 0b101]))
        self.emit((displacement & 0xFFFFFFFF).to_bytes(4, "little"))

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

    def mov_store_rsp(self, source: str) -> None:
        """`mov [rsp], r64`: a store into the topmost pad slot."""
        src = REGISTERS[source]
        self.emit(bytes([_rex(True, src >> 3, 0, 0), 0x89, 0x04 | ((src & 7) << 3), 0x24]))

    def mov_load_rsp(self, destination: str) -> None:
        """`mov r64, [rsp]`: a load from the topmost pad slot."""
        dst = REGISTERS[destination]
        self.emit(bytes([_rex(True, dst >> 3, 0, 0), 0x8B, 0x04 | ((dst & 7) << 3), 0x24]))

    def mov_store_rsp_disp(self, offset: int, source: str) -> None:
        """`mov [rsp+disp8], r64`: a store into one of the real frame's hold slots."""
        src = REGISTERS[source]
        self.emit(bytes([_rex(True, src >> 3, 0, 0), 0x89, 0x44 | ((src & 7) << 3), 0x24]),
                  bytes([offset & 0xFF]))

    def mov_load_rsp_disp(self, offset: int, destination: str) -> None:
        """`mov r64, [rsp+disp8]`: a load from one of the real frame's hold slots."""
        dst = REGISTERS[destination]
        self.emit(bytes([_rex(True, dst >> 3, 0, 0), 0x8B, 0x44 | ((dst & 7) << 3), 0x24]),
                  bytes([offset & 0xFF]))

    def lea_rbp(self, destination: str, offset: int) -> None:
        """`lea r64, [rbp+disp]` for a frame address."""
        dst = REGISTERS[destination]
        self.emit(bytes([_rex(True, dst >> 3, 0, 0), 0x8D]), _rbp_displacement(dst, offset))

    def lea_rip(self, destination: str, target: str) -> None:
        """`lea r64, [rip+disp32]`, used for read-only data addresses."""
        dst = REGISTERS[destination]
        self.emit(bytes([_rex(True, dst >> 3, 0, 0), 0x8D, 0x00 | ((dst & 7) << 3) | 5]))
        self.rel32(target)

    def movzx_from8(self, destination: str, source: str) -> None:
        """`movzx r32, r8`: the `& 255` mask of a register's value in one instruction.

        The 32-bit destination zero-extends into the full 64-bit register, and the
        8-bit read never needs the source's sign: both come free with the opcode.
        """
        self._movzx(0xB6, destination, source)

    def movzx_from16(self, destination: str, source: str) -> None:
        """`movzx r32, r16`: the `& 65535` mask of a register's value in one instruction."""
        self._movzx(0xB7, destination, source)

    def _movzx(self, extension: int, destination: str, source: str) -> None:
        """Shared `movzx r32, r/m16|8` register form; REX appears only for r8-r15."""
        dst, src = REGISTERS[destination], REGISTERS[source]
        prefix = bytes([_rex(False, dst >> 3, 0, src >> 3)]) if (dst >= 8 or src >= 8) else b""
        self.emit(prefix, b"\x0F", bytes([extension]), bytes(_modrm(3, dst, src)))

    def xor_zero(self, reg: str) -> None:
        """`xor r32, r32`: the zero idiom, which also clears the upper 32 bits."""
        code = REGISTERS[reg]
        self.emit(bytes([_rex(False, code >> 3, 0, code >> 3), 0x31] + list(_modrm(3, code, code))))

    def binary(self, opcode: int, destination: str, source: str) -> None:
        """A 64-bit `op r/m64, r64` form: add, sub, and, or, xor and cmp share it."""
        dst, src = REGISTERS[destination], REGISTERS[source]
        self.emit(bytes([_rex(True, src >> 3, 0, dst >> 3), opcode, *_modrm(3, src, dst)]))

    def cmp_reg_reg(self, left: str, right: str) -> None:
        """`cmp r64, r64`: compare `left` against `right` (G-002 named wrapper)."""
        self.binary(0x39, left, right)

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

    def adc_imm(self, reg: str, value: int) -> None:
        """`adc r64, imm` (group-1 extension 2): adds the immediate plus carry."""
        self._group1_imm(2, reg, value)

    def cmov(self, condition: str, destination: str, source: str) -> None:
        """`cmovcc r64, r/m64`: conditional move, selected by the current flags.

        A data-flow select with no branch, no prediction and no misprediction; the
        source and destination both stay unchanged when the condition is false.
        """
        if condition not in CMOV_CONDITIONS:
            raise ValueError(f"unknown cmov condition: {condition}")
        dst, src = REGISTERS[destination], REGISTERS[source]
        self.emit(bytes([_rex(True, dst >> 3, 0, src >> 3), 0x0F, CMOV_CONDITIONS[condition],
                         *_modrm(3, dst, src)]))

    def cmp_imm(self, reg: str, value: int) -> None:
        """`cmp r64, imm`, so a comparison with a small literal needs no second register."""
        self._group1_imm(7, reg, value)

    def arith_imm(self, extension: int, reg: str, value: int) -> None:
        """`op r64, imm` by group-1 extension: add (0), or (1), and (4), sub (5), xor (6)."""
        self._group1_imm(extension, reg, value)

    def imul(self, destination: str, source: str) -> None:
        """`imul r64, r/m64`: two-operand signed multiply."""
        dst, src = REGISTERS[destination], REGISTERS[source]
        self.emit(bytes([_rex(True, dst >> 3, 0, src >> 3), 0x0F, 0xAF,
                         *_modrm(3, dst, src)]))

    def imul_high(self, reg: str) -> None:
        """One-operand `imul r64`: the signed 128-bit `rdx:rax = rax * reg`.

        The high half is what a magic-multiply division reads; no 128-bit product of
        two int64 values can overflow the 128-bit container, so this never faults.
        """
        code = REGISTERS[reg]
        self.emit(bytes([_rex(True, 0, 0, code >> 3), 0xF7,
                         0xC0 | (0x5 << 3) | (code & 7)]))

    def test(self, reg: str) -> None:
        """`test r64, r64`, setting flags from a value without changing it."""
        code = REGISTERS[reg]
        self.emit(bytes([_rex(True, code >> 3, 0, code >> 3), 0x85] + list(_modrm(3, code, code))))

    def test_imm(self, reg: str, value: int) -> None:
        """`test r/m8, imm8` (F6 /0) for a small positive mask: flags only, value kept.

        The masks are bit queries such as `x % 2**b == 0` through `and 2**b - 1` with b at
        most 7, so the byte form always reaches and the zero flag decides the branch.
        """
        if not 0 <= value <= 127:
            raise ValueError("test mask does not fit in 8 bits")
        code = REGISTERS[reg]
        self.emit(bytes([_rex(False, 0, 0, code >> 3), 0xF6,
                         0xC0 | (code & 7), value & 0xFF]))

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

    def shift_imm(self, extension: int, reg: str, count: int) -> None:
        """`shl` (4) or `sar` (7) of r64 by imm8, so a literal count needs no cl round-trip.

        A count of one uses the shorter `D1 /n` form, which every division-by-two step emits.
        """
        if not 0 <= count <= 63:
            raise ValueError("shift count does not fit in six bits")
        code = REGISTERS[reg]
        if count == 1:
            self.emit(bytes([_rex(True, 0, 0, code >> 3), 0xD1,
                             0xC0 | (extension << 3) | (code & 7)]))
            return
        self.emit(bytes([_rex(True, 0, 0, code >> 3), 0xC1,
                         0xC0 | (extension << 3) | (code & 7), count & 0xFF]))

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
