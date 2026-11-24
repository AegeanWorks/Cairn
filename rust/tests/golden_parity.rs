//! Rust-only verification against frozen reference-output hashes.
//!
//! `parity/golden.sha256` was captured once from the Python compiler after the Rust
//! implementation was proven byte-identical over the whole corpus. This test regenerates
//! every canonical dump with the Rust binary and compares SHA-256 digests; no Python
//! process is involved in ongoing verification.

mod common;

use common::sha256;
use std::path::{Path, PathBuf};
use std::process::Command;

const REJECTED: &[&str] = &[
    "let x: i64 = 9223372036854775809;",
    "fn main() -> f64 { return 0; }",
    "fn main() -> i64 { 1; return 0; }",
    "fn main() -> i64 { return 0 }",
    "fn main() -> i64 { for i in 0 10 { } return 0; }",
    "fn main() -> i64 { var a: [4]i64 = [0; n]; return 0; }",
    "fn main() -> i64 { return -9223372036854775808; }",
    "let caf\u{e9}: i64 = 1;",
    "print_str(\"bad\\q\");",
];

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

fn binary() -> PathBuf {
    let base = root().join("rust").join("target").join("debug").join("cairn-rs");
    if base.is_file() {
        return base;
    }
    base.with_extension("exe")
}

fn source_path(name: &str) -> Option<PathBuf> {
    let base = root();
    for folder in ["examples", "benchmarks", "parity/specs/codegen", "parity/specs"] {
        let candidate = base.join(folder).join(name);
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}


#[test]
fn rust_output_matches_frozen_reference_hashes() {
    let manifest = std::fs::read_to_string(root().join("parity").join("golden.sha256"))
        .expect("parity/golden.sha256 is required");
    let bin = binary();
    assert!(bin.is_file(), "build the Rust binary before running this test");
    let mut checked = 0usize;
    let mut failures = Vec::new();
    for line in manifest.lines() {
        let (expected, key) = line.split_once("  ").expect("malformed golden manifest");
        let (command, filename) = key.split_once('/').expect("malformed golden key");
        let mut args: Vec<String> = vec![command.to_string()];
        let source: Option<PathBuf> = if filename == "runtime.txt"
            || filename == "capabilities.txt" {
            None
        } else if filename.starts_with("rejected-") {
            let digits: String = filename
                .trim_start_matches("rejected-")
                .chars()
                .take_while(|c| c.is_ascii_digit())
                .collect();
            let index: usize = digits.parse().unwrap();
            let path = std::env::temp_dir().join(format!("cairn-golden-rejected-{}.crn", index));
            std::fs::write(&path, REJECTED[index]).unwrap();
            Some(path)
        } else {
            Some(source_path(filename).unwrap_or_else(|| panic!("missing source {}", filename)))
        };
        if let Some(path) = source {
            args.push(path.to_string_lossy().into_owned());
        }
        let output = Command::new(&bin).args(&args).output().expect("failed to run cairn-rs");
        let actual = sha256(&output.stdout);
        checked += 1;
        if actual != expected {
            failures.push(format!("{}: expected {} got {}", key, expected, actual));
        }
    }
    assert!(failures.is_empty(), "{} of {} golden checks failed:\n{}",
            failures.len(), checked, failures.join("\n"));
    assert_eq!(checked, 591, "unexpected golden manifest size");
}
