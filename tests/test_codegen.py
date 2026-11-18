"""Code-generation tests: where a value lives, and what the frame must carry for it.

The lowering is invisible at the language level, so these tests watch the assembler calls instead
of the program output: a promoted slot must never touch its old frame slot, and the registers a
function uses must be saved and restored around its body.
"""

import os
import subprocess
import sys
import tempfile
import unittest

from cairn import parser, sema, syntax
from cairn.codegen import HOME_REGISTERS, CodeGenerator, function_label
from cairn.machine import ARGUMENT_REGISTERS, Assembler
from cairn.runtime import READ_CHAR_LABEL

HOLD_REGISTERS = ("r13", "r15")  # the registers a copy's inline merge may hold its left value in

LOOP = """
fn main() -> i64 {
    var i: i64 = 0;
    var total: i64 = 0;
    while i < 10 {
        total = total + i;
        i = i + 1;
    }
    return total;
}
"""

SEVEN_LOCALS = ("fn main() -> i64 { "
                + " ".join(f"let v{index}: i64 = {index};" for index in range(9))
                + " print(v8); return 0; }")


class RecordingAssembler(Assembler):
    """An assembler that remembers every frame slot it reads or writes per function.

    A function starts at its `fn.<name>` label and ends when the runtime helpers begin; the
    runtime's own frame traffic must not be mistaken for the function's.
    """

    def __init__(self) -> None:
        super().__init__()
        self.function = None
        self.loads = []
        self.stores = []
        self.pushes = []
        self.pops = []
        self.rsp_stores = []
        self.rsp_loads = []
        self.hold_stores = []

    def label(self, name: str) -> None:
        """Track which function the next frame access belongs to.

        A function's own label is `fn.<name>`; labels inside it, such as `fn.<name>.epilogue`,
        keep the same owner, and a `cairn.*` label marks the runtime helpers.
        """
        if name.startswith("fn.") and name.count(".") == 1:
            self.function = name
        elif name.startswith("cairn."):
            self.function = None
        super().label(name)

    def mov_load_rbp(self, destination: str, offset: int) -> None:
        """Record a frame load, then emit it."""
        self.loads.append((self.function, offset))
        super().mov_load_rbp(destination, offset)

    def mov_store_rbp(self, offset: int, source: str) -> None:
        """Record a frame store, then emit it."""
        self.stores.append((self.function, offset))
        super().mov_store_rbp(offset, source)

    def push(self, reg: str) -> None:
        """Record a push, then emit it."""
        self.pushes.append((self.function, reg))
        super().push(reg)

    def pop(self, reg: str) -> None:
        """Record a pop, then emit it."""
        self.pops.append((self.function, reg))
        super().pop(reg)

    def mov_store_rsp(self, source: str) -> None:
        """Record a pad-slot store, then emit it."""
        self.rsp_stores.append(self.function)
        super().mov_store_rsp(source)

    def mov_load_rsp(self, destination: str) -> None:
        """Record a pad-slot load, then emit it."""
        self.rsp_loads.append(self.function)
        super().mov_load_rsp(destination)

    def mov_reg_reg(self, destination: str, source: str) -> None:
        """Record hold-register writes, then emit them."""
        if destination in HOLD_REGISTERS:
            self.hold_stores.append((self.function, destination))
        super().mov_reg_reg(destination, source)

    def pops_of(self, function: str):
        """Registers popped inside one function."""
        return {reg for owner, reg in self.pops if owner == function}

    def pushes_of(self, function: str):
        """Registers pushed inside one function."""
        return {reg for owner, reg in self.pushes if owner == function}

    def loads_of(self, function: str):
        """Offsets of frame loads inside one function."""
        return {offset for owner, offset in self.loads if owner == function}

    def stores_of(self, function: str):
        """Offsets of frame stores inside one function."""
        return {offset for owner, offset in self.stores if owner == function}


def lower(source: str):
    """Check and lower one source, returning the program and its recording assembler."""
    program = sema.analyze(parser.parse(source))
    assembler = RecordingAssembler()
    CodeGenerator(assembler=assembler).generate(program)
    return program, assembler


class SlotHomeTest(unittest.TestCase):
    """A slot with a home register must not be read from or written to the stack."""

    def test_two_hot_locals_never_touch_their_frame_slots(self) -> None:
        program, assembler = lower(LOOP)
        main = function_label(program.functions[0].name)
        self.assertEqual(program.functions[0].slots, 2)
        self.assertEqual(assembler.loads_of(main) & {-8, -16}, set())
        self.assertEqual(assembler.stores_of(main) & {-8, -16}, set())

    def test_used_homes_are_saved_and_restored(self) -> None:
        program, assembler = lower(LOOP)
        main = function_label(program.functions[0].name)
        self.assertEqual(program.functions[0].slots, 2)
        saved = [reg for owner, reg in assembler.pushes if owner == main]
        restored = [reg for owner, reg in assembler.pops if owner == main]
        self.assertIn("rbx", saved)
        self.assertNotIn("rbp", saved)
        self.assertEqual(restored, list(reversed(saved)))

    def test_the_displaced_slot_keeps_the_stack_fallback(self) -> None:
        program, assembler = lower(SEVEN_LOCALS)
        main = function_label(program.functions[0].name)
        self.assertEqual(program.functions[0].slots, 9)
        # Depth-zero ties break by descending slot, so v8 keeps a home and the
        # earliest-declared local (slot 0, [rbp-8]) falls to the frame instead:
        # even the extras (r10/r11) are second to the deepest loop's slots.
        self.assertIn(-8, assembler.stores_of(main))
        self.assertNotIn(-8, assembler.loads_of(main))


def body_text(source: str, name: str) -> bytes:
    """The .text bytes of one function's body, sliced from the generated artifact."""
    program = sema.analyze(parser.parse(source))
    assembler = Assembler()
    artifact = CodeGenerator(assembler=assembler).generate(program)
    start = artifact.text_labels[function_label(name)]
    ends = sorted(offset for label, offset in artifact.text_labels.items()
                  if offset > start) + [len(artifact.text)]
    return artifact.text[start:ends[0]]


def region_text(source: str, name: str) -> bytes:
    """One function and its internal labels, stopping at the runtime helpers.

    `body_text` cuts at the next label of any kind, which hides instructions the
    emitter places after a fresh internal label such as a division's overflow check;
    this slice keeps them until the first `cairn.*` runtime label.
    """
    program = sema.analyze(parser.parse(source))
    assembler = Assembler()
    artifact = CodeGenerator(assembler=assembler).generate(program)
    start = artifact.text_labels[function_label(name)]
    ends = [offset for label, offset in artifact.text_labels.items()
            if offset > start and label.startswith("cairn.")]
    return artifact.text[start:min(ends) if ends else len(artifact.text)]


ARGUMENT_IMM = ("fn f(a: i64) -> i64 { return a - 7; } "
                "fn main() -> i64 { print(f(3)); return 0; }")
SHIFT_IMM = "fn g(x: i64) -> i64 { return x >> 7; } fn main() -> i64 { print(g(9)); return 0; }"
STACK_ARGS_CALL = ("fn bump(x: i64) -> i64 { return x + 1; }"
                   "fn combine(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64) -> i64"
                   " { return bump(a) + f; }"
                   "fn main() -> i64 { print(combine(1, 2, 3, 4, 5, 6)); return 0; }")


class ArgumentPathTest(unittest.TestCase):
    """Arguments and shifts by literals must not pay for registers they do not need."""

    def test_a_literal_subtrahend_uses_the_immediate_form(self) -> None:
        body = body_text(ARGUMENT_IMM, "f")
        self.assertIn(b"\x48\x8d\x43\xf9", body)  # lea rax, [rbx-7]
        self.assertNotIn(b"\x48\xb8\x07", body)

    def test_a_single_argument_moves_into_rcx_without_the_stack(self) -> None:
        body = body_text(ARGUMENT_IMM, "main")
        self.assertIn(b"\x48\xb9\x03", body)  # the literal binds straight into rcx
        self.assertNotIn(b"\x50\x59", body)

    def test_a_shift_by_literal_shifts_in_place(self) -> None:
        body = body_text(SHIFT_IMM, "g")
        self.assertIn(b"\x48\xc1\xf8\x07", body)
        self.assertNotIn(b"\x48\xd3\xf8", body)
        self.assertNotIn(b"\x48\xb8\x07", body)


class PowerOfTwoDivisionTest(unittest.TestCase):
    """`x / 2**b` and `x % 2**b` lower as a sar/shr fixup chain, never as `idiv`.

    The formula matches truncating division exactly: `x >>sar 63` (0 or all-ones) is
    turned into `0` or `2**b - 1` by a logical shift, added to force negatives up to
    the next multiple, and the arithmetic shift gives the truncated quotient; the
    remainder subtracts the quotient's product back out via the mask.
    """

    DIVIDE_BY_TWO = ("fn main() -> i64 { var x: i64 = 9;"
                     " print(x / 2); print(x % 2); return 0; }")

    FIXUP = ("fn probe(x: i64) -> i64 { return x / 2; }"
             "fn main() -> i64 { print(probe(9)); return 0; }")

    def test_division_by_a_power_of_two_literal_never_divides(self) -> None:
        body = region_text(self.DIVIDE_BY_TWO, "main")
        self.assertNotIn(b"\x48\xf7", body)  # neither idiv nor any r64 F7 form

    def test_a_proven_nonnegative_dividend_divides_as_one_shift(self) -> None:
        # x's literal init proves x non-negative, so a non-negative dividend
        # truncates like it floors and the whole quotient is one sar.
        body = region_text(self.DIVIDE_BY_TWO, "main")
        self.assertIn(b"\x48\xd1\xf8", body)       # sar rax, 1
        self.assertNotIn(b"\x48\xc1\xf9\x3f", body)  # the sign word never loads

    def test_division_by_two_emits_the_shift_fixup_chain(self) -> None:
        body = region_text(self.FIXUP, "probe")
        self.assertIn(b"\x48\xc1\xf9\x3f", body)  # sar rcx, 63: the sign word
        self.assertIn(b"\x48\xd1\xf8", body)      # sar rax, 1: the final one-count shift
        self.assertIn(b"\x48\xc1\xe9\x3f", body)  # shr rcx, 63 (group /5): sign word to 0/1
        self.assertNotIn(b"\x48\xc1\xf1", body)   # /6 is SAL, not SHR: never emitted

    def test_a_remainder_by_two_masks_instead_of_dividing(self) -> None:
        body = region_text(self.DIVIDE_BY_TWO, "main")
        self.assertIn(b"\x48\x83\xe0\x01", body)  # and rax, 1: the remainder mask

    def test_a_non_power_of_two_literal_still_divides(self) -> None:
        body = region_text("fn main() -> i64 { var x: i64 = 9; print(x / 3); return 0; }", "main")
        self.assertIn(b"\x48\xf7", body)  # the idiv form stays for divisor 3

    def test_a_negative_power_of_two_still_divides(self) -> None:
        body = region_text("fn main() -> i64 { var x: i64 = 9; print(x / -4); return 0; }", "main")
        self.assertIn(b"\x48\xf7", body)  # the idiv form stays for divisor -4


class HalvingPairLoopTest(unittest.TestCase):
    """The fused odd-pair collatz body lowers to one branchless halving pass.

    `while k > 1 { if k % 2 == 0 { k = k/2; s = s + 1 } else { k = (k*m + c)/2; s = s + 2 } }`
    with m, c odd commits one halving per pass and selects the odd value with `cmov`
    instead of a branch, so the dynamic step is three instructions with no mispredicts:
    one `shr` (which carries the parity bit in CF), one scaled `lea` of the halved slot
    ((k*m + c) / 2 == m*(k >> 1) + (m + c) / 2 for odd m and c), one `cmov`.
    """

    COLLATZ = ("fn main() -> i64 { var k: i64 = 3; var s: i64 = 0;"
               " while k > 1 {"
               " if k % 2 == 0 { k = k / 2; s = s + 1; }"
               " else { k = (k * 3 + 1) / 2; s = s + 2; } }"
               " print(s); return 0; }")

    def test_a_fused_pair_lowers_branchless(self) -> None:
        body = region_text(self.COLLATZ, "main")
        self.assertIn(b"\x0f\x42", body)            # cmovb: the pair's branch is a move
        self.assertIn(b"\xd1", body)                 # a one-count shift form in the loop
        self.assertNotIn(b"\xf7", body)              # no idiv: the pair rides lea + cmov

    def test_the_steps_counter_pays_both_steps_with_one_adc(self) -> None:
        body = region_text(self.COLLATZ, "main")
        self.assertIn(b"\x83\xd4\x01", body)        # adc r12, 1: s += 1 + carry
        self.assertNotIn(b"\x49\x83\xc4\x02", body)  # no odd path paying two, it is folded

    def test_a_negative_addend_stays_on_the_generic_path(self) -> None:
        # m + c = 2 < 4: a fused odd step can return k = 1, which the guard-free cmov form
        # cannot re-test; the loop keeps the shrink-chain emission.
        source = ("fn main() -> i64 { var k: i64 = 3; var s: i64 = 0;"
                  " while k > 1 {"
                  " if k % 2 == 0 { k = k / 2; s = s + 1; }"
                  " else { k = (k * 3 - 1) / 2; s = s + 2; } }"
                  " print(s); return 0; }")
        body = region_text(source, "main")
        self.assertNotIn(b"\x0f\x42", body)  # no cmov: refused shape

    def test_a_body_with_break_stays_on_the_generic_path(self) -> None:
        source = ("fn main() -> i64 { var k: i64 = 9; var s: i64 = 0;"
                  " while k > 1 {"
                  " if k == 5 { print(s); k = 1; }"
                  " if k % 2 == 0 { k = k / 2; s = s + 1; }"
                  " else { k = (k * 3 + 1) / 2; s = s + 2; } }"
                  " print(s); return 0; }")
        body = region_text(source, "main")
        self.assertNotIn(b"\x0f\x42", body)  # the shape is more than one parity dispatch

    def test_the_swapped_parity_test_takes_the_same_form(self) -> None:
        source = ("fn main() -> i64 { var k: i64 = 3; var s: i64 = 0;"
                  " while k > 1 {"
                  " if k % 2 != 0 { k = (k * 3 + 1) / 2; s = s + 2; }"
                  " else { k = k / 2; s = s + 1; } }"
                  " print(s); return 0; }")
        body = region_text(source, "main")
        self.assertIn(b"\x0f\x42", body)  # the same loop, sides swapped, still branchless

    def test_a_non_zero_parity_target_stays_on_the_generic_path(self) -> None:
        # `k % 2 == 5` is never true and never the parity dispatch shape.
        source = ("fn main() -> i64 { var k: i64 = 3; var s: i64 = 0;"
                  " while k > 1 {"
                  " if k % 2 == 5 { k = k / 2; s = s + 1; }"
                  " else { k = (k * 3 + 1) / 2; s = s + 2; } }"
                  " print(s); return 0; }")
        body = region_text(source, "main")
        self.assertNotIn(b"B", body)  # refused: not the parity dispatch


