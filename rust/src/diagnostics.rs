//! Diagnostics: every failure carries a stable code, a span and a fix.
//!
//! An agent reads the code and the span, not the prose, so the machine-readable form is part of
//! the contract: `{"code", "severity", "path", "line", "column", "end_line", "end_column",
//! "message", "fix"}`, with exactly that key order.

use std::fmt;

/// A half-open source range, in byte offsets and in 1-based line/column pairs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Span {
    pub start: usize,
    pub end: usize,
    pub line: usize,
    pub column: usize,
    pub end_line: usize,
    pub end_column: usize,
}

impl Span {
    /// A span between two positions, mirroring the Python constructor's argument order.
    pub fn new(start: usize, end: usize, line: usize, column: usize,
               end_line: usize, end_column: usize) -> Self {
        Self { start, end, line, column, end_line, end_column }
    }

    /// A span covering one position, used for a missing token.
    pub fn point(offset: usize, line: usize, column: usize) -> Self {
        Self { start: offset, end: offset, line, column, end_line: line, end_column: column }
    }

    /// The smallest span covering both inputs.
    pub fn join(first: Self, last: Self) -> Self {
        Self {
            start: first.start,
            end: last.end,
            line: first.line,
            column: first.column,
            end_line: last.end_line,
            end_column: last.end_column,
        }
    }
}

/// One error or warning: what is wrong, where, and how to fix it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Diagnostic {
    pub code: String,
    pub message: String,
    pub fix: String,
    pub span: Span,
    pub path: String,
    pub severity: String,
}

impl Diagnostic {
    pub fn new(code: &str, message: &str, fix: &str, span: Span, path: &str) -> Self {
        Self {
            code: code.to_string(),
            message: message.to_string(),
            fix: fix.to_string(),
            span,
            path: path.to_string(),
            severity: "error".to_string(),
        }
    }

    /// The JSON form an agent reads; keys and order are the contract.
    pub fn to_json(&self) -> String {
        format!(
            "{{\"code\":{},\"severity\":{},\"path\":{},\"line\":{},\"column\":{},\
             \"end_line\":{},\"end_column\":{},\"message\":{},\"fix\":{}}}",
            json_string(&self.code),
            json_string(&self.severity),
            json_string(&self.path),
            self.span.line,
            self.span.column,
            self.span.end_line,
            self.span.end_column,
            json_string(&self.message),
            json_string(&self.fix),
        )
    }

    /// The human form: a positioned line, the offending text, and a fix line.
    pub fn render(&self, source: Option<&str>) -> String {
        let mut lines = vec![format!(
            "{}:{}:{}: {}[{}]: {}",
            self.path, self.span.line, self.span.column, self.severity, self.code, self.message
        )];
        let excerpt = source.and_then(|text| excerpt(text, self.span));
        if let Some(excerpt) = excerpt {
            lines.push(format!("  {}", excerpt));
            lines.push(format!(
                "  {}{}",
                " ".repeat(self.span.column.saturating_sub(1)),
                "^".repeat(self.span.end_column.saturating_sub(self.span.column).max(1))
            ));
        }
        lines.push(format!("  fix: {}", self.fix));
        lines.join("\n")
    }
}

/// The source line the span starts on, without its line ending.
fn excerpt(source: &str, span: Span) -> Option<&str> {
    let line = source.lines().nth(span.line.checked_sub(1)?)?;
    Some(line)
}

/// `json.dumps`-compatible string quoting with `ensure_ascii=True`, matching the Python compiler.
fn json_string(value: &str) -> String {
    let mut out = String::from("\"");
    for ch in value.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 || (c as u32) > 0x7f => {
                let code = c as u32;
                if code <= 0xffff {
                    out.push_str(&format!("\\u{:04x}", code));
                } else {
                    let adjusted = code - 0x10000;
                    let high = 0xd800 + (adjusted >> 10);
                    let low = 0xdc00 + (adjusted & 0x3ff);
                    out.push_str(&format!("\\u{:04x}\\u{:04x}", high, low));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// Raised by a stage that cannot continue; carries every diagnostic found so far.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiagnosticError {
    pub diagnostics: Vec<Diagnostic>,
}

impl DiagnosticError {
    pub fn message(&self) -> &str {
        self.diagnostics
            .first()
            .map(|diagnostic| diagnostic.message.as_str())
            .unwrap_or("compilation failed")
    }

    /// All diagnostics, one after another.
    pub fn render(&self, source: Option<&str>) -> String {
        self.diagnostics
            .iter()
            .map(|diagnostic| diagnostic.render(source))
            .collect::<Vec<_>>()
            .join("\n")
    }
}

impl fmt::Display for DiagnosticError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.message())
    }
}

impl std::error::Error for DiagnosticError {}

/// One diagnostic as an error, for the stage that cannot continue past it.
pub fn fail(code: &str, message: &str, fix: &str, span: Span, path: &str) -> DiagnosticError {
    DiagnosticError { diagnostics: vec![Diagnostic::new(code, message, fix, span, path)] }
}
