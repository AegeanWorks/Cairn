"""Code-generation tests: where a value lives, and what the frame must carry for it.

The lowering is invisible at the language level, so these tests watch the assembler calls instead
of the program output: a promoted slot must never touch its old frame slot, and the registers a
function uses must be saved and restored around its body.
"""

import unittest

from cairn import parser, sema
from cairn.codegen import CodeGenerator, function_label
from cairn.machine import Assembler

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
        self.assertIn(b"\x48\x89\xc1\xe8", body)
        self.assertNotIn(b"\x50\x59", body)

    def test_a_shift_by_literal_shifts_in_place(self) -> None:
        body = body_text(SHIFT_IMM, "g")
        self.assertIn(b"\x48\xc1\xf8\x07", body)
        self.assertNotIn(b"\x48\xd3\xf8", body)
        self.assertNotIn(b"\x48\xb8\x07", body)


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
