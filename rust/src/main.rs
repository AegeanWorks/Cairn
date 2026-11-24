//! Entry point for the Rust compiler: the user-facing commands and the canonical
//! JSON/dump commands the parity gate compares against the frozen manifest.

use std::io::Write;
use std::path::{Path, PathBuf};

/// Read a source file and normalise it the way the reference compiler did.
fn read_source(path: &str) -> String {
    match std::fs::read_to_string(path) {
        Ok(source) => source.trim_start_matches('\u{feff}')
            .replace("\r\n", "\n").replace('\r', "\n"),
        Err(error) => {
            eprintln!("cannot read {}: {}", path, error);
            std::process::exit(2);
        }
    }
}

/// Compile or print the stable diagnostic lines and exit with the compile-error code.
fn compile_or_exit(source: &str, path: &str, optimise: bool) -> cairn_rs::builder::Build {
    match cairn_rs::builder::compile_source(source, path, optimise) {
        Ok(build) => build,
        Err(error) => {
            for diagnostic in &error.diagnostics {
                println!("ERROR[{},{},{},{}]", diagnostic.code,
                         diagnostic.message, diagnostic.fix, diagnostic.span.start);
            }
            std::process::exit(1);
        }
    }
}

/// Write an image to a process-unique temporary executable.
fn write_temp_exe(image: &[u8]) -> PathBuf {
    let folder = std::env::temp_dir().join(format!(
        "cairn-rs-run-{}", std::process::id()));
    let _ = std::fs::create_dir_all(&folder);
    let exe = folder.join("program.exe");
    if let Err(error) = cairn_rs::builder::write_executable(&exe, image) {
        eprintln!("cannot write {}: {}", exe.display(), error);
        std::process::exit(2);
    }
    exe
}

/// Run a built program with captured streams and a wall-clock timeout.
fn execute(exe: &Path, timeout_secs: u64) -> (i64, bool, Vec<u8>, Vec<u8>) {
    let child = std::process::Command::new(exe)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn();
    match child {
        Ok(mut child) => {
            let deadline = std::time::Instant::now()
                + std::time::Duration::from_secs(timeout_secs);
            let mut done = None;
            while std::time::Instant::now() < deadline {
                match child.try_wait() {
                    Ok(Some(status)) => { done = Some(status); break; }
                    Ok(None) => std::thread::sleep(std::time::Duration::from_millis(10)),
                    Err(_) => break,
                }
            }
            match done {
                Some(status) => {
                    let output = child.wait_with_output().unwrap();
                    (status.code().unwrap_or(-1) as u32 as i64,
                     false, output.stdout, output.stderr)
                }
                None => {
                    let _ = child.kill();
                    let output = child.wait_with_output().unwrap();
                    (-1, true, output.stdout, output.stderr)
                }
            }
        }
        Err(_) => (-1, false, Vec::new(), Vec::new()),
    }
}

/// The run envelope shared by `run --json` and the parity `runjson` command.
fn run_report(display: &str, returncode: i64, timed_out: bool,
              out: &[u8], err: &[u8]) -> String {
    let string = |bytes: &[u8]| String::from_utf8_lossy(bytes).replace("\r\n", "\n");
    format!("{{\n  \"ok\": {},\n  \"path\": {},\n  \"returncode\": {},\n  \"timed_out\": {},\n  \"stdout\": {},\n  \"stderr\": {},\n  \"diagnostics\": []\n}}",
            if timed_out || returncode != 0 { "false" } else { "true" },
            cairn_rs::diagnostics::json_string(display), returncode, timed_out,
            cairn_rs::diagnostics::json_string(&string(out)),
            cairn_rs::diagnostics::json_string(&string(err)))
}

