"""Emitter tests: the exact bytes of the encodings the generator relies on.

The backend is a fixed instruction subset. Pinning the bytes here means a wrong ModRM or a
missing REX bit fails in a unit test instead of in a produced executable.
"""

import unittest

from cairn.machine import Assembler



class FrameInPlaceEncodingTest(unittest.TestCase):
    """A frame slot's in-place update encodes as one memory-operand instruction."""

    def code(self, build: object) -> bytes:
        assembler = Assembler()
        build(assembler)
        return bytes(assembler.code)

    def test_frame_slot_binary_in_place(self) -> None:
        self.assertEqual(self.code(lambda a: a.binary_store_rbp(0x01, -8, "rax")),
                         b"\x48\x01\x45\xf8")
        self.assertEqual(self.code(lambda a: a.binary_store_rbp(0x29, -8, "r8")),
                         b"\x4c\x29\x45\xf8")

    def test_frame_slot_binary_in_place_beyond_disp8(self) -> None:
        self.assertEqual(self.code(lambda a: a.binary_store_rbp(0x01, -200, "rbx")),
                         b"\x48\x01\x9d\x38\xff\xff\xff")

    def test_frame_slot_group1_imm_in_place(self) -> None:
        self.assertEqual(self.code(lambda a: a.group1_store_rbp_imm(0, -8, 1)),
                         b"\x48\x83\x45\xf8\x01")
        self.assertEqual(self.code(lambda a: a.group1_store_rbp_imm(5, -16, -1)),
                         b"\x48\x83\x6d\xf0\xff")
        self.assertEqual(self.code(lambda a: a.group1_store_rbp_imm(0, -200, 1000)),
                         b"\x48\x81\x85\x38\xff\xff\xff\xe8\x03\x00\x00")

    def test_frame_slot_group1_imm_rejects_wide_literals(self) -> None:
        with self.assertRaises(ValueError):
            self.code(lambda a: a.group1_store_rbp_imm(0, -8, 1 << 32))

class IndexedElementEncodingTest(unittest.TestCase):
    """An indexed element access is one SIB memory operand over `rbp` and the index."""

    def code(self, build) -> bytes:
        assembler = Assembler()
        build(assembler)
        return bytes(assembler.code)

    def test_indexed_load_with_disp8(self) -> None:
        self.assertEqual(self.code(lambda a: a.mov_load_indexed("rax", "rbp", "rbx", 3, -64)),
                         b"\x48\x8b\x44\xdd\xc0")

    def test_indexed_load_with_r12_as_index_and_disp32(self) -> None:
        self.assertEqual(self.code(lambda a: a.mov_load_indexed("rax", "rbp", "r12", 3, -524288)),
                         b"\x4a\x8b\x84\xe5\x00\x00\xf8\xff")

    def test_indexed_binary_update(self) -> None:
        self.assertEqual(
            self.code(lambda a: a.binary_store_indexed(0x01, "rbp", "rbx", 3, -64, "r12")),
            b"\x4c\x01\x64\xdd\xc0")

    def test_indexed_store_with_r12_source(self) -> None:
        self.assertEqual(
            self.code(lambda a: a.mov_store_indexed("rbp", "r12", 3, -524288, "r12")),
            b"\x4e\x89\xa4\xe5\x00\x00\xf8\xff")

    def test_indexed_immediate_store(self) -> None:
        self.assertEqual(
            self.code(lambda a: a.mov_store_indexed_imm("rbp", "rbx", 3, -64, 1)),
            b"\x48\xc7\x44\xdd\xc0\x01\x00\x00\x00")

    def test_indexed_immediate_store_rejects_wide_literals(self) -> None:
        with self.assertRaises(ValueError):
            self.code(lambda a: a.mov_store_indexed_imm("rbp", "rbx", 3, -64, 1 << 32))

    def test_indexed_group1_immediate_update(self) -> None:
        self.assertEqual(
            self.code(lambda a: a.group1_store_indexed_imm(0, "rbp", "rbx", 3, -64, 7)),
            b"\x48\x83\x44\xdd\xc0\x07")
        self.assertEqual(
            self.code(lambda a: a.group1_store_indexed_imm(5, "rbp", "rbx", 3, -64, 1000)),
            b"\x48\x81\x6c\xdd\xc0\xe8\x03\x00\x00")

    def test_indexed_group1_rejects_wide_literals(self) -> None:
        with self.assertRaises(ValueError):
            self.code(lambda a: a.group1_store_indexed_imm(0, "rbp", "rbx", 3, -64, 1 << 32))

    def test_indexed_memory_rejects_rsp(self) -> None:
        with self.assertRaises(AssertionError):
            self.code(lambda a: a.mov_load_indexed("rax", "rbp", "rsp", 3, -64))
        with self.assertRaises(AssertionError):
            self.code(lambda a: a.mov_load_indexed("rax", "rsp", "rbx", 3, -64))


