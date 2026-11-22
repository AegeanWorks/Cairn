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

/// The `verify --json` envelope for a source compiled in memory.
pub fn verify_json(source: &str, path: &str) -> String {
    let build = match crate::builder::compile_source(source, path, true) {
        Ok(build) => build,
        Err(error) => return compile_error(path, &error.diagnostics),
    };
    let problems = crate::verify::verify_image(&build.image);
    let problems_json = if problems.is_empty() {
        "[]".to_string()
    } else {
        let items = problems
            .iter()
            .map(|problem| {
                format!(
                    "    {{\n      \"code\": {},\n      \"detail\": {}\n    }}",
                    json_string(problem.code), json_string(&problem.detail)
                )
            })
            .collect::<Vec<_>>()
            .join(",\n");
        format!("[\n{}\n  ]", items)
    };
    format!(
        "{{\n  \"ok\": true,\n  \"path\": {},\n  \"kind\": \"source\",\n  \"image_bytes\": {},\n  \"problems\": {},\n  \"diagnostics\": []\n}}\n",
        json_string(path), build.image.len(), problems_json
    )
}

/// The `listing --json` envelope: text size, sorted labels and the rendered listing.
pub fn listing_json(source: &str, path: &str) -> String {
    let build = match crate::builder::compile_source(source, path, true) {
        Ok(build) => build,
        Err(error) => return compile_error(path, &error.diagnostics),
    };
    let listing = crate::listing::render_listing(&build.artifact).unwrap_or_default();
    let mut labels: Vec<&str> = build.artifact.text_labels.keys().map(String::as_str).collect();
    labels.sort_unstable();
    let labels_json = labels
        .iter()
        .map(|label| format!("    {}", json_string(label)))
        .collect::<Vec<_>>()
        .join(",\n");
    format!(
        "{{\n  \"ok\": true,\n  \"path\": {},\n  \"text_bytes\": {},\n  \"labels\": [\n{}\n  ],\n  \"listing\": {},\n  \"diagnostics\": []\n}}\n",
        json_string(path),
        build.artifact.text.len(),
        labels_json,
        json_string(&listing),
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
