//! The machine-readable description of the compiler surface.
//!
//! A small JSON value tree is built functionally and rendered at the edge. The rendering matches
//! Python's `json.dumps(..., indent=2)` so the canonical manifest is byte-identical.

use crate::codes::REGISTRY;
use crate::diagnostics::json_string;
use crate::image::IMPORTS;
use crate::optimize::{INT64_MAX, INT64_MIN};
use crate::parser::{PRECEDENCE, TYPE_NAMES, UNARY_OPERATORS};
use crate::runtime;
use crate::sema::{BUILTINS, MAX_PARAMETERS};
use crate::values::VALUES;
use crate::version::{LANGUAGE_VERSION, VERSION};

#[derive(Debug, Clone)]
enum Json {
    String(String),
    Number(String),
    Object(Vec<(String, Json)>),
    Array(Vec<Json>),
}

impl Json {
    fn string(value: &str) -> Self {
        Self::String(value.to_string())
    }

    fn number(value: impl std::fmt::Display) -> Self {
        Self::Number(value.to_string())
    }

    /// Render with Python's `json.dumps(indent=2)` layout and ASCII escaping.
    fn render(&self, level: usize, out: &mut String) {
        match self {
            Self::String(value) => out.push_str(&json_string(value)),
            Self::Number(value) => out.push_str(value),
            Self::Array(items) => {
                if items.is_empty() {
                    out.push_str("[]");
                    return;
                }
                out.push_str("[\n");
                for (index, item) in items.iter().enumerate() {
                    out.push_str(&" ".repeat((level + 1) * 2));
                    item.render(level + 1, out);
                    if index + 1 < items.len() {
                        out.push(',');
                    }
                    out.push('\n');
                }
                out.push_str(&" ".repeat(level * 2));
                out.push(']');
            }
            Self::Object(fields) => {
                if fields.is_empty() {
                    out.push_str("{}");
                    return;
                }
                out.push_str("{\n");
                for (index, (key, value)) in fields.iter().enumerate() {
                    out.push_str(&" ".repeat((level + 1) * 2));
                    out.push_str(&json_string(key));
                    out.push_str(": ");
                    value.render(level + 1, out);
                    if index + 1 < fields.len() {
                        out.push(',');
                    }
                    out.push('\n');
                }
                out.push_str(&" ".repeat(level * 2));
                out.push('}');
            }
        }
    }

    fn to_json(&self) -> String {
        let mut out = String::new();
        self.render(0, &mut out);
        out
    }
}

fn object(fields: Vec<(&str, Json)>) -> Json {
    Json::Object(fields.into_iter().map(|(key, value)| (key.to_string(), value)).collect())
}

