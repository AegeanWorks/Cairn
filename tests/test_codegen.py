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
        save_area = -8 * (program.functions[0].slots + 1)
        saved = {offset for offset in assembler.stores_of(main) if offset <= save_area}
        restored = {offset for offset in assembler.loads_of(main) if offset <= save_area}
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved, restored)

    def test_the_seventh_slot_keeps_the_stack_fallback(self) -> None:
        program, assembler = lower(SEVEN_LOCALS)
        main = function_label(program.functions[0].name)
        self.assertEqual(program.functions[0].slots, 7)
        self.assertIn(-56, assembler.loads_of(main))


if __name__ == "__main__":
    unittest.main()
