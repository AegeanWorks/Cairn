"""Verification of a built artifact: structural checks that need no execution.

An agent should be able to prove that a `.exe` is well formed before running it. Every check here
is decidable from the bytes alone and reports a stable `V####` code, so a failure names the
invariant that broke rather than an offset. Nothing is written and nothing is executed.
"""

import struct
from dataclasses import dataclass
from typing import List, Tuple

from cairn.image import IMPORTS
from cairn.pe import DESCRIPTOR_SIZE, FILE_ALIGNMENT, HEADERS_SIZE, PE32_PLUS_MAGIC, TEXT_RVA

MACHINE_AMD64 = 0x8664
EXPECTED_SECTIONS = (b".text", b".rdata", b".data")
OPTIONAL_OFFSET = 0x98
SECTION_TABLE_OFFSET = OPTIONAL_OFFSET + 0xF0
SECTION_READ = 0x40000000
SECTION_WRITE = 0x80000000
SECTION_EXECUTE = 0x20000000
IMAGE_SCN_CNT_CODE = 0x00000020


@dataclass(frozen=True)
class Problem:
    """One failed invariant, addressed by its code."""

    code: str
    detail: str

    def render(self) -> str:
        """The one-line report form."""
        return f"[{self.code}] {self.detail}"


def _u16(image: bytes, offset: int) -> int:
    return struct.unpack_from("<H", image, offset)[0]


def _u32(image: bytes, offset: int) -> int:
    return struct.unpack_from("<I", image, offset)[0]