class SIBByteLoadTest(unittest.TestCase):
    """`movzx r32,[base+index]` needs the disp8 form for an rbp/r13 base.

    With mod 00, a base whose low bits are 5 means "no base, disp32 follows", and
    REX.B does not override it, so the short form silently decodes as an indexed
    load off four bytes of the next instruction (ADR-079).
    """

    def code(self, base: str, index: str) -> bytes:
        assembler = Assembler()
        assembler.movzx_load8_sib("rax", base, index)
        return bytes(assembler.code)

    def test_a_plain_base_takes_the_five_byte_form(self) -> None:
        self.assertEqual(self.code("r10", "rax"), b"\x41\x0f\xb6\x04\x02")
        self.assertEqual(self.code("r12", "r13"), b"\x43\x0f\xb6\x04\x2c")

    def test_an_r13_base_takes_the_disp8_zero_form(self) -> None:
        self.assertEqual(self.code("r13", "r14"), b"\x43\x0f\xb6\x44\x35\x00")

    def test_an_rbp_base_takes_the_disp8_zero_form(self) -> None:
        self.assertEqual(self.code("rbp", "rax"), b"\x0f\xb6\x44\x05\x00")

    def test_rsp_is_refused_on_either_side(self) -> None:
        with self.assertRaises(AssertionError):
            self.code("rsp", "rax")
        with self.assertRaises(AssertionError):
            self.code("rax", "rsp")


class RipStateEncodingTest(unittest.TestCase):
    """`cmp r64,[rip]` and `mov [rip],r64` read and write static state in place."""

    def code(self, build) -> bytes:
        assembler = Assembler()
        build(assembler)
        return bytes(assembler.code)

    def test_cmp_register_with_rip_state(self) -> None:
        assembler = Assembler()
        assembler.cmp_reg_rip("rax", "state")
        self.assertEqual(bytes(assembler.code), b"\x48\x3b\x05\x00\x00\x00\x00")
        self.assertEqual(assembler.fixups, [(3, "state")])

    def test_cmp_extended_register_with_rip_state_takes_rex_r(self) -> None:
        assembler = Assembler()
        assembler.cmp_reg_rip("r14", "state")
        self.assertEqual(bytes(assembler.code), b"\x4c\x3b\x35\x00\x00\x00\x00")
        self.assertEqual(assembler.fixups, [(3, "state")])

    def test_store_of_rip_state(self) -> None:
        assembler = Assembler()
        assembler.mov_store_rip("r14", "state")
        self.assertEqual(bytes(assembler.code), b"\x4c\x89\x35\x00\x00\x00\x00")
        self.assertEqual(assembler.fixups, [(3, "state")])


class NarrowDivisionEncodingTest(unittest.TestCase):
    """`div r/m32` is one F7 /6 form, with a REX prefix only for an extended register."""

    def code(self, register: str) -> bytes:
        assembler = Assembler()
        assembler.divide32(register)
        return bytes(assembler.code)

    def test_plain_and_extended_forms(self) -> None:
        self.assertEqual(self.code("rsi"), b"\xf7\xf6")
        self.assertEqual(self.code("rcx"), b"\xf7\xf1")
        self.assertEqual(self.code("r12"), b"\x41\xf7\xf4")


