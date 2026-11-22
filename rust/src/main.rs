//! Entry point for the Rust compiler. The CLI is ported in a later task; for now this binary
//! reports the version so the crate is runnable from the first milestone.

fn main() {
    println!("cairn-rs {} ({})", cairn_rs::version::VERSION,
             cairn_rs::version::LANGUAGE_VERSION);
}
