//! Verification of a built artifact: structural checks that need no execution.
//!
//! Every check is decidable from the bytes alone and reports a stable `V####` code, so a failure
//! names the invariant that broke rather than an offset.

use crate::image::IMPORTS;
use crate::pe::{DESCRIPTOR_SIZE, FILE_ALIGNMENT, HEADERS_SIZE, PE32_PLUS_MAGIC, TEXT_RVA};

const MACHINE_AMD64: u16 = 0x8664;
const EXPECTED_SECTIONS: &[&str] = &[".text", ".rdata", ".data"];
const OPTIONAL_OFFSET: usize = 0x98;
const SECTION_TABLE_OFFSET: usize = OPTIONAL_OFFSET + 0xF0;
const SECTION_READ: u32 = 0x40000000;
const SECTION_WRITE: u32 = 0x80000000;
const SECTION_EXECUTE: u32 = 0x20000000;

/// One failed invariant, addressed by its code.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Problem {
    pub code: &'static str,
    pub detail: String,
}

impl Problem {
    fn new(code: &'static str, detail: impl Into<String>) -> Self {
        Self { code, detail: detail.into() }
    }

    /// The one-line report form.
    pub fn render(&self) -> String {
        format!("[{}] {}", self.code, self.detail)
    }
}

fn u16_at(image: &[u8], offset: usize) -> u16 {
    image.get(offset..offset + 2).map_or(0, |part| u16::from_le_bytes([part[0], part[1]]))
}

fn u32_at(image: &[u8], offset: usize) -> u32 {
    image
        .get(offset..offset + 4)
        .map_or(0, |part| u32::from_le_bytes([part[0], part[1], part[2], part[3]]))
}

fn u64_at(image: &[u8], offset: usize) -> u64 {
    image.get(offset..offset + 8).map_or(0, |part| {
        u64::from_le_bytes([part[0], part[1], part[2], part[3], part[4], part[5], part[6], part[7]])
    })
}

fn section_name(raw: &[u8]) -> String {
    let end = raw.iter().position(|byte| *byte == 0).unwrap_or(raw.len());
    String::from_utf8_lossy(&raw[..end]).into_owned()
}

/// `(name, header offset)` for every section table entry that fits in the file.
fn sections(image: &[u8]) -> Vec<(Vec<u8>, usize)> {
    let declared = u16_at(image, 0x86) as usize;
    let available = image.len().saturating_sub(SECTION_TABLE_OFFSET) / 40;
    let count = declared.min(available);
    (0..count)
        .map(|index| {
            let start = SECTION_TABLE_OFFSET + index * 40;
            (image.get(start..start + 8).unwrap_or(&[]).to_vec(), start)
        })
        .collect()
}

/// Translate an RVA through the section table; -1 when it lands in no section.
fn rva_to_offset(image: &[u8], rva: u32) -> i64 {
    for (_, header) in sections(image) {
        let start = u32_at(image, header + 12);
        let size = u32_at(image, header + 8).max(1);
        if start <= rva && rva < start + size {
            return (u32_at(image, header + 20) as i64) + (rva - start) as i64;
        }
    }
    -1
}

/// The section name an RVA lands in; the empty string when it lands in none.
fn section_of(image: &[u8], rva: u32) -> String {
    for (name, header) in sections(image) {
        let start = u32_at(image, header + 12);
        let size = u32_at(image, header + 8).max(1);
        if start <= rva && rva < start + size {
            return section_name(&name);
        }
    }
    String::new()
}

fn check_signatures(image: &[u8], problems: &mut Vec<Problem>) -> bool {
    if image.len() < HEADERS_SIZE {
        problems.push(Problem::new(
            "V0001",
            format!("image is {} bytes, shorter than the headers", image.len()),
        ));
        return false;
    }
    if image.get(0..2) != Some(b"MZ".as_slice()) {
        problems.push(Problem::new("V0001", "no MZ signature at offset 0"));
    }
    let lfanew = u32_at(image, 0x3C);
    if lfanew != 0x80 {
        problems.push(Problem::new(
            "V0001",
            format!("e_lfanew is {:#x}, expected 0x80", lfanew),
        ));
    } else if image.get(0x80..0x84) != Some(b"PE\x00\x00".as_slice()) {
        problems.push(Problem::new("V0001", "no PE signature where e_lfanew points"));
    }
    true
}

