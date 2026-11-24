//! Rust-only verification against frozen reference-output hashes.
//!
//! `parity/golden.sha256` was captured once from the Python compiler after the Rust
//! implementation was proven byte-identical over the whole corpus. This test regenerates
//! every canonical dump with the Rust binary and compares SHA-256 digests; no Python
//! process is involved in ongoing verification.

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

fn sha256(bytes: &[u8]) -> String {
    // Minimal SHA-256 implementation to keep this test dependency-free.
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let mut state: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    let mut data = bytes.to_vec();
    let bit_len = (data.len() as u64) * 8;
    data.push(0x80);
    while data.len() % 64 != 56 {
        data.push(0);
    }
    data.extend_from_slice(&bit_len.to_be_bytes());
    for chunk in data.chunks(64) {
        let mut words = [0u32; 64];
        for (i, word) in words[..16].iter_mut().enumerate() {
            *word = u32::from_be_bytes([
                chunk[4 * i], chunk[4 * i + 1], chunk[4 * i + 2], chunk[4 * i + 3],
            ]);
        }
        for i in 16..64 {
            let s0 = words[i - 15].rotate_right(7) ^ words[i - 15].rotate_right(18)
                ^ (words[i - 15] >> 3);
            let s1 = words[i - 2].rotate_right(17) ^ words[i - 2].rotate_right(19)
                ^ (words[i - 2] >> 10);
            words[i] = words[i - 16]
                .wrapping_add(s0)
                .wrapping_add(words[i - 7])
                .wrapping_add(s1);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = state;
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let choice = (e & f) ^ ((!e) & g);
            let t1 = h.wrapping_add(s1)
                .wrapping_add(choice)
                .wrapping_add(K[i])
                .wrapping_add(words[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let majority = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(majority);
            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        state[0] = state[0].wrapping_add(a);
        state[1] = state[1].wrapping_add(b);
        state[2] = state[2].wrapping_add(c);
        state[3] = state[3].wrapping_add(d);
        state[4] = state[4].wrapping_add(e);
        state[5] = state[5].wrapping_add(f);
        state[6] = state[6].wrapping_add(g);
        state[7] = state[7].wrapping_add(h);
    }
    state.iter().map(|word| format!("{:08x}", word)).collect()
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
