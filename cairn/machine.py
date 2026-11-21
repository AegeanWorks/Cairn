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

# The two SSE2 registers the array vectorizer uses: xmm0 is the value lane,
# xmm1 the broadcast-addend lane. SSE2 is part of the x86-64 baseline.
XMM_REGISTERS = {"xmm0": 0, "xmm1": 1}

ARGUMENT_REGISTERS = ("rcx", "rdx", "r8", "r9")

# group-1 immediate extensions whose result sets ZF = (register == 0)
ZF_IMM_EXTENSIONS = (0, 1, 4, 5, 6)


CONDITIONS = {
    "e": 0x4, "ne": 0x5, "l": 0xC, "le": 0xE, "g": 0xF, "ge": 0xD,
    "z": 0x4, "nz": 0x5, "s": 0x8, "ns": 0x9, "c": 0x2, "nc": 0x3,
    "b": 0x2, "ae": 0x3, "a": 0x7, "be": 0x6,
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
    # ZF after the last emitted instruction: a register name when ZF compares that
    # register's CURRENT value against zero, None when the flags say nothing usable.
    zf_status: Optional[str] = None

    def here(self) -> int:
        """The current offset in the code buffer."""
        return len(self.code)

    def emit(self, *parts: bytes) -> None:
        """Append raw bytes; any such append invalidates the flag summary."""
        self.zf_status = None
        for part in parts:
            self.code.extend(part)

    def label(self, name: str) -> None:
        """Bind `name` to the current offset; a duplicate name is a compiler bug."""
        if name in self.labels:
            raise ValueError(f"duplicate label {name!r}: a label may be bound once")
        self.zf_status = None
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

    def binary_store_rbp(self, opcode: int, offset: int, source: str) -> None:
        """`op [rbp+disp], r64` for an in-place frame update (add, sub, and, or, xor).

        The memory form sets ZF from the memory operand, which no register names, so the
        flag summary stays cleared — a following zero-branch keeps its own compare.
        """
        src = REGISTERS[source]
        self.emit(bytes([_rex(True, src >> 3, 0, 0), opcode]), _rbp_displacement(src, offset))

    def group1_store_rbp_imm(self, extension: int, offset: int, value: int) -> None:
        """`op [rbp+disp], imm8/imm32` sign-extended, in the register form's group-1 style."""
        if not -0x80000000 <= value <= 0x7FFFFFFF:
            raise ValueError("immediate does not fit in 32 signed bits")
        if -128 <= value <= 127:
            self.emit(bytes([_rex(True, 0, 0, 0), 0x83]), _rbp_displacement(extension, offset),
                      bytes([value & 0xFF]))
        else:
            self.emit(bytes([_rex(True, 0, 0, 0), 0x81]), _rbp_displacement(extension, offset),
                      (value & 0xFFFFFFFF).to_bytes(4, "little"))

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

    def mov_load_rip(self, destination: str, target: str) -> None:
        """`mov r64, [rip+disp32]`: read a qword of state without an address register."""
        dst = REGISTERS[destination]
        self.emit(bytes([_rex(True, dst >> 3, 0, 0), 0x8B, 0x00 | ((dst & 7) << 3) | 5]))
        self.rel32(target)

    def mov_store_rip(self, source: str, target: str) -> None:
        """`mov [rip+disp32], r64`: write a qword of static state, no address register."""
        src = REGISTERS[source]
        self.emit(bytes([_rex(True, src >> 3, 0, 0), 0x89, 0x00 | ((src & 7) << 3) | 5]))
        self.rel32(target)

    def cmp_reg_rip(self, left: str, target: str) -> None:
        """`cmp r64, [rip+disp32]`: compare a register with a qword of static state.

        The displacement ends the instruction, so the section fixup's usual
        resolve-from-instruction-end rule holds unchanged.
        """
        dst = REGISTERS[left]
        self.emit(bytes([_rex(True, dst >> 3, 0, 0), 0x3B, 0x00 | ((dst & 7) << 3) | 5]))
        self.rel32(target)

    def mov_load(self, destination: str, base: str) -> None:
        """`mov r64, [base]`: read a qword through a pointer register."""
        dst, base_code = REGISTERS[destination], REGISTERS[base]
        assert base_code != REGISTERS["rsp"]
        self.emit(bytes([_rex(True, dst >> 3, 0, base_code >> 3), 0x8B,
                         *_modrm(0, dst, base_code)]))

    def mov_store_reg(self, base: str, source: str) -> None:
        """`mov [base], r64`: write a qword through a pointer register.

        Array element stores land through an address register: the frame
        displacement of a dynamic index is not a compile-time constant, so the
        address forms in a scratch register and this writes the element.
        """
        src, base_code = REGISTERS[source], REGISTERS[base]
        assert base_code != REGISTERS["rsp"]
        self.emit(bytes([_rex(True, src >> 3, 0, base_code >> 3), 0x89,
                         *_modrm(0, src, base_code)]))

    def binary_store_reg(self, opcode: int, base: str, source: str) -> None:
        """`op [base], r64`: one in-place ALU update through a pointer register.

        The fused array element update (`a[i] = a[i] op e`) needs exactly this
        shape: the element address sits in `base`, the other operand in
        `source`, and the memory-operand form reads, computes and stores in
        one instruction.
        """
        src, base_code = REGISTERS[source], REGISTERS[base]
        assert base_code != REGISTERS["rsp"]
        self.emit(bytes([_rex(True, src >> 3, 0, base_code >> 3), opcode,
                         *_modrm(0, src, base_code)]))

    def group1_store_reg_imm(self, extension: int, base: str, value: int) -> None:
        """`op [base], imm8/imm32` sign-extended through a pointer register.

        The fused array element update with a literal operand needs no temporary:
        the imm8 form is two bytes shorter than materialising the constant in a
        register and is the shape the baselines emit for `a[i] += 1`.
        """
        if not -0x80000000 <= value <= 0x7FFFFFFF:
            raise ValueError("immediate does not fit in 32 signed bits")
        base_code = REGISTERS[base]
        assert base_code != REGISTERS["rsp"]
        rex = _rex(True, 0, 0, base_code >> 3)
        if -128 <= value <= 127:
            self.emit(bytes([rex, 0x83, *_modrm(0, extension, base_code)]),
                      bytes([value & 0xFF]))
        else:
            self.emit(bytes([rex, 0x81, *_modrm(0, extension, base_code)]),
                      (value & 0xFFFFFFFF).to_bytes(4, "little"))

    def mov_load_indexed(self, destination: str, base: str, index: str,
                         scale_log2: int, displacement: int) -> None:
        """`mov r64, [base + index*2**scale_log2 + disp]`: a frame array element load.

        Element `i` of a frame array is base-displacement plus `8i`, so one SIB
        load reads it with no address register and no `lea`.
        """
        self._indexed_memory(0x8B, REGISTERS[destination], base, index,
                             scale_log2, displacement)

    def binary_store_indexed(self, opcode: int, base: str, index: str,
                             scale_log2: int, displacement: int, source: str) -> None:
        """`op [base + index*scale + disp], r64`: a fused element update, no address register."""
        self._indexed_memory(opcode, REGISTERS[source], base, index,
                             scale_log2, displacement)

    def mov_store_indexed(self, base: str, index: str, scale_log2: int,
                          displacement: int, source: str) -> None:
        """`mov [base + index*scale + disp], r64`: a frame array element store in one instruction."""
        self._indexed_memory(0x89, REGISTERS[source], base, index,
                             scale_log2, displacement)

    def mov_store_indexed_imm(self, base: str, index: str, scale_log2: int,
                              displacement: int, value: int) -> None:
        """`mov qword [base + index*scale + disp], imm32`: a literal element store."""
        if not -0x80000000 <= value <= 0x7FFFFFFF:
            raise ValueError("immediate does not fit in 32 signed bits")
        self._indexed_memory(0xC7, 0, base, index, scale_log2, displacement)
        self.emit((value & 0xFFFFFFFF).to_bytes(4, "little"))

    def group1_store_indexed_imm(self, extension: int, base: str, index: str,
                                 scale_log2: int, displacement: int, value: int) -> None:
        """`op [base + index*scale + disp], imm8/imm32`: a fused element update by a literal."""
        if not -0x80000000 <= value <= 0x7FFFFFFF:
            raise ValueError("immediate does not fit in 32 signed bits")
        small = -128 <= value <= 127
        self._indexed_memory(0x83 if small else 0x81, extension, base, index,
                             scale_log2, displacement)
        if small:
            self.emit(bytes([value & 0xFF]))
        else:
            self.emit((value & 0xFFFFFFFF).to_bytes(4, "little"))

    def _indexed_memory(self, opcode: int, reg: int, base: str, index: str,
                        scale_log2: int, displacement: int) -> None:
        """One 64-bit instruction with `[base + index*2**scale_log2 + disp]` as its operand.

        `reg` is the ModRM.reg field: a register code for the mov/ALU forms, or a
        group-1 opcode extension (REX.R stays clear). The base may not be `rsp`,
        and `rbp` takes the forced-displacement SIB form, so mod 00 never
        applies. `r12` is a legal index through REX.X, exactly as in `lea_scale`.
        """
        base_code, index_code = REGISTERS[base], REGISTERS[index]
        assert base_code != REGISTERS["rsp"] and index_code != REGISTERS["rsp"]
        wide = not (-128 <= displacement <= 127)
        self.emit(bytes([_rex(True, reg >> 3, index_code >> 3, base_code >> 3), opcode,
                         *_modrm(2 if wide else 1, reg, 4)]),
                  bytes([(scale_log2 << 6) | (index_code & 7) << 3 | (base_code & 7)]))
        if wide:
            self.emit((displacement & 0xFFFFFFFF).to_bytes(4, "little"))
        else:
            self.emit(bytes([displacement & 0xFF]))

    def movzx_load8(self, destination: str, base: str) -> None:
        """`movzx r32, [base]`: read the byte at a pointer register, zero-extended."""
        dst, base_code = REGISTERS[destination], REGISTERS[base]
        assert base_code != REGISTERS["rsp"]
        prefix = bytes([_rex(False, dst >> 3, 0, base_code >> 3)]) \
            if (dst >= 8 or base_code >= 8) else b""
        self.emit(prefix, b"\x0F\xB6", bytes(_modrm(0, dst, base_code)))

    def movzx_load8_sib(self, destination: str, base: str, index: str) -> None:
        """`movzx r32, [base + index]`: one byte load off two pointer registers.

        The SIB byte covers base+index*1; a pointer and its index both living in
        homes folds the whole address into one load (ADR-079). Neither may be
        `rsp`. A base whose low three bits are 5 (`rbp`, `r13`) must take the
        disp8=0 form: with mod 00 that bit pattern means "no base, disp32
        follows", and REX.B does not change it — the short form silently decodes
        as `[index + <the next four bytes>]` and faults (ADR-079).
        """
        dst = REGISTERS[destination]
        base_code, index_code = REGISTERS[base], REGISTERS[index]
        assert base_code != REGISTERS["rsp"]
        assert index_code != REGISTERS["rsp"]
        forced = base_code & 7 == 5
        prefix = bytes([_rex(False, dst >> 3, index_code >> 3, base_code >> 3)]) \
            if (dst >= 8 or index_code >= 8 or base_code >= 8) else b""
        self.emit(prefix, b"\x0F\xB6", bytes(_modrm(1 if forced else 0, dst, 4)),
                  bytes([(index_code & 7) << 3 | (base_code & 7)]))
        if forced:
            self.emit(b"\x00")

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

    ZF_SETTERS = (0x01, 0x09, 0x21, 0x29, 0x31)  # add, or, and, sub, xor: ZF = (result == 0)

    def binary(self, opcode: int, destination: str, source: str) -> None:
        """A 64-bit `op r/m64, r64` form: add, sub, and, or, xor and cmp share it."""
        dst, src = REGISTERS[destination], REGISTERS[source]
        self.emit(bytes([_rex(True, src >> 3, 0, dst >> 3), opcode, *_modrm(3, src, dst)]))
        if opcode in self.ZF_SETTERS:
            self.zf_status = destination

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
        if extension in ZF_IMM_EXTENSIONS:
            self.zf_status = reg

    def imul(self, destination: str, source: str) -> None:
        """`imul r64, r/m64`: two-operand signed multiply."""
        dst, src = REGISTERS[destination], REGISTERS[source]
        self.emit(bytes([_rex(True, dst >> 3, 0, src >> 3), 0x0F, 0xAF,
                         *_modrm(3, dst, src)]))

    def imul_imm(self, destination: str, source: str, literal: int) -> None:
        """`imul r64, r/m64, imm`: three-operand signed multiply by a constant.

        The destination register is written; the source register reads unchanged,
        so `x * 31` where x keeps a live home pays one instruction instead of a
        ten-byte constant load feeding the two-operand form. Overflow wraps in
        the low 64 bits exactly like every other multiply in this surface; ZF is
        undefined after imul, so a following zero-branch keeps its own compare.
        """
        dst, src = REGISTERS[destination], REGISTERS[source]
        if -2 ** 31 <= literal < 2 ** 31:
            if -128 <= literal < 128:
                self.emit(bytes([_rex(True, dst >> 3, 0, src >> 3), 0x6B,
                                 *_modrm(3, dst, src), literal & 0xFF]))
            else:
                self.emit(bytes([_rex(True, dst >> 3, 0, src >> 3), 0x69,
                                 *_modrm(3, dst, src)]
                                + list(literal.to_bytes(4, "little", signed=True))))
            return
        raise ValueError("literal does not fit the three-operand imul form")

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
        self.zf_status = reg

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

    def divide32(self, reg: str) -> None:
        """`div r/m32`: unsigned 32-bit division of edx:eax, quotient eax, remainder edx.

        A 32-bit divide is much cheaper than the 64-bit `idiv` (ADR-085 measured a
        purpose-built trial-division twin at -29 percent cycles), so the compiler
        takes this form behind a guard that proves both operands fit: the caller
        sets edx to zero and rax to the zero-extended dividend.
        """
        code = REGISTERS[reg]
        prefix = bytes([_rex(False, 0, 0, code >> 3)]) if code >= 8 else b""
        self.emit(prefix, bytes([0xF7, 0xC0 | (0x6 << 3) | (code & 7)]))

    def unary(self, extension: int, reg: str) -> None:
        """`F7 /n` forms: not (2), neg (3)."""
        code = REGISTERS[reg]
        self.emit(bytes([_rex(True, 0, 0, code >> 3), 0xF7, 0xC0 | (extension << 3) | (code & 7)]))

    def shift_cl(self, extension: int) -> None:
        """`shl` (4), shr (5) or sar (7) of rax by cl."""
        self.emit(b"\x48\xD3", bytes([0xC0 | (extension << 3)]))

    def shift_imm(self, extension: int, reg: str, count: int) -> None:
        """`shl` (4), `shr` (5) or `sar` (7) of r64 by imm8, so a literal count needs no cl round-trip.

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

    NOP_PATTERNS = (
        (9, b"\x66\x0F\x1F\x84\x00\x00\x00\x00\x00"),
        (8, b"\x0F\x1F\x84\x00\x00\x00\x00\x00"),
        (7, b"\x0F\x1F\x80\x00\x00\x00\x00"),
        (6, b"\x66\x0F\x1F\x44\x00\x00"),
        (5, b"\x0F\x1F\x44\x00\x00"),
        (4, b"\x0F\x1F\x40\x00"),
        (3, b"\x0F\x1F\x00"),
        (2, b"\x66\x90"),
        (1, b"\x90"),
    )

    def nop_pad(self, size: int) -> None:
        """`size` bytes of padding as canonical multi-byte nops.

        A pad of single-byte nops rides the fall-through path straight into
        hot loop bodies, and this core spends a decode slot on every one-byte
        nop, so the largest nop form that fits is always preferred.
        """
        while size > 0:
            for length, pattern in self.NOP_PATTERNS:
                if length <= size:
                    self.emit(pattern)
                    size -= length
                    break

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

    def movq_from_reg(self, destination: str, source: str) -> None:
        """`movq xmm, r64` (66 REX.W 0F 6E /r), the GPR-to-XMM form gcc emits."""
        dst, src = XMM_REGISTERS[destination], REGISTERS[source]
        rex = _rex(True, 0, 0, src >> 3)
        self.emit(bytes([0x66, rex, 0x0F, 0x6E, 0xC0 | (dst << 3) | (src & 7)]))

    def punpcklqdq(self, destination: str, source: str) -> None:
        """`punpcklqdq xmm, xmm` (66 0F 6C): broadcast the low qword to both lanes."""
        dst, src = XMM_REGISTERS[destination], XMM_REGISTERS[source]
        self.emit(b"\x66\x0F\x6C", bytes([0xC0 | (dst << 3) | src]))

    def paddq_reg(self, destination: str, source: str) -> None:
        """`paddq xmm, xmm` (66 0F D4 /r): two wrapping i64 adds in one instruction."""
        dst, src = XMM_REGISTERS[destination], XMM_REGISTERS[source]
        self.emit(b"\x66\x0F\xD4", bytes([0xC0 | (dst << 3) | src]))

    def movdqu_load_indexed(self, destination: str, base: str, index: str,
                            scale_log2: int, offset: int) -> None:
        """`movdqu xmm, [base + index*scale + disp32]` (F3 0F 6F /r), unaligned 16B."""
        dst = XMM_REGISTERS[destination]
        base_code, index_code = REGISTERS[base], REGISTERS[index]
        rex = _rex(False, 0, (index_code >> 3) & 1, (base_code >> 3) & 1)
        modrm = 0x80 | (dst << 3) | 4
        sib = (scale_log2 << 6) | ((index_code & 7) << 3) | (base_code & 7)
        self.emit(bytes([0xF3, rex, 0x0F, 0x6F, modrm, sib]),
                  (offset & 0xFFFFFFFF).to_bytes(4, "little"))

    def movdqu_store_indexed(self, base: str, index: str, scale_log2: int,
                             offset: int, source: str) -> None:
        """`movdqu [base + index*scale + disp32], xmm` (F3 0F 7F /r), unaligned 16B."""
        src = XMM_REGISTERS[source]
        base_code, index_code = REGISTERS[base], REGISTERS[index]
        rex = _rex(False, 0, (index_code >> 3) & 1, (base_code >> 3) & 1)
        modrm = 0x80 | (src << 3) | 4
        sib = (scale_log2 << 6) | ((index_code & 7) << 3) | (base_code & 7)
        self.emit(bytes([0xF3, rex, 0x0F, 0x7F, modrm, sib]),
                  (offset & 0xFFFFFFFF).to_bytes(4, "little"))

    def popcnt(self, destination: str, source: str) -> None:
        """`popcnt r64, r/m64` (F3 REX.W 0F B8 /r): one instruction set-bit count.

        POPCNT is part of the ABM extension present on every modern x86-64; the
        helper that uses it documents the requirement (ADR-102).
        """
        dst, src = REGISTERS[destination], REGISTERS[source]
        rex = _rex(True, dst >> 3, 0, src >> 3)
        self.emit(bytes([0xF3, rex, 0x0F, 0xB8, 0xC0 | ((dst & 7) << 3) | (src & 7)]))

    def lzcnt(self, destination: str, source: str) -> None:
        """`lzcnt r64, r/m64` (F3 REX.W 0F BD /r): one leading-zero count.

        LZCNT is part of ABM, like POPCNT: it returns 64 for a zero input, unlike
        `bsr`, and is present on every x86-64 CPU since 2008 (ADR-108).
        """
        dst, src = REGISTERS[destination], REGISTERS[source]
        rex = _rex(True, dst >> 3, 0, src >> 3)
        self.emit(bytes([0xF3, rex, 0x0F, 0xBD, 0xC0 | ((dst & 7) << 3) | (src & 7)]))

    def tzcnt(self, destination: str, source: str) -> None:
        """`tzcnt r64, r/m64` (F3 REX.W 0F BC /r): one trailing-zero count.

        TZCNT returns 64 for a zero input, unlike `bsf`, and is part of BMI1,
        present on every x86-64 CPU since 2013 (ADR-110). The call site lowers
        inline, so no runtime helper is needed.
        """
        dst, src = REGISTERS[destination], REGISTERS[source]
        rex = _rex(True, dst >> 3, 0, src >> 3)
        self.emit(bytes([0xF3, rex, 0x0F, 0xBC, 0xC0 | ((dst & 7) << 3) | (src & 7)]))

    def hlt(self) -> None:
        """`hlt`, used only on unreachable paths such as after ExitProcess."""
        self.emit(b"\xF4")


__all__ = ["ARGUMENT_REGISTERS", "Assembler", "CONDITIONS", "EIGHT_BIT",
           "REGISTERS", "XMM_REGISTERS"]
