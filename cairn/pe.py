"""PE writer: link an artifact and emit a Windows PE32+ console executable.

No assembler, linker or C compiler is involved. The writer lays out `.text` (code), `.rdata`
(import names, strings) and `.data` (the import address table), resolves every 32-bit relative
fixup, and writes the headers Windows expects. Timestamps are fixed at zero, so building the
same source twice produces the same bytes.
"""

import struct
from typing import Dict, Tuple

from cairn.image import IMPORT_DLL, Artifact, iat_label

FILE_ALIGNMENT = 0x200
SECTION_ALIGNMENT = 0x1000
IMAGE_BASE = 0x140000000
HEADERS_SIZE = 0x200
TEXT_RVA = 0x1000
DOS_STUB_OFFSET = 0x80

MACHINE_AMD64 = 0x8664
PE32_PLUS_MAGIC = 0x20B
SUBSYSTEM_CONSOLE = 3
CHARACTERISTICS_EXECUTABLE = 0x0022
SECTION_CODE = 0x60000020
SECTION_READ_ONLY = 0x40000040
SECTION_READ_WRITE = 0xC0000040

DESCRIPTOR_SIZE = 40


def align(value: int, boundary: int) -> int:
    """Round `value` up to the next multiple of `boundary`."""
    return (value + boundary - 1) // boundary * boundary


def import_blob(imports: Tuple[str, ...]) -> Tuple[bytearray, Dict[str, int]]:
    """The import descriptor, ILT, DLL name and hint/name table, with their `.rdata` offsets.

    RVA fields inside the blob stay zero until `patch_imports` runs, because they depend on the
    section addresses, which depend on the blob's size.
    """
    ilt_offset = DESCRIPTOR_SIZE
    ilt_size = 8 * (len(imports) + 1)
    dll_offset = ilt_offset + ilt_size
    dll_name = IMPORT_DLL.encode("ascii") + b"\x00"
    blob = bytearray(DESCRIPTOR_SIZE + ilt_size)
    blob.extend(dll_name)
    if len(blob) % 2:
        blob.extend(b"\x00")
    offsets: Dict[str, int] = {"ilt": ilt_offset, "dll": dll_offset}
    for function in imports:
        offsets[function] = len(blob)
        entry = b"\x00\x00" + function.encode("ascii") + b"\x00"
        if len(entry) % 2:
            entry += b"\x00"
        blob.extend(entry)
    return blob, offsets


def patch_imports(blob: bytearray, imports: Tuple[str, ...], offsets: Dict[str, int],
                  rdata_rva: int, data_rva: int) -> None:
    """Write the descriptor and the ILT entries now that both section RVAs are known."""
    struct.pack_into("<IIIII", blob, 0, rdata_rva + offsets["ilt"], 0, 0,
                     rdata_rva + offsets["dll"], data_rva)
    for index, function in enumerate(imports):
        struct.pack_into("<Q", blob, offsets["ilt"] + index * 8,
                         rdata_rva + offsets[function])


def _section_header(name: str, virtual_size: int, virtual_address: int, raw_size: int,
                    raw_pointer: int, characteristics: int) -> bytes:
    """One 40-byte section table entry."""
    return struct.pack("<8sIIIIIIHHI", name.encode("ascii"), virtual_size, virtual_address,
                       raw_size, raw_pointer, 0, 0, 0, 0, characteristics)


def _optional_header(size_of_code: int, size_of_data: int, entry_rva: int,
                     size_of_image: int, import_rva: int, import_size: int) -> bytes:
    """The 240-byte PE32+ optional header with one data directory filled in."""
    body = struct.pack("<HBBIIIII", PE32_PLUS_MAGIC, 0, 0, size_of_code, size_of_data, 0,
                       entry_rva, TEXT_RVA)
    body += struct.pack("<Q", IMAGE_BASE)
    body += struct.pack("<II", SECTION_ALIGNMENT, FILE_ALIGNMENT)
    body += struct.pack("<HHHHHH", 6, 0, 0, 0, 6, 0)
    body += struct.pack("<I", 0)
    body += struct.pack("<III", size_of_image, HEADERS_SIZE, 0)
    body += struct.pack("<HH", SUBSYSTEM_CONSOLE, 0)
    body += struct.pack("<QQQQ", 0x100000, 0x1000, 0x100000, 0x1000)
    body += struct.pack("<II", 0, 16)
    directories = [(0, 0)] * 16
    directories[1] = (import_rva, import_size)
    body += b"".join(struct.pack("<II", rva, size) for rva, size in directories)
    return body


