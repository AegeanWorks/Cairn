"""Code-generation tests: where a value lives, and what the frame must carry for it.

The lowering is invisible at the language level, so these tests watch the assembler calls instead
of the program output: a promoted slot must never touch its old frame slot, and the registers a
function uses must be saved and restored around its body.
"""

import unittest

from cairn import parser, sema
from cairn.codegen import CodeGenerator, function_label
from cairn.machine import Assembler

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


    def pops_of(self, function: str):
        """Registers popped inside one function."""
        return {reg for owner, reg in self.pops if owner == function}

    def pushes_of(self, function: str):
        """Registers pushed inside one function."""
        return {reg for owner, reg in self.pushes if owner == function}


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


class SelfRecursionInliningTest(unittest.TestCase):
    """The left operand of a merge may be a self-call lowered in place, not a real call."""

    def test_a_merge_left_self_call_is_inlined(self) -> None:
        program = sema.analyze(parser.parse(FIB_MERGE))
        artifact = CodeGenerator().generate(program)
        inline_labels = [label for label in artifact.text_labels if ".inline" in label]
        self.assertTrue(inline_labels)

    def test_a_comparison_with_a_small_literal_skips_the_rax_load(self) -> None:
        body = body_text(FIB_MERGE, "fib")
        self.assertIn(b"\x48\x83\xfb\x02", body)  # cmp rbx, 2