class ScaledSumTest(unittest.TestCase):
    """`c1 + k*c2` lowers as one scaled `lea`; the multiply path stays for other factors."""

    def test_three_k_plus_one_is_one_lea(self) -> None:
        body = region_text("fn main() -> i64 { var k: i64 = 7; print(3 * k + 1); return 0; }",
                           "main")
        self.assertIn(b"\x48\x8d\x44\x5b\x01", body)  # lea rax, [rbx + rbx*2 + 1]
        self.assertNotIn(b"\x0f\xaf", body)           # no imul

    def test_the_mirrored_operand_order_is_one_lea_too(self) -> None:
        body = region_text("fn main() -> i64 { var k: i64 = 7; print(k * 3 + 1); return 0; }",
                           "main")
        self.assertIn(b"\x48\x8d\x44\x5b\x01", body)

    def test_two_k_plus_five_uses_a_scale_of_one(self) -> None:
        body = region_text("fn main() -> i64 { var k: i64 = 7; print(2 * k + 5); return 0; }",
                           "main")
        self.assertIn(b"\x48\x8d\x44\x1b\x05", body)  # lea rax, [rbx + rbx + 5]

    def test_four_k_nine_takes_the_index_only_form(self) -> None:
        body = region_text("fn main() -> i64 { var k: i64 = 7; print(4 * k + 9); return 0; }",
                           "main")
        self.assertIn(b"\x48\x8d\x04\x9d\x09\x00\x00\x00", body)  # lea rax, [rbx*4 + 9]

    def test_an_unsupported_factor_still_multiplies(self) -> None:
        body = region_text("fn main() -> i64 { var k: i64 = 7; print(6 * k + 1); return 0; }",
                           "main")
        self.assertIn(b"\x0f\xaf", body)  # imul stays for factor 6
        self.assertNotIn(b"\x48\x8d", body)  # and no lea

    def test_an_unscaled_k_is_left_alone(self) -> None:
        body = region_text("fn main() -> i64 { var k: i64 = 7; print(k + 1); return 0; }", "main")
        self.assertNotIn(b"\x48\x8d\x44", body)  # the plain update already folds in place


class ParityCompareTest(unittest.TestCase):
    """`x % 2**b == 0` is `(x & (2**b-1)) == 0` for every sign: one `and`, one branch."""

    def test_an_even_test_is_a_test_mask_and_a_branch(self) -> None:
        source = ("fn main() -> i64 { var k: i64 = 5;"
                  " if k % 2 == 0 { print(1); } print(2); return 0; }")
        body = region_text(source, "main")
        self.assertIn(b"\x40\xf6\xc3\x01", body)  # test kl, 1 in the homed slot: value kept
        self.assertNotIn(b"\x48\x83\xe0\x01", body)  # nothing loads the slot just to mask it
        self.assertNotIn(b"\x48\xf7", body)       # no divide anywhere

    def test_an_uneven_test_branches_on_the_same_and(self) -> None:
        source = ("fn main() -> i64 { var k: i64 = 5;"
                  " if k % 2 != 0 { print(1); } print(2); return 0; }")
        body = region_text(source, "main")
        self.assertIn(b"\x40\xf6\xc3\x01", body)
        self.assertIn(b"\x0f\x84", body)  # the even side takes the inverted jump

    def test_a_wider_mask_counts_to_eight(self) -> None:
        source = ("fn main() -> i64 { var k: i64 = 5;"
                  " if k % 8 == 0 { print(1); } print(2); return 0; }")
        body = region_text(source, "main")
        self.assertIn(b"\x40\xf6\xc3\x07", body)  # test kl, 7 in the homed slot

    def test_a_mask_beyond_imm32_lands_in_a_scratch_register(self) -> None:
        source = ("fn main() -> i64 { var k: i64 = 5;"
                  " if k % 4503599627370496 == 0 { print(1); } print(2); return 0; }")
        body = region_text(source, "main")
        self.assertIn(b"\x48\xbe", body)     # movabs rsi, 2**52 - 1
        self.assertIn(b"\x48\x21\xf0", body)  # and rax, rsi

    def test_an_unequal_compare_against_a_nonzero_literal_still_divides(self) -> None:
        source = ("fn main() -> i64 { var k: i64 = 5;"
                  " if k % 8 == 3 { print(1); } print(2); return 0; }")
        body = region_text(source, "main")
        self.assertIn(b"\x48\x83\xe0\x07", body)   # the mask form computes the remainder
        self.assertNotIn(b"\x48\xf7", body)


CONDITIONS_SOURCE = """
fn main() -> i64 {
    var x: i64 = 5;
    var y: i64 = 0;
    while (x & 1) == 0 && (y & 1) == 0 {
        x = x >> 1;
        y = y + 1;
    }
    if (x & 1) == 0 { y = 1; }
    if (x & 3) != 0 { y = y + 2; }
    print(y);
    return 0;
}
"""


def region_bytes(source: str, name: str) -> bytes:
    """The .text bytes of one function's body, its inline copies included.

    The label-slicing helper above stops at the next label of any kind; a pair-inlined
    body is littered with its own copy labels, so this slice runs to the first label
    outside the function's own namespace.
    """
    program = sema.analyze(parser.parse(source))
    assembler = Assembler()
    artifact = CodeGenerator(assembler=assembler).generate(program)
    start = artifact.text_labels[function_label(name)]
    later = sorted(offset for label, offset in artifact.text_labels.items()
                   if offset > start and label.startswith("fn.")
                   and label.count(".") == 1)
    return artifact.text[start:later[0] if later else len(artifact.text)]

def region_of(source: str, name: str = "main") -> bytes:
    """One function's bytes with its inline copies, as region_bytes does in helpers above."""
    program = sema.analyze(parser.parse(source))
    assembler = Assembler()
    artifact = CodeGenerator(assembler=assembler).generate(program)
    start = artifact.text_labels[function_label(name)]
    later = sorted(offset for label, offset in artifact.text_labels.items()
                   if offset > start and (label.startswith("cairn.")
                                          or (label.startswith("fn.")
                                              and label.count(".") == 1)))
    return artifact.text[start:later[0] if later else len(artifact.text)]


def has_setcc(blob: bytes) -> bool:
    """Any setcc opcode (0F 90..0F 9F): a comparison materialised as 0 or 1."""
    return any(blob.startswith and False for _ in ()) or any(
        b"" + bytes([extension]) in blob for extension in range(0x90, 0xA0))


def count_test_imms(blob: bytes) -> int:
    """How many `test r/m8, imm8` byte tests (F6 with register-modrm, then a mask) appear."""
    return sum(1 for index in range(len(blob) - 3)
               if blob[index] == 0xF6 and (blob[index + 1] & 0xC0) == 0xC0)


class FlagFusedConditionTest(unittest.TestCase):
    """Bit-test and short-circuit conditions branch from their flags, never setcc."""

    def test_a_bit_test_condition_branches_from_the_flags(self) -> None:
        region = region_of("fn main() -> i64 { var x: i64 = 5; var y: i64 = 0; "
                           "if (x & 1) == 0 { y = 1; } return y; }")
        self.assertFalse(has_setcc(region), "setcc round-trip survived in a branch")
        self.assertGreaterEqual(count_test_imms(region), 1)

    def test_conjoined_bit_tests_branch_from_two_flag_pairs(self) -> None:
        region = region_of("fn main() -> i64 { var x: i64 = 5; var y: i64 = 0; "
                           "while (x & 1) == 0 && (y & 1) == 0 { y = y + 1; } "
                           "return y; }")
        self.assertFalse(has_setcc(region))
        self.assertGreaterEqual(count_test_imms(region), 4)  # head guard + bottom latch

    def test_a_disjoined_condition_short_circuits_its_right_side(self) -> None:
        region = region_of("fn main() -> i64 { var x: i64 = 5; var y: i64 = 0; "
                           "if (x & 1) == 1 || y > 2 { y = 1; } return y; }")
        self.assertFalse(has_setcc(region))

    def test_a_comparison_assigned_as_a_value_still_materialises_setcc(self) -> None:
        region = region_of("fn main() -> i64 { var x: i64 = 5; var z: i64 = (x & 1) == 0; "
                           "return z; }")
        self.assertTrue(has_setcc(region), "value context must keep its 0/1 result")

    def test_masks_above_the_byte_form_still_decide_the_branch(self) -> None:
        region = region_of("fn main() -> i64 { var x: i64 = 5; var y: i64 = 0; "
                           "if (x & 4096) == 0 { y = 1; } return y; }")
        self.assertFalse(has_setcc(region))


MASK_SHIFTS = ("fn main() -> i64 { var x: i64 = -123456789; "
               "var t: i64 = (x >> 30) & 17179869183; print(t); return t; }")


def _has_shr(blob: bytes, count: int) -> bool:
    return any(blob[index] == 0xC1 and ((blob[index + 1] >> 3) & 0x7) == 0x5
               and blob[index + 2] == count for index in range(len(blob) - 2))


def _has_mask_movabs(blob: bytes) -> bool:
    """The 0x3FFFFFFFF mask riding a movabs immediate."""
    return bytes([0xFF, 0xFF, 0xFF, 0xFF, 0x03, 0x00, 0x00, 0x00]) in blob


class LogicalShiftPeepholeTest(unittest.TestCase):
    """`(a >> k) & (2**(64-k) - 1)` needs no arithmetic-shift fixup or mask load."""

    def test_a_covering_mask_lowers_as_one_logical_shift(self) -> None:
        region = region_of(MASK_SHIFTS)
        self.assertTrue(_has_shr(region, 30))
        self.assertFalse(_has_mask_movabs(region))

    def test_a_partial_mask_keeps_its_and(self) -> None:
        region = region_of("fn main() -> i64 { var x: i64 = -123456789; "
                           "var t: i64 = (x >> 30) & 1023; print(t); return t; }")
        self.assertFalse(_has_shr(region, 30))


MIX_PROGRAM = ("fn main() -> i64 { var x: i64 = 7; var z: i64 = x; "
               "z = (z ^ ((z >> 30) & 17179869183)) * 3; return z; }")

HOIST_LOOP = ("fn main() -> i64 { var z: i64 = 548732102; var i: i64 = 64; "
              "while i > 0 { z = (z ^ ((z >> 30) & 17179869183)) * 6364136223846793005; "
              "z = z + 7046029254386353131; i = i - 1; } return z; }")


def _movabs_rax(value: int) -> bytes:
    """The full `movabs rax, value` encoding."""
    return bytes([0x48, 0xB8]) + (value & ((1 << 64) - 1)).to_bytes(8, "little")


def _hoisted_movabs_candidates(value: int) -> List[bytes]:
    """`movabs <home>, value` for every home a hoist could pick."""
    payload = (value & ((1 << 64) - 1)).to_bytes(8, "little")
    return [bytes([0x48, 0xB8 + index]) + payload for index in range(8)] + \
           [bytes([0x49, 0xB8 + (index - 8)]) + payload for index in range(8, 16)]


class LoopConstantHoistTest(unittest.TestCase):
    """Countdown loops read their big literals from pre-loaded spare homes."""

    def test_the_loop_never_rematerialises_its_big_constants(self) -> None:
        region = region_of(HOIST_LOOP)
        for value in (1812433253, 7046029254386353131):
            self.assertNotIn(_movabs_rax(value), region,
                             f"{value} re-materialised into rax inside the loop")

    def test_the_constants_load_into_spare_homes_before_the_loop(self) -> None:
        region = region_of(HOIST_LOOP)
        loads = [region.index(encoding) for encoding in
                 _hoisted_movabs_candidates(6364136223846793005)
                 if encoding in region] + [region.index(encoding) for encoding in
                                           _hoisted_movabs_candidates(7046029254386353131)
                                           if encoding in region]
        self.assertEqual(len(loads), 2, "each big constant must load exactly once")
        self.assertLess(max(loads), region.find(bytes([0xC1, 0xE8, 0x1E])),
                        "the loads must precede the loop body")


def _combine_right_after_shr(region: bytes, count: int) -> bool:
    """Whether an update combine follows its peephole `shr rax, count` with no park."""
    shr = bytes([0xC1, 0xE8, count])
    index = region.find(shr)
    if index < 0:
        return False
    tail = region[index + len(shr):index + len(shr) + 3]
    # xor <callee-saved home>, rax: REX.W (+REX.B for the high homes), opcode 31,
    # then modrm 11 reg000 rm — rbx..rdi sit at C3..C7.
    return any(tail == bytes([rex, 0x31, 0xC0 | rm])
               for rex in (0x48, 0x49)
               for rm in (3, 4, 5, 6, 7))


class SwapSelectTest(unittest.TestCase):
    """The gcd swap shape `if a > b { var t = a; a = b; b = t; }` lowers branchless.

    One scratch holds the old `a`, and two cmovs on the comparison flags do the
    exchange; data-dependent swap branches mispredict, which is the whole cost
    of a Stein-GCD inner loop. Refusals fall back to the branchy path.
    """

    SWAP = ('fn main() -> i64 {'
            ' var a: i64 = 3; var b: i64 = 8;'
            ' if a > b { var t: i64 = a; a = b; b = t; }'
            ' print(a); print(b); return 0; }')

    def code(self, source: str) -> bytes:
        """Main's own bytes only: label to label, so runtime helpers never match."""
        program = sema.analyze(parser.parse(source))
        assembler = RecordingAssembler()
        from cairn.codegen import CodeGenerator, function_label
        CodeGenerator(assembler=assembler).generate(program)
        code = bytes(assembler.code)
        labels = assembler.labels
        start = labels[function_label("main")]
        end = labels[function_label("main") + ".epilogue"]
        return code[start:end]

    def test_the_swap_shape_fuses_into_two_cmovs_and_no_branch(self) -> None:
        code = self.code(self.SWAP)
        self.assertIn(b"\x0f\x4f", code)   # cmovg
        self.assertNotIn(b"\x0f\x8e", code)  # jle: the branchy swap is gone

    def test_the_temp_being_read_after_the_if_refuses_the_fusion(self) -> None:
        code = self.code('fn main() -> i64 {'
                         ' var a: i64 = 3; var b: i64 = 8;'
                         ' if a > b { var t: i64 = a; a = b; b = t; }'
                         ' print(t); print(a); print(b); return 0; }')
        self.assertIn(b"\x0f\x8e", code)

    def test_a_two_statement_body_refuses_the_fusion(self) -> None:
        code = self.code('fn main() -> i64 {'
                         ' var a: i64 = 3; var b: i64 = 8;'
                         ' if a > b { a = b; b = 0; }'
                         ' print(a); print(b); return 0; }')
        self.assertIn(b"\x0f\x8e", code)

    def test_a_condition_on_other_slots_refuses_the_fusion(self) -> None:
        code = self.code('fn main() -> i64 {'
                         ' var a: i64 = 3; var b: i64 = 8; var c: i64 = 1;'
                         ' if a > c { var t: i64 = a; a = b; b = t; }'
                         ' print(a); print(b); return 0; }')
        self.assertIn(b"\x0f\x8e", code)