fn check_coff(image: &[u8], problems: &mut Vec<Problem>) {
    let machine = u16_at(image, 0x84);
    if machine != MACHINE_AMD64 {
        problems.push(Problem::new(
            "V0002",
            format!("machine is {:#06x}, expected 0x8664", machine),
        ));
    }
    let count = u16_at(image, 0x86);
    if count as usize != EXPECTED_SECTIONS.len() {
        problems.push(Problem::new(
            "V0002",
            format!("section count is {}, expected {}", count, EXPECTED_SECTIONS.len()),
        ));
    }
    let size = u16_at(image, 0x94);
    if size != 0xF0 {
        problems.push(Problem::new(
            "V0002",
            format!("optional header size is {}, expected 240", size),
        ));
    }
}

fn check_optional(image: &[u8], problems: &mut Vec<Problem>) {
    let magic = u16_at(image, OPTIONAL_OFFSET);
    if magic != PE32_PLUS_MAGIC {
        problems.push(Problem::new(
            "V0003",
            format!("magic is {:#06x}, expected 0x20b", magic),
        ));
    }
    let subsystem = u16_at(image, OPTIONAL_OFFSET + 68);
    if subsystem != 3 {
        problems.push(Problem::new(
            "V0003",
            format!("subsystem is {}, expected 3 (console)", subsystem),
        ));
    }
}

fn check_section_names(image: &[u8], problems: &mut Vec<Problem>) -> bool {
    let names: Vec<String> = sections(image).iter().map(|(name, _)| section_name(name)).collect();
    if names.iter().map(String::as_str).collect::<Vec<_>>() != EXPECTED_SECTIONS {
        problems.push(Problem::new(
            "V0004",
            format!("sections are {:?}, expected {:?}", names, EXPECTED_SECTIONS),
        ));
        return false;
    }
    true
}

fn check_section_geometry(image: &[u8], problems: &mut Vec<Problem>) {
    let mut previous_end = HEADERS_SIZE as u32;
    for (name, header) in sections(image) {
        let label = section_name(&name);
        let pointer = u32_at(image, header + 20);
        let size = u32_at(image, header + 16);
        if pointer % FILE_ALIGNMENT as u32 != 0 {
            problems.push(Problem::new(
                "V0005",
                format!("{} raw pointer {:#x} is not {:#x}-aligned",
                        label, pointer, FILE_ALIGNMENT),
            ));
        }
        if pointer < previous_end {
            problems.push(Problem::new(
                "V0005",
                format!("{} overlaps the previous section", label),
            ));
        }
        previous_end = pointer + size;
        if previous_end as usize > image.len() {
            problems.push(Problem::new(
                "V0010",
                format!("{} claims bytes up to {:#x}, past the {:#x}-byte file",
                        label, previous_end, image.len()),
            ));
        }
    }
}

fn check_permissions(image: &[u8], problems: &mut Vec<Problem>) {
    for (name, header) in sections(image) {
        let label = section_name(&name);
        let flags = u32_at(image, header + 36);
        let readable = flags & SECTION_READ != 0;
        let writable = flags & SECTION_WRITE != 0;
        let executable = flags & SECTION_EXECUTE != 0;
        if writable && executable {
            problems.push(Problem::new(
                "V0009",
                format!("{} is both writable and executable", label),
            ));
        }
        if !readable {
            problems.push(Problem::new("V0009", format!("{} is not readable", label)));
        }
        if label == ".text" && (writable || !executable) {
            problems.push(Problem::new("V0009", ".text must be executable and not writable"));
        }
        if label == ".data" && (!writable || executable) {
            problems.push(Problem::new("V0009", ".data must be writable and not executable"));
        }
        if label == ".rdata" && (writable || executable) {
            problems.push(Problem::new("V0009", ".rdata must be read-only and not executable"));
        }
    }
}

fn check_entry(image: &[u8], problems: &mut Vec<Problem>) {
    let entry = u32_at(image, OPTIONAL_OFFSET + 16);
    let mut range = (TEXT_RVA as u32, TEXT_RVA as u32);
    for (name, header) in sections(image) {
        if section_name(&name) == ".text" {
            let start = u32_at(image, header + 12);
            range = (start, start + u32_at(image, header + 8).max(1));
        }
    }
    if !(range.0 <= entry && entry < range.1) {
        problems.push(Problem::new(
            "V0006",
            format!("entry RVA {:#x} is outside .text [{:#x}, {:#x})",
                    entry, range.0, range.1),
        ));
    }
}

