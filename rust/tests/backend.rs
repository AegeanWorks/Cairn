//! Backend tests: deterministic PE linking, verifier invariants and the label-grouped listing.

use std::path::Path;

use cairn_rs::image::{Artifact, ReadOnlyData};
use cairn_rs::listing::render_listing;
use cairn_rs::pe::{align, link};
use cairn_rs::spec;
use cairn_rs::verify::verify_image;

fn spec_path() -> &'static str {
    "../parity/specs/pe_basic.txt"
}

fn artifact() -> Artifact {
    spec::load(Path::new(spec_path())).unwrap()
}

#[test]
fn alignment_rounds_up() {
    assert_eq!(align(0, 0x200), 0);
    assert_eq!(align(1, 0x200), 0x200);
    assert_eq!(align(0x201, 0x200), 0x400);
    assert_eq!(align(0x1000, 0x1000), 0x1000);
}

#[test]
fn link_is_deterministic_and_starts_with_mz() {
    let first = link(&artifact()).unwrap();
    let second = link(&artifact()).unwrap();
    assert_eq!(first, second);
    assert_eq!(&first[0..2], b"MZ");
    assert_eq!(&first[0x80..0x84], b"PE\x00\x00");
    assert_eq!(first.len() % 0x200, 0);
}

#[test]
fn a_fresh_image_has_no_verifier_problems() {
    let image = link(&artifact()).unwrap();
    assert!(verify_image(&image).is_empty(), "{:?}", verify_image(&image));
}

#[test]
fn a_broken_signature_is_named_by_its_code() {
    let mut image = link(&artifact()).unwrap();
    image[0] = b'X';
    let codes: Vec<&str> = verify_image(&image).iter().map(|p| p.code).collect();
    assert!(codes.contains(&"V0001"), "{:?}", codes);
}

#[test]
fn a_broken_section_name_is_named_by_its_code() {
    let mut image = link(&artifact()).unwrap();
    image[0x188 + 3] = b'X';
    let codes: Vec<&str> = verify_image(&image).iter().map(|p| p.code).collect();
    assert!(codes.contains(&"V0004"), "{:?}", codes);
}

#[test]
fn the_listing_groups_bytes_by_label() {
    let listing = render_listing(&artifact()).unwrap();
    assert!(listing.starts_with("# .text, 37 bytes, 2 labels"));
    assert!(listing.contains("<cairn.entry>  (16 bytes)"));
    assert!(listing.contains("<helper>  (21 bytes)"));
    assert!(listing.contains("000010 <helper>"));
    assert!(listing.contains("# .rdata, 6 bytes"));
    assert!(listing.contains("<message>"));
}

#[test]
fn an_empty_text_cannot_be_linked() {
    let mut artifact = artifact();
    artifact.text.clear();
    assert!(link(&artifact).is_err());
}

#[test]
fn an_entry_outside_the_text_is_refused() {
    let mut artifact = artifact();
    artifact.entry = "missing".to_string();
    assert!(link(&artifact).is_err());
}

#[test]
fn rodata_labels_are_bound_once() {
    let mut data = ReadOnlyData::default();
    data.add("first", b"ab");
    assert_eq!(data.labels["first"], 0);
    data.align(8);
    assert_eq!(data.data.len(), 8);
}