class ReadFuseTest(unittest.TestCase):
    """`read_char()` fuses its buffered fast path into the call site, drain stays in the helper.

    The helper's per-byte cost is a call/ret plus two rip-lea/load pairs and a
    cursor re-store; ADR-058 measured the refill path inlined as worse, so only
    the have-path fuses: two rip loads, one compare, one SIB movzx and one
    in-place memory increment, with the drained-buffer and EOF paths falling to
    the call. Refusals keep the plain call when the fusion's scratch or the
    answer register is not free.
    """

    LOOP = ('fn main() -> i64 {'
            ' var total: i64 = 0;'
            ' while true {'
            '  var c: i64 = read_char();'
            '  if c < 0 { break; }'
            '  total = total + c;'
            ' }'
            ' print(total); return 0; }')

    def _generate(self, source: str):
        program = sema.analyze(parser.parse(source))
        assembler = RecordingAssembler()
        CodeGenerator(assembler=assembler).generate(program)
        code = bytes(assembler.code)
        labels = assembler.labels
        start = labels[function_label("main")]
        end = labels[function_label("main") + ".epilogue"]
        return code[start:end], assembler, start, end

    def _calls_to(self, start: int, end: int, assembler: Assembler, label: str) -> int:
        """Count `call rel32` fixups inside [start, end) that land on `label`.

        The fixup's offset points at the rel32, one byte after the opcode, and
        resolves relative to the instruction's end, matching the link rule.
        """
        return sum(1 for offset, target in assembler.fixups
                   if target == label and start <= offset - 1 < end)

    def test_the_read_loop_fuses_the_fast_path_and_keeps_one_drain_call(self) -> None:
        region, assembler, start, end = self._generate(self.LOOP)
        self.assertIn(b"\x49\x83\x02\x01", region)   # add qword [r10], 1: cursor bump
        self.assertEqual(self._calls_to(start, end, assembler, READ_CHAR_LABEL), 1)

    def test_a_read_char_inside_another_call_fuses_too(self) -> None:
        # The fusion's clobbers (rax, r10, flags) are a subset of the helper
        # call's own contract, so a call inside another call's argument fuses
        # the same way: the answer still lands in rax for the argument move.
        region, assembler, start, end = self._generate(
            'fn main() -> i64 { print(read_char()); return 0; }')
        self.assertIn(b"\x49\x83\x02\x01", region)
        self.assertEqual(self._calls_to(start, end, assembler, READ_CHAR_LABEL), 1)

    def test_r10_borrowed_as_an_extra_home_refuses_the_fusion(self) -> None:
        # r10 carries a live borrowed value across calls (pushed/popped by every
        # call site); the fused fast path would clobber it with no crossing.
        program = sema.analyze(parser.parse(
            'fn main() -> i64 { read_char(); return 0; }'))
        generator = CodeGenerator(assembler=RecordingAssembler())
        call = program.functions[0].body.statements[0].expression
        assert isinstance(call, syntax.Call), "the extracted node must be the call"
        generator.current = program.functions[0]
        generator.extra_homes = ("r10",)
        generator._emit_call(call)
        self.assertEqual(self._calls_to(0, 1 << 20, generator.assembler,
                                        READ_CHAR_LABEL), 1)
        self.assertNotIn(b"\x49\x83\x02\x01", bytes(generator.assembler.code))

    def test_the_read_loop_keeps_the_branch_out_of_the_fast_path(self) -> None:
        # The fused cursor bump addresses through a register: the PE linker's
        # rel32 fixup resolves against the instruction's end, which the
        # imm8-after-disp32 rip form would violate. The lea+add pair stays.
        region, _, _, _ = self._generate(self.LOOP)
        self.assertIn(b"\x49\x83\x02\x01", region)
        self.assertNotIn(b"\x48\x83\x05", region)

    def test_a_break_only_if_jumps_straight_to_the_break_target(self) -> None:
        # `if c < 0 { break; }` inverts: one conditional jump to the break
        # target, no then-label, no fallthrough jmp after it — no fresh label.
        _, assembler, _, _ = self._generate(self.LOOP)
        self.assertEqual([name for name in assembler.labels if name.startswith("if.")], [])


class MergedMaskMixTest(unittest.TestCase):
    """A masked xor merged into its home register lowers park-free, in place."""

    def test_the_mixer_never_materialises_or_spills_its_mask(self) -> None:
        region = region_of(MIX_PROGRAM)
        self.assertTrue(_has_shr(region, 30))
        self.assertFalse(_has_mask_movabs(region),
                         "a covering mask must ride the peephole, not a movabs")

    def test_the_xor_combines_in_place_right_after_the_shift(self) -> None:
        region = region_of(MIX_PROGRAM)
        self.assertTrue(_combine_right_after_shr(region, 30),
                        "no in-place combine directly after the peephole shr")

    def test_the_merged_mul_stays_in_its_home_register(self) -> None:
        region = region_of(MIX_PROGRAM)
        # imul <home>, rax (0F AF /r): the destination rides the modrm reg field, so a
        # high home carries its index there with REX.R — the probe shape is
        # 4C 0F AF E0 (imul r12, rax). The product must land in a callee-saved home
        # the xor merged into, with no closing move or re-park between them.
        candidates = []
        for home_index in (3, 4, 5, 6, 7, 12, 13, 14, 15):
            rex = 0x4C if home_index >= 8 else 0x48
            candidates.append(bytes([rex, 0x0F, 0xAF, 0xC0 | ((home_index & 7) << 3)]))
        self.assertTrue(any(pattern in region for pattern in candidates),
                        "the multiply must write a callee-saved home via rax")



class ArrayInPlaceUpdateTest(unittest.TestCase):
    """`a[k] = a[k] op e` is one memory-operand ALU instruction, not load-op-store.

    The read-modify-write sweep is the whole array workload class; paying two
    address computations plus a parked store for every element was a measured
    25 percent deficit against C on the array_sum benchmark. The fused form
    computes the address once and updates through it.
    """

    def program_and_code(self, source: str) -> bytes:
        """Main's own code bytes, so runtime helpers never satisfy the asserts."""
        from cairn import codegen as codegen_module
        from cairn import optimize
        artifact = codegen_module.generate(optimize.optimize(sema.analyze(parser.parse(source))))
        labels = artifact.text_labels
        main_start = labels[codegen_module.function_label("main")]
        runtime_start = min(offset for name, offset in labels.items()
                            if name.startswith("cairn.runtime."))
        text = bytes(artifact.text)
        return text[main_start:runtime_start]

    def test_a_static_index_update_is_one_memory_operand_alu(self) -> None:
        code = self.program_and_code(
            "fn main() -> i64 { var a: [4]i64 = [0; 4]; var r: i64 = 2;"
            " a[3] = a[3] + r; return 0; }")
        binary_store = b"\x48\x01\x5d"      # add [rbp+disp8], rbx (r's home)
        via_rax = b"\x48\x01\x45"           # add [rbp+disp8], rax: a needless copy
        load_pair = b"\x48\x89\xc1"          # mov rcx, rax: the fused-free shuffle
        self.assertIn(binary_store, code)
        self.assertNotIn(via_rax, code)
        self.assertNotIn(load_pair, code)

    def test_a_static_index_update_folds_a_literal_into_the_instruction(self) -> None:
        code = self.program_and_code(
            "fn main() -> i64 { var a: [4]i64 = [0; 4];"
            " a[3] = a[3] + 7; return 0; }")
        self.assertIn(b"\x48\x83\x45", code)  # add qword [rbp+disp8], imm8

    def test_a_dynamic_index_update_computes_the_address_once(self) -> None:
        code = self.program_and_code(
            "fn main() -> i64 { var a: [4]i64 = [0; 4]; var i: i64 = 0;"
            " var r: i64 = 2; while i < 4 { a[i] = a[i] + r; i = i + 1; }"
            " return 0; }")
        through_pointer = b"\x48\x01\x19"    # add [rcx], rbx (r's home)
        neg_rax = b"\x48\xf7\xd8"
        park = b"\x48\x89\x8d"              # mov [rbp+disp32], rcx: the address park
        self.assertIn(through_pointer, code)
        self.assertNotIn(park, code)
        self.assertEqual(code.count(neg_rax), 1)

    def test_a_dynamic_index_update_folds_a_literal_into_the_instruction(self) -> None:
        code = self.program_and_code(
            "fn main() -> i64 { var a: [4]i64 = [0; 4]; var i: i64 = 0;"
            " while i < 4 { a[i] = a[i] + 7; i = i + 1; } return 0; }")
        self.assertIn(b"\x48\x83\x01\x07", code)  # add qword [rcx], imm8
        self.assertNotIn(b"\x48\x89\x8d", code)

    def test_a_reversed_commutative_update_fuses_with_the_other_side_only(self) -> None:
        code = self.program_and_code(
            "fn main() -> i64 { var a: [4]i64 = [0; 4]; var r: i64 = 2;"
            " a[3] = r + a[3]; return 0; }")
        self.assertIn(b"\x48\x01\x5d", code)
        self.assertNotIn(b"\x48\x89\xc1", code)

    def test_a_subtraction_from_the_element_fuses_only_left_to_right(self) -> None:
        code = self.program_and_code(
            "fn main() -> i64 { var a: [4]i64 = [0; 4]; var r: i64 = 2;"
            " a[3] = a[3] - r; return 0; }")
        self.assertIn(b"\x48\x29\x5d", code)

    def test_an_index_calling_update_never_fuses(self) -> None:
        # A called index must run twice exactly as written; fusion would drop a call.
        source = ("fn pick(x: i64) -> i64 { print(x); return x; }"
                  "fn main() -> i64 { var a: [4]i64 = [0; 4];"
                  " a[pick(3)] = a[pick(3)] + 5; return 0; }")
        self.assertNotIn(b"\x48\x01\x45", self.program_and_code(source))

if __name__ == "__main__":
    unittest.main()

FIB_MERGE = ("fn fib(n: i64) -> i64 { if n < 2 { return n; } "
             "return fib(n - 1) + fib(n - 2); } fn main() -> i64 { print(fib(10)); return 0; }")

PAD_MERGE = ("fn walk(n: i64) -> i64 { if n < 2 { return n; } var base: i64 = 3; "
             "return walk(n - 1) + walk(n - 2) - base; } "
             "fn main() -> i64 { print(walk(10)); return 0; }")


class SelfRecursionInliningTest(unittest.TestCase):
    """The left operand of a merge may be a self-call lowered in place, not a real call."""

    def test_a_merge_left_self_call_is_inlined(self) -> None:
        program = sema.analyze(parser.parse(FIB_MERGE))
        artifact = CodeGenerator().generate(program)
        copy_labels = [label for label in artifact.text_labels
                       if ".inline" in label or ".delta" in label]
        self.assertTrue(copy_labels)

    def test_a_comparison_with_a_small_literal_skips_the_rax_load(self) -> None:
        body = body_text(FIB_MERGE, "fib")
        self.assertIn(b"\x48\x83\xfb\x02", body)  # cmp rbx, 2

    def test_a_merge_over_calls_never_takes_the_stack_form(self) -> None:
        program, assembler = lower(FIB_MERGE)
        self.assertNotIn(("fn.fib", "rax"), assembler.pushes)

    def test_a_pad_slot_holds_a_merge_value_in_memory(self) -> None:
        program, assembler = lower(PAD_MERGE)
        code = bytes(assembler.code)
        self.assertIn(b"\x48\x89\x04\x24", code)  # mov [rsp], rax
        self.assertIn(b"\x48\x8b\x0c\x24", code)  # mov rcx, [rsp]

    def test_only_the_real_merge_keeps_its_pad_slot(self) -> None:
        program, assembler = lower(PAD_MERGE)
        # One merge lives in fn.walk's real body and keeps its value in the pad slot; every
        # inline-copy merge holds its value in the copy's pushed hold register instead. A
        # two-slot copy's hold register is r15 (its shadow pair is r13 and r14).
        self.assertEqual(len(assembler.rsp_stores), 1)
        self.assertEqual(len(assembler.rsp_loads), 1)
        copies = len([label for label in assembler.labels if ".inline" in label])
        self.assertGreater(copies, 1)
        merges = [entry for entry in assembler.hold_stores if entry[1] == "r15"]
        self.assertEqual(len(merges), copies)


class DeltaInliningTest(unittest.TestCase):
    """A one-slot recursive function lowers its self-calls as pure text copies.

    The copies carry no stack state of their own: the parameter is the caller's parameter
    plus an accumulated delta, and every merge's left value rests in a level-indexed slot
    of the real frame.
    """

    def test_delta_copies_never_touch_the_stack(self) -> None:
        program, assembler = lower(FIB_MERGE)
        fib = function_label(program.functions[0].name)
        self.assertEqual(assembler.pushes_of(fib), {"rbx"})  # the prologue save alone
        self.assertEqual(assembler.pops_of(fib), {"rbx"})

    def test_a_delta_function_skips_the_shadow_copies(self) -> None:
        program, assembler = lower(FIB_MERGE)
        self.assertEqual([label for label in assembler.labels if ".inline" in label], [])
        self.assertEqual(assembler.hold_stores, [])
        self.assertEqual(assembler.rsp_stores, [])
        self.assertEqual(assembler.rsp_loads, [])

    def test_delta_labels_bloom_pure_text_copies(self) -> None:
        program, assembler = lower(FIB_MERGE)
        delta = [label for label in assembler.labels if ".delta" in label]
        self.assertGreater(len(delta), 1)

    def test_merges_hold_values_in_level_indexed_frame_slots(self) -> None:
        program, assembler = lower(FIB_MERGE)
        code = bytes(assembler.code)
        self.assertIn(b"\x48\x89\x44\x24\x00", code)  # mov [rsp], rax
        self.assertIn(b"\x48\x8b\x4c\x24\x00", code)  # mov rcx, [rsp]
        self.assertIn(b"\x48\x89\x44\x24\x08", code)  # the next level's slot


TAIL_SWING = ("fn f(c: i64) -> i64 { if c > 0 { return 7; } return 9; } "
              "fn main() -> i64 { print(f(5)); return 0; }")


class TailReturnElisionTest(unittest.TestCase):
    """A `return` in tail position falls through into the epilogue label instead of jumping.

    The implicit return zero runs at entry, so the body's own final return can reach the
    epilogue without an instruction; every other return must still jump.
    """

    def test_a_tail_return_emits_no_jmp(self) -> None:
        body = body_text(TAIL_SWING, "main")
        # main's `return 0` still falls straight into the epilogue: the only jmp in the
        # region is the inlined copy's `return 7`, which is not in tail position.
        self.assertEqual(body.count(b"\xe9"), 1)  # the copy's non-tail return alone

    def test_a_non_tail_return_still_jumps_to_the_epilogue(self) -> None:
        body = body_text(TAIL_SWING, "f")
        # `return 7` sits inside an if the merge Rewrite follows; only `return 9` is a tail.
        self.assertEqual(body.count(b"\xe9"), 1)


TWO_ARG_CALL = ("fn g(a: i64, b: i64) -> i64 { return a + b; } "
                "fn f(c: i64, d: i64) -> i64 { return g(c - 1, d); } "
                "fn main() -> i64 { print(f(3, 4)); return 0; }")



push_rax = b"\x50"
pop_rcx = b"\x59"
pop_rdx = b"\x5a"
call_op = b"\xe8"
lea_rcx = b"\x48\x8d\x4b\xff"  # lea rcx, [rbx-1]
lea_rax_rbx_r12 = b"\x4a\x8d\x44\x21\x00"  # lea rax, [rcx + r12*1]
mov_rdx_r12 = b"\x4c\x89\xe2"  # mov rdx, r12
mov_rax_r12 = b"\x4c\x89\xe0"  # mov rax, r12
mov_rdx_rbx = b"\x48\x89\xda"  # mov rdx, rbx
add_rax_rcx = b"\x48\x01\xc8"  # add rax, rcx
sar_rdx_2 = b"\x48\xc1\xfa\x02"  # sar rdx, 2
movzx_r8d_bx = b"\x44\x0f\xb7\xc3"  # movzx r8d, bx
and_r8_65535 = b"\x49\x81\xe0\xff\xff\x00\x00"
and_rax_neg4 = b"\x48\x83\xe0\xfc"  # and rax, -4: the fused (x >> 2) << 2
movabs_one = b"\x48\xb8\x01"
test_rax_rax = b"\x48\x85\xc0"
cmp_rax_minus1 = b"\x48\x83\xf8\xff"

