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
    // The loop body used to be 76 bytes with a `mov rax,r15` before `add`; a
    // multiply by the other hoisted constant also copied it through rax first,
    // and the third constant now lives in caller-saved r8 instead of rax.
    assert!(listing.contains("<while.start.1>  (60 bytes)"),
            "hoisted-literal copy was not eliminated");
}

/// A bit builtin assigned straight to a home register must land there without a
/// rax round trip: one instruction per call, no `mov` afterwards.
#[test]
fn bit_builtins_write_straight_into_their_home() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let binary = root.join("rust/target/debug/cairn-rs").with_extension("exe");
    let folder = std::env::temp_dir().join(format!("cairn-builtin-into-{}", std::process::id()));
    let _ = std::fs::create_dir_all(&folder);
    let source = folder.join("builtin.crn");
    std::fs::write(&source, "\
fn main() -> i64 {
    var a: i64 = 255;
    var b: i64 = 33;
    var x: i64 = 0;
    var y: i64 = 0;
    var i: i64 = 0;
    while i < 100 {
        x = clz(a);
        y = bswap(b);
        a = a ^ (b + i);
        b = b + (x & 7) + (y & 3);
        i = i + 1;
    }
    print(a + b + x + y);
    return 0;
}
").unwrap();
    let output = std::process::Command::new(binary)
        .arg("listjson").arg(&source)
        .output()
        .expect("build the Rust binary before running this test");
    let listing = String::from_utf8_lossy(&output.stdout);
    // lzcnt rax,rax followed by a copy into the home was the shape before: three
    // instructions per builtin assignment. The direct form targets the home
    // register, so each assignment is a load into the home plus the operation.
    assert!(listing.contains("<while.start.1>  (92 bytes)"),
            "the loop body is not the direct form: {}", listing);
    assert!(!listing.contains("f3 48 0f bd c0"),
            "the leading-zero count still round-trips through rax");
    assert!(!listing.contains("48 0f c8"),
            "the byte swap still targets rax");
    assert!(listing.contains("f3 4d 0f bd"),
            "the leading-zero count did not reach a home register: {}", listing);
    let _ = std::fs::remove_dir_all(&folder);
}
