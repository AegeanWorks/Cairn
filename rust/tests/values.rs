//! The no-dependency guard named by the core value registry.

use std::path::{Path, PathBuf};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

#[test]
fn the_crate_declares_no_dependencies() {
    let manifest = std::fs::read_to_string(root().join("rust").join("Cargo.toml"))
        .expect("rust/Cargo.toml is required");
    let mut in_dependencies = false;
    for line in manifest.lines() {
        let line = line.trim();
        if line.starts_with('[') {
            in_dependencies = line == "[dependencies]";
            continue;
        }
        if in_dependencies && !line.is_empty() && !line.starts_with('#') {
            panic!("Cargo.toml declares a dependency: {}", line);
        }
    }
}