class EncodingTest(unittest.TestCase):
    """Each test names one encoding and its expected bytes."""

    def code(self, build) -> bytes:
        """Run a builder function against a fresh assembler and return its bytes."""
        assembler = Assembler()
        build(assembler)
        return bytes(assembler.code)

    def test_push_and_pop_extended_registers_mask_the_register_bits(self) -> None:
        self.assertEqual(self.code(lambda a: a.push("r8")), b"\x41\x50")
        self.assertEqual(self.code(lambda a: a.pop("r8")), b"\x41\x58")
        self.assertEqual(self.code(lambda a: a.push("rax")), b"\x50")

    def test_integer_literal_is_a_full_imm64_move(self) -> None:
        self.assertEqual(self.code(lambda a: a.mov_imm64("rax", 1)),
                         b"\x48\xb8\x01\x00\x00\x00\x00\x00\x00\x00")

    def test_frame_slot_load_and_store(self) -> None:
        self.assertEqual(self.code(lambda a: a.mov_load_rbp("rax", -8)), b"\x48\x8b\x45\xf8")
        self.assertEqual(self.code(lambda a: a.mov_store_rbp(-8, "r8")), b"\x4c\x89\x45\xf8")

    def test_frame_slot_beyond_disp8_uses_a_four_byte_displacement(self) -> None:
        self.assertEqual(self.code(lambda a: a.mov_load_rbp("rax", -200)),
                         b"\x48\x8b\x85\x38\xff\xff\xff")
        self.assertEqual(self.code(lambda a: a.mov_store_rbp(-400, "rax")),
                         b"\x48\x89\x85\x70\xfe\xff\xff")

    def test_signed_division_of_r10(self) -> None:
        self.assertEqual(self.code(lambda a: a.divide("r10", signed=True)), b"\x49\xf7\xfa")

    def test_arithmetic_shift_uses_cl(self) -> None:
        self.assertEqual(self.code(lambda a: a.shift_cl(7)), b"\x48\xd3\xf8")

    def test_lea_with_small_offset_uses_disp8(self) -> None:
        self.assertEqual(self.code(lambda a: a.lea_imm("rcx", "rbx", -1)),
                         b"\x48\x8d\x4b\xff")

    def test_lea_from_r12_takes_the_sib_form(self) -> None:
        self.assertEqual(self.code(lambda a: a.lea_imm("rax", "r12", 7)),
                         b"\x49\x8d\x44\x24\x07")

    def test_lea_with_wide_offset_uses_disp32(self) -> None:
        self.assertEqual(self.code(lambda a: a.lea_imm("rcx", "rbx", 4096)),
                         b"\x48\x8d\x8b\x00\x10\x00\x00")

    def test_shift_by_immediate_needs_no_count_register(self) -> None:
        self.assertEqual(self.code(lambda a: a.shift_imm(4, "rax", 13)), b"\x48\xc1\xe0\x0d")
        self.assertEqual(self.code(lambda a: a.shift_imm(7, "rax", 7)), b"\x48\xc1\xf8\x07")
        self.assertEqual(self.code(lambda a: a.shift_imm(4, "r12", 1)), b"\x49\xd1\xe4")
        with self.assertRaises(ValueError):
            self.code(lambda a: a.shift_imm(4, "rax", 64))
        with self.assertRaises(ValueError):
            self.code(lambda a: a.shift_imm(4, "rax", -1))

    def test_comparison_result_is_zero_extended_into_rax(self) -> None:
        self.assertEqual(self.code(lambda a: a.setcc("l")), b"\x0f\x9c\xc0\x48\x0f\xb6\xc0")

    def test_shell_alignment_is_one_instruction(self) -> None:
        self.assertEqual(self.code(lambda a: a.align_rsp()), b"\x48\x83\xe4\xf0")

    def test_register_immediate_add_uses_the_short_form_when_it_fits(self) -> None:
        self.assertEqual(self.code(lambda a: a.add_imm("r12", 1)), b"\x49\x83\xc4\x01")
        self.assertEqual(self.code(lambda a: a.add_imm("rbx", -1)), b"\x48\x83\xc3\xff")

    def test_register_immediate_sub_and_cmp_use_the_group_one_encoding(self) -> None:
        self.assertEqual(self.code(lambda a: a.sub_imm("r12", 1)), b"\x49\x83\xec\x01")
        self.assertEqual(self.code(lambda a: a.cmp_imm("rax", 1000)),
                         b"\x48\x81\xf8\xe8\x03\x00\x00")

    def test_relative_call_records_a_fixup_at_the_displacement(self) -> None:
        assembler = Assembler()
        assembler.call("target")
        self.assertEqual(bytes(assembler.code), b"\xe8\x00\x00\x00\x00")
        self.assertEqual(assembler.fixups, [(1, "target")])

    def test_label_records_the_current_offset(self) -> None:
        assembler = Assembler()
        assembler.emit(b"\x90")
        assembler.label("here")
        assembler.ret()
        self.assertEqual(assembler.labels["here"], 1)
        self.assertEqual(bytes(assembler.code), b"\x90\xc3")

    def test_duplicate_label_is_a_compiler_bug(self) -> None:
        assembler = Assembler()
        assembler.label("dup")
        with self.assertRaises(ValueError):
            assembler.label("dup")

    def test_cmp_reg_reg_matches_the_cmp_encoding(self) -> None:
        self.assertEqual(self.code(lambda a: a.cmp_reg_reg("rcx", "rax")),
                         self.code(lambda a: a.binary(0x39, "rcx", "rax")))


    def test_movzx_word_selects_the_extended_register_bits(self) -> None:
        self.assertEqual(self.code(lambda a: a.movzx_from16("r8", "r12")),
                         b"\x45\x0f\xb7\xc4")
        self.assertEqual(self.code(lambda a: a.movzx_from16("rax", "rcx")),
                         b"\x0f\xb7\xc1")

    def test_movzx_byte_selects_the_extended_register_bits(self) -> None:
        self.assertEqual(self.code(lambda a: a.movzx_from8("rax", "rbx")),
                         b"\x0f\xb6\xc3")
        self.assertEqual(self.code(lambda a: a.movzx_from8("r15", "r9")),
                         b"\x45\x0f\xb6\xf9")



