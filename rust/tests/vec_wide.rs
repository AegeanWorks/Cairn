//! Regression test for the four-element SSE2 RMW loop.

use std::path::Path;

#[test]
fn array_rmw_uses_a_four_element_fast_loop() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let binary = root.join("rust/target/debug/cairn-rs").with_extension("exe");
    let output = std::process::Command::new(binary)
        .arg("listjson")
        .arg(root.join("benchmarks/array_sum.crn"))
        .output()
        .expect("build the Rust binary before running this test");
    let listing = String::from_utf8_lossy(&output.stdout);
    assert!(listing.contains("vec_rmw.wide"), "four-element loop missing");
    assert!(listing.contains("vec_rmw.start"), "two-element tail missing");
}
