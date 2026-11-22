//! Entry point for the Rust compiler. The CLI is ported in a later task; until then this binary
//! reports the version, or prints the canonical front-end dump used by the parity check.

use std::path::Path;

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() == 3 && arguments[1] == "pe" {
        match cairn_rs::spec::dump(Path::new(&arguments[2])) {
            Ok(dump) => print!("{}", dump),
            Err(error) => {
                eprintln!("cannot dump {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    if arguments.len() == 3 && arguments[1] == "dump" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            // Python's text-mode read translates CRLF and bare CR to LF; mirror that so spans
            // and offsets match the reference compiler exactly.
            Ok(source) => {
                let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
                print!("{}", cairn_rs::dump::dump_source(&normalized, &arguments[2]));
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    println!("cairn-rs {} ({})", cairn_rs::version::VERSION,
             cairn_rs::version::LANGUAGE_VERSION);
}
