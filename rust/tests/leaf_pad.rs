//! Regression tests for leaf-function stack alignment.

use std::path::Path;

fn listing(source: &str, name: &str) -> String {
    let folder = std::env::temp_dir().join(format!("cairn-leaf-pad-{}", std::process::id()));
    let _ = std::fs::create_dir_all(&folder);
    let src = folder.join(format!("{name}.crn"));
    std::fs::write(&src, source).unwrap();
    let output = std::process::Command::new(
        Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap()
            .join("rust/target/debug/cairn-rs")
            .with_extension("exe"),
    )
    .arg("listjson")
    .arg(&src)
    .output()
    .expect("build the Rust binary before running this test");
    String::from_utf8_lossy(&output.stdout).into_owned()
}

fn prologue(text: &str, label: &str) -> String {
    let start = text.find(label).unwrap();
    text[start..start + 400].to_string()
}

#[test]
fn a_leaf_function_skips_the_alignment_pad() {
    // mix3 uses four callee-saved homes (even) but makes no call, so it must
    // not carry the sub rsp,8 / add rsp,8 alignment pair.
    let text = listing(include_str!("../../benchmarks/call_chain.crn"), "call_chain");
    let prologue = prologue(&text, "<fn.mix3>");
    assert!(!prologue.contains("48 81 ec 08"), "leaf still pads: {prologue}");
}

#[test]
fn a_calling_function_keeps_the_alignment_pad() {
    // ack is recursive and must keep the stack 16-byte aligned at its call sites.
    let text = listing(include_str!("../../benchmarks/ackermann.crn"), "ackermann");
    let prologue = prologue(&text, "<fn.ack>");
    assert!(prologue.contains("48 81 ec 08"), "caller lost its pad: {prologue}");
}