class ImulImmediateEncodingTest(unittest.TestCase):
    """`x * constant` lowers as one three-operand imul, source kept intact."""

    def code(self, build: object) -> bytes:
        assembler = Assembler()
        build(assembler)
        return bytes(assembler.code)

    def test_imul_imm8_selects_the_byte_form(self) -> None:
        self.assertEqual(self.code(lambda a: a.imul_imm("r11", "rbx", 31)),
                         b"\x4c\x6b\xdb\x1f")
        self.assertEqual(self.code(lambda a: a.imul_imm("rax", "r12", 37)),
                         b"\x49\x6b\xc4\x25")

    def test_imul_imm32_selects_the_long_form(self) -> None:
        self.assertEqual(self.code(lambda a: a.imul_imm("rax", "rbx", 171)),
                         b"\x48\x69\xc3\xab\x00\x00\x00")
        self.assertEqual(self.code(lambda a: a.imul_imm("rax", "rbx", -100000)),
                         b"\x48\x69\xc3\x60\x79\xfe\xff")

    def test_imul_imm_rejects_values_beyond_imm32(self) -> None:
        with self.assertRaises(ValueError):
            self.code(lambda a: a.imul_imm("rax", "rbx", 2 ** 31))
        with self.assertRaises(ValueError):
            self.code(lambda a: a.imul_imm("rax", "rbx", -2 ** 31 - 1))



class NopPadEncodingTest(unittest.TestCase):
    """Loop-head padding composes canonical multi-byte no-ops, not nop trains.

    A pad of one-byte nops rides the fall-through path straight into hot
    loop bodies, and this core spends real decode slots on every single
    byte nop. The canonical table is the standard nop set: one `90`,
    `66 90`, then the `0F 1F` family generously addressed, largest first.
    """

    def code(self, build) -> bytes:
        assembler = Assembler()
        build(assembler)
        return bytes(assembler.code)

    NINE = bytes([0x66, 0x0F, 0x1F, 0x84, 0x00, 0x00, 0x00, 0x00, 0x00])
    SIX = bytes([0x66, 0x0F, 0x1F, 0x44, 0x00, 0x00])

    def test_three_bytes_use_the_compact_nopl_form(self) -> None:
        self.assertEqual(self.code(lambda a: a.nop_pad(3)),
                         bytes([0x0F, 0x1F, 0x00]))

    def test_two_bytes_use_xchg_arrays_swap(self) -> None:
        self.assertEqual(self.code(lambda a: a.nop_pad(2)),
                         bytes([0x66, 0x90]))

    def test_fifteen_bytes_compose_from_nine_and_six(self) -> None:
        body = self.code(lambda a: a.nop_pad(15))
        self.assertEqual(len(body), 15)
        self.assertEqual(body, self.NINE + self.SIX)

    def test_zero_bytes_pad_nothing(self) -> None:
        self.assertEqual(self.code(lambda a: a.nop_pad(0)), b"")

    def test_every_size_rounds_to_its_requested_length(self) -> None:
        for size in range(1, 16):
            body = self.code(lambda a, s=size: a.nop_pad(s))
            self.assertEqual(len(body), size, f'size {size}')