fn check_image_size(image: &[u8], problems: &mut Vec<Problem>) {
    let size = u32_at(image, OPTIONAL_OFFSET + 56);
    let mut highest = 0u32;
    for (_, header) in sections(image) {
        let start = u32_at(image, header + 12);
        let section_size = u32_at(image, header + 8);
        highest = highest.max(start + ((section_size + 0xFFF) & !0xFFF));
    }
    if size < highest {
        problems.push(Problem::new(
            "V0008",
            format!("SizeOfImage is {:#x}, below the {:#x} the layout needs", size, highest),
        ));
    }
}

fn check_imports(image: &[u8], problems: &mut Vec<Problem>) {
    let directory_rva = u32_at(image, OPTIONAL_OFFSET + 120);
    let directory_size = u32_at(image, OPTIONAL_OFFSET + 124);
    if directory_rva == 0 || directory_size == 0 {
        problems.push(Problem::new("V0007", "the import directory is empty"));
        return;
    }
    if (directory_size as usize) < DESCRIPTOR_SIZE {
        problems.push(Problem::new(
            "V0007",
            format!("import directory size {} is below {}", directory_size, DESCRIPTOR_SIZE),
        ));
    }
    if rva_to_offset(image, directory_rva) < 0 {
        problems.push(Problem::new(
            "V0007",
            format!("import directory RVA {:#x} is outside every section", directory_rva),
        ));
        return;
    }
    let offset = rva_to_offset(image, directory_rva) as usize;
    if offset + DESCRIPTOR_SIZE > image.len() {
        problems.push(Problem::new(
            "V0007",
            format!("import directory at {:#x} is past the {:#x}-byte file",
                    offset, image.len()),
        ));
        return;
    }
    let section = section_of(image, directory_rva);
    if section != ".rdata" {
        problems.push(Problem::new(
            "V0007",
            format!("import directory RVA {:#x} is in {}, expected .rdata",
                    directory_rva, if section.is_empty() { "no section" } else { &section }),
        ));
        return;
    }
    let ilt_rva = u32_at(image, offset);
    let dll_rva = u32_at(image, offset + 12);
    let iat_rva = u32_at(image, offset + 16);
    if ilt_rva == 0 || dll_rva == 0 || iat_rva == 0 {
        problems.push(Problem::new("V0007", "the import descriptor has an empty field"));
        return;
    }
    for (label, rva) in [("ILT", ilt_rva), ("DLL name", dll_rva)] {
        let section = section_of(image, rva);
        if section != ".rdata" {
            problems.push(Problem::new(
                "V0007",
                format!("{} RVA {:#x} is in {}, expected .rdata",
                        label, rva, if section.is_empty() { "no section" } else { &section }),
            ));
            return;
        }
    }
    let iat_section = section_of(image, iat_rva);
    if iat_section != ".data" {
        problems.push(Problem::new(
            "V0007",
            format!("IAT RVA {:#x} is in {}, expected .data",
                    iat_rva, if iat_section.is_empty() { "no section" } else { &iat_section }),
        ));
        return;
    }
    let dll_offset = rva_to_offset(image, dll_rva) as usize;
    let iat = rva_to_offset(image, iat_rva) as usize;
    if dll_offset + 16 > image.len() {
        problems.push(Problem::new("V0007", "the DLL name is past the end of the file"));
        return;
    }
    if iat + 8 * (IMPORTS.len() + 1) > image.len() {
        problems.push(Problem::new(
            "V0007",
            "the import address table is past the end of the file",
        ));
        return;
    }
    let dll = image.get(dll_offset..dll_offset + 16).unwrap_or(&[]);
    let lowered = String::from_utf8_lossy(dll).to_lowercase();
    if !lowered.contains("kernel32.dll") {
        problems.push(Problem::new("V0007", "the import descriptor does not name kernel32.dll"));
    }
    if u64_at(image, iat + 8 * IMPORTS.len()) != 0 {
        problems.push(Problem::new("V0007", "the import address table has no null terminator"));
    }
}

/// Every structural invariant of one produced executable; an empty list means verified.
pub fn verify_image(image: &[u8]) -> Vec<Problem> {
    let mut problems = Vec::new();
    if !check_signatures(image, &mut problems) {
        return problems;
    }
    check_coff(image, &mut problems);
    check_optional(image, &mut problems);
    if !check_section_names(image, &mut problems) {
        return problems;
    }
    check_section_geometry(image, &mut problems);
    check_permissions(image, &mut problems);
    check_entry(image, &mut problems);
    check_image_size(image, &mut problems);
    check_imports(image, &mut problems);
    problems
}