class MultiArgumentCallTest(unittest.TestCase):
    """Pure arguments bind straight into their ABI registers, without the stack round-trip.

    The generic two-argument path pushed and popped each argument; a push/pop pair costs
    more uops than the direct move it replaces, and every multi-argument call site paid
    for both. Only leaf-ish arguments (names, literals, name +/- literal) may rebind
    directly: they cannot call or trap, so rebinding them out of source order is
    unobservable.
    """


    def test_both_pure_arguments_bind_without_the_stack(self) -> None:
        # g's copy inside f: the computed argument c - 1 binds once into rcx, the bare
        # name d needs no bind at all (the copy's parameter reads d's home r12), and
        # the sum lands straight in rax as the copy's return home. No push, no pop,
        # no call instruction: the copy replaced the call.
        body = body_text(TWO_ARG_CALL, "f")
        self.assertIn(lea_rcx, body)
        self.assertNotIn(mov_rdx_r12, body)  # d aliases its home: no bind
        self.assertIn(lea_rax_rbx_r12, body)  # the sum is one lea over both homes
        self.assertNotIn(add_rax_rcx, body)
        self.assertNotIn(push_rax, body)  # f's only pushes are prologue home saves
        self.assertNotIn(pop_rcx, body)
        self.assertNotIn(movabs_one, body)  # no 10-byte movabs for a small literal

    def test_mixed_call_free_arguments_bind_reversed_without_the_stack(self) -> None:
        # call_chain's shape: f(x, x >> 2, x & 65535). The bare name aliases onto its
        # home inside the copy, the masked argument binds as one movzx from that home,
        # and ADR-049 forwards the shifted argument into its single use: no bind, no
        # sar, the fused and-immediate lowers at the use site instead. Reverse order
        # costs nothing observable, and no rax round-trip happens anywhere.
        source = ("fn f(a: i64, b: i64, c: i64) -> i64 { return (a * 3) ^ (b << 2) + c; } "
                  "fn main() -> i64 { var x: i64 = 7; return f(x, x >> 2, x & 65535); }")
        body = body_text(source, "main")
        self.assertNotIn(push_rax, body)  # no push at the call site
        self.assertNotIn(pop_rdx, body)
        self.assertNotIn(pop_rcx, body)
        self.assertIn(movzx_r8d_bx, body)  # the mask is the bind
        self.assertNotIn(and_r8_65535, body)
        self.assertNotIn(sar_rdx_2, body)
        fused = []
        for reg in ("rax", "rcx", "rdx", "r8", "r9", *HOME_REGISTERS):
            probe = Assembler()
            probe.arith_imm(4, reg, -4)   # and <reg>, -4: the fused use-site mask
            fused.append(bytes(probe.code))
        fused_at = min(position for form in fused
                       if (position := body.find(form)) >= 0)
        self.assertLess(body.index(movzx_r8d_bx), fused_at)

    def test_a_call_inside_an_argument_keeps_the_stack_fallback(self) -> None:
        # A nested call that cannot inline (g calls itself, so no copy is taken)
        # clobbers the caller-saved argument registers, so an already-bound argument
        # would not survive later evaluations: reverse binding must refuse.
        source = ("fn f(a: i64, b: i64, c: i64) -> i64 { return a + b + c; } "
                  "fn g(v: i64) -> i64 { return g(v * 2); } "
                  "fn main() -> i64 { var x: i64 = 7; return f(x, x, g(x)); }")
        body = body_text(source, "main")

    def test_a_division_inside_an_argument_keeps_the_stack_fallback(self) -> None:
        # Division can trap, so it never binds in reverse when a sibling could trap
        # first. Here the substituted names defer every read into the copy, so the
        # division is the only argument bound at the call site: its trap still fires
        # in the source's order, and the result rides the push/pop fallback across
        # the bound call while y's proven non-value keeps the overflow guards out.
        source = ("fn f(a: i64, b: i64, c: i64) -> i64 { return a + b + c; } "
                  "fn main() -> i64 { var x: i64 = 7; var y: i64 = 2; "
                  "return f(x, y, x / y); }")
        body = body_text(source, "main")
        self.assertIn(test_rax_rax, body)  # the division-by-zero guard
        self.assertNotIn(cmp_rax_minus1, body)  # y = 2 can never be the -1 divisor

ACKERMANN_SHAPE = (
    "fn ack(m: i64, n: i64) -> i64 { "
    "if m == 0 { return n + 1; } "
    "if n == 0 { return ack(m - 1, 1); } "
    "return ack(m - 1, ack(m, n - 1)); } "
    "fn main() -> i64 { print(ack(2, 3)); return 0; }"
)


class StackParameterTest(unittest.TestCase):
    """Parameters five to eight travel through the caller's outgoing area.

    The callee reads stack parameter j at [rbp+16+8j], the slot right above the saved
    return address; the caller reserves pad plus the stack-arg slots in one sub, so the
    call runs with the register arguments bound and rsp 16-byte aligned. combine calls
    `bump`, so its call cannot take the foreign-copy path and must keep this shape.
    """

    def test_callee_pulls_stack_parameters_from_above_the_return_address(self) -> None:
        body = body_text(STACK_ARGS_CALL, "combine")
        self.assertIn(bytes([0x48, 0x8B, 0x45, 0x10]), body)  # mov rax, [rbp+16]
        self.assertIn(bytes([0x48, 0x8B, 0x45, 0x18]), body)  # mov rax, [rbp+24]

    def test_caller_stores_stack_arguments_into_the_outgoing_area(self) -> None:
        body = body_text(STACK_ARGS_CALL, "main")
        # The four register arguments are parked, so the store displacements name
        # the slots at [rsp+32] and [rsp+40] (= base+0 and base+8 after the pops).
        self.assertIn(bytes([0x48, 0x89, 0x44, 0x24, 0x20]), body)
        self.assertIn(bytes([0x48, 0x89, 0x44, 0x24, 0x28]), body)

    def test_the_outgoing_area_is_one_reservation_with_call_alignment(self) -> None:
        body = body_text(STACK_ARGS_CALL, "main")
        # sub rsp, 16 = two stack-arg slots; depth 0 + 16 keeps the call aligned.
        self.assertIn(bytes([0x48, 0x81, 0xEC, 16, 0, 0, 0]), body)


class BuiltinPrintHelpersTest(unittest.TestCase):
    """print_char and print_hex dispatch to their own runtime helpers.

    Both keep print's stack round-trip: the helper clobbers rax and rcx, and the pop is what
    makes the call expression return its argument.
    """

    def test_the_helpers_are_emitted_into_every_executable(self) -> None:
        program = sema.analyze(parser.parse("fn main() -> i64 { return 0; }"))
        artifact = CodeGenerator(assembler=Assembler()).generate(program)
        self.assertIn("cairn.runtime.print_char", artifact.text_labels)
        self.assertIn("cairn.runtime.print_hex", artifact.text_labels)

    def test_print_char_calls_its_helper_with_the_stack_round_trip(self) -> None:
        program = sema.analyze(parser.parse("fn main() -> i64 { print_char(65); return 0; }"))
        artifact = CodeGenerator(assembler=Assembler()).generate(program)
        targets = {label for _, label in artifact.fixups}
        self.assertIn("cairn.runtime.print_char", targets)

    def test_print_hex_calls_its_helper(self) -> None:
        program = sema.analyze(parser.parse("fn main() -> i64 { print_hex(255); return 0; }"))
        artifact = CodeGenerator(assembler=Assembler()).generate(program)
        targets = {label for _, label in artifact.fixups}
        self.assertIn("cairn.runtime.print_hex", targets)


class PairDeltaInliningTest(unittest.TestCase):
    """A two-parameter all-return-tree recursion inlines as pure text copies.

    The Ackermann shape: the first parameter only ever shifts by a constant and is read
    through the delta machinery; the second is carried in its own home register and
    rebinds at every copy's entry. A copy must carry no stack state of its own, and the
    shapes the emitter refuses keep their real calls.
    """

    def _pair_labels(self, source: str, name: str) -> int:
        program, assembler = lower(source)
        prefix = f"{function_label(program.functions[0].name)}.pair"
        return sum(1 for label in assembler.labels if label.startswith(prefix))

    def test_pair_copies_carry_no_stack_state(self) -> None:
        program, assembler = lower(ACKERMANN_SHAPE)
        ack = function_label(program.functions[0].name)
        # The prologue saves both homes plus the chain counter r13; no argument pushes,
        # no rbp frame loads, no merge stores.
        self.assertEqual(assembler.pushes_of(ack), {"rbx", "r12", "r13"})
        self.assertEqual(assembler.pops_of(ack), {"rbx", "r12", "r13"})
        self.assertEqual(assembler.loads_of(ack), set())
        self.assertEqual(assembler.stores_of(ack), set())

    def test_the_n_chain_lowers_as_an_iterated_loop(self) -> None:
        body = region_bytes(ACKERMANN_SHAPE, "ack")
        # R(j) = f**(j+1)(1): the chain site becomes a counted loop - the counter takes
        # the enclosing value (mov r13, r12), the value register seeds at 1, and the
        # body is one m-shifted copy per iteration.
        self.assertIn(b"\x4d\x89\xe5", body)       # mov r13, r12
        self.assertIn(b"\x49\x83\xfd\x00", body)   # cmp r13, 0
        self.assertIn(b"\x49\xbc\x01", body)       # mov r12, 1 (the seed)
        # The loop replaces the 3**depth text-copy explosion: a 6-deep expansion used
        # to litter thousands of pair labels; the looped form stays linear.
        self.assertLessEqual(self._pair_labels(ACKERMANN_SHAPE, "ack"), 40)

    def test_the_value_register_rebinds_for_literal_copies(self) -> None:
        body = region_bytes(ACKERMANN_SHAPE, "ack")
        self.assertIn(b"\x49\xbc\x01", body)     # mov r12, 1: the n == 0 copy's literal bind
        self.assertIn(b"\x49\x89\xc4", body)     # mov r12, rax: a nested copy's result bind

    def test_a_shifted_m_compare_reads_the_delta_form(self) -> None:
        body = region_bytes(ACKERMANN_SHAPE, "ack")
        # Inside the outer copies the shifted condition folds into the compare:
        # `m - depth == 0` lowers as `cmp rbx, depth` - distinct literals down the copy
        # chain, and no redundant `lea rax, [rbx-1]` ahead of a test.
        self.assertIn(b"\x48\x83\xfb\x01", body)  # cmp rbx, 1
        self.assertIn(b"\x48\x83\xfb\x03", body)  # cmp rbx, 3
        self.assertNotIn(b"\x48\x8d\x43\xff", body)  # no unshifted lea rax, [rbx-1]

    def test_a_parameter_rebind_stays_on_the_real_call_path(self) -> None:
        source = ACKERMANN_SHAPE.replace(
            "if n == 0 { return ack(m - 1, 1); }",
            "if n == 0 { n = n - 1; return ack(m - 1, 1); }")
        self.assertEqual(self._pair_labels(source, "ack"), 0)

    def test_a_loop_in_the_body_stays_on_the_real_call_path(self) -> None:
        source = ACKERMANN_SHAPE.replace(
            "if m == 0 { return n + 1; }",
            "while m < 0 { return ack(m, n); } if m == 0 { return n + 1; }")
        self.assertEqual(self._pair_labels(source, "ack"), 0)

    def test_an_other_shaped_second_argument_stays_on_the_real_call_path(self) -> None:
        source = ACKERMANN_SHAPE.replace(
            "return ack(m - 1, ack(m, n - 1));",
            "return ack(m - 1, n * 2);")
        self.assertEqual(self._pair_labels(source, "ack"), 0)




MIX3_CALL_CHAIN = (
    "fn mix3(a: i64, b: i64, c: i64) -> i64 { "
    "var t: i64 = (a * 3) ^ (b << 2); "
    "if t > c { t = t - c; } else { t = t + c; } "
    "return t; } "
    "fn main() -> i64 { var x: i64 = 1; var i: i64 = 0; var total: i64 = 0; "
    "while i < 20000000 { "
    "x = mix3(x, i >> 2, i & 65535); total = total + (x & 255); i = i + 1; } "
    "print(total); return 0; }"
)

TWO_SLOT_FOREIGN_CALL = (
    "fn h(a: i64, b: i64) -> i64 { var t: i64 = a + b; "
    "if t > 10 { t = t - 1; } else { t = t + 1; } "
    "return t; } "
    "fn main() -> i64 { var x: i64 = h(4, 5); return h(x, 7); }"
)


def calls_of(source: str, caller: str, callee: str) -> int:
    """How many real `call` sites inside `caller`'s region target `callee`.

    Only a direct symbol call leaves a fixup on the bare function label; every other
    branch fixup lands on a label in a function's own namespace.
    """
    program = sema.analyze(parser.parse(source))
    artifact = CodeGenerator().generate(program)
    start = artifact.text_labels[function_label(caller)]
    later = sorted(offset for label, offset in artifact.text_labels.items()
                   if offset > start and label.startswith("fn.")
                   and label.count(".") == 1)
    end = later[0] if later else len(artifact.text)
    return sum(1 for offset, label in artifact.fixups
               if label == function_label(callee) and start <= offset < end)


class ForeignInlineTest(unittest.TestCase):
    """A call to another small, call-free function lowers as one copy of its body.

    gcc's edge on call_chain was inlining `mix3` entirely, and the same shape is what a
    hot helper wants here: the copy binds its parameters once from the argument registers
    (the callee can never observe a rebind order, because it cannot call), keeps its
    remaining slots in spare home registers, crosses a reused caller home on the stack,
    and leaves its result in rax. Callees that loop, call, or divide refuse the copy and
    stay real calls.
    """

    def test_a_small_foreign_function_is_copied_into_its_caller(self) -> None:
        self.assertEqual(calls_of(MIX3_CALL_CHAIN, "main", "mix3"), 0)

    def test_a_copy_labels_the_caller_namespace(self) -> None:
        program = sema.analyze(parser.parse(MIX3_CALL_CHAIN))
        assembler = Assembler()
        artifact = CodeGenerator(assembler=assembler).generate(program)
        self.assertIn("fn.mix3", artifact.text_labels)
        copies = [label for label in assembler.labels
                  if label.startswith("fn.mix3.copy.")]
        self.assertTrue(copies)

    def test_free_homes_absorb_a_two_slot_copy_without_any_push(self) -> None:
        program, assembler = lower(TWO_SLOT_FOREIGN_CALL)
        main = function_label("main")
        # main's prologue saves its one local's home (rbx); the two copies of h use
        # spare homes only, so no other push or pop appears anywhere in main.
        self.assertEqual([reg for owner, reg in assembler.pushes if owner == main],
                         ["rbx"])
        self.assertEqual([reg for owner, reg in assembler.pops if owner == main],
                         ["rbx"])

    def test_parameters_ride_their_argument_registers_through_a_copy(self) -> None:
        program, assembler = lower(MIX3_CALL_CHAIN)
        main = function_label("main")
        pushes = [reg for owner, reg in assembler.pushes if owner == main]
        pops = [reg for owner, reg in assembler.pops if owner == main]
        # main's prologue saves rbx, r12, r13 and the print helper round-trips rax;
        # the copy of mix3 needs nothing else: the parameters already sit in the ABI
        # argument registers (which the call-free body never rebinds) and its local
        # lives in a spare home, so no call-site stack traffic survives.
        self.assertEqual(pushes, ["rbx", "r12", "r13", "rax"])
        self.assertEqual(pops, ["rax", "r13", "r12", "rbx"])

    def test_a_parameter_rebind_falls_back_to_the_bind_once_copy(self) -> None:
        source = ("fn h(a: i64) -> i64 { a = a + 1; return a; } "
                  "fn main() -> i64 { var x: i64 = h(4); return x; }")
        self.assertEqual(calls_of(source, "main", "h"), 0)  # still a copy, just bound
        program, assembler = lower(source)
        copies = [label for label in assembler.labels
                  if label.startswith("fn.h.copy.")]
        self.assertEqual(len(copies), 1)

    def test_a_looping_callee_without_breaks_copies(self) -> None:
        source = ("fn h(a: i64) -> i64 { var t: i64 = 0; while t < a { t = t + 1; } "
                  "return t; } fn main() -> i64 { return h(5); }")
        self.assertEqual(calls_of(source, "main", "h"), 0)  # the loop copies now
        program, assembler = lower(source)
        copies = [label for label in assembler.labels
                  if label.startswith("fn.h.copy.")]
        self.assertEqual(len(copies), 1)

    def test_a_breaking_looping_callee_copies_too(self) -> None:
        source = ("fn h(a: i64) -> i64 { var t: i64 = 0;"
                  " while t < a { t = t + 1; if t == 3 { break; } } "
                  "return t; } fn main() -> i64 { return h(5); }")
        self.assertEqual(calls_of(source, "main", "h"), 0)  # breaks bind inside the copy

    def test_a_calling_callee_stays_a_real_call(self) -> None:
        source = ("fn g(v: i64) -> i64 { return v * 2; } "
                  "fn h(a: i64) -> i64 { return g(a + 1); } "
                  "fn main() -> i64 { return h(5); }")
        self.assertGreater(calls_of(source, "main", "h"), 0)

    def test_a_dividing_callee_stays_a_real_call(self) -> None:
        source = ("fn h(a: i64) -> i64 { return a / 2; } "
                  "fn main() -> i64 { return h(84); }")
        self.assertGreater(calls_of(source, "main", "h"), 0)

    def test_a_printing_callee_stays_a_real_call(self) -> None:
        source = ("fn h(a: i64) -> i64 { print(a); return a; } "
                  "fn main() -> i64 { return h(5); }")
        self.assertGreater(calls_of(source, "main", "h"), 0)

    def test_a_frame_full_callee_stays_a_real_call(self) -> None:
        # Nine slots cannot all live in home registers; the copy would read the caller's
        # frame, so the shape must refuse.
        body = " ".join(f"let v{index}: i64 = {index}; " for index in range(8))
        source = f"fn h(a: i64) -> i64 {{ {body}return v7; }} " \
                 "fn main() -> i64 { return h(3); }"
        self.assertGreater(calls_of(source, "main", "h"), 0)

    def test_a_one_argument_callee_copies_with_a_direct_bind(self) -> None:
        source = ("fn h(a: i64) -> i64 { var t: i64 = a + 1; "
                  "if t > 3 { t = t - 1; } return t; } "
                  "fn main() -> i64 { return h(9); }")
        self.assertEqual(calls_of(source, "main", "h"), 0)
        program, assembler = lower(source)
        main = function_label("main")
        self.assertEqual([reg for owner, reg in assembler.pushes if owner == main], [])


