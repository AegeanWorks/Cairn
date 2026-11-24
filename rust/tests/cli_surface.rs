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