/// The whole surface as one JSON-serialisable value.
fn capabilities() -> Json {
    let mut tiers: Vec<(u8, Vec<&str>)> = Vec::new();
    for (symbol, level) in PRECEDENCE {
        if let Some(entry) = tiers.iter_mut().find(|(found, _)| found == level) {
            entry.1.push(symbol);
        } else {
            tiers.push((*level, vec![symbol]));
        }
    }
    tiers.sort_by_key(|(level, _)| *level);
    let operators = Json::Array(
        tiers
            .into_iter()
            .map(|(tier, mut symbols)| {
                symbols.sort_unstable();
                object(vec![
                    ("tier", Json::number(tier)),
                    ("operators", Json::Array(symbols.into_iter().map(Json::string).collect())),
                ])
            })
            .collect(),
    );
    let mut builtins: Vec<(&str, usize)> = BUILTINS.iter().map(|(name, arity)| (*name, *arity)).collect();
    builtins.sort_by_key(|(name, _)| *name);
    let builtin_json = Json::Object(
        builtins
            .into_iter()
            .map(|(name, arity)| {
                (name.to_string(), object(vec![
                    ("parameters", Json::number(arity)),
                    ("returns", Json::string("its argument")),
                ]))
            })
            .collect(),
    );
    let imports = Json::Object(
        IMPORTS.iter().map(|name| (name.to_string(), Json::string("kernel32.dll"))).collect(),
    );
    object(vec![
        ("name", Json::string("cairn")),
        ("version", Json::string(VERSION)),
        ("language_version", Json::string(LANGUAGE_VERSION)),
        ("core_values", Json::Array(VALUES.iter().map(|value| object(vec![
            ("key", Json::string(value.key)),
            ("name", Json::string(value.name)),
            ("claim", Json::string(value.claim)),
            ("evidence", Json::string(value.evidence)),
        ])).collect())),
        ("target", object(vec![
            ("os", Json::string("windows")),
            ("architecture", Json::string("x86-64")),
            ("format", Json::string("PE32+ console")),
            ("sections", object(vec![
                (".text", Json::string("executable code")),
                (".rdata", Json::string("import names and string constants")),
                (".data", Json::string("the import address table")),
            ])),
            ("imports", imports),
        ])),
        ("types", Json::Array(TYPE_NAMES.iter().map(|name| Json::string(name)).collect())),
        ("literals", object(vec![
            ("integer", Json::string("decimal, signed 64-bit")),
            ("boolean", Json::string("true is 1, false is 0")),
        ])),
        ("statements", Json::Array([
            "let", "var", "assignment", "if/else", "while", "for", "return",
            "break", "continue", "block",
        ].into_iter().map(Json::string).collect())),
        ("operators_by_precedence", operators),
        ("unary_operators", Json::Array(UNARY_OPERATORS.iter().map(|op| Json::string(op)).collect())),
        ("builtins", builtin_json),
        ("comments", Json::string("// to the end of the line")),
        ("limits", object(vec![
            ("parameters_per_function", Json::number(MAX_PARAMETERS)),
            ("integer_min", Json::number(INT64_MIN)),
            ("integer_max", Json::number(INT64_MAX)),
            ("elements_per_array", Json::number(65536)),
            ("identifiers", Json::string("ASCII letters, digits and underscore; no shadowing")),
        ])),
        ("arrays", object(vec![
            ("declaration", Json::string("[N]i64")),
            ("element_type", Json::string("i64")),
            ("initializers", Json::Array([
                Json::string("[v; N]"), Json::string("[e0, e1, ...]"),
            ].to_vec())),
            ("element_count", Json::string("1 .. 65536 (E3014)")),
            ("index", Json::string("a[i]; literal indices bounds-checked (E3017), dynamic not")),
        ])),
        ("semantics", object(vec![
            ("overflow", Json::string("wraps")),
            ("division", Json::string("truncates toward zero")),
            ("division_by_zero", Json::string("trap, exit code 3")),
            ("right_shift", Json::string("arithmetic, shift count masked to 63 in folding and hardware")),
            ("comparison_result", Json::string("0 or 1")),
            ("evaluation", Json::string("left to right")),
            ("short_circuit", Json::Array([Json::string("&&"), Json::string("||")].to_vec())),
        ])),
        ("commands", object(vec![
            ("build", Json::string("compile a .crn file to a Windows .exe")),
            ("check", Json::string("parse and analyse without writing an executable")),
            ("run", Json::string("build to a temporary file, run it, and forward its exit code")),
            ("verify", Json::string("check a built .exe, or a .crn compiled in memory, against the artifact invariants")),
            ("listing", Json::string("print the emitted machine code by label, without writing a file")),
            ("capabilities", Json::string("print this object")),
        ])),
        ("flags", object(vec![
            ("--json", Json::string("machine-readable output")),
            ("--no-optimize", Json::string("skip constant folding")),
            ("--timeout", Json::string("run: seconds before the program is stopped")),
        ])),
        ("exit_codes", object(vec![
            ("0", Json::string("success")),
            ("1", Json::string("the source did not compile, or the program could not start")),
            ("2", Json::string("bad command line, or the input file could not be read")),
        ])),
        ("diagnostic_codes", Json::Array(REGISTRY.iter().map(|entry| object(vec![
            ("code", Json::string(entry.code)),
            ("stage", Json::string(entry.stage)),
            ("summary", Json::string(entry.summary)),
        ])).collect())),
        ("guarantees", Json::Array([
            "no undefined behaviour: integer overflow wraps, division by zero traps",
            "deterministic output: the same source produces byte-identical executables",
            "no external toolchain: no assembler, linker, C compiler or runtime library",
            "no third-party dependency: the compiler imports only the Rust standard library",
            "defined reads: every frame slot is zeroed on entry",
            "one repair pass: semantic analysis reports every finding, not just the first",
        ].into_iter().map(Json::string).collect())),
    ])
}

/// The canonical JSON manifest.
pub fn capabilities_json() -> String {
    capabilities().to_json() + "\n"
}

#[allow(dead_code)]
fn _runtime_labels_are_kept_in_scope() -> &'static str {
    runtime::PRINT_LABEL
}
