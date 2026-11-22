//! Entry point for the Rust compiler. The CLI is ported in a later task; until then this binary
//! reports the version, or prints the canonical front-end dump used by the parity check.

use std::path::Path;

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() == 2 && arguments[1] == "runtime" {
        let (asm, rodata, writable) = cairn_rs::runtime::emit_all();
        let hex = |bytes: &[u8]| bytes.iter().map(|b| format!("{:02x}", b))
            .collect::<Vec<_>>().join("");
        let mut lines = vec![format!("CODE {}", hex(&asm.code))];
        lines.push(format!("LABELS {}", asm.labels.iter()
            .map(|(name, offset)| format!("{}={}", name, offset))
            .collect::<Vec<_>>().join("|")));
        lines.push(format!("FIXUPS {}", asm.fixups.iter()
            .map(|(offset, target)| format!("{}={}", offset, target))
            .collect::<Vec<_>>().join("|")));
        lines.push(format!("RODATA {}", hex(&rodata.data)));
        lines.push(format!("ROLABELS {}", rodata.labels.iter()
            .map(|(name, offset)| format!("{}={}", name, offset))
            .collect::<Vec<_>>().join("|")));
        lines.push(format!("WRSIZE {}", writable.data.len()));
        lines.push(format!("WRLABELS {}", writable.labels.iter()
            .map(|(name, offset)| format!("{}={}", name, offset))
            .collect::<Vec<_>>().join("|")));
        println!("{}", lines.join("\n"));
        return;
    }
    if arguments.len() == 3 && arguments[1] == "fold" {
        let text = std::fs::read_to_string(&arguments[2]).unwrap_or_else(|error| {
            eprintln!("cannot read {}: {}", arguments[2], error);
            std::process::exit(2);
        });
        for line in text.lines() {
            let fields: Vec<&str> = line.split_whitespace().collect();
            let answer = match fields.as_slice() {
                ["BIN", op, left, right] => {
                    let left = left.parse::<i128>().unwrap_or(0);
                    let right = right.parse::<i128>().unwrap_or(0);
                    cairn_rs::optimize::fold_constant(op, left, right)
                }
                ["UN", op, value] => {
                    cairn_rs::optimize::fold_unary(op, value.parse::<i128>().unwrap_or(0))
                }
                _ => None,
            };
            match answer {
                Some(value) => println!("{} -> {}", line, value),
                None => println!("{} -> NONE", line),
            }
        }
        return;
    }
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
