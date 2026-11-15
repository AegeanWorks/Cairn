"""Code-generation tests: where a value lives, and what the frame must carry for it.

The lowering is invisible at the language level, so these tests watch the assembler calls instead
of the program output: a promoted slot must never touch its old frame slot, and the registers a
function uses must be saved and restored around its body.
"""

import unittest

from cairn import parser, sema
from cairn.codegen import HOME_REGISTERS, CodeGenerator, function_label
from cairn.machine import ARGUMENT_REGISTERS, Assembler

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
                + " ".join(f"let v{index}: i64 = {index};" for index in range(7))
                + " print(v6); return 0; }")


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

    def test_the_seventh_slot_keeps_the_stack_fallback(self) -> None:
        program, assembler = lower(SEVEN_LOCALS)
        main = function_label(program.functions[0].name)
        self.assertEqual(program.functions[0].slots, 7)
        self.assertIn(-56, assembler.loads_of(main))


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

    def test_division_by_a_power_of_two_literal_never_divides(self) -> None:
        body = region_text(self.DIVIDE_BY_TWO, "main")
        self.assertNotIn(b"\x48\xf7", body)  # neither idiv nor any r64 F7 form

    def test_division_by_two_emits_the_shift_fixup_chain(self) -> None:
        body = region_text(self.DIVIDE_BY_TWO, "main")
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


class MultiArgumentCallTest(unittest.TestCase):
    """Pure arguments bind straight into their ABI registers, without the stack round-trip.

    The generic two-argument path pushed and popped each argument; a push/pop pair costs
    more uops than the direct move it replaces, and every multi-argument call site paid
    for both. Only leaf-ish arguments (names, literals, name +/- literal) may rebind
    directly: they cannot call or trap, so rebinding them out of source order is
    unobservable.
    """

    def test_both_pure_arguments_bind_without_the_stack(self) -> None:
        body = body_text(TWO_ARG_CALL, "f")
        self.assertIn(b"\x4c\x89\xe2", body)  # mov rdx, r12
        self.assertIn(b"\x48\x8d\x4b\xff", body)  # lea rcx, [rbx-1]
        self.assertNotIn(b"\x50", body)  # no push: f's only pushes are prologue home saves
        self.assertNotIn(b"\x59", body)
        self.assertNotIn(b"\x48\xb8\x01", body)  # no 10-byte movabs for a small literal

    def test_literal_arguments_bind_without_the_stack_from_main(self) -> None:
        body = body_text(TWO_ARG_CALL, "main")
        # f(3, 4) from main: rdx binds first, rcx second, no stack traffic in between.
        rdx_then_rcx = b"\x48\xba\x04\x00\x00\x00\x00\x00\x00\x00\x48\xb9\x03"
        self.assertIn(rdx_then_rcx, body)

    def test_mixed_call_free_arguments_bind_reversed_without_the_stack(self) -> None:
        # call_chain's shape: f(x, y >> 2, y & 255). The impure operands are call-free
        # arithmetic, so reverse binding costs nothing observable: the third argument
        # evaluates into rax and moves into r8, the second evaluates into rax and moves
        # into rdx, the first is a name that moves straight into rcx. No push, no pop.
        source = ("fn f(a: i64, b: i64, c: i64) -> i64 { return (a * 3) ^ (b << 2) + c; } "
                  "fn main() -> i64 { var x: i64 = 7; return f(x, x >> 2, x & 65535); }")
        body = body_text(source, "main")
        self.assertNotIn(b"\x50", body)  # no push at the call site
        self.assertNotIn(b"\x5a", body)  # no pop rdx
        self.assertNotIn(b"\x59", body)  # no pop rcx
        # The masked and shifted arguments compute in their own ABI registers: r8 takes
        # x and masks in place, then rdx takes x and shifts in place — reverse order,
        # and no rax round-trip between them.
        r8 = b"\x49\x89\xd8"
        rdx = b"\x48\x89\xda"
        self.assertIn(r8, body)
        self.assertIn(rdx, body)
        self.assertLess(body.index(r8), body.index(rdx))
        self.assertIn(b"\x49\x81\xe0\xff\xff\x00\x00", body)  # and r8, 65535
        self.assertIn(b"\x48\xc1\xfa\x02", body)  # sar rdx, 2

    def test_a_call_inside_an_argument_keeps_the_stack_fallback(self) -> None:
        # A nested call clobbers the caller-saved argument registers, so an already-bound
        # argument would not survive later evaluations: reverse binding must refuse.
        source = ("fn f(a: i64, b: i64, c: i64) -> i64 { return a + b + c; } "
                  "fn g(v: i64) -> i64 { return v * 2; } "
                  "fn main() -> i64 { var x: i64 = 7; return f(x, x, g(x)); }")
        body = body_text(source, "main")
        self.assertIn(b"\x50", body)  # the generic push path survives

    def test_a_division_inside_an_argument_keeps_the_stack_fallback(self) -> None:
        # Division can trap; its trap would fire in a different order than the source's
        # left-to-right argument order once binding runs in reverse, so it must refuse.
        source = ("fn f(a: i64, b: i64, c: i64) -> i64 { return a + b + c; } "
                  "fn main() -> i64 { var x: i64 = 7; var y: i64 = 2; "
                  "return f(x, y, x / y); }")
        body = body_text(source, "main")
        self.assertIn(b"\x50", body)  # the generic push path survives


ACKERMANN_SHAPE = (
    "fn ack(m: i64, n: i64) -> i64 { "
    "if m == 0 { return n + 1; } "
    "if n == 0 { return ack(m - 1, 1); } "
    "return ack(m - 1, ack(m, n - 1)); } "
    "fn main() -> i64 { print(ack(2, 3)); return 0; }"
)


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

    def test_a_looping_callee_stays_a_real_call(self) -> None:
        source = ("fn h(a: i64) -> i64 { var t: i64 = 0; while t < a { t = t + 1; } "
                  "return t; } fn main() -> i64 { return h(5); }")
        self.assertGreater(calls_of(source, "main", "h"), 0)

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
        # The ten-byte zero move has no reason to run once per iteration; only main's own
        # prologue (once per function entry) keeps one.
        body = region_bytes(TAIL_RETURN_COPIES, "main")
        zero = b"\x48\xb8\x00\x00\x00\x00\x00\x00\x00\x00"
        self.assertEqual(body.count(zero), 2)  # main's implicit zero + its tail `return 0`

    def test_a_copy_whose_last_statement_is_not_a_return_keeps_the_implicit_zero(self) -> None:
        # Both branches of the tail if return, so the copy cannot fall off its end either —
        # but the last statement is an If, so the guard stays: one zero per copy remains
        # (main's implicit zero and its tail `return 0` are the other two).
        source = TAIL_RETURN_COPIES.replace(
            "return t; } ", "if t > 3 { return t - 1; } else { return t + 1; } } ")
        body = region_bytes(source, "main")
        zero = b"\x48\xb8\x00\x00\x00\x00\x00\x00\x00\x00"
        self.assertEqual(body.count(zero), 3)

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