TAIL_RETURN_COPIES = (
    "fn h(a: i64) -> i64 { var t: i64 = a + 1; "
    "if t > 3 { t = t - 1; } "
    "return t; } "
    "fn main() -> i64 { var x: i64 = 5; while x < 100 { x = h(x); } print(x); return 0; }"
)

HOMED_CONDITION = (
    "fn main() -> i64 { var a: i64 = 7; var c: i64 = 5; "
    "if a < c { a = a + 1; } "
    "return a; }"
)


class InsideACopyTest(unittest.TestCase):
    """A copy runs once per loop iteration, so prologue-once costs must not repeat in it.

    The implicit-return-zero move, the rax round-trip of a two-name compare, and the
    move in front of a folded update are all loop-carried waste inside a copy. Each
    drops only when the shape proves it dead.
    """

    def test_a_tail_returning_copy_skips_the_implicit_return_zero(self) -> None:
        # mix3's shape: the body ends in a return, so no path falls off the copy's end.
        # The ten-byte zero move has no reason to run once per iteration; since the
        # prologue-slim gate (ADR-050) main's own implicit zero drops too, so only
        # main's tail `return 0` keeps one.
        body = region_bytes(TAIL_RETURN_COPIES, "main")
        zero = b"\x48\xb8\x00\x00\x00\x00\x00\x00\x00\x00"
        self.assertEqual(body.count(zero), 1)  # main's tail `return 0`

    def test_a_copy_whose_last_statement_is_not_a_return_keeps_the_implicit_zero(self) -> None:
        # Both branches of the tail if return, so the copy cannot fall off its end either —
        # but the last statement is an If, so the guard stays: one zero per copy remains
        # (main's tail `return 0` is the other; its implicit zero drops per ADR-050).
        source = TAIL_RETURN_COPIES.replace(
            "return t; } ", "if t > 3 { return t - 1; } else { return t + 1; } } ")
        body = region_bytes(source, "main")
        zero = b"\x48\xb8\x00\x00\x00\x00\x00\x00\x00\x00"
        self.assertEqual(body.count(zero), 2)

    def test_two_homed_names_compare_in_place(self) -> None:
        body = region_bytes(HOMED_CONDITION, "main")
        probe = Assembler()
        probe.cmp_reg_reg("rbx", "r12")
        self.assertIn(bytes(probe.code), body)

    def test_a_folded_update_reads_the_other_home_directly(self) -> None:
        source = ("fn h(a: i64, b: i64) -> i64 { var t: i64 = a; t = t + b; return t; } "
                  "fn main() -> i64 { return h(4, 5); }")
        body = region_bytes(source, "main")
        # h is a copy: b lives in the ABI register rdx, not a home, and the folded
        # `t = t + b` must read its register directly — no move through rax first.
        registers = HOME_REGISTERS + ARGUMENT_REGISTERS
        for home in registers:
            probe = Assembler()
            probe.binary(1, home, "rax")
            self.assertNotIn(bytes(probe.code), body)
        shapes = []
        for home in registers:
            for other in registers:
                if home == other:
                    continue
                probe = Assembler()
                probe.binary(1, home, other)
                shapes.append(bytes(probe.code))
        self.assertTrue(any(shape in body for shape in shapes))

    def test_two_homed_names_compare_never_routes_through_rax(self) -> None:
        body = region_bytes(HOMED_CONDITION, "main")
        probe = Assembler()
        probe.binary(0x39, "rbx", "rax")  # cmp rax against a name's home
        self.assertNotIn(bytes(probe.code), body)


push_rax = b"\x50"
pop_rcx = b"\x59"
pop_rdx = b"\x5a"
call_op = b"\xe8"
lea_rcx = b"\x48\x8d\x4b\xff"  # lea rcx, [rbx-1]
lea_rax_rbx_r12 = b"\x4a\x8d\x44\x21\x00"  # lea rax, [rcx + r12*1]
mov_rdx_r12 = b"\x4c\x89\xe2"  # mov rdx, r12
mov_rax_r12 = b"\x4c\x89\xe0"  # mov rax, r12
mov_rdx_rbx = b"\x48\x89\xda"  # mov rdx, rbx
add_rax_rcx = b"\x48\x01\xc8"  # add rax, rcx
sar_rdx_2 = b"\x48\xc1\xfa\x02"  # sar rdx, 2
movzx_r8d_bx = b"\x44\x0f\xb7\xc3"  # movzx r8d, bx
and_r8_65535 = b"\x49\x81\xe0\xff\xff\x00\x00"
movabs_one = b"\x48\xb8\x01"
test_rax_rax = b"\x48\x85\xc0"
cmp_rax_minus1 = b"\x48\x83\xf8\xff"


SUBSTITUTION_CHAIN = MIX3_CALL_CHAIN

REUSED_HOME_CHAIN = (
    "fn mix4(a: i64, b: i64, c: i64, d: i64) -> i64 { "
    "var u: i64 = a + b; var v: i64 = c + d; return u ^ v; } "
    "fn main() -> i64 { var v0: i64 = 1; var v1: i64 = 2; var v2: i64 = 3; "
    "var v3: i64 = 4; var v4: i64 = 5; var v5: i64 = 6; "
    "print(mix4(v0, v1, v2, v3)); return 0; }"
)

TWICE_USED_ARGUMENT = (
    "fn h(p: i64, q: i64) -> i64 { return p ^ q; } "
    "fn main() -> i64 { var x: i64 = 7; return h(x >> 1, x >> 1); }"
)


class ZeroExtendMaskTest(unittest.TestCase):
    """A mask of 255 or 65535 over a name is a zero extend, not an and-and-move.

    gcc lowers the call_chain argument `i & 65535` as one `movzwl`; the
    mov-then-and pair cost two instructions per binding for the same value. A
    name whose home is a frame slot keeps the generic mask: movzx reads only
    registers.
    """

    def _any_shape(self, source: str, builder) -> bool:
        """Whether any dest/src register pair of the builder's shape lands in main."""
        body = region_bytes(source, "main")
        for destination in (*HOME_REGISTERS, *ARGUMENT_REGISTERS):
            for src in HOME_REGISTERS:
                probe = Assembler()
                builder(probe, destination, src)
                if bytes(probe.code) in body:
                    return True
        return False

    def test_low_byte_mask_loads_the_home_without_mov_or_and(self) -> None:
        source = ("fn main() -> i64 { var x: i64 = 12345; "
                  "var t: i64 = x & 255; print(t); return t; }")
        self.assertTrue(self._any_shape(source, lambda a, d, s: a.movzx_from8(d, s)))

    def test_low_word_mask_loads_the_home_without_mov_or_and(self) -> None:
        source = ("fn main() -> i64 { var x: i64 = 12345; "
                  "var t: i64 = x & 65535; print(t); return t; }")
        self.assertTrue(self._any_shape(source, lambda a, d, s: a.movzx_from16(d, s)))

    def test_a_masked_argument_binds_as_one_movzx(self) -> None:
        source = ("fn h(c: i64) -> i64 { return c + 1; } "
                  "fn main() -> i64 { var x: i64 = 12345; "
                  "var t: i64 = h(x & 255); return t; }")
        self.assertTrue(self._any_shape(source, lambda a, d, s: a.movzx_from8(d, s)))

    def test_a_frame_slot_name_keeps_the_generic_mask(self) -> None:
        body = " ".join(f"let v{index}: i64 = {index + 1};" for index in range(7))
        source = f"fn main() -> i64 {{ {body} print(v6 & 255); return v6; }}"
        self.assertFalse(self._any_shape(source, lambda a, d, s: a.movzx_from8(d, s)))
        self.assertFalse(self._any_shape(source, lambda a, d, s: a.movzx_from16(d, s)))


class PureArgumentSubstitutionTest(unittest.TestCase):
    """A bare-name argument aliases into the copy instead of binding to a register.

    The call_chain loop used to bind `x` into an argument register and the copy
    read it back: one move per call that gcc does not pay. A bare name with a
    register home aliases - the copy's parameter slot resolves to the caller's
    home - so the bind move never happens and repeated uses read their caller
    home directly. Computed arguments still bind exactly once; a copy whose
    guest borrows caller homes does not alias onto them.
    """

    def test_the_call_chain_copy_binds_only_what_computes(self) -> None:
        # ADR-049: the shifted argument now forwards into its single use (the bind
        # and its sar are gone entirely); the mask and the bare-name alias hold.
        body = region_bytes(SUBSTITUTION_CHAIN, "main")
        probe = Assembler()
        probe.mov_reg_reg("rcx", "rbx")       # bind a := x
        self.assertNotIn(bytes(probe.code), body)
        self.assertIn(b"\x45\x0f\xb7\xc4", body)  # movzx r8d, r12w: the mask is the bind
        self.assertNotIn(sar_rdx_2, body)  # the shifted argument fuses at its use site

    def test_the_substituted_factor_reads_the_caller_home_directly(self) -> None:
        body = region_bytes(SUBSTITUTION_CHAIN, "main")
        shapes = []
        for destination in (*HOME_REGISTERS, *ARGUMENT_REGISTERS):
            for src in HOME_REGISTERS:
                probe = Assembler()
                probe.lea_scale(destination, src, src, 1, 0)   # `x * 3` off x's home
                shapes.append(bytes(probe.code))
        self.assertTrue(any(shape in body for shape in shapes))

    def test_a_twice_used_argument_copies_instead_of_calling(self) -> None:
        self.assertEqual(calls_of(TWICE_USED_ARGUMENT, "main", "h"), 0)

    def test_a_copy_whose_guest_borrows_caller_homes_still_copies(self) -> None:
        # mix4 has four parameters and two locals; main's homes are all busy, so
        # the copy's guest must borrow caller homes and the aliased reads refuse
        # where a borrow would erase them (correctness is the e2e twin).
        self.assertEqual(calls_of(REUSED_HOME_CHAIN, "main", "mix4"), 0)


class CommutativeHomePeepholeTest(unittest.TestCase):
    """A commutative binary whose left operand already lives in a register skips its copy.

    `x & y` lowerd as `mov rcx, x; mov rax, y; and rax, rcx` pays two moves for one
    register-register op; when the left operand's home is a register and the right operand
    cannot call, `and rax, home` is equivalent and shorter. The same fact decides a
    `cmp rax, 0` that immediately follows a ZF-setting op on rax: the flags already
    compare the result against zero.
    """

    def test_an_and_of_a_homed_left_operand_skips_the_rcx_copy(self) -> None:
        blob = region_bytes(
            "fn probe(a: i64, b: i64) -> i64 { return a & b; }"
            " fn main() -> i64 { return probe(15, 3); }", "probe")
        self.assertIn(and_rax_rbx, blob)          # and rax, rbx -- the left home directly
        self.assertNotIn(and_rax_rcx, blob)       # no via-rcx form
        self.assertNotIn(mov_rcx_rbx, blob)       # no left-operand copy into rcx

    def test_an_add_of_a_homed_left_operand_skips_the_rcx_copy(self) -> None:
        blob = region_bytes(
            "fn probe(a: i64, b: i64) -> i64 { return a + b; }"
            " fn main() -> i64 { return probe(15, 3); }", "probe")
        self.assertTrue(any(blob[i] in range(0x48, 0x50) and blob[i + 1] == 0x8d
                            for i in range(len(blob) - 1)),
                        "a two-home sum must be one lea over both homes")
        self.assertNotIn(and_rax_rcx, blob)       # no via-rcx form
        self.assertNotIn(mov_rcx_rbx, blob)       # no left-operand copy into rcx

    def test_a_zero_branch_after_an_and_skips_the_redundant_compare(self) -> None:
        blob = region_bytes(
            "fn check(a: i64, b: i64) -> i64 { if (a & b) != 0 { return 7; } return 0; }"
            " fn main() -> i64 { return check(240, 240); }", "check")
        self.assertIn(b"\x4c\x85", blob)          # test <home>, rbx -- flags only
        self.assertNotIn(and_rax_rbx, blob)       # no dead value computes into rax
        self.assertNotIn(and_rax_rcx, blob)
        self.assertNotIn(cmp_rax_zero, blob)      # the test's ZF decides the branch


# Commutative-home peephole reference encodings.
and_rax_rbx = b"\x48\x21\xd8"       # and rax, rbx
and_rax_rcx = b"\x48\x21\xc8"       # and rax, rcx
add_rax_rbx = b"\x48\x01\xd8"       # add rax, rbx
add_rax_rcx = b"\x48\x01\xc8"       # add rax, rcx
cmp_rax_zero = b"\x48\x83\xf8\x00"  # cmp rax, 0
mov_rcx_rbx = b"\x48\x89\xd9"       # mov rcx, rbx


