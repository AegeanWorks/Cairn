//! Entry point for the Rust compiler. The CLI is ported in a later task; until then this binary
//! reports the version, or prints the canonical front-end dump used by the parity check.

use std::path::Path;

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() == 3 && arguments[1] == "verifyjson" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            Ok(source) => {
                let normalized = source.trim_start_matches('\u{feff}')
                    .replace("\r\n", "\n").replace('\r', "\n");
                let display = arguments[2].replace('/', "\\");
                print!("{}", cairn_rs::cli::verify_json(&normalized, &display));
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    if arguments.len() == 3 && arguments[1] == "listjson" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            Ok(source) => {
                let normalized = source.trim_start_matches('\u{feff}')
                    .replace("\r\n", "\n").replace('\r', "\n");
                let display = arguments[2].replace('/', "\\");
                print!("{}", cairn_rs::cli::listing_json(&normalized, &display));
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    if arguments.len() == 3 && arguments[1] == "build" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            Ok(source) => {
                let normalized = source.trim_start_matches('\u{feff}')
                    .replace("\r\n", "\n").replace('\r', "\n");
                match cairn_rs::builder::compile_source(&normalized, &arguments[2], true) {
                    Ok(build) => {
                        let hex = build.image.iter().map(|byte| format!("{:02x}", byte))
                            .collect::<Vec<_>>().join("");
                        println!("FOLDED {}", build.folded);
                        println!("SIZE {}", build.image.len());
                        println!("IMAGE {}", hex);
                    }
                    Err(error) => {
                        for diagnostic in &error.diagnostics {
                            println!("ERROR[{},{},{},{}]", diagnostic.code,
                                     diagnostic.message, diagnostic.fix,
                                     diagnostic.span.start);
                        }
                    }
                }
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    if arguments.len() == 3 && arguments[1] == "check" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            Ok(source) => {
                let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
                let display = arguments[2].replace('/', "\\");
                print!("{}", cairn_rs::cli::check_json(&normalized, &display));
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    if arguments.len() == 2 && arguments[1] == "capjson" {
        print!("{}", cairn_rs::capabilities::capabilities_json());
        return;
    }
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
    if arguments.len() == 3 && arguments[1] == "lower" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            Ok(source) => {
                let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
                let program = cairn_rs::parser::parse(&normalized, &arguments[2])
                    .and_then(|parsed| {
                        let mut analysed = parsed;
                        cairn_rs::sema::analyze(&mut analysed, &arguments[2])?;
                        Ok(cairn_rs::optimize::lower_for_loops(&analysed))
                    });
                match program {
                    Ok(program) => println!("{}", cairn_rs::dump::program_dump(&program)),
                    Err(error) => {
                        for diagnostic in &error.diagnostics {
                            println!("ERROR[{},{},{},{}]", diagnostic.code,
                                     diagnostic.message, diagnostic.fix,
                                     diagnostic.span.start);
                        }
                    }
                }
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    if arguments.len() == 3 && arguments[1] == "copyprop" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            Ok(source) => {
                let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
                let program = cairn_rs::parser::parse(&normalized, &arguments[2])
                    .and_then(|parsed| {
                        let mut analysed = parsed;
                        cairn_rs::sema::analyze(&mut analysed, &arguments[2])?;
                        let lowered = cairn_rs::optimize::lower_for_loops(&analysed);
                        let mut result = lowered;
                        cairn_rs::optimize::apply_copy_propagation(&mut result);
                        Ok(result)
                    });
                match program {
                    Ok(program) => println!("{}", cairn_rs::dump::program_dump(&program)),
                    Err(error) => {
                        for diagnostic in &error.diagnostics {
                            println!("ERROR[{},{},{},{}]", diagnostic.code,
                                     diagnostic.message, diagnostic.fix,
                                     diagnostic.span.start);
                        }
                    }
                }
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    if arguments.len() == 3 && arguments[1] == "codegen" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            Ok(source) => {
                let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
                let program = cairn_rs::parser::parse(&normalized, &arguments[2])
                    .and_then(|mut parsed| {
                        cairn_rs::sema::analyze(&mut parsed, &arguments[2])?;
                        let _ = cairn_rs::pipeline::Pipeline::new()
                            .run(&mut parsed, &arguments[2]);
                        Ok(parsed)
                    });
                match program {
                    Ok(program) => {
                        let artifact = cairn_rs::generator::CodeGenerator::new().generate(&program);
                        let hex = |bytes: &[u8]| bytes.iter().map(|b| format!("{:02x}", b))
                            .collect::<Vec<_>>().join("");
                        let mut lines = vec![format!("TEXT {}", hex(&artifact.text))];
                        lines.push(format!("LABELS {}", artifact.text_labels.iter()
                            .map(|(name, offset)| format!("{}={}", name, offset))
                            .collect::<Vec<_>>().join("|")));
                        lines.push(format!("FIXUPS {}", artifact.fixups.iter()
                            .map(|(offset, target)| format!("{}={}", offset, target))
                            .collect::<Vec<_>>().join("|")));
                        lines.push(format!("RODATA {}", hex(&artifact.rodata.data)));
                        lines.push(format!("ROLABELS {}", artifact.rodata.labels.iter()
                            .map(|(name, offset)| format!("{}={}", name, offset))
                            .collect::<Vec<_>>().join("|")));
                        lines.push(format!("WRITABLE {}", hex(&artifact.writable)));
                        lines.push(format!("WRLABELS {}", artifact.writable_labels.iter()
                            .map(|(name, offset)| format!("{}={}", name, offset))
                            .collect::<Vec<_>>().join("|")));
                        println!("{}", lines.join("\n"));
                    }
                    Err(error) => {
                        for diagnostic in &error.diagnostics {
                            println!("ERROR[{},{},{},{}]", diagnostic.code,
                                     diagnostic.message, diagnostic.fix,
                                     diagnostic.span.start);
                        }
                    }
                }
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    if arguments.len() == 3 && arguments[1] == "magic" {
        let text = std::fs::read_to_string(&arguments[2]).unwrap_or_else(|error| {
            eprintln!("cannot read {}: {}", arguments[2], error);
            std::process::exit(2);
        });
        for line in text.lines() {
            let divisor = line.parse::<i128>().unwrap_or(0);
            match cairn_rs::codegen::magic_division_parameters(divisor) {
                Some((multiplier, shift)) => println!("{} -> {},{}", line, multiplier, shift),
                None => println!("{} -> NONE", line),
            }
        }
        return;
    }
    if arguments.len() == 3 && arguments[1] == "final" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            Ok(source) => {
                let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
                let program = cairn_rs::parser::parse(&normalized, &arguments[2])
                    .and_then(|parsed| {
                        let mut analysed = parsed;
                        cairn_rs::sema::analyze(&mut analysed, &arguments[2])?;
                        let mut result = cairn_rs::optimize::lower_for_loops(&analysed);
                        cairn_rs::optimize::apply_copy_propagation(&mut result);
                        cairn_rs::optimize::apply_algebraic_simplification(&mut result);
                        cairn_rs::optimize::apply_constant_loop_unrolling(&mut result);
                        cairn_rs::optimize::apply_countdown_latches(&mut result);
                        let first = cairn_rs::optimize::apply_odd_pair_fusion(&mut result);
                        if first > 0 {
                            result = cairn_rs::optimize::Optimizer::new().fold_program(&result);
                            cairn_rs::optimize::apply_odd_pair_fusion(&mut result);
                        }
                        cairn_rs::optimize::apply_induction_strength_reduction(&mut result);
                        cairn_rs::optimize::apply_bottom_test_rotation(&mut result);
                        Ok(result)
                    });
                match program {
                    Ok(program) => println!("{}", cairn_rs::dump::program_dump(&program)),
                    Err(error) => {
                        for diagnostic in &error.diagnostics {
                            println!("ERROR[{},{},{},{}]", diagnostic.code,
                                     diagnostic.message, diagnostic.fix,
                                     diagnostic.span.start);
                        }
                    }
                }
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    if arguments.len() == 3 && arguments[1] == "fuse" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            Ok(source) => {
                let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
                let program = cairn_rs::parser::parse(&normalized, &arguments[2])
                    .and_then(|parsed| {
                        let mut analysed = parsed;
                        cairn_rs::sema::analyze(&mut analysed, &arguments[2])?;
                        let mut result = cairn_rs::optimize::lower_for_loops(&analysed);
                        cairn_rs::optimize::apply_copy_propagation(&mut result);
                        cairn_rs::optimize::apply_algebraic_simplification(&mut result);
                        cairn_rs::optimize::apply_countdown_latches(&mut result);
                        let first = cairn_rs::optimize::apply_odd_pair_fusion(&mut result);
                        if first > 0 {
                            let mut folded = cairn_rs::optimize::Optimizer::new()
                                .fold_program(&result);
                            cairn_rs::optimize::apply_odd_pair_fusion(&mut folded);
                            result = folded;
                        }
                        Ok(result)
                    });
                match program {
                    Ok(program) => println!("{}", cairn_rs::dump::program_dump(&program)),
                    Err(error) => {
                        for diagnostic in &error.diagnostics {
                            println!("ERROR[{},{},{},{}]", diagnostic.code,
                                     diagnostic.message, diagnostic.fix,
                                     diagnostic.span.start);
                        }
                    }
                }
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    if arguments.len() == 3 && arguments[1] == "inline" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            Ok(source) => {
                let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
                let program = cairn_rs::parser::parse(&normalized, &arguments[2])
                    .and_then(|parsed| {
                        let mut analysed = parsed;
                        cairn_rs::sema::analyze(&mut analysed, &arguments[2])?;
                        let mut result = analysed;
                        cairn_rs::optimize::apply_pure_inlining(&mut result);
                        Ok(result)
                    });
                match program {
                    Ok(program) => println!("{}", cairn_rs::dump::program_dump(&program)),
                    Err(error) => {
                        for diagnostic in &error.diagnostics {
                            println!("ERROR[{},{},{},{}]", diagnostic.code,
                                     diagnostic.message, diagnostic.fix,
                                     diagnostic.span.start);
                        }
                    }
                }
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    if arguments.len() == 3 && arguments[1] == "unroll" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            Ok(source) => {
                let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
                let program = cairn_rs::parser::parse(&normalized, &arguments[2])
                    .and_then(|parsed| {
                        let mut analysed = parsed;
                        cairn_rs::sema::analyze(&mut analysed, &arguments[2])?;
                        let mut result = cairn_rs::optimize::lower_for_loops(&analysed);
                        cairn_rs::optimize::apply_copy_propagation(&mut result);
                        cairn_rs::optimize::apply_algebraic_simplification(&mut result);
                        cairn_rs::optimize::apply_constant_loop_unrolling(&mut result);
                        Ok(result)
                    });
                match program {
                    Ok(program) => println!("{}", cairn_rs::dump::program_dump(&program)),
                    Err(error) => {
                        for diagnostic in &error.diagnostics {
                            println!("ERROR[{},{},{},{}]", diagnostic.code,
                                     diagnostic.message, diagnostic.fix,
                                     diagnostic.span.start);
                        }
                    }
                }
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    if arguments.len() == 3 && arguments[1] == "count" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            Ok(source) => {
                let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
                let program = cairn_rs::parser::parse(&normalized, &arguments[2])
                    .and_then(|parsed| {
                        let mut analysed = parsed;
                        cairn_rs::sema::analyze(&mut analysed, &arguments[2])?;
                        let mut result = cairn_rs::optimize::lower_for_loops(&analysed);
                        cairn_rs::optimize::apply_copy_propagation(&mut result);
                        cairn_rs::optimize::apply_algebraic_simplification(&mut result);
                        cairn_rs::optimize::apply_countdown_latches(&mut result);
                        Ok(result)
                    });
                match program {
                    Ok(program) => println!("{}", cairn_rs::dump::program_dump(&program)),
                    Err(error) => {
                        for diagnostic in &error.diagnostics {
                            println!("ERROR[{},{},{},{}]", diagnostic.code,
                                     diagnostic.message, diagnostic.fix,
                                     diagnostic.span.start);
                        }
                    }
                }
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
        return;
    }
    if arguments.len() == 3 && arguments[1] == "alg" {
        let path = Path::new(&arguments[2]);
        match std::fs::read_to_string(path) {
            Ok(source) => {
                let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
                let program = cairn_rs::parser::parse(&normalized, &arguments[2])
                    .and_then(|parsed| {
                        let mut analysed = parsed;
                        cairn_rs::sema::analyze(&mut analysed, &arguments[2])?;
                        let lowered = cairn_rs::optimize::lower_for_loops(&analysed);
                        let mut result = lowered;
                        cairn_rs::optimize::apply_copy_propagation(&mut result);
                        cairn_rs::optimize::apply_algebraic_simplification(&mut result);
                        Ok(result)
                    });
                match program {
                    Ok(program) => println!("{}", cairn_rs::dump::program_dump(&program)),
                    Err(error) => {
                        for diagnostic in &error.diagnostics {
                            println!("ERROR[{},{},{},{}]", diagnostic.code,
                                     diagnostic.message, diagnostic.fix,
                                     diagnostic.span.start);
                        }
                    }
                }
            }
            Err(error) => {
                eprintln!("cannot read {}: {}", arguments[2], error);
                std::process::exit(2);
            }
        }
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