def _sections(image: bytes) -> List[Tuple[bytes, int]]:
    """`(name, header offset)` for every section table entry that fits in the file."""
    declared = _u16(image, 0x86)
    available = max((len(image) - SECTION_TABLE_OFFSET) // 40, 0)
    count = min(declared, available)
    return [(image[SECTION_TABLE_OFFSET + index * 40:SECTION_TABLE_OFFSET + index * 40 + 8],
             SECTION_TABLE_OFFSET + index * 40) for index in range(count)]


def _rva_to_offset(image: bytes, rva: int) -> int:
    """Translate an RVA through the section table; -1 when it lands in no section."""
    for _, header in _sections(image):
        start = _u32(image, header + 12)
        size = max(_u32(image, header + 8), 1)
        if start <= rva < start + size:
            return _u32(image, header + 20) + (rva - start)
    return -1


def _check_signatures(image: bytes, problems: List[Problem]) -> None:
    """MZ at the start, e_lfanew at 0x3C, and `PE\\0\\0` where it points."""
    if len(image) < HEADERS_SIZE:
        problems.append(Problem("V0001", f"image is {len(image)} bytes, shorter than the headers"))
        return
    if image[0:2] != b"MZ":
        problems.append(Problem("V0001", "no MZ signature at offset 0"))
    if _u32(image, 0x3C) != 0x80:
        problems.append(Problem("V0001", f"e_lfanew is {_u32(image, 0x3C)}, expected 0x80"))
    elif image[0x80:0x84] != b"PE\x00\x00":
        problems.append(Problem("V0001", "no PE signature where e_lfanew points"))


def _check_coff(image: bytes, problems: List[Problem]) -> None:
    """Machine, section count and the optional header size."""
    machine = _u16(image, 0x84)
    if machine != MACHINE_AMD64:
        problems.append(Problem("V0002", f"machine is {machine:#06x}, expected 0x8664"))
    if _u16(image, 0x86) != len(EXPECTED_SECTIONS):
        problems.append(Problem("V0002",
                                f"section count is {_u16(image, 0x86)}, "
                                f"expected {len(EXPECTED_SECTIONS)}"))
    if _u16(image, 0x94) != 0xF0:
        problems.append(Problem("V0002", f"optional header size is {_u16(image, 0x94)}, "
                                         "expected 240"))


def _check_optional(image: bytes, problems: List[Problem]) -> None:
    """PE32+ magic and the console subsystem."""
    if _u16(image, OPTIONAL_OFFSET) != PE32_PLUS_MAGIC:
        problems.append(Problem("V0003", f"magic is {_u16(image, OPTIONAL_OFFSET):#06x}, "
                                         "expected 0x20b"))
    if _u16(image, OPTIONAL_OFFSET + 68) != 3:
        problems.append(Problem("V0003", f"subsystem is {_u16(image, OPTIONAL_OFFSET + 68)}, "
                                         "expected 3 (console)"))


def _check_section_names(image: bytes, problems: List[Problem]) -> None:
    """The three sections in order, so code, data and imports are where the writer puts them."""
    names = [name.rstrip(b"\x00") for name, _ in _sections(image)]
    if tuple(names) != EXPECTED_SECTIONS:
        problems.append(Problem("V0004", f"sections are {names}, expected "
                                         f"{[name.decode() for name in EXPECTED_SECTIONS]}"))


def _check_section_geometry(image: bytes, problems: List[Problem]) -> None:
    """File alignment, pointer ordering and the file actually containing each section."""
    previous_end = HEADERS_SIZE
    for name, header in _sections(image):
        raw_pointer = _u32(image, header + 20)
        raw_size = _u32(image, header + 16)
        label = name.rstrip(b"\x00").decode("ascii", "replace")
        if raw_pointer % FILE_ALIGNMENT:
            problems.append(Problem("V0005", f"{label} raw pointer {raw_pointer:#x} is not "
                                             f"{FILE_ALIGNMENT:#x}-aligned"))
        if raw_pointer < previous_end:
            problems.append(Problem("V0005", f"{label} overlaps the previous section"))
        previous_end = raw_pointer + raw_size
        if previous_end > len(image):
            problems.append(Problem("V0010", f"{label} claims bytes up to {previous_end:#x}, "
                                             f"past the {len(image):#x}-byte file"))


def _check_permissions(image: bytes, problems: List[Problem]) -> None:
    """Code is executable and read-only; data is writable and never executable."""
    for name, header in _sections(image):
        flags = _u32(image, header + 36)
        label = name.rstrip(b"\x00").decode("ascii", "replace")
        writable = bool(flags & SECTION_WRITE)
        executable = bool(flags & SECTION_EXECUTE)
        if writable and executable:
            problems.append(Problem("V0009", f"{label} is both writable and executable"))
        if label == ".text" and (writable or not executable):
            problems.append(Problem("V0009", ".text must be executable and not writable"))
        if label == ".data" and (not writable or executable):
            problems.append(Problem("V0009", ".data must be writable and not executable"))
        if label == ".rdata" and (writable or executable):
            problems.append(Problem("V0009", ".rdata must be read-only and not executable"))


def _check_entry(image: bytes, problems: List[Problem]) -> None:
    """The entry point must fall inside `.text`, or Windows would start in data."""
    entry = _u32(image, OPTIONAL_OFFSET + 16)
    text_size = 0
    for name, header in _sections(image):
        if name.rstrip(b"\x00") == b".text":
            text_size = max(_u32(image, header + 8), 1)
    if not TEXT_RVA <= entry < TEXT_RVA + text_size:
        problems.append(Problem("V0006", f"entry RVA {entry:#x} is outside "
                                         f".text [{TEXT_RVA:#x}, {TEXT_RVA + text_size:#x})"))


def _check_image_size(image: bytes, problems: List[Problem]) -> None:
    """`SizeOfImage` must cover every section, aligned to the section alignment."""
    image_size = _u32(image, OPTIONAL_OFFSET + 56)
    highest = 0
    for _, header in _sections(image):
        start = _u32(image, header + 12)
        size = _u32(image, header + 8)
        highest = max(highest, start + ((size + 0xFFF) & ~0xFFF))
    if image_size < highest:
        problems.append(Problem("V0008", f"SizeOfImage is {image_size:#x}, below the "
                                         f"{highest:#x} the layout needs"))


def _check_imports(image: bytes, problems: List[Problem]) -> None:
    """The descriptor sits in `.rdata`, names kernel32, and its IAT ends with a null slot."""
    directory_rva = _u32(image, OPTIONAL_OFFSET + 120)
    directory_size = _u32(image, OPTIONAL_OFFSET + 124)
    if directory_rva == 0 or directory_size == 0:
        problems.append(Problem("V0007", "the import directory is empty"))
        return
    if directory_size < DESCRIPTOR_SIZE:
        problems.append(Problem("V0007", f"import directory size {directory_size} is below "
                                         f"{DESCRIPTOR_SIZE}"))
    if _rva_to_offset(image, directory_rva) < 0:
        problems.append(Problem("V0007", f"import directory RVA {directory_rva:#x} is outside "
                                         "every section"))
        return
    offset = _rva_to_offset(image, directory_rva)
    if offset + DESCRIPTOR_SIZE > len(image):
        problems.append(Problem("V0007", f"import directory at {offset:#x} is past the "
                                         f"{len(image):#x}-byte file"))
        return
    ilt_rva, _, _, dll_rva, iat_rva = struct.unpack_from("<IIIII", image, offset)
    if not ilt_rva or not dll_rva or not iat_rva:
        problems.append(Problem("V0007", "the import descriptor has an empty field"))
        return
    for label, rva in (("ILT", ilt_rva), ("DLL name", dll_rva), ("IAT", iat_rva)):
        if _rva_to_offset(image, rva) < 0:
            problems.append(Problem("V0007", f"{label} RVA {rva:#x} is outside every section"))
            return
    dll_offset = _rva_to_offset(image, dll_rva)
    iat = _rva_to_offset(image, iat_rva)
    if dll_offset + 16 > len(image):
        problems.append(Problem("V0007", "the DLL name is past the end of the file"))
        return
    if iat + 8 * (len(IMPORTS) + 1) > len(image):
        problems.append(Problem("V0007", "the import address table is past the end of the file"))
        return
    dll = image[dll_offset:dll_offset + 16]
    if b"kernel32.dll" not in dll.lower():
        problems.append(Problem("V0007", "the import descriptor does not name kernel32.dll"))
    if struct.unpack_from("<Q", image, iat + 8 * len(IMPORTS))[0] != 0:
        problems.append(Problem("V0007", "the import address table has no null terminator"))


def verify_image(image: bytes) -> List[Problem]:
    """Every structural invariant of one produced executable; an empty list means verified."""
    problems: List[Problem] = []
    _check_signatures(image, problems)
    if problems:
        return problems
    _check_coff(image, problems)
    _check_optional(image, problems)
    _check_section_names(image, problems)
    if not any(problem.code == "V0004" for problem in problems):
        _check_section_geometry(image, problems)
        _check_permissions(image, problems)
        _check_entry(image, problems)
        _check_image_size(image, problems)
        _check_imports(image, problems)
    return problems


__all__ = ["Problem", "verify_image"]
