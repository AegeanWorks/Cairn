"""Backend and PE tests: labels, fixups, determinism and the produced image's structure."""

import struct
import unittest

from cairn import codegen, parser, sema
from cairn.builder import compile_source
from cairn.image import ENTRY_LABEL
from cairn.pe import link

HELLO = "fn main() -> i64 { print(40 + 2); return 0; }"
OPTIONAL = 0x98
SECTIONS = OPTIONAL + 0xF0


def artifact_for(source: str) -> object:
    """The linked artifact for a source, without going through the file writer."""
    return codegen.generate(sema.analyze(parser.parse(source)))


def rva_to_offset(image: bytes, rva: int) -> int:
    """Translate one RVA through the section table."""
    count = struct.unpack_from("<H", image, 0x86)[0]
    for index in range(count):
        header = SECTIONS + index * 40
        virtual_address = struct.unpack_from("<I", image, header + 12)[0]
        virtual_size = struct.unpack_from("<I", image, header + 8)[0]
        raw_pointer = struct.unpack_from("<I", image, header + 20)[0]
        if virtual_address <= rva < virtual_address + max(virtual_size, 1):
            return raw_pointer + (rva - virtual_address)
    raise AssertionError(f"RVA {rva:#x} is outside every section")


class CodegenTest(unittest.TestCase):
    """The backend is checked through the artifact it produces."""

    def test_artifact_names_entry_main_and_runtime(self) -> None:
        artifact = artifact_for(HELLO)
        self.assertIn(ENTRY_LABEL, artifact.text_labels)
        self.assertIn("fn.main", artifact.text_labels)
        self.assertIn("cairn.runtime.print_i64", artifact.text_labels)
        self.assertIn("cairn.runtime.div_zero", artifact.text_labels)

    def test_every_fixup_resolves(self) -> None:
        artifact = artifact_for("fn main() -> i64 { if 1 < 2 && 3 > 2 { print(1); } "
                                "while 0 { } return 0; }")
        self.assertTrue(artifact.fixups)
        link(artifact)

    def test_unresolved_label_is_a_compiler_bug_report(self) -> None:
        artifact = artifact_for(HELLO)
        artifact.fixups.append((0, "no.such.label"))
        with self.assertRaises(KeyError):
            link(artifact)

    def test_build_is_byte_reproducible(self) -> None:
        self.assertEqual(compile_source(HELLO).image, compile_source(HELLO).image)


class PeHeaderTest(unittest.TestCase):
    """Byte-level checks, because there is no loader to ask at test time."""

    def setUp(self) -> None:
        self.image = compile_source(HELLO).image

    def test_dos_and_pe_signatures(self) -> None:
        self.assertEqual(self.image[0:2], b"MZ")
        self.assertEqual(struct.unpack_from("<I", self.image, 0x3C)[0], 0x80)
        self.assertEqual(self.image[0x80:0x84], b"PE\x00\x00")

    def test_coff_header_names_amd64_and_three_sections(self) -> None:
        machine, sections = struct.unpack_from("<HH", self.image, 0x84)
        self.assertEqual(machine, 0x8664)
        self.assertEqual(sections, 3)
        self.assertEqual(struct.unpack_from("<H", self.image, 0x94)[0], 0xF0)

    def test_optional_header_is_pe32_plus_console(self) -> None:
        self.assertEqual(struct.unpack_from("<H", self.image, OPTIONAL)[0], 0x20B)
        self.assertEqual(struct.unpack_from("<H", self.image, OPTIONAL + 68)[0], 3)

    def test_sections_are_text_rdata_and_writable_data(self) -> None:
        names = [self.image[SECTIONS + index * 40:SECTIONS + index * 40 + 8]
                 for index in range(3)]
        self.assertEqual([name.rstrip(b"\x00") for name in names],
                         [b".text", b".rdata", b".data"])
        data_characteristics = struct.unpack_from("<I", self.image, SECTIONS + 80 + 36)[0]
        self.assertEqual(data_characteristics & 0x80000000, 0x80000000)

    def test_entry_point_lies_inside_text(self) -> None:
        entry_rva, base_of_code = struct.unpack_from("<II", self.image, OPTIONAL + 16)
        text_size = struct.unpack_from("<I", self.image, SECTIONS + 8)[0]
        self.assertEqual(base_of_code, 0x1000)
        self.assertTrue(0x1000 <= entry_rva < 0x1000 + text_size)

    def test_import_directory_and_iat_are_filled(self) -> None:
        descriptor_rva, descriptor_size = struct.unpack_from("<II", self.image, OPTIONAL + 120)
        self.assertGreater(descriptor_rva, 0)
        self.assertEqual(descriptor_size, 40)
        descriptor = rva_to_offset(self.image, descriptor_rva)
        first_thunk = struct.unpack_from("<I", self.image, descriptor + 16)[0]
        slots = [struct.unpack_from("<Q", self.image, rva_to_offset(self.image, first_thunk)
                                    + index * 8)[0] for index in range(4)]
        self.assertEqual(slots[3], 0)
        self.assertTrue(all(slot > 0 for slot in slots[:3]))

    def test_section_raw_pointers_follow_the_headers(self) -> None:
        pointers = [struct.unpack_from("<I", self.image, SECTIONS + index * 40 + 20)[0]
                    for index in range(3)]
        self.assertEqual(pointers[0], 0x200)
        self.assertEqual(pointers, sorted(pointers))


if __name__ == "__main__":
    unittest.main()
