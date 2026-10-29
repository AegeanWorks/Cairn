"""Verifier tests: every artifact invariant is decided from bytes and names its code."""

import struct
import unittest

from cairn.builder import compile_source
from cairn.pe import FILE_ALIGNMENT, HEADERS_SIZE
from cairn.verify import verify_image

SOURCE = "fn main() -> i64 { print(40 + 2); return 0; }"
OPTIONAL = 0x98
SECTIONS = OPTIONAL + 0xF0
TEXT = SECTIONS
RDATA = SECTIONS + 40
DATA = SECTIONS + 80


def image_bytes() -> bytes:
    """A real build, which verification must accept unchanged."""
    return compile_source(SOURCE).image


def patched(offset: int, payload: bytes) -> bytes:
    """A build with one field overwritten, to isolate the invariant it belongs to."""
    image = bytearray(image_bytes())
    image[offset:offset + len(payload)] = payload
    return bytes(image)


class VerifyTest(unittest.TestCase):
    """Each test breaks one rule and expects exactly the code that names it."""

    def test_a_fresh_build_has_no_problems(self) -> None:
        self.assertEqual(verify_image(image_bytes()), [])

    def test_every_verification_code_is_reachable(self) -> None:
        cases = {
            "V0001": image_bytes()[:0x100],
            "V0002": patched(0x84, struct.pack("<H", 0x014C)),
            "V0003": patched(OPTIONAL, struct.pack("<H", 0x10B)),
            "V0004": patched(TEXT, b".txT\x00\x00\x00\x00"),
            "V0005": patched(TEXT + 20, struct.pack("<I", FILE_ALIGNMENT + 1)),
            "V0006": patched(OPTIONAL + 16, struct.pack("<I", 0)),
            "V0007": patched(OPTIONAL + 120, struct.pack("<I", 0)),
            "V0008": patched(OPTIONAL + 56, struct.pack("<I", 0x1000)),
            "V0009": patched(DATA + 36, struct.pack("<I", 0xE0000040)),
            "V0010": image_bytes()[:HEADERS_SIZE + 1],
        }
        seen = set()
        for expected, broken in cases.items():
            with self.subTest(code=expected):
                problems = verify_image(broken)
                self.assertIn(expected, {problem.code for problem in problems})
                seen.add(expected)
        self.assertEqual(seen, {f"V{number:04d}" for number in range(1, 11)})

    def test_a_problem_renders_with_its_code(self) -> None:
        problems = verify_image(image_bytes()[:0x100])
        self.assertTrue(problems)
        self.assertIn("[V0001]", problems[0].render())


if __name__ == "__main__":
    unittest.main()