class SameNameBinaryTest(unittest.TestCase):
    """`x * x` (and any same-name `+ & | ^`) loads its operand from a frame slot once.

    The two loads of the identical slot back to back buy nothing: one `mov load` into
    rax followed by `op rax, rax` computes the same value with half the frame traffic,
    which is most of an inner loop's cost when the operand is frame-resident.
    """

    def test_a_same_name_product_of_a_frame_slot_loads_once(self) -> None:
        blob = region_bytes(
            "fn probe() -> i64 { "
            + " ".join(f"var v{i}: i64 = {i};" for i in range(6))
            + " var g: i64 = 0; var i: i64 = 0;"
            " while i < 3 { g = g + 1; i = i + 1; }"
            " return g * g; }"
            " fn main() -> i64 { return probe(); }", "probe")
        self.assertIn(imul_rax_rax, blob)      # imul rax, rax
        self.assertNotIn(imul_rax_rcx, blob)   # no two-register product form

    def test_a_same_name_product_of_a_homed_slot_loads_once(self) -> None:
        blob = region_bytes(
            "fn probe(a: i64, b: i64) -> i64 { return a * a; }"
            " fn main() -> i64 { return probe(6, 7); }", "probe")
        self.assertIn(imul_rax_rax, blob)      # mov rax, rbx; imul rax, rax
        self.assertNotIn(imul_rax_rcx, blob)


class MultiplyHomeFastPathTest(unittest.TestCase):
    """A homed left operand multiplies in place, exactly like the M2 commutative forms."""

    def test_a_homed_left_operand_imuls_in_its_own_register(self) -> None:
        blob = region_bytes(
            "fn probe(a: i64, b: i64) -> i64 { return a * b; }"
            " fn main() -> i64 { return probe(6, 7); }", "probe")
        self.assertIn(imul_rax_rbx, blob)      # imul rax, rbx
        self.assertNotIn(imul_rax_rcx, blob)   # no parking copy into rcx


# Same-name and multiply fast-path reference encodings.
imul_rax_rax = b"\x48\x0f\xaf\xc0"  # imul rax, rax
imul_rax_rcx = b"\x48\x0f\xaf\xc1"  # imul rax, rcx
imul_rax_rbx = b"\x48\x0f\xaf\xc3"  # imul rax, rbx


FRAME_MAIN_CHAIN = (
    "fn twice(v: i64) -> i64 { return v + v; }"
    "fn main() -> i64 { "
    + " ".join(f"var a{i}: i64 = {i};" for i in range(6))
    + " var g: i64 = 0; var i: i64 = 0;"
    " while i < 3 { g = g + 1; i = i + 1; }"
    " return twice(g) + twice(2) + twice(a0) + g; }")


class FramePathInlineTest(unittest.TestCase):
    """A frame-path function (more slots than homes) still foreign-copies its callees.

    The rbp prologue never entered `current` into the generator state, so
    `_foreign_inline_target` refused every copy inside a 7+-slot function and left
    real calls behind where a leaf main of the same source shape inline-copies.
    """

    def test_a_frame_main_copies_a_call_free_callee(self) -> None:
        program = sema.analyze(parser.parse(FRAME_MAIN_CHAIN))
        self.assertGreater(program.functions[-1].slots, len(HOME_REGISTERS))
        self.assertEqual(calls_of(FRAME_MAIN_CHAIN, "main", "twice"), 0)

    def test_a_frame_main_copies_inside_a_loop_body_too(self) -> None:
        looped = (
            "fn twice(v: i64) -> i64 { return v + v; }"
            "fn main() -> i64 { "
            + " ".join(f"var a{i}: i64 = {i};" for i in range(6))
            + " var g: i64 = 0; var i: i64 = 0;"
            " while i < 3 { g = g + twice(g + a0); i = i + 1; }"
            " return g + a0 + a1 + a2 + a3 + a4 + a5; }")
        program = sema.analyze(parser.parse(looped))
        self.assertGreater(program.functions[-1].slots, len(HOME_REGISTERS))
        self.assertEqual(calls_of(looped, "main", "twice"), 0)


# A callee whose body calls another function (not copy-free) keeps its call inside a
# frame function (fallback parity), exactly as from a leaf function.
FRAME_MAIN_REBINDING = (
    "fn twice(v: i64) -> i64 { return v + v; }"
    "fn wrap(v: i64) -> i64 { return twice(v) + twice(v); }"
    "fn main() -> i64 { "
    + " ".join(f"var a{i}: i64 = {i};" for i in range(6))
    + " var g: i64 = 0; var i: i64 = 0;"
    " while i < 3 { g = g + 1; i = i + 1; }"
    " return wrap(g) + wrap(4) + g; }")


class FramePathRebindFallbackTest(unittest.TestCase):
    """A callee whose body calls another function keeps the real call from a frame function."""

    def test_the_calling_callee_stays_a_call(self) -> None:
        program = sema.analyze(parser.parse(FRAME_MAIN_REBINDING))
        self.assertGreater(program.functions[-1].slots, len(HOME_REGISTERS))
        self.assertEqual(calls_of(FRAME_MAIN_REBINDING, "main", "wrap"), 2)


# A six-parameter callee exceeds the ABI's four register parameters: args five and six
# arrive through the caller's stack area, which is why the shape previously kept its call
# and paid the call prologue's home tax on every pass.
SIX_PARAM_CALLEE = (
    "fn mix(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64) -> i64"
    " { return a * 31 + b * 37 + c * 41 + d * 43 + e * 47 + f * 53; }"
    "fn main() -> i64 { return mix(1, 2, 3, 4, 5, 6); }")

SIX_PARAM_REBINDING = (
    "fn mix(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64) -> i64"
    " { a = a + 1;"
    " return a * 31 + b * 37 + c * 41 + d * 43 + e * 47 + f * 53; }"
    "fn main() -> i64 { return mix(1, 2, 3, 4, 5, 6); }")

SIX_PARAM_CALLING = (
    "fn inner(x: i64) -> i64 { return x + 1; }"
    "fn mix(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64) -> i64"
    " { return inner(a) * 31 + b * 37 + c * 41 + d * 43 + e * 47 + f * 53; }"
    "fn main() -> i64 { return mix(1, 2, 3, 4, 5, 6); }")


class StackParamCopyTest(unittest.TestCase):
    """A six-parameter callee's copy parks its fifth and sixth arguments, not a call."""

    def test_a_six_parameter_callee_copies(self) -> None:
        program = sema.analyze(parser.parse(SIX_PARAM_CALLEE))
        self.assertEqual(program.functions[0].slots, 6)
        self.assertEqual(calls_of(SIX_PARAM_CALLEE, "main", "mix"), 0)

    def test_a_six_parameter_rebinding_callee_takes_the_bind_once_copy(self) -> None:
        program = sema.analyze(parser.parse(SIX_PARAM_REBINDING))
        self.assertEqual(program.functions[0].slots, 6)
        self.assertEqual(calls_of(SIX_PARAM_REBINDING, "main", "mix"), 0)

    def test_a_six_parameter_calling_callee_keeps_the_stack_call(self) -> None:
        program = sema.analyze(parser.parse(SIX_PARAM_CALLING))
        self.assertEqual(calls_of(SIX_PARAM_CALLING, "main", "mix"), 1)


# The frame-function shape the in-place memory forms fire on: the loop reads and
# updates the six fillers plus t and i, so all eight slots tie at depth 1 and the
# ties go to the later bindings, displacing a0/a1 ([rbp-8]/[rbp-16]) to the frame.
FRAME_IN_PLACE_MAIN = (
    "fn main() -> i64 { "
    + " ".join(f"var a{i}: i64 = {i};" for i in range(8))
    + " var t: i64 = 0; var i: i64 = 0;"
    " while i < 3 { t = t + a0 + a1 + a2 + a3 + a4 + a5 + a6 + a7; i = i + 1; }"
    " a0 = a0 + 2;"
    " a1 = a1 + 3;"
    " return t; }")

FRAME_IN_PLACE_TRIPLE = (
    "fn main() -> i64 { "
    + " ".join(f"var a{i}: i64 = {i};" for i in range(6))
    + " var t: i64 = 0; var u: i64 = 0; var i: i64 = 0;"
    " while i < 2 { i = i + a3 + a4; }"
    " a0 = a0 + a3;"
    " a0 = a0 - a3;"
    " u = u + 2;"
    " t = t + u;"
    " return t + a0; }")


class FrameInPlaceUpdateTest(unittest.TestCase):
    """A frame slot's `slot = slot op value` is one memory-operand instruction."""

    def test_a_displaced_counter_adds_in_place(self) -> None:
        blob = region_bytes(FRAME_IN_PLACE_MAIN, "main")
        # The loop-hot ties go to i (rbx) and t (r12): i's update folds into its
        # register and t accumulates into r12 straight from the displaced names'
        # rax loads (a2/a3 ride the r10/r11 extras, a4..a7 the six homes); a0/a1
        # stay frame-resident and update in the one-instruction memory forms.
        self.assertIn(bytes([0x48, 0x83, 0xC3, 0x01]), blob)    # add rbx, 1
        self.assertIn(bytes([0x49, 0x01, 0xC4]), blob)          # add r12, rax
        self.assertIn(bytes([0x48, 0x83, 0x45, (-8) & 0xFF, 0x02]), blob)   # add [rbp-8], 2
        self.assertIn(bytes([0x48, 0x83, 0x45, (-16) & 0xFF, 0x03]), blob)  # add [rbp-16], 3

    def test_a_frame_add_of_a_frame_name_folds_into_the_store(self) -> None:
        blob = region_bytes(FRAME_IN_PLACE_TRIPLE, "main")
        # a0 (slot 0, the ninth-ranked slot) adds a3's home straight into the
        # store: one memory add, no rax round trip, no register bind first.
        self.assertIn(bytes([0x4C, 0x01, 0x6D, (-8) & 0xFF]), blob)    # add [rbp-8], r13
        # a0 = a0 - a3, also frame-resident: the memory sub form.
        self.assertIn(bytes([0x4C, 0x29, 0x6D, (-8) & 0xFF]), blob)    # sub [rbp-8], r13

    def test_updates_beyond_disp8_take_the_four_byte_displacement(self) -> None:
        deep = ("fn main() -> i64 { "
                + " ".join(f"var a{i}: i64 = {i};" for i in range(20))
                + " var i: i64 = 0;"
                " while i < 2 { i = i + a5 + a6 + a7 + a8 + a9 + a10; }"
                " a17 = a17 + a0; return a17; }")
        blob = region_bytes(deep, "main")
        # The loop makes slots 5..10 and i the hottest set (a5 and a19 take the
        # r10/r11 extras), so a17 stays frame-resident at [rbp-144]: it routes
        # through rax and its fold takes the disp32 form add [rbp+disp32], rax.
        expected = bytes([0x48, 0x01, 0x85]) + ((-144) & 0xFFFFFFFF).to_bytes(4, "little")
        self.assertIn(expected, blob)


class HomeMapContractTest(unittest.TestCase):
    """`_home` reads the slot-to-home map, not the positional register list.

    The map is the assignment policy's surface: `homes` stays the ordered register list
    the save area iterates, and the map decides which slot owns it.
    """

    def test_a_slot_loses_its_home_when_the_map_does_not_grant_it(self) -> None:
        generator = CodeGenerator()
        generator.homes = ("rbx", "r12")
        generator.home_of = {1: "r12"}
        self.assertEqual(generator._home(1), "r12")
        self.assertIsNone(generator._home(0))

    def test_the_map_grants_homes_beyond_the_positional_default(self) -> None:
        generator = CodeGenerator()
        generator.homes = ("r13",)
        generator.home_of = {7: "r13"}
        self.assertEqual(generator._home(7), "r13")
        self.assertIsNone(generator._home(0))


# A frame main where the loop slots (i, s) sit at HIGHER slot indices than the dead
# fillers: identity assignment leaves i in the frame, preference homes it.
PREFERENCE_MAIN = (
    "fn main() -> i64 { "
    " var a0: i64 = 7; var f1: i64 = 1; var f2: i64 = 1; var f3: i64 = 1;"
    " var f4: i64 = 1; var f5: i64 = 1; var f6: i64 = 1; var s: i64 = 0; var i: i64 = 0;"
    " while i < 3 { s = s + 5; i = i + 1; }"
    " a0 = a0 + 2;"
    " print(s); print(a0); print(f1 + f2 + f3 + f4 + f5 + f6);"
    " return 0; }")


class HomePreferenceTest(unittest.TestCase):
    """A frame function grants the registers to its deepest-loop slots, not its first six."""

    def test_a_loop_slot_beyond_the_identity_prefix_homes_as_a_register(self) -> None:
        blob = region_bytes(PREFERENCE_MAIN, "main")
        # i = slot 6, s = slot 5, both rank 1; preference homes them first (rbx, r12),
        # so their self-updates fold in place: add rbx, 1 and add r12, 5.
        self.assertIn(bytes([0x48, 0x83, 0xC3, 0x01]), blob)   # add rbx, 1
        self.assertIn(bytes([0x49, 0x83, 0xC4, 0x05]), blob)   # add r12, 5

    def test_a_displaced_depth_zero_slot_takes_the_memory_form(self) -> None:
        blob = region_bytes(PREFERENCE_MAIN, "main")
        # a0 = slot 0: the loop slots and every higher slot outrank it (f2/f1
        # take the r10/r11 extras), so `a0 = a0 + 2` becomes one memory add
        # instead of the register fold identity assignment gave it.
        self.assertIn(bytes([0x48, 0x83, 0x45, (-8) & 0xFF, 0x02]), blob)

    def test_a_leaf_function_keeps_its_declaration_order(self) -> None:
        leaf = ("fn main() -> i64 { var a: i64 = 1; var b: i64 = 2; var c: i64 = 0;"
                " while c < 3 { a = a + b; c = c + 1; } return a; }")
        blob = region_bytes(leaf, "main")
        # a = slot 0 keeps rbx; the fold adds b's home directly: add rbx, r12.
        self.assertIn(bytes([0x4C, 0x01, 0xE3]), blob)   # add rbx, r12


class ImulImmediateShapeTest(unittest.TestCase):
    """A value-position `homed * literal` emits the imul, not a constant load."""

    def test_a_const_multiplied_home_takes_the_three_operand_form(self) -> None:
        blob = region_bytes("fn main() -> i64 { var a: i64 = 3; var b: i64 = a * 37;"
                            " var c: i64 = a * 171; print(b + c + a); return 0; }", "main")
        # a homes in rbx; both products take the three-operand imul with the
        # destination register holding the result: no constant re-load, no imul rax.
        self.assertIn(bytes([0x4C, 0x6B, 0xE3, 0x25]), blob)     # imul r12, rbx, 37
        self.assertIn(bytes([0x4C, 0x69, 0xEB, 0xAB, 0x00, 0x00, 0x00]), blob)  # 171
        self.assertNotIn(bytes([0x48, 0xB8, 0x25, 0x00, 0x00, 0x00]), blob)     # no movabs 37
        self.assertNotIn(bytes([0x4C, 0x0F, 0xAF]), blob)        # no two-operand imul


class CommutativeMergeShapeTest(unittest.TestCase):
    """A commutative chain folds every step into the destination register itself."""

    def test_a_chain_addition_folds_into_the_destination_home(self) -> None:
        blob = region_bytes("fn main() -> i64 { var m: i64 = 0; var t: i64 = 1;"
                            " var u: i64 = 2; m = m + t + u; print(m); return 0; }", "main")
        # m, t, u home in declaration order (rbx, r12, r13): each chain step is one
        # add into m's home, with no parked rcx copy and no rax round-trip.
        self.assertIn(bytes([0x4C, 0x01, 0xE3]), blob)   # add rbx, r12
        self.assertIn(bytes([0x4C, 0x01, 0xEB]), blob)   # add rbx, r13

    def test_a_multiply_accumulate_chain_never_parks_its_accumulator(self) -> None:
        blob = region_bytes("fn main() -> i64 { var a: i64 = 1; var b: i64 = 2;"
                            " var c: i64 = 3; var s: i64 = a * 31 + b * 37 + c * 41;"
                            " print(s); return 0; }", "main")
        # The chain of homed-times-literal products folds straight into the
        # destination: one imul per term plus one add, no rcx park and no
        # temp-to-rax round-trip per step.
        self.assertIn(bytes([0x49, 0x6B, 0xC4, 0x25]), blob)     # imul rax, r12, 37
        self.assertNotIn(bytes([0x48, 0x89, 0xD9]), blob)        # no mov rcx, r11


