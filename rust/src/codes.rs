//! The diagnostic code registry: one record per code the compiler can emit.
//!
//! Codes are a contract (ADR-003), so they live in one place instead of only inside the raise site.
//! Families: E1xxx lexer, E2xxx parser, E3xxx semantics, Vxxxx verification of a built artifact.

use std::collections::BTreeSet;

/// One diagnostic code: its stage family and a one-line statement of what it means.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Code {
    pub code: &'static str,
    pub stage: &'static str,
    pub summary: &'static str,
}

impl Code {
    pub const fn new(code: &'static str, stage: &'static str, summary: &'static str) -> Self {
        Self { code, stage, summary }
    }
}

/// Every registered code, in registry order.
pub const REGISTRY: &[Code] = &[
    Code::new("E1001", "lexer", "Unexpected character"),
    Code::new("E1004", "lexer", "Unterminated string or unknown escape"),
    Code::new("E1003", "lexer", "Integer literal does not fit in i64"),
    Code::new("E2001", "parser", "Grammar expected a specific token"),
    Code::new("E2002", "parser", "Unknown type"),
    Code::new("E2003", "parser", "Assignment target is not a name"),
    Code::new("E2004", "parser", "Expression statement with no effect"),
    Code::new("E2005", "parser", "For range missing '..' or '..='"),
    Code::new("E3001", "semantics", "Duplicate function"),
    Code::new("E3003", "semantics", "Unknown function"),
    Code::new("E3004", "semantics", "Wrong argument count"),
    Code::new("E3005", "semantics", "Assignment to an immutable binding"),
    Code::new("E3006", "semantics", "Duplicate binding or parameter"),
    Code::new("E3007", "semantics", "Undeclared name"),
    Code::new("E3008", "semantics", "break or continue outside a loop"),
    Code::new("E3009", "semantics", "Function can finish without returning"),
    Code::new("E3010", "semantics", "More than eight parameters"),
    Code::new("E3011", "semantics", "Missing or ill-formed main"),
    Code::new("E3012", "semantics", "Builtin redefined or used as a value"),
    Code::new("E3013", "semantics", "exit used inside an expression instead of as a statement"),
    Code::new("E3014", "semantics", "Array binding initializer is not a matching array literal"),
    Code::new("E3015", "semantics", "Indexed value is not an array"),
    Code::new("E3016", "semantics", "Array name used as a value (must be indexed)"),
    Code::new("E3017", "semantics", "Static array index is out of the declared range"),
    Code::new("V0001", "verify", "DOS or PE signature is missing"),
    Code::new("V0002", "verify", "COFF header does not describe an amd64 image"),
    Code::new("V0003", "verify", "Optional header is not PE32+ console"),
    Code::new("V0004", "verify", "Section table is not .text, .rdata, .data"),
    Code::new("V0005", "verify", "Section alignment or raw pointer is wrong"),
    Code::new("V0006", "verify", "Entry point is outside .text"),
    Code::new("V0007", "verify", "Import directory is missing or outside .rdata"),
    Code::new("V0008", "verify", "Image size does not match the section layout"),
    Code::new("V0009", "verify", "A section has the wrong permissions"),
    Code::new("V0010", "verify", "The file is shorter than its section table claims"),
];

/// The registered code with this name, if any.
pub fn by_code(code: &str) -> Option<&'static Code> {
    REGISTRY.iter().find(|entry| entry.code == code)
}

/// Registered codes grouped by stage, in registry order, for the capabilities manifest.
pub fn codes_by_stage() -> Vec<(&'static str, Vec<&'static str>)> {
    let mut grouped: Vec<(&'static str, Vec<&'static str>)> = Vec::new();
    for entry in REGISTRY {
        if let Some((_, codes)) = grouped.iter_mut().find(|(stage, _)| *stage == entry.stage) {
            codes.push(entry.code);
        } else {
            grouped.push((entry.stage, vec![entry.code]));
        }
    }
    grouped
}

/// Every `[EV]dddd` code literal in quoted source text, which is what the compiler emits.
///
/// This mirrors `cairn.codes.codes_in_source` without a regex dependency: only quoted spans are
/// inspected, and a code is an `E` or `V` followed by exactly four ASCII digits.
pub fn codes_in_source(texts: &[&str]) -> BTreeSet<String> {
    let mut found = BTreeSet::new();
    for text in texts {
        let bytes = text.as_bytes();
        let mut index = 0;
        while index < bytes.len() {
            let quote = bytes[index];
            if quote != b'\'' && quote != b'"' {
                index += 1;
                continue;
            }
            index += 1;
            let start = index;
            while index < bytes.len() && bytes[index] != quote {
                index += 1;
            }
            let inner = &text[start..index];
            let chars: Vec<char> = inner.chars().collect();
            if chars.len() == 5
                && (chars[0] == 'E' || chars[0] == 'V')
                && chars[1..].iter().all(|c| c.is_ascii_digit())
            {
                found.insert(inner.to_string());
            }
            index += 1;
        }
    }
    found
}
