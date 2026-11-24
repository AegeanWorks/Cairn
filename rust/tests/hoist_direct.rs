//! Regression test for combining hoisted literals directly with their home.

use std::path::Path;

#[test]
fn splitmix_uses_hoisted_constant_directly() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let binary = root.join("rust/target/debug/cairn-rs").with_extension("exe");
    let output = std::process::Command::new(binary)
        .arg("listjson")
        .arg(root.join("benchmarks/splitmix.crn"))
        .output()
        .expect("build the Rust binary before running this test");
    let listing = String::from_utf8_lossy(&output.stdout);
    // The loop body used to be 76 bytes with a `mov rax,r15` before `add`.
    assert!(listing.contains("<while.start.1>  (73 bytes)"),
            "hoisted-literal copy was not eliminated");
}
