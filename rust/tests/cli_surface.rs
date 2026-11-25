//! The user-facing CLI surface: build, run, verify, listing and capabilities.

use std::path::PathBuf;

fn root() -> PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

fn binary() -> PathBuf {
    let base = root().join("rust").join("target").join("debug").join("cairn-rs");
    if base.is_file() {
        return base;
    }
    base.with_extension("exe")
}

fn hello() -> PathBuf {
    root().join("examples").join("hello.crn")
}

fn scratch(name: &str) -> PathBuf {
    let folder = std::env::temp_dir().join(format!("cairn-cli-{}-{}", std::process::id(), name));
    let _ = std::fs::remove_dir_all(&folder);
    std::fs::create_dir_all(&folder).unwrap();
    folder
}

#[test]
fn build_writes_a_runnable_executable() {
    let folder = scratch("build");
    let exe = folder.join("hello.exe");
    let status = std::process::Command::new(binary())
        .arg("build").arg(hello()).arg("-o").arg(&exe)
        .status().expect("build the Rust binary before running this test");
    assert!(status.success(), "build command failed");
    let image = std::fs::read(&exe).expect("build wrote no executable");
    assert_eq!(&image[..2], b"MZ", "not a PE image");
    let output = std::process::Command::new(&exe).output().unwrap();
    assert_eq!(String::from_utf8_lossy(&output.stdout), "42\n");
    assert!(output.status.success());
    let _ = std::fs::remove_dir_all(&folder);
}

#[test]
fn run_prints_the_program_output_and_forwards_the_exit_code() {
    let output = std::process::Command::new(binary())
        .arg("run").arg(hello())
        .output().expect("build the Rust binary before running this test");
    assert_eq!(String::from_utf8_lossy(&output.stdout), "42\n");
    assert!(output.status.success());
}

#[test]
fn verify_accepts_a_built_image() {
    let folder = scratch("verify");
    let exe = folder.join("hello.exe");
    assert!(std::process::Command::new(binary())
        .arg("build").arg(hello()).arg("-o").arg(&exe)
        .status().unwrap().success());
    let output = std::process::Command::new(binary())
        .arg("verify").arg(&exe)
        .output().unwrap();
    assert!(output.status.success());
    let text = String::from_utf8_lossy(&output.stdout);
    assert!(text.starts_with("verified "), "unexpected verdict: {}", text);
    let _ = std::fs::remove_dir_all(&folder);
}

#[test]
fn listing_names_the_entry_and_main_labels() {
    let output = std::process::Command::new(binary())
        .arg("listing").arg(hello())
        .output().expect("build the Rust binary before running this test");
    assert!(output.status.success());
    let text = String::from_utf8_lossy(&output.stdout);
    assert!(text.contains("<cairn.entry>"), "listing has no entry label");
    assert!(text.contains("<fn.main>"), "listing has no main label");
}

#[test]
fn capabilities_publishes_the_manifest() {
    let output = std::process::Command::new(binary())
        .arg("capabilities")
        .output().expect("build the Rust binary before running this test");
    assert!(output.status.success());
    let text = String::from_utf8_lossy(&output.stdout);
    assert!(text.contains("\"name\": \"cairn\""), "manifest has no name");
    assert!(text.contains("\"commands\""), "manifest has no command list");
}

/// Compile and run one inline program, returning its stdout.
fn run_source(name: &str, source: &str) -> String {
    let folder = scratch(name);
    let path = folder.join("program.crn");
    std::fs::write(&path, source).unwrap();
    let output = std::process::Command::new(binary())
        .arg("run").arg(&path)
        .output().expect("build the Rust binary before running this test");
    let _ = std::fs::remove_dir_all(&folder);
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    String::from_utf8_lossy(&output.stdout).into_owned()
}

#[test]
fn an_indexed_store_with_an_expression_writes_the_element() {
    let folder = scratch("indexed-store");
    let source = folder.join("store.crn");
    std::fs::write(&source, "\
fn main() -> i64 {
    var a: [4]i64 = [0; 4];
    var i: i64 = 0;
    while i < 4 { a[i] = i + 10; i = i + 1; }
    var r: i64 = 0;
    while r < 3 {
        i = 0;
        while i < 4 { a[i] = a[i] + 1; i = i + 1; }
        r = r + 1;
    }
    print(a[0]);
    print(a[1]);
    print(a[2]);
    print(a[3]);
    return 0;
}
").unwrap();
    let output = std::process::Command::new(binary())
        .arg("run").arg(&source)
        .output().expect("build the Rust binary before running this test");
    assert!(output.status.success(), "{}",
            String::from_utf8_lossy(&output.stderr));
    assert_eq!(String::from_utf8_lossy(&output.stdout), "13\n14\n15\n16\n");
    let _ = std::fs::remove_dir_all(&folder);
}
/// The rotate count's own home can be the destination; loading rcx first must
/// keep the count even though the destination is overwritten afterwards.
#[test]
fn rotl_keeps_the_count_when_it_lives_in_the_destination() {
    let text = run_source("rotl-alias", "\
fn main() -> i64 {
    var x: i64 = 6;
    var k: i64 = 3;
    var i: i64 = 0;
    while i < 4 {
        x = rotl(x, k);
        k = k + 1;
        i = i + 1;
    }
    print(x);
    return 0;
}
");
    // rotl(6,3)=48, rotl(48,4)=768, rotl(768,5)=24576, rotl(24576,6)=1572864.
    assert_eq!(text.trim(), "1572864", "rotl lost its count when the count shared the target");
}

/// popcount into its own home, then into a fresh home, must both count correctly.
#[test]
fn popcount_targets_its_home_and_its_own_value() {
    let text = run_source("popcount-home", "\
fn main() -> i64 {
    var a: i64 = 255;
    var b: i64 = 0;
    b = popcount(a);
    print(b);
    a = popcount(a);
    print(a);
    b = popcount(a);
    print(b);
    return 0;
}
");
    // 255 has eight bits, so both first counts are 8; the next popcount of 8 is 1.
    assert_eq!(text.trim(), "8\n8\n1", "popcount produced the wrong counts");
}

#[test]
fn a_loop_with_a_break_still_exits_at_the_right_iteration() {
    let text = run_source("break-guard", "\
fn main() -> i64 {
    var i: i64 = 0;
    var t: i64 = 0;
    while i < 10 {
        if i == 3 {
            break;
        }
        t = t + i;
        i = i + 1;
    }
    print(t);
    print(i);
    return 0;
}
");
    assert_eq!(text.trim(), "3\n3", "a duplicated body ignored the early exit");
}
