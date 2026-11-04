"""LLM card tests: the fenced programs in LLM_SYSTEM.md must actually compile."""

import re
import unittest
from pathlib import Path

from cairn.builder import compile_source

CARD = Path(__file__).resolve().parent.parent / "LLM_SYSTEM.md"


def main_programs() -> list:
    """Every fenced block that claims to be a complete program."""
    fences = re.findall(r"```(?:cairn)?\n(.*?)```", CARD.read_text(encoding="utf-8"),
                        re.DOTALL)
    return [block for block in fences if "fn main() -> i64" in block]


class LlmCardTest(unittest.TestCase):
    """If the card shows a program, that program is checked."""

    def test_card_has_a_canonical_program(self) -> None:
        self.assertTrue(main_programs())

    def test_every_card_program_compiles(self) -> None:
        for block in main_programs():
            with self.subTest(block=block[:40]):
                compile_source(block)


if __name__ == "__main__":
    unittest.main()