class IndirectStoreEncodingTest(unittest.TestCase):
    """Dynamic array stores write through a pointer register: `mov [base], r64`.

    The fill loop and the park-reload store sequence are exercised end to
    end; these tests pin the bytes so a wrong REX or ModRM bit fails here
    instead of corrupting a stack frame inside a produced executable.
    """

    def code(self, build) -> bytes:
        assembler = Assembler()
        build(assembler)
        return bytes(assembler.code)

    def test_pointer_store_encodes_the_base_register_directly(self) -> None:
        self.assertEqual(self.code(lambda a: a.mov_store_reg("rcx", "rax")),
                         b"\x48\x89\x01")

    def test_pointer_store_masks_extended_register_bits_into_rex(self) -> None:
        self.assertEqual(self.code(lambda a: a.mov_store_reg("r9", "r10")),
                         b"\x4d\x89\x11")

    def test_pointer_store_swaps_src_bits_into_the_reg_field(self) -> None:
        self.assertEqual(self.code(lambda a: a.mov_store_reg("rcx", "rcx")),
                         b"\x48\x89\x09")

    def test_pointer_store_rejects_a_stack_base(self) -> None:
        with self.assertRaises(AssertionError):
            self.code(lambda a: a.mov_store_reg("rsp", "rax"))


class AluThroughRegisterPointerTest(unittest.TestCase):
    """A read-modify-write through a pointer register: `op [base], r64`.

    The fused array element update lowers to one memory-operand ALU
    instruction; a wrong reg/rm direction usually survives compilation and
    only shows up as a wrong element value, so the bytes are pinned here.
    """

    def code(self, build) -> bytes:
        assembler = Assembler()
        build(assembler)
        return bytes(assembler.code)

    def test_add_through_a_pointer_register(self) -> None:
        self.assertEqual(self.code(lambda a: a.binary_store_reg(0x01, "rcx", "rax")),
                         b"\x48\x01\x01")

    def test_sub_through_an_extended_base_masks_into_rex(self) -> None:
        self.assertEqual(self.code(lambda a: a.binary_store_reg(0x29, "r9", "r10")),
                         b"\x4d\x29\x11")

    def test_rejects_a_stack_base(self) -> None:
        with self.assertRaises(AssertionError):
            self.code(lambda a: a.binary_store_reg(0x01, "rsp", "rax"))


class AluThroughPointerWithImmediateTest(unittest.TestCase):
    """`op qword [base], imm8/imm32` for a fused element update with a literal."""

    def code(self, build) -> bytes:
        assembler = Assembler()
        build(assembler)
        return bytes(assembler.code)

    def test_add_an_imm8_through_a_pointer_register(self) -> None:
        self.assertEqual(self.code(lambda a: a.group1_store_reg_imm(0, "rcx", 7)),
                         b"\x48\x83\x01\x07")

    def test_sub_an_imm8_through_an_extended_base(self) -> None:
        self.assertEqual(self.code(lambda a: a.group1_store_reg_imm(5, "r9", -1)),
                         b"\x49\x83\x29\xff")

    def test_an_imm32_extends_the_displacement_size(self) -> None:
        self.assertEqual(self.code(lambda a: a.group1_store_reg_imm(6, "rax", 4096)),
                         b"\x48\x81\x30\x00\x10\x00\x00")

    def test_rejects_an_immediate_wider_than_32_signed_bits(self) -> None:
        with self.assertRaises(ValueError):
            self.code(lambda a: a.group1_store_reg_imm(0, "rcx", 0x80000000))

    def test_rejects_a_stack_base(self) -> None:
        with self.assertRaises(AssertionError):
            self.code(lambda a: a.group1_store_reg_imm(0, "rsp", 1))

if __name__ == "__main__":
    unittest.main()
