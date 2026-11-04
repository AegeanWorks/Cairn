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


def section_field(image: bytes, index: int, field: int) -> int:
    """One dword from a section header: field 8 is size, 12 is VA, 20 is the raw pointer."""
    return struct.unpack_from("<I", image, SECTIONS + index * 40 + field)[0]


def file_offset(image: bytes, rva: int) -> int:
    """Translate one RVA through the section table."""
    for index in range(3):
        virtual_address = section_field(image, index, 12)
        size = max(section_field(image, index, 8), 1)
        if virtual_address <= rva < virtual_address + size:
            return section_field(image, index, 20) + (rva - virtual_address)
    raise AssertionError(f"RVA {rva:#x} is outside every section")


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

    def test_import_directory_must_live_in_rdata(self) -> None:
        image = bytearray(image_bytes())
        text_raw = section_field(image, 0, 20)
        directory_rva, _ = struct.unpack_from("<II", image, OPTIONAL + 120)
        descriptor = bytes(image[file_offset(image, directory_rva):
                                 file_offset(image, directory_rva) + 20])
        image[text_raw:text_raw + 20] = descriptor
        struct.pack_into("<I", image, OPTIONAL + 120, 0x1000)
        problems = verify_image(bytes(image))
        self.assertIn("V0007", {problem.code for problem in problems})

    def test_iat_must_live_in_data(self) -> None:
        image = bytearray(image_bytes())
        directory_rva, _ = struct.unpack_from("<II", image, OPTIONAL + 120)
        descriptor_offset = file_offset(image, directory_rva)
        rdata_rva = section_field(image, 1, 12)
        rdata_raw_size = section_field(image, 1, 16)
        # Point the IAT at zero padding inside .rdata: the terminator check
        # passes there, so only the section rule can report it.
        struct.pack_into("<I", image, descriptor_offset + 16,
                         rdata_rva + rdata_raw_size - 32)
        problems = verify_image(bytes(image))
        self.assertIn("V0007", {problem.code for problem in problems})

    def test_entry_is_checked_against_the_text_section(self) -> None:
        image = bytearray(image_bytes())
        struct.pack_into("<I", image, TEXT + 12, 0x2000)
        problems = verify_image(bytes(image))
        self.assertIn("V0006", {problem.code for problem in problems})

    def test_text_must_be_readable(self) -> None:
        image = bytearray(image_bytes())
        flags = struct.unpack_from("<I", image, TEXT + 36)[0]
        image[TEXT + 36:TEXT + 40] = struct.pack("<I", flags & ~0x40000000)
        problems = verify_image(bytes(image))
        self.assertIn("V0009", {problem.code for problem in problems})


if __name__ == "__main__":
    unittest.main()