def headers(text_size: int, text_raw: int, entry_rva: int, rdata_size: int, rdata_raw: int,
            rdata_rva: int, data_size: int, data_raw: int, data_rva: int,
            import_rva: int, import_size: int) -> bytes:
    """The complete header block, padded to the file alignment."""
    sections = [
        _section_header(".text", text_size, TEXT_RVA, text_raw, HEADERS_SIZE, SECTION_CODE),
        _section_header(".rdata", rdata_size, rdata_rva, rdata_raw,
                        HEADERS_SIZE + text_raw, SECTION_READ_ONLY),
        _section_header(".data", data_size, data_rva, data_raw,
                        HEADERS_SIZE + text_raw + rdata_raw, SECTION_READ_WRITE),
    ]
    size_of_image = data_rva + align(data_size, SECTION_ALIGNMENT)
    optional = _optional_header(text_raw, rdata_raw + data_raw, entry_rva, size_of_image,
                                import_rva, import_size)
    coff = struct.pack("<HHIIIHH", MACHINE_AMD64, len(sections), 0, 0, 0, 0xF0,
                       CHARACTERISTICS_EXECUTABLE)
    dos = bytearray(DOS_STUB_OFFSET)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, DOS_STUB_OFFSET)
    block = bytes(dos) + b"PE\x00\x00" + coff + optional + b"".join(sections)
    return block + b"\x00" * (HEADERS_SIZE - len(block))


def _import_address_table(imports: Tuple[str, ...], offsets: Dict[str, int],
                          rdata_rva: int) -> bytes:
    """The IAT: one hint/name RVA per import, terminated by a null slot."""
    entries = [struct.pack("<Q", rdata_rva + offsets[function]) for function in imports]
    entries.append(struct.pack("<Q", 0))
    return b"".join(entries)


def link(artifact: Artifact) -> bytes:
    """Lay out sections, resolve fixups and return the executable image bytes."""
    text = bytearray(artifact.text)
    imports = artifact.imports
    blob, offsets = import_blob(imports)
    rodata_offset = align(len(blob), 8)

    text_raw = align(len(text), FILE_ALIGNMENT)
    rdata_rva = TEXT_RVA + align(len(text), SECTION_ALIGNMENT)
    rdata_size = rodata_offset + len(artifact.rodata.data)
    rdata_raw = align(rdata_size, FILE_ALIGNMENT)
    data_rva = rdata_rva + align(rdata_size, SECTION_ALIGNMENT)
    data = _import_address_table(imports, offsets, rdata_rva)
    data_raw = align(len(data), FILE_ALIGNMENT)

    patch_imports(blob, imports, offsets, rdata_rva, data_rva)
    rodata = bytes(blob) + b"\x00" * (rodata_offset - len(blob)) + bytes(artifact.rodata.data)

    targets = {name: TEXT_RVA + offset for name, offset in artifact.text_labels.items()}
    for name, offset in artifact.rodata.labels.items():
        targets[name] = rdata_rva + rodata_offset + offset
    for index, function in enumerate(imports):
        targets[iat_label(function)] = data_rva + index * 8

    for offset, target in artifact.fixups:
        if target not in targets:
            raise KeyError(f"unresolved label {target!r} at text offset {offset}")
        displacement = targets[target] - (TEXT_RVA + offset + 4)
        struct.pack_into("<i", text, offset, displacement)

    block = headers(len(text), text_raw, targets[artifact.entry], rdata_size, rdata_raw,
                    rdata_rva, len(data), data_raw, data_rva, rdata_rva, DESCRIPTOR_SIZE)
    image = bytearray(block)
    image.extend(text)
    image.extend(b"\x00" * (text_raw - len(text)))
    image.extend(rodata)
    image.extend(b"\x00" * (rdata_raw - len(rodata)))
    image.extend(data)
    image.extend(b"\x00" * (data_raw - len(data)))
    return bytes(image)


__all__ = ["align", "headers", "import_blob", "link", "patch_imports"]
