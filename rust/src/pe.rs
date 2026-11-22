//! PE writer: link an artifact and emit a Windows PE32+ console executable.
//!
//! No assembler, linker or C compiler is involved. Timestamps are fixed at zero, so two builds of
//! the same artifact produce the same bytes.

use std::collections::BTreeMap;

use crate::image::{iat_label, Artifact, IMPORT_DLL};

pub const FILE_ALIGNMENT: usize = 0x200;
pub const SECTION_ALIGNMENT: usize = 0x1000;
pub const IMAGE_BASE: u64 = 0x140000000;
pub const HEADERS_SIZE: usize = 0x200;
pub const TEXT_RVA: usize = 0x1000;
pub const DOS_STUB_OFFSET: usize = 0x80;
pub const MACHINE_AMD64: u16 = 0x8664;
pub const PE32_PLUS_MAGIC: u16 = 0x20B;
pub const SUBSYSTEM_CONSOLE: u16 = 3;
pub const CHARACTERISTICS_EXECUTABLE: u32 = 0x0022;
pub const SECTION_CODE: u32 = 0x60000020;
pub const SECTION_READ_ONLY: u32 = 0x40000040;
pub const SECTION_READ_WRITE: u32 = 0xC0000040;
pub const DESCRIPTOR_SIZE: usize = 40;

/// Round `value` up to the next multiple of `boundary`.
pub fn align(value: usize, boundary: usize) -> usize {
    assert!(boundary > 0, "alignment boundary must be positive");
    (value + boundary - 1) / boundary * boundary
}

fn put_u16(buffer: &mut [u8], offset: usize, value: u16) {
    buffer[offset..offset + 2].copy_from_slice(&value.to_le_bytes());
}