class ParkedCopyArgumentTest(unittest.TestCase):
    """A five-or-six-argument copy binds its spare arguments into their guest homes."""

    SOURCE = ("fn mix(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64) -> i64 {"
              " return a + b + c + d + e + f; }"
              " fn main() -> i64 { var i: i64 = 0; var total: i64 = 0;"
              " while i < 3 { total = total + mix(i, i + 1, i + 2, i + 3, i + 4, i + 5);"
              " i = i + 1; } print(total); return 0; }")

    def test_spare_arguments_land_in_their_guest_homes(self) -> None:
        blob = region_bytes(self.SOURCE, "main")
        # guest is (rcx, rdx, r8, r9, r13, r14): arguments 4 and 5 compute straight
        # into r13 and r14 instead of parking in r11/r10 for an entry-time rebind.
        self.assertIn(bytes([0x4C, 0x8D, 0x6B, 0x04]), blob)   # lea r13, [rbx+4]
        self.assertIn(bytes([0x4C, 0x8D, 0x73, 0x05]), blob)   # lea r14, [rbx+5]

    def test_the_copy_entry_skips_the_rebind_pair(self) -> None:
        blob = region_bytes(self.SOURCE, "mix")
        self.assertNotIn(bytes([0x4D, 0x89, 0xDD]), blob)      # no mov r13, r11
        self.assertNotIn(bytes([0x4D, 0x89, 0xD6]), blob)      # no mov r14, r10


class HoistedMagicDivisionTest(unittest.TestCase):
    """A loop's magic division reads its seed and divisor from hoisted spare homes."""

    def test_a_looped_modulo_hoists_the_divisor_and_seed(self) -> None:
        blob = region_bytes("fn main() -> i64 { var i: i64 = 0; var t: i64 = 0;"
                            " while i < 10 { t = t + (i * 48271) % 2147483647;"
                            " i = i + 1; } print(t); return 0; }", "main")
        # The divisor and its seed hoist into spare homes; the remainder multiplies
        # the quotient by the hoisted divisor register instead of reloading it.
        self.assertIn(bytes([0x49, 0x0F, 0xAF, 0xD5]), blob)    # imul rdx, r13
        self.assertIn(bytes([0x4C, 0x89, 0xF6]), blob)          # mov rsi, r14 (seed)
        self.assertNotIn(bytes([0x48, 0xBE, 0xFF, 0xFF, 0xFF, 0x7F]), blob)  # no
        #                                                            movabs rsi, 0x7fffffff
        self.assertNotIn(bytes([0x48, 0xBE, 0x03, 0x00, 0x00, 0x00,
                                0x01, 0x00, 0x00, 0x80]), blob)  # no movabs rsi, seed


class ForeignLoopCopyTest(unittest.TestCase):
    """A foreign callee whose body loops copies in place like any other."""

    SOURCE = ("fn loopy(n: i64) -> i64 { var t: i64 = 0;"
              " while t < n { t = t + 3; } return t; }"
              " fn main() -> i64 { print(loopy(20)); return 0; }")

    def test_the_callees_loop_lowers_inside_the_caller(self) -> None:
        blob = region_bytes(self.SOURCE, "main")
        forms = {bytes([rex, 0x83, modrm, 0x03])
                 for rex in (0x48, 0x49) for modrm in range(0xC0, 0xC8)}
        self.assertTrue(any(form in blob for form in forms), "no copied loop fold in main")


class ShiftThroughFusionTest(unittest.TestCase):
    """`(a >> m) << m` fuses into one and-immediate, across call boundaries too."""

    DIRECT = ("fn main() -> i64 { var x: i64 = 1234567890123;"
              " var y: i64 = (x >> 3) << 3; print(y); return 0; }")

    REFUSED_COUNTS = ("fn main() -> i64 { var x: i64 = 1234567890123;"
                      " var y: i64 = (x >> 3) << 2; print(y); return 0; }")

    FORWARDED = ("fn f3(a: i64, b: i64, c: i64) -> i64 {"
                 " var t: i64 = (a * 3) ^ (b << 2);"
                 " if t > c { t = t - c; } else { t = t + c; } return t; }"
                 " fn main() -> i64 { var i: i64 = 0; var x: i64 = 1;"
                 " while i < 8 { x = f3(x, i >> 2, i); i = i + 1; }"
                 " print(x); return 0; }")

    REFUSED_DOUBLE = ("fn f3(a: i64, b: i64, c: i64) -> i64 {"
                      " var t: i64 = (a * 3) ^ (b << 2);"
                      " if t > c { t = t - c; } else { t = (t | (b & 7)) + c; }"
                      " return t; }"
                      " fn main() -> i64 { var i: i64 = 0; var x: i64 = 1;"
                      " while i < 8 { x = f3(x, i >> 2, i); i = i + 1; }"
                      " print(x); return 0; }")

    def test_a_direct_shift_pair_lowers_as_one_and(self) -> None:
        blob = region_bytes(self.DIRECT, "main")
        and_forms = {bytes([rex, 0x83, modrm, 0xf8])
                     for rex in (0x48, 0x49, 0x4c, 0x4d)
                     for modrm in range(0xE0, 0xE8)}
        self.assertTrue(any(form in blob for form in and_forms), "no fused and-immediate -8")
        self.assertNotIn(b"\xc1\xfc\x03", blob, "sar rax, 3 survived the fusion")
        self.assertNotIn(b"\xc1\xe4\x03", blob, "shl rax, 3 survived the fusion")

    def test_unequal_shift_counts_stay_two_instructions(self) -> None:
        blob = region_bytes(self.REFUSED_COUNTS, "main")
        self.assertIn(b"\xc1\xfc\x03", blob, "sar rax, 3 must survive a k != m pair")
        self.assertIn(b"\xc1\xe4\x02", blob, "shl rax, 2 must survive a k != m pair")

    def test_a_forwarded_parameter_leaves_no_sar_behind(self) -> None:
        blob = region_bytes(self.FORWARDED, "main")
        self.assertNotIn(b"\xc1\xfa\x02", blob, "the forwarded argument still pays sar rdx, 2")
        self.assertIn(b"\x48\x83\xe0\xfc", blob, "no fused and rax, -4 at the copy's use site")

    def test_a_twice_used_parameter_keeps_its_bind(self) -> None:
        blob = region_bytes(self.REFUSED_DOUBLE, "main")
        self.assertIn(b"\xc1\xfa\x02", blob, "a twice-read parameter must keep its sar bind")


class PrologueSlimTest(unittest.TestCase):
    """The entry prologue carries only what the body leaves undefined.

    A body whose last statement returns reaches the epilogue with rax already
    holding the value, so the implicit return zero is dead; a slot whose first
    top-level touch assigns it needs no prologue clear; and `name = 0` lowers
    as the xor zero-idiom rather than a ten-byte move of a zero constant.
    """

    RETURNING = ("fn f(a: i64) -> i64 { return a + 1; }"
                 " fn main() -> i64 { print(f(2)); return 0; }")

    BRANCHED = ("fn f(a: i64) -> i64 { if a > 4 { return 5; } else { return 9; } }"
                " fn main() -> i64 { print(f(9)); return 0; }")

    LOCAL_ZERO = ("fn f(a: i64) -> i64 { var t: i64 = 0; var u: i64 = t + a;"
                  " print(u + t); return 0; }"
                  " fn main() -> i64 { print(f(2)); return 0; }")

    XOR_IDIOMS = {bytes([rex, 0x31, 0xC0 | ((code & 7) << 3) | (code & 7)])
                  for rex in (0x40, 0x45)
                  for code in range(16)}

    def test_a_final_return_skips_the_implicit_zero(self) -> None:
        blob = region_bytes(self.RETURNING, "f")
        self.assertNotIn(b"\x48\xb8" + b"\x00" * 8, blob,
                         "mov rax, 0 still runs on entry of a final-return body")

    def test_a_body_without_a_final_return_keeps_the_implicit_zero(self) -> None:
        blob = region_bytes(self.BRANCHED, "f")
        self.assertIn(b"\x48\xb8" + b"\x00" * 8, blob,
                      "a fall-through margin must keep the implicit return zero")

    def test_a_zero_provenance_region_emits_exactly_one_xor(self) -> None:
        # f clears t and u today (2 xor idioms); after the change `var t = 0` is
        # itself the one xor and u's first Let needs no clear.
        blob = region_bytes(self.LOCAL_ZERO, "f")
        count = sum(blob.count(pattern) for pattern in self.XOR_IDIOMS)
        self.assertEqual(count, 1,
                         "the zero provenance region should carry exactly one xor idiom")


class SelectIfOrderTest(unittest.TestCase):
    """The select lowering keeps its plus candidate off the critical path.

    `t - c` and `t + c` are both one cycle after `t` only when the plus candidate's
    `lea` reads `t`'s home *before* the flag-setting `sub` rewrites it. A `lea` that
    sits between the `sub` and the `cmov` chains the select one cycle deeper, which
    is a whole serial step on every loop that leans on the select.
    """

    SELECT = ("fn f(a: i64, c: i64) -> i64 {"
              " var t: i64 = a * 3;"
              " if t > c { t = t - c; } else { t = t + c; }"
              " return t; }"
              "fn main() -> i64 { print(f(7, 3)); return 0; }")

    CMOV_OPCODES = (b"\x0f\x4e", b"\x0f\x4f")

    def test_the_lowering_selects_branchlessly(self) -> None:
        body = region_text(self.SELECT, "f")
        self.assertTrue(any(opcode in body for opcode in self.CMOV_OPCODES),
                        "the select-if shape must lower to a cmov, not a branch")

    def test_no_lea_between_the_flag_setting_sub_and_the_cmov(self) -> None:
        body = region_text(self.SELECT, "f")
        cmov = min(body.index(opcode) for opcode in self.CMOV_OPCODES
                   if opcode in body)
        sub = body[:cmov].rindex(b"\x29")          # the last sub before the select
        between = body[sub + 1:cmov]
        self.assertNotIn(b"\x8d", between,
                         "the plus candidate must not chain through the sub's result")


class StrByteInlineTest(unittest.TestCase):
    """`str_byte(s, j)` with a leaf index lowers inside the caller, with no call.

    The scan-loop shape pays the runtime helper's call/ret round trip for a byte;
    the inlined form runs the same sign and NUL tests in place (r10 holds the
    pointer across the leaf load, rax both the index and the answer), so a data-
    dependent byte walk loses its two branch-and-link stalls per byte.
    """

    SOURCE = ('fn main() -> i64 { let s: i64 = "scan me";'
              " var i: i64 = 0;"
              " while i < str_len(s) {"
              " var b: i64 = str_byte(s, i);"
              " if b >= 0 { i = i + 1; } else { exit(1); } }"
              " return str_len(s); }")

    def test_a_leaf_index_loads_inside_the_function_body(self) -> None:
        body = region_text(self.SOURCE, "main")
        self.assertIn(b"\x0f\xb6", body,
                      "the byte load must sit in main's body, not only the runtime")

    def test_the_inline_keeps_the_minus_one_answer(self) -> None:
        body = region_text(self.SOURCE, "main")
        # mov rax, -1 (48 c7 c0 ff...) rides the miss label, exactly per contract.
        mov_minus_one_present = any(b"\xff\xff\xff\xff\xff" in body[i:i + 10]
                                    for i, byte in enumerate(body)
                                    if byte == 0xb8)
        self.assertTrue(mov_minus_one_present,
                        "the inlined miss path must still answer -1 via mov rax, -1")


class DirectVarCopyTest(unittest.TestCase):
    """A variable-to-variable copy moves straight into the target's home register.

    The swap shape `t = a; a = b; b = t` runs inside the hottest loops it feeds
    (every Stein-gcd iteration round), and the round trip through rax paid two
    moves per copy where one direct `mov dst_home, src_home` does the same work.
    The copies are sequential in source order and each reads a value no earlier
    copy in the run has overwritten, so each one is a single register move.
    """

    SWAP = ("fn f(a: i64, b: i64) -> i64 {"
            " var t: i64 = 0; t = a; a = b; b = t;"
            " return b; }"
            "fn main() -> i64 { print(f(9, 3)); return 0; }")

    @staticmethod
    def reg_reg_movs(code: bytes):
        """Decode 3-byte REX.W register-to-register movs as (dst, src) register numbers."""
        moves = []
        for i in range(len(code) - 2):
            byte0, byte1, byte2 = code[i], code[i + 1], code[i + 2]
            if (0x40 <= byte0 <= 0x4F and byte0 & 0x08 and byte1 == 0x89
                    and byte2 >= 0xC0):
                dst = ((byte2 >> 3) & 7) | (8 if byte0 & 0x04 else 0)
                src = (byte2 & 7) | (8 if byte0 & 0x01 else 0)
                moves.append((dst, src))
        return moves

    def test_no_copy_round_trips_through_rax(self) -> None:
        body = region_bytes(self.SWAP, "f")
        moves = self.reg_reg_movs(body)
        round_trips = sum(1 for i, (dst, _) in enumerate(moves[: -1])
                          if dst == 0 and moves[i + 1][1] == 0)
        self.assertEqual(round_trips, 0,
                         "var-to-var copies must not route through rax")


class NameMaskBitTestTest(unittest.TestCase):
    """`if (free & bit) != 0` branches from `test free_home, bit_home` alone.

    The recursion scans a bitmask column set, so the masked-nonzero test decides
    every node's fan-out; the general path paid `mov rax, bit` before the `and`
    that the flags already read, and `test` computes the same ZF with no copy.
    """

    MASKED = ("fn f(free: i64, bit: i64) -> i64 {"
              " var c: i64 = 0;"
              " if (free & bit) != 0 { c = c + 1; }"
              " return c; }"
              "fn main() -> i64 { print(f(12, 4)); return 0; }")

    def test_the_masked_nonzero_condition_tests_two_registers(self) -> None:
        body = region_bytes(self.MASKED, "f")
        # a two-register test: opcode 0x85 with register operands, not test rax, rax
        two_register = any(body[i] in range(0x40, 0x50) and body[i + 1] == 0x85
                           and body[i + 2] >= 0xC0 and body[i + 2] != 0xC0
                           for i in range(len(body) - 2))
        self.assertTrue(two_register,
                        "the masked condition must test the two homes directly")

    def test_the_masked_nonzero_condition_keeps_no_dead_and(self) -> None:
        body = region_bytes(self.MASKED, "f")
        dead_ands = any(body[i] in range(0x40, 0x50) and body[i + 1] == 0x21
                        for i in range(len(body) - 1))
        self.assertFalse(dead_ands,
                         "the condition's and must not compute a dead value")


