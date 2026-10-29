"""Emitter tests: the exact bytes of the encodings the generator relies on.

The backend is a fixed instruction subset. Pinning the bytes here means a wrong ModRM or a
missing REX bit fails in a unit test instead of in a produced executable.
"""

import unittest

from cairn.machine import Assembler


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


if __name__ == "__main__":
    unittest.main()