fn put_u32(buffer: &mut [u8], offset: usize, value: u32) {
    buffer[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
}

fn put_u64(buffer: &mut [u8], offset: usize, value: u64) {
    buffer[offset..offset + 8].copy_from_slice(&value.to_le_bytes());
}

fn put_i32(buffer: &mut [u8], offset: usize, value: i32) {
    buffer[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
}

fn read_u16(image: &[u8], offset: usize) -> u16 {
    image.get(offset..offset + 2).map_or(0, |part| u16::from_le_bytes([part[0], part[1]]))
}

fn read_u32(image: &[u8], offset: usize) -> u32 {
    image
        .get(offset..offset + 4)
        .map_or(0, |part| u32::from_le_bytes([part[0], part[1], part[2], part[3]]))
}

fn reserved_names() -> &'static [&'static str] {
    &["ilt", "dll"]
}

/// The import descriptor, ILT, DLL name and hint/name table, with their `.rdata` offsets.
pub fn import_blob(imports: &[String]) -> (Vec<u8>, BTreeMap<String, usize>) {
    for function in imports {
        assert!(!reserved_names().contains(&function.as_str()),
                "import name {} collides with the blob layout", function);
    }
    let ilt_offset = DESCRIPTOR_SIZE;
    let ilt_size = 8 * (imports.len() + 1);
    let dll_offset = ilt_offset + ilt_size;
    let mut blob = vec![0u8; DESCRIPTOR_SIZE + ilt_size];
    blob.extend_from_slice(IMPORT_DLL.as_bytes());
    blob.push(0);
    if blob.len() % 2 != 0 {
        blob.push(0);
    }
    let mut offsets = BTreeMap::new();
    offsets.insert("ilt".to_string(), ilt_offset);
    offsets.insert("dll".to_string(), dll_offset);
    for function in imports {
        offsets.insert(function.clone(), blob.len());
        let mut entry = vec![0u8, 0];
        entry.extend_from_slice(function.as_bytes());
        entry.push(0);
        if entry.len() % 2 != 0 {
            entry.push(0);
        }
        blob.extend_from_slice(&entry);
    }
    (blob, offsets)
}

/// Write the descriptor and ILT entries now that both section RVAs are known.
pub fn patch_imports(blob: &mut [u8], imports: &[String], offsets: &BTreeMap<String, usize>,
                     rdata_rva: usize, data_rva: usize) {
    put_u32(blob, 0, (rdata_rva + offsets["ilt"]) as u32);
    put_u32(blob, 4, 0);
    put_u32(blob, 8, 0);
    put_u32(blob, 12, (rdata_rva + offsets["dll"]) as u32);
    put_u32(blob, 16, data_rva as u32);
    for (index, function) in imports.iter().enumerate() {
        put_u64(blob, offsets["ilt"] + index * 8,
                (rdata_rva + offsets[function]) as u64);
    }
}

fn section_header(name: &str, virtual_size: usize, virtual_address: usize,
                  raw_size: usize, raw_pointer: usize, characteristics: u32) -> Vec<u8> {
    let mut header = vec![0u8; 40];
    let bytes = name.as_bytes();
    header[..bytes.len()].copy_from_slice(bytes);
    put_u32(&mut header, 8, virtual_size as u32);
    put_u32(&mut header, 12, virtual_address as u32);
    put_u32(&mut header, 16, raw_size as u32);
    put_u32(&mut header, 20, raw_pointer as u32);
    put_u32(&mut header, 36, characteristics);
    header
}

fn optional_header(size_of_code: usize, size_of_data: usize, entry_rva: usize,
                   size_of_image: usize, import_rva: usize, import_size: usize) -> Vec<u8> {
    let mut body = Vec::with_capacity(240);
    let mut head = Vec::new();
    head.extend_from_slice(&PE32_PLUS_MAGIC.to_le_bytes());
    head.push(0);
    head.push(0);
    head.extend_from_slice(&(size_of_code as u32).to_le_bytes());
    head.extend_from_slice(&(size_of_data as u32).to_le_bytes());
    head.extend_from_slice(&0u32.to_le_bytes());
    head.extend_from_slice(&(entry_rva as u32).to_le_bytes());
    head.extend_from_slice(&(TEXT_RVA as u32).to_le_bytes());
    body.extend(head);
    body.extend_from_slice(&IMAGE_BASE.to_le_bytes());
    body.extend_from_slice(&(SECTION_ALIGNMENT as u32).to_le_bytes());
    body.extend_from_slice(&(FILE_ALIGNMENT as u32).to_le_bytes());
    for value in [6u16, 0, 0, 0, 6, 0] {
        body.extend_from_slice(&value.to_le_bytes());
    }
    body.extend_from_slice(&0u32.to_le_bytes());
    body.extend_from_slice(&(size_of_image as u32).to_le_bytes());
    body.extend_from_slice(&(HEADERS_SIZE as u32).to_le_bytes());
    body.extend_from_slice(&0u32.to_le_bytes());
    body.extend_from_slice(&SUBSYSTEM_CONSOLE.to_le_bytes());
    body.extend_from_slice(&0u16.to_le_bytes());
    for value in [0x800000u64, 0x800000, 0x100000, 0x1000] {
        body.extend_from_slice(&value.to_le_bytes());
    }
    body.extend_from_slice(&0u32.to_le_bytes());
    body.extend_from_slice(&16u32.to_le_bytes());
    let mut directories = vec![(0u32, 0u32); 16];
    directories[1] = (import_rva as u32, import_size as u32);
    for (rva, size) in directories {
        body.extend_from_slice(&rva.to_le_bytes());
        body.extend_from_slice(&size.to_le_bytes());
    }
    assert_eq!(body.len(), 240);
    body
}

/// The complete header block, padded to the file alignment.
#[allow(clippy::too_many_arguments)]
pub fn headers(text_size: usize, text_raw: usize, entry_rva: usize,
               rdata_size: usize, rdata_raw: usize, rdata_rva: usize,
               data_size: usize, data_raw: usize, data_rva: usize,
               import_rva: usize, import_size: usize) -> Vec<u8> {
    let sections = [
        section_header(".text", text_size, TEXT_RVA, text_raw, HEADERS_SIZE, SECTION_CODE),
        section_header(".rdata", rdata_size, rdata_rva, rdata_raw,
                       HEADERS_SIZE + text_raw, SECTION_READ_ONLY),
        section_header(".data", data_size, data_rva, data_raw,
                       HEADERS_SIZE + text_raw + rdata_raw, SECTION_READ_WRITE),
    ];
    let size_of_image = data_rva + align(data_size, SECTION_ALIGNMENT);
    let optional = optional_header(text_raw, rdata_raw + data_raw, entry_rva, size_of_image,
                                   import_rva, import_size);
    let mut coff = Vec::new();
    coff.extend_from_slice(&MACHINE_AMD64.to_le_bytes());
    coff.extend_from_slice(&3u16.to_le_bytes());
    coff.extend_from_slice(&0u32.to_le_bytes());
    coff.extend_from_slice(&0u32.to_le_bytes());
    coff.extend_from_slice(&0u32.to_le_bytes());
    coff.extend_from_slice(&0xF0u16.to_le_bytes());
    coff.extend_from_slice(&(CHARACTERISTICS_EXECUTABLE as u16).to_le_bytes());
    let mut dos = vec![0u8; DOS_STUB_OFFSET];
    dos[0..2].copy_from_slice(b"MZ");
    put_u32(&mut dos, 0x3C, DOS_STUB_OFFSET as u32);
    let mut block = dos;
    block.extend_from_slice(b"PE\x00\x00");
    block.extend_from_slice(&coff);
    block.extend_from_slice(&optional);
    for section in &sections {
        block.extend_from_slice(section);
    }
    block.resize(HEADERS_SIZE, 0);
    block
}

fn import_address_table(imports: &[String], offsets: &BTreeMap<String, usize>,
                        rdata_rva: usize) -> Vec<u8> {
    let mut table = Vec::new();
    for function in imports {
        table.extend_from_slice(&((rdata_rva + offsets[function]) as u64).to_le_bytes());
    }
    table.extend_from_slice(&0u64.to_le_bytes());
    table
}

/// Lay out sections, resolve fixups and return the executable image bytes.
pub fn link(artifact: &Artifact) -> Result<Vec<u8>, String> {
    if artifact.text.is_empty() {
        return Err("cannot link an artifact with empty .text".to_string());
    }
    if !artifact.text_labels.contains_key(&artifact.entry) {
        return Err(format!("entry {:?} is not a .text label", artifact.entry));
    }
    if let Some(name) = artifact.text_labels.keys()
        .find(|name| artifact.rodata.labels.contains_key(*name)) {
        return Err(format!("label {:?} is bound in .text and .rdata", name));
    }
    let mut text = artifact.text.clone();
    let (mut blob, offsets) = import_blob(&artifact.imports);
    let rodata_offset = align(blob.len(), 8);
    let text_raw = align(text.len(), FILE_ALIGNMENT);
    let rdata_rva = TEXT_RVA + align(text.len(), SECTION_ALIGNMENT);
    let rdata_size = rodata_offset + artifact.rodata.data.len();
    let rdata_raw = align(rdata_size, FILE_ALIGNMENT);
    let data_rva = rdata_rva + align(rdata_size, SECTION_ALIGNMENT);
    let iat = import_address_table(&artifact.imports, &offsets, rdata_rva);
    let mut data = iat.clone();
    data.extend_from_slice(&artifact.writable);
    let data_raw = align(data.len(), FILE_ALIGNMENT);
    let writable_base = data_rva + iat.len();

    patch_imports(&mut blob, &artifact.imports, &offsets, rdata_rva, data_rva);
    let mut rodata = blob;
    rodata.resize(rodata_offset, 0);
    rodata.extend_from_slice(&artifact.rodata.data);

    let mut targets: BTreeMap<String, usize> = artifact
        .text_labels
        .iter()
        .map(|(name, offset)| (name.clone(), TEXT_RVA + offset))
        .collect();
    for (name, offset) in &artifact.rodata.labels {
        targets.insert(name.clone(), rdata_rva + rodata_offset + offset);
    }
    for (index, function) in artifact.imports.iter().enumerate() {
        targets.insert(iat_label(function), data_rva + index * 8);
    }
    for name in artifact.text_labels.keys() {
        if artifact.writable_labels.contains_key(name) || artifact.rodata.labels.contains_key(name) {
            return Err(format!("label {:?} is bound in two sections", name));
        }
    }
    for (name, offset) in &artifact.writable_labels {
        targets.insert(name.clone(), writable_base + offset);
    }

    for (offset, target) in &artifact.fixups {
        let Some(destination) = targets.get(target) else {
            return Err(format!("unresolved label {:?} at text offset {}", target, offset));
        };
        if *offset + 4 > text.len() {
            return Err(format!("fixup for {:?} at text offset {} is outside the {}-byte .text",
                               target, offset, text.len()));
        }
        put_i32(&mut text, *offset, (*destination as i32) - ((TEXT_RVA + offset + 4) as i32));
    }

    let block = headers(text.len(), text_raw, targets[&artifact.entry], rdata_size, rdata_raw,
                        rdata_rva, data.len(), data_raw, data_rva, rdata_rva, DESCRIPTOR_SIZE);
    let mut image = block;
    image.extend_from_slice(&text);
    image.resize(image.len() + (text_raw - text.len()), 0);
    image.extend_from_slice(&rodata);
    image.resize(image.len() + (rdata_raw - rodata.len()), 0);
    image.extend_from_slice(&data);
    image.resize(image.len() + (data_raw - data.len()), 0);
    Ok(image)
}

/// Read an image's optional-header magic, for callers that need a bounded probe.
pub fn image_magic(image: &[u8]) -> u16 {
    read_u16(image, 0x98)
}

/// Read the section count from a COFF header, for callers that need a bounded probe.
pub fn image_section_count(image: &[u8]) -> u16 {
    read_u16(image, 0x86)
}
