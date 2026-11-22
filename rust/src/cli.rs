//! The command-line core: pure functions that produce canonical envelopes.
//!
//! All file reading and printing stays in `main.rs`; this module only maps a source string to a
//! JSON report or a structured command result.

use crate::diagnostics::json_string;
use crate::generator::CodeGenerator;
use crate::optimize::Optimizer;
use crate::parser::parse;
use crate::pe::link;
use crate::pipeline::Pipeline;
use crate::sema::analyze;

/// The `check --json` envelope for one source string and its display path.
pub fn check_json(source: &str, path: &str) -> String {
    let mut program = match parse(source, path) {
        Ok(program) => program,
        Err(error) => return compile_error(path, &error.diagnostics),
    };
    if let Err(error) = analyze(&mut program, path) {
        return compile_error(path, &error.diagnostics);
    }
    let functions = program.functions.len();
    let mut optimiser = Optimizer::new();
    let mut folded_program = program.clone();
    folded_program = optimiser.fold_program(&folded_program);
    crate::optimize::apply_pure_inlining(&mut folded_program);
    let _ = optimiser.fold_program(&folded_program);
    let folded = optimiser.folded;
    let _ = Pipeline::new().run(&mut program, path);
    let image_bytes = match link(&CodeGenerator::new().generate(&program)) {
        Ok(image) => image.len(),
        Err(_) => 0,
    };
    format!(
        "{{\n  \"ok\": true,\n  \"path\": {},\n  \"functions\": {},\n  \"folded\": {},\n  \"image_bytes\": {},\n  \"diagnostics\": []\n}}\n",
        json_string(path), functions, folded, image_bytes
    )
}

fn compile_error(path: &str, diagnostics: &[crate::diagnostics::Diagnostic]) -> String {
    let items = diagnostics
        .iter()
        .map(|item| {
            format!(
                "    {{\n      \"code\": {},\n      \"severity\": {},\n      \"path\": {},\n      \"line\": {},\n      \"column\": {},\n      \"end_line\": {},\n      \"end_column\": {},\n      \"message\": {},\n      \"fix\": {}\n    }}",
                json_string(&item.code),
                json_string(&item.severity),
                json_string(&item.path),
                item.span.line,
                item.span.column,
                item.span.end_line,
                item.span.end_column,
                json_string(&item.message),
                json_string(&item.fix),
            )
        })
        .collect::<Vec<_>>()
        .join(",\n");
    format!(
        "{{\n  \"ok\": false,\n  \"path\": {},\n  \"error\": \"compile-error\",\n  \"diagnostics\": [\n{}\n  ]\n}}\n",
        json_string(path), items
    )
}
