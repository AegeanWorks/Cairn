//! Twin parity: every benchmark's Cairn build prints the same total as its C twin.
//!
//! `parity/twin_expected.sha256` holds SHA-256 digests of each C twin's stdout,
//! captured once on 2026-11-24 after checking that the C++, Rust and Cairn twins
//! printed the identical text. This test rebuilds every case with the Rust
//! compiler and compares digests, so a codegen change cannot silently earn a
//! standing against a twin that computes something else.
//!
//! argv_walk is excluded by design: its checksum folds the program's own path
//! bytes, so the four twins differ per build path (see benchmarks/argv_walk.c).

mod common;

use common::sha256;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

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

fn expected() -> Vec<(String, String)> {
    let text = std::fs::read_to_string(root().join("parity").join("twin_expected.sha256"))
        .expect("parity/twin_expected.sha256 is required");
    text.lines()
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .map(|line| {
            let (digest, case) = line.split_once("  ").expect("malformed twin manifest");
            (case.to_string(), digest.to_string())
        })
        .collect()
}

/// The piped stdin the benchmark harness feeds a case, if any.
fn stdin_for(case: &str) -> Option<Vec<u8>> {
    if case != "char_stream" {
        return None;
    }
    // 0x1A is text-mode EOF on the Windows CRT, so it is excluded (ADR-112).
    let pattern: Vec<u8> = (0u16..256).filter(|byte| *byte != 0x1A)
        .map(|byte| byte as u8).collect();
    let mut data = Vec::with_capacity(4_194_304);
    while data.len() < 4_194_304 {
        let take = (4_194_304 - data.len()).min(pattern.len());
        data.extend_from_slice(&pattern[..take]);
    }
    Some(data)
}

fn total(case: &str) -> String {
    let folder = std::env::temp_dir().join(format!(
        "cairn-twin-{}-{}", std::process::id(), case));
    let _ = std::fs::create_dir_all(&folder);
    let exe = folder.join("program.exe");
    let source = root().join("benchmarks").join(format!("{}.crn", case));
    let build = Command::new(binary())
        .arg("buildexe").arg(&source).arg(&exe)
        .output()
        .expect("build the Rust binary before running this test");
    let log = String::from_utf8_lossy(&build.stdout);
    assert!(build.status.success() && exe.is_file(),
            "{} did not build: {}", case, log);
    let mut child = Command::new(&exe)
        .stdin(if stdin_for(case).is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the built executable could not start");
    if let Some(data) = stdin_for(case) {
        child.stdin.as_mut().unwrap().write_all(&data).unwrap();
    }
    let output = child.wait_with_output().unwrap();
    let _ = std::fs::remove_dir_all(&folder);
    assert!(output.status.success(),
            "{} exited with {:?}: {}", case, output.status.code(),
            String::from_utf8_lossy(&output.stderr));
    String::from_utf8_lossy(&output.stdout).into_owned()
}

fn verify(cases: &[&str]) {
    let manifest = expected();
    assert_eq!(manifest.len(), 23, "unexpected twin manifest size");
    for case in cases {
        let digest = manifest.iter().find(|(name, _)| name == case)
            .unwrap_or_else(|| panic!("{} is missing from the twin manifest", case))
            .1
            .clone();
        let actual = sha256(total(case).as_bytes());
        assert_eq!(actual, digest,
                   "{} no longer prints its C twin's total", case);
    }
}

#[test]
fn latency_and_frame_cases_match_their_c_twins() {
    verify(&["xorshift", "fib30", "folded_math", "collatz", "ackermann",
             "nqueens", "binary_gcd"]);
}

#[test]
fn call_and_memory_cases_match_their_c_twins() {
    verify(&["call_chain", "tak", "primes", "mandelbrot", "splitmix",
             "hex_io", "many_params"]);
}

#[test]
fn bit_and_stream_cases_match_their_c_twins() {
    verify(&["char_stream", "str_scan", "array_sum", "sieve", "popcount",
             "clz", "ctz", "bswap", "rotl"]);
}