class DivisorNoOverflowGuardTest(unittest.TestCase):
    """A proven non-negative divisor skips idiv's minus-one overflow guards.

    `idiv` faults only on a zero divisor or `INT64_MIN / -1`. When the divisor
    slot provably holds a non-negative value (a literal init, positive updates),
    the `-1` divisor test and the `INT64_MIN` dividend probe are unreachable, and
    the trial-division loop pays five instructions less per division.
    """

    POSITIVE = ("fn probe(n: i64) -> i64 {"
                " var d: i64 = 3;"
                " return n / d; }"
                "fn main() -> i64 { print(probe(30)); return 0; }")

    def test_a_nonnegative_divisor_keeps_only_the_zero_trap(self) -> None:
        body = region_bytes(self.POSITIVE, "probe")
        self.assertNotIn(b"\x48\x83\xf8\xff", body,
                         "no cmp rax, -1 divisor test when the divisor is non-negative")

    def test_a_negative_divisor_keeps_both_guards(self) -> None:
        body = region_bytes(
            "fn probe(n: i64) -> i64 { var d: i64 = -3; return n / d; }"
            "fn main() -> i64 { print(probe(30)); return 0; }", "probe")
        self.assertIn(b"\x48\x83\xf8\xff", body,
                      "cmp rax, -1 must stay for an unchecked divisor")


class HomeSumLeaTest(unittest.TestCase):
    """`a + b` with both names homed is one lea, not a load plus an add.

    The mandelbrot escape guard computes `x2 + y2` every iteration, and every
    register-bound loop sums values; the general path pays a move plus an add
    where `lea rax, [home + home]` reads both homes in one instruction.
    """

    SUM = ("fn probe(a: i64, b: i64) -> i64 { return a + b; }"
           "fn main() -> i64 { print(probe(2, 3)); return 0; }")

    def test_a_two_home_sum_is_one_lea_with_no_add(self) -> None:
        body = body_text(self.SUM, "probe")
        self.assertTrue(any(body[i] in range(0x48, 0x50) and body[i + 1] == 0x8d
                            for i in range(len(body) - 1)),
                        "the sum must use a lea")
        self.assertNotIn(b"\x48\x01", body, "no add when both homes exist")
class ExtraHomeFrameTest(unittest.TestCase):
    """A frame function grants its seventh and eighth hot slots r10/r11.

    Frame functions keep every slot in the frame unless it ranks top six; a hot
    seventh live value reloads [rbp-..] every iteration. The extras are
    caller-saved, so every call site protects them with push/pop, and every
    lowering that borrows r10/r11 (the temporary pool, str_byte's inline
    scratch) must see the shrunken pool.
    """

    HOT = ("fn hot(n: i64) -> i64 { "
           "var a: i64 = 3; var b: i64 = 5; var c: i64 = 7; var d: i64 = 11; "
           "var e: i64 = 13; var f: i64 = 17; var g: i64 = 19; var h: i64 = 23; "
           "var i: i64 = 0; "
           "while i < n { "
           "a = a + b; b = b + c; c = c + d; d = d + e; g = g + h; h = h + i; i = i + 1; } "
           "print(a); return g; } "
           "fn main() -> i64 { print(hot(100)); return 0; }")

    WIDE = ('fn main() -> i64 { let s: i64 = "abc"; var p: i64 = 1; var q: i64 = 2; '
            "var r: i64 = 3; var t: i64 = 4; var u: i64 = 5; var v: i64 = 6; "
            "var b: i64 = str_byte(s, 1); "
            "p = p + b; q = q + b; r = r + b; t = t + b; u = u + b; v = v + b; "
            "print(p + q + r + t + u + v); return b; }")

    def test_a_call_site_pushes_and_pops_the_extra_homes(self) -> None:
        body = region_text(self.HOT, "hot")
        self.assertIn(b"\x41\x52", body,  # push r10 across the print call
                      "an extra home must survive every machine call")
        self.assertIn(b"\x41\x5a", body,  # pop r10 restores it after the call
                      "the extra home is restored, not zeroed, by the call")

    def test_str_byte_inline_bails_when_r10_is_a_home(self) -> None:
        body = region_text(self.WIDE, "main")
        self.assertNotIn(b"\x0f\xb6", body,
                         "r10 holds a live value, so the byte load must use the helper")
class StrByteSibLoadTest(unittest.TestCase):
    """str_byte(s, j) with homed pointer and homed, proven-nonnegative index.

    The scan-loop shape (`while j < str_len(s)`) pays four dead instructions
    per byte: a pointer hop through rax, an index hop through rax, a lea into
    r10, and an unreachable sign test. The byte load folds into one movzx.
    """

    SCAN = ('fn main() -> i64 { let s: i64 = "abcdefghijklmnopqrstuvwxyz";'
            " var j: i64 = 0; var total: i64 = 0;"
            " while j < str_len(s) {"
            " total = total + str_byte(s, j);"
            " j = j + 1; }"
            " return total; }")

    NEG = ('fn main() -> i64 { let s: i64 = "abc"; var j: i64 = 0;'
           " var total: i64 = 0;"
           " while j < 3 {"
           " total = total + str_byte(s, j);"
           " j = j + 1; }"
           " var q: i64 = -1;"
           " j = q;"
           " while j < 3 {"
           " total = total + str_byte(s, j);"
           " j = j + 1; }"
           " return total; }")

    def test_the_byte_load_folds_into_one_sib_movzx(self) -> None:
        body = region_text(self.SCAN, "main")
        # movzx r32, [base + index]: 0F B6 with SIB addressing (rm == 100b).
        sib = False
        for i in range(len(body) - 3):
            if body[i] == 0x0F and body[i + 1] == 0xB6 and (body[i + 2] & 7) == 0x04:
                sib = True
        self.assertTrue(sib, "the byte load must address [ptr_home + idx_home] directly")

    def test_the_pointer_never_hops_through_rax(self) -> None:
        body = region_text(self.SCAN, "main")
        self.assertNotIn(b"\x49\x89\xc2", body,  # mov r10, rax
                         "the pointer binds straight into r10, no rax hop")

    def test_an_unproven_index_keeps_the_sign_test(self) -> None:
        body = region_text(self.NEG, "main")
        # j = -1 drops the non-negative fact: the sign branch must come back
        # (test reg, reg + js) before any byte load in that pass.
        self.assertTrue(b"\x78" in body or b"\x0f\x88" in body,
                        "a negative index possibility keeps the sign test")
class ArgByteInlineTest(unittest.TestCase):
    """arg_byte(0, j) loads its cache slot rip-relative right at the call site.

    argv_walk pays a call plus twelve helper instructions for every byte of
    the program path. With a literal token 0 the slot 0 pointer and length
    bind as static .data loads, the index home checks sign and length in
    registers, and the byte folds into movzx eax, [r10 + home]. Misses (a
    negative j, j at or past the length, an unfilled table) call the runtime
    helper unchanged, and it answers the same -1 wrappers it always did.
    """

    WALK = ("fn main() -> i64 { var total: i64 = 0; var j: i64 = 0;"
            " while j < 64 { var c: i64 = arg_byte(0, j);"
            " if c >= 0 { total = total + c; } j = j + 1; }"
            " print(total); return 0; }")

    def test_the_slot0_blob_binds_rip_relative(self) -> None:
        body = region_text(self.WALK, "main")
        self.assertIn(bytes([0x4C, 0x8B, 0x15]), body,  # mov r10, [rip+disp32]
                      "the inlined path reads the slot 0 pointer straight from .data")
        self.assertIn(bytes([0x4C, 0x8B, 0x1D]), body,  # mov r11, [rip+disp32]
                      "the inlined path reads the token length straight from .data")

    def test_the_byte_loads_off_the_loaded_pointer(self) -> None:
        body = region_text(self.WALK, "main")
        self.assertIn(bytes([0x0F, 0xB6]), body,  # movzx eax, byte [r10 + home]
                      "the byte load folds the loaded pointer and the index home")
        self.assertIn(bytes([0x0F, 0x8D]), body,  # jge past-end (rel32)
                      "the length check branches on the register comparison")

    MASKED = ("fn main() -> i64 { var total: i64 = 0; var j: i64 = 0;"
             " while j < 64 { var c: i64 = arg_byte(0, (j + 5) & 63);"
             " if c >= 0 { total = total + c; } j = j + 1; }"
             " print(total); return 0; }")

    def test_a_masked_index_drops_the_sign_test(self) -> None:
        # (j + 5) & 63 is non-negative for every j - the AND mask clears the
        # sign bit - so the fast path keeps only the length check.
        body = region_text(self.MASKED, "main")
        self.assertNotIn(bytes([0x0F, 0x88]), body,  # js to the miss route
                         "the masked index cannot go negative, so no sign test")
        self.assertIn(bytes([0x0F, 0x8D]), body,  # jge past-end (rel32)
                      "the mask proves nothing about the length, which still checks")

    # j walks 0..63 and cannot go negative, so the fact-holding scan shape
    # drops the sign test; a parameter index carries no such fact and one
    # shape must exercise it.
    PARAM = ("fn probe(k: i64) -> i64 { var c: i64 = arg_byte(0, k);"
             " if c >= 0 { return c; } return -2; }"
             " fn main() -> i64 { print(probe(5)); return 0; }")

    def test_a_negative_index_never_leaves_the_table(self) -> None:
        body = region_text(self.PARAM, "probe")
        self.assertIn(bytes([0x0F, 0x88]), body,  # js to the miss route (rel32)
                      "a negative index still answers -1, through the helper call")
        self.assertIn(bytes([0x4C, 0x8B, 0x15]), body,  # mov r10, [rip+disp32]
                      "the rip-relative slot 0 pointer binds in the inline too")


class ZeroCompareFlagReuseTest(unittest.TestCase):
    """A homed name compared against zero right after its sub leaves ZF set.

    binary_gcd's subtraction loop pays `cmp $0, home` after every `sub
    home, left` even though the sub already set ZF from exactly the value
    the `==`/`!=` test reads. When the comparison is an equality against
    the integer zero and the assembler's zF summary names the very home,
    the re-compare is dropped and the branch keeps the sub's flags. Every
    signed comparison still emits its cmp, because jge/jl need SF and OF,
    and any other emitted byte (store, move, label) invalidates the
    summary by construction.
    """

    LOOP = ("fn probe(a: i64, b: i64) -> i64 { b = b - a;"
            " if b == 0 { return 7; } return b; }"
            " fn main() -> i64 { print(probe(12, 18)); return 0; }")

    @staticmethod
    def _cmp_zero_forms() -> tuple:
        forms = []
        for reg in range(16):
            forms.append(bytes([0x48 | (reg >> 3), 0x83,
                                0xC0 | (7 << 3) | (reg & 7), 0x00]))
        return tuple(forms)

    def test_an_equal_zero_compare_after_sub_keeps_the_sub_flag(self) -> None:
        body = region_text(self.LOOP, "probe")
        for form in self._cmp_zero_forms():
            self.assertNotIn(form, body,
                             "the break check re-compares a value the sub just zero-tested")

    def test_the_sub_and_the_branch_fall_directly_adjacent(self) -> None:
        body = region_text(self.LOOP, "probe")
        self.assertIn(bytes([0x29]), body,
                      "the in-place subtraction is present to carry the zero flag")

    NOT_EQUAL = ("fn probe(a: i64, b: i64) -> i64 { b = b - a;"
                 " if b != 0 { return 7; } return b; }"
                 " fn main() -> i64 { print(probe(12, 18)); return 0; }")

    def test_an_inequal_zero_compare_reuses_the_flag_too(self) -> None:
        body = region_text(self.NOT_EQUAL, "probe")
        for form in self._cmp_zero_forms():
            self.assertNotIn(form, body,
                             "!= 0 reads the same zero flag as == 0")

    SIGNED = ("fn probe(a: i64, b: i64) -> i64 {"
              " b = b - a; if b >= 0 { return 7; } return b; }"
              " fn main() -> i64 { print(probe(12, 18)); return 0; }")

    def test_a_signed_compare_still_emits_its_own_compare(self) -> None:
        # jge reads SF and OF, which a plain sub against an unknown operand
        # does not carry the equality reading from, so the cmp must stay.
        body = region_text(self.SIGNED, "probe")
        self.assertTrue(any(form in body for form in self._cmp_zero_forms()),
                        "a signed zero comparison re-compare keeps its cmp")

class ScanIndexSignGuardTest(unittest.TestCase):
    """A zero-initialized loop index never pays the inline's sign test.

    `var j: i64 = 0` lands as an xor-zero store, and zero is provably
    non-negative, so the fact must be recorded there like any other
    non-negative literal store. `j = j + 1` only keeps existing facts,
    which left every str_byte inline in a `while j < len` scan testing
    the sign of the own index before the byte - two of the nine
    instructions per byte in str_scan.
    """

    SCAN = ("fn main() -> i64 {"
            ' let s: i64 = "abcde";'
            " var len: i64 = str_len(s);"
            " var total: i64 = 0;"
            " var j: i64 = 0;"
            " while j < len {"
            "   var c: i64 = str_byte(s, j);"
            "   total = total + c; j = j + 1; }"
            " print(total); return 0; }")

    def test_a_zero_initialized_index_skips_the_sign_test(self) -> None:
        body = region_text(self.SCAN, "main")
        self.assertIn(bytes([0x0F, 0xB6]), body,  # movzx byte load folded
                      "the leaf index loads the byte straight from the pointer")
        self.assertNotIn(bytes([0x0F, 0x88]), body,  # js rel32 miss route
                         "the sign test of a proven non-negative index is gone")
        self.assertNotIn(bytes([0x78]), body,  # short js
                         "no short-circuit sign branch survives either")

    SIGNED = ("fn probe(s: i64, k: i64) -> i64 {"
              " return str_byte(s, k); }"
              " fn main() -> i64 {"
              ' let s: i64 = "abcde";'
              " return probe(s, 2); }")

    def test_an_unproven_index_keeps_the_sign_test(self) -> None:
        # A parameter count is no fact: the inline keeps its miss route
        # because k may be negative.
        body = region_text(self.SIGNED, "probe")
        self.assertIn(bytes([0x0F, 0x88]), body,
                      "a parameter index may be negative, the miss route stays")


class TrueLoopBranchTest(unittest.TestCase):
    """A literal-true loop condition branches with one jmp, never by testing 1.

    char_stream (a `while true` with break) paid a 10-byte `movabs $1,%rax`
    plus `test` and `jne` on every byte's back edge. A literal's truth is a
    compile-time fact: match the requested branch polarity and the condition
    is a bare `jmp` (a mismatched polarity emits nothing and falls through),
    so no iteration decodes an immediate that only exists to be tested.
    """

    STREAM = ("fn main() -> i64 {"
              "  var total: i64 = 0;"
              "  while true {"
              "    var c: i64 = 5;"
              "    if c < 0 { break; }"
              "    total = total + c;"
              "  }"
              "  print(total); return 0; }")

    def test_a_while_true_back_edge_is_a_bare_branch(self) -> None:
        body = region_text(self.STREAM, "main")
        movabs_one = bytes([0x48, 0xB8, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        self.assertNotIn(movabs_one, body,
                         "but the guard materialised the literal true as movabs")
        self.assertNotIn(bytes([0x48, 0x85, 0xC0]), body,
                         "and re-tested that immediate")
        # A back edge that is a bare branch may encode near (E9) or, once the
        # break-only if stops padding the distance, short (EB).
        self.assertTrue(bytes([0xE9]) in body or bytes([0xEB]) in body,
                        "the latch now reaches its start with a bare jmp")

    FALSE_HOLDER = ("fn main() -> i64 {"
                    "  var total: i64 = 0;"
                    "  while false { total = total + 1; }"
                    "  print(total); return 0; }")

    def test_a_while_false_body_never_runs(self) -> None:
        # The head jump-to-end must be a real branch, not a silent fall-through,
        # or the do-while rotation would execute an unreachable body once.
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "probe.crn")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.FALSE_HOLDER)
            completed = subprocess.run([sys.executable, "-m", "cairn", "run", path],
                                       capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, b"0\n")