/// Print the verification verdict for one image and exit with its status code.
fn verify_and_report(display: &str, image: &[u8], kind: &str, json: bool) -> ! {
    let problems = cairn_rs::verify::verify_image(image);
    if json {
        let items = problems.iter().map(|problem| format!(
            "    {{\n      \"code\": {},\n      \"detail\": {}\n    }}",
            cairn_rs::diagnostics::json_string(problem.code),
            cairn_rs::diagnostics::json_string(&problem.detail)))
            .collect::<Vec<_>>().join(",\n");
        let problems_json = if problems.is_empty() {
            "[]".to_string()
        } else {
            format!("[\n{}\n  ]", items)
        };
        println!("{{\n  \"ok\": true,\n  \"path\": {},\n  \"kind\": {},\n  \"image_bytes\": {},\n  \"problems\": {},\n  \"diagnostics\": []\n}}",
                 cairn_rs::diagnostics::json_string(display),
                 cairn_rs::diagnostics::json_string(kind),
                 image.len(), problems_json);
    } else if problems.is_empty() {
        println!("verified {} ({} bytes)", display, image.len());
    } else {
        for problem in &problems {
            println!("{} {}", problem.code, problem.detail);
        }
    }
    std::process::exit(if problems.is_empty() { 0 } else { 1 });
}

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() == 3 && arguments[1] == "runjson" {
        let path = &arguments[2];
        let source = read_source(path);
        let display = path.replace('/', "\\");
        let build = compile_or_exit(&source, path, true);
        let exe = write_temp_exe(&build.image);
        let (returncode, timed_out, out, err) = execute(&exe, 30);
        let _ = std::fs::remove_file(&exe);
        println!("{}", run_report(&display, returncode, timed_out, &out, &err));
        return;
    }
    if arguments.len() >= 2 && arguments[1] == "capabilities" {
        print!("{}", cairn_rs::capabilities::capabilities_json());
        return;
    }
    if arguments.len() >= 3 && arguments[1] == "build" {
        let mut source: Option<&String> = None;
        let mut destination: Option<&Path> = None;
        let mut optimise = true;
        let mut json = false;
        let mut index = 2;
        while index < arguments.len() {
            match arguments[index].as_str() {
                "-o" => {
                    index += 1;
                    match arguments.get(index) {
                        Some(value) => destination = Some(Path::new(value)),
                        None => {
                            eprintln!("build: -o needs a destination path");
                            std::process::exit(2);
                        }
                    }
                }
                "--no-optimize" => optimise = false,
                "--json" => json = true,
                _ if source.is_none() => source = Some(&arguments[index]),
                other => {
                    eprintln!("build: unexpected argument '{}'", other);
                    std::process::exit(2);
                }
            }
            index += 1;
        }
        let Some(source) = source else {
            eprintln!("build: missing source file");
            std::process::exit(2);
        };
        let output = cairn_rs::builder::output_path(Path::new(source), destination);
        let text = read_source(source);
        let build = compile_or_exit(&text, source, optimise);
        if let Err(error) = cairn_rs::builder::write_executable(&output, &build.image) {
            eprintln!("cannot write {}: {}", output.display(), error);
            std::process::exit(2);
        }
        if json {
            println!("{{\n  \"ok\": true,\n  \"source\": {},\n  \"output\": {},\n  \"image_bytes\": {},\n  \"folded\": {},\n  \"diagnostics\": []\n}}",
                     cairn_rs::diagnostics::json_string(source),
                     cairn_rs::diagnostics::json_string(&output.display().to_string()),
                     build.image.len(), build.folded);
        }
        return;
    }
    if arguments.len() >= 3 && arguments[1] == "run" {
        let mut source: Option<&String> = None;
        let mut optimise = true;
        let mut json = false;
        let mut timeout = 30u64;
        let mut index = 2;
        while index < arguments.len() {
            match arguments[index].as_str() {
                "--no-optimize" => optimise = false,
                "--json" => json = true,
                "--timeout" => {
                    index += 1;
                    match arguments.get(index).and_then(|value| value.parse().ok()) {
                        Some(seconds) => timeout = seconds,
                        None => {
                            eprintln!("run: --timeout needs a whole number of seconds");
                            std::process::exit(2);
                        }
                    }
                }
                _ if source.is_none() => source = Some(&arguments[index]),
                other => {
                    eprintln!("run: unexpected argument '{}'", other);
                    std::process::exit(2);
                }
            }
            index += 1;
        }
        let Some(source) = source else {
            eprintln!("run: missing source file");
            std::process::exit(2);
        };
        let text = read_source(source);
        let display = source.replace('/', "\\");
        let build = compile_or_exit(&text, source, optimise);
        let exe = write_temp_exe(&build.image);
        let (returncode, timed_out, out, err) = execute(&exe, timeout);
        let _ = std::fs::remove_file(&exe);
        if json {
            println!("{}", run_report(&display, returncode, timed_out, &out, &err));
            return;
        }
        let _ = std::io::stdout().write_all(&out);
        let _ = std::io::stderr().write_all(&err);
        if timed_out {
            eprintln!("run: timed out after {}s", timeout);
            std::process::exit(1);
        }
        std::process::exit(returncode as i32);
    }
    if arguments.len() >= 3 && arguments[1] == "verify" {
        let mut target: Option<&String> = None;
        let mut json = false;
        let mut index = 2;
        while index < arguments.len() {
            match arguments[index].as_str() {
                "--json" => json = true,
                _ if target.is_none() => target = Some(&arguments[index]),
                other => {
                    eprintln!("verify: unexpected argument '{}'", other);
                    std::process::exit(2);
                }
            }
            index += 1;
        }
        let Some(target) = target else {
            eprintln!("verify: missing source or executable");
            std::process::exit(2);
        };
        let display = target.replace('/', "\\");
        if target.to_lowercase().ends_with(".crn") {
            if json {
                let text = read_source(target);
                print!("{}", cairn_rs::cli::verify_json(&text, &display));
                return;
            }
            let text = read_source(target);
            let build = compile_or_exit(&text, target, true);
            verify_and_report(&display, &build.image, "source", false);
        }
        let image = match std::fs::read(target) {
            Ok(image) => image,
            Err(error) => {
                eprintln!("cannot read {}: {}", target, error);
                std::process::exit(2);
            }
        };
        verify_and_report(&display, &image, "image", json);
    }
    if arguments.len() >= 3 && arguments[1] == "listing" {
        let mut target: Option<&String> = None;
        let mut json = false;
        let mut index = 2;
        while index < arguments.len() {
            match arguments[index].as_str() {
                "--json" => json = true,
                _ if target.is_none() => target = Some(&arguments[index]),
                other => {
                    eprintln!("listing: unexpected argument '{}'", other);
                    std::process::exit(2);
                }
            }
            index += 1;
        }
        let Some(target) = target else {
            eprintln!("listing: missing source file");
            std::process::exit(2);
        };
        let display = target.replace('/', "\\");
        let text = read_source(target);
        if json {
            print!("{}", cairn_rs::cli::listing_json(&text, &display));
            return;
        }
        let build = compile_or_exit(&text, target, true);
        print!("{}", cairn_rs::listing::render_listing(&build.artifact).unwrap_or_default());
        return;
    }
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
    if (arguments.len() == 4 || arguments.len() == 5) && arguments[1] == "buildexe" {
        let optimize = arguments.get(4).map(|flag| flag != "--no-optimize").unwrap_or(true);
        let source_path = Path::new(&arguments[2]);
        match std::fs::read_to_string(source_path) {
            Ok(source) => {
                let normalized = source.trim_start_matches('\u{feff}')
                    .replace("\r\n", "\n").replace('\r', "\n");
                match cairn_rs::builder::compile_source(&normalized, &arguments[2], optimize) {
                    Ok(build) => {
                        let destination = Path::new(&arguments[3]);
                        if let Err(error) =
                            cairn_rs::builder::write_executable(destination, &build.image) {
                            eprintln!("cannot write {}: {}", destination.display(), error);
                            std::process::exit(2);
                        }
                    }
                    Err(error) => {
                        for diagnostic in &error.diagnostics {
                            println!("ERROR[{},{},{},{}]", diagnostic.code,
                                     diagnostic.message, diagnostic.fix,
                                     diagnostic.span.start);
                        }
                        std::process::exit(1);
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
    if arguments.len() == 3 && arguments[1] == "buildimage" {
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
    if (arguments.len() == 3 || (arguments.len() == 4 && arguments[3] == "--json")) && arguments[1] == "check" {
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
                        cairn_rs::optimize::apply_shift_loop_unrolling(&mut result);
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
