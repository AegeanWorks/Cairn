//! Lexer: source text to a flat token list.
//!
//! The token set is deliberately small so an agent can hold all of it at once: one integer type,
//! one keyword per statement form, and only the operators that lower to one instruction. The
//! scanner works on Unicode scalar values so offsets and columns match the Python reference
//! exactly, including its immediate refusal of the first non-ASCII character.

use crate::diagnostics::{fail, DiagnosticError, Span};

/// The fixed keywords of Cairn 0.1.
pub const KEYWORDS: &[&str] = &[
    "fn", "let", "var", "if", "else", "while", "for", "in", "return", "break", "continue",
    "true", "false",
];

/// Multi-character symbols, longest first so `..=` wins over `..`.
pub const MULTI_CHAR_SYMBOLS: &[&str] = &[
    "..=", "..", "->", "==", "!=", "<=", ">=", "&&", "||", "<<", ">>",
];

/// Single-character punctuation and operators.
pub const SINGLE_CHAR_SYMBOLS: &str = "(){}:,;=+-*/%<>!&|^~[]";

/// The largest literal the scanner admits. `2**63` is allowed so unary minus can fold i64::MIN.
pub const INT64_MAX: i128 = 9_223_372_036_854_775_807;

/// One lexical unit: its kind, its text, its value when it is an integer, and its span.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Token {
    pub kind: &'static str,
    pub text: String,
    pub span: Span,
    pub value: i128,
}

impl Token {
    /// Whether this is the given punctuation or operator.
    pub fn is_symbol(&self, text: &str) -> bool {
        self.kind == "symbol" && self.text == text
    }

    /// Whether this is the given keyword.
    pub fn is_keyword(&self, text: &str) -> bool {
        self.kind == "keyword" && self.text == text
    }
}

/// A single-pass scanner over source text.
pub struct Lexer {
    source: Vec<char>,
    path: String,
    offset: usize,
    line: usize,
    column: usize,
}

impl Lexer {
    pub fn new(source: &str, path: &str) -> Self {
        Self {
            source: source.chars().collect(),
            path: path.to_string(),
            offset: 0,
            line: 1,
            column: 1,
        }
    }

    /// Every token in order, ending with one `eof` token.
    pub fn tokenize(mut self) -> Result<Vec<Token>, DiagnosticError> {
        let mut tokens = Vec::new();
        loop {
            self.skip_blank();
            if self.offset >= self.source.len() {
                tokens.push(Token {
                    kind: "eof",
                    text: String::new(),
                    span: Span::point(self.offset, self.line, self.column),
                    value: 0,
                });
                return Ok(tokens);
            }
            tokens.push(self.next_token()?);
        }
    }

    /// Consume whitespace and `//` comments.
    fn skip_blank(&mut self) {
        while self.offset < self.source.len() {
            let ch = self.source[self.offset];
            if ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n' {
                self.advance(ch);
            } else if ch == '/' && self.starts_with("//") {
                while self.offset < self.source.len() && self.source[self.offset] != '\n' {
                    let current = self.source[self.offset];
                    self.advance(current);
                }
            } else {
                return;
            }
        }
    }

    /// Move one character forward, keeping the line and column accurate.
    fn advance(&mut self, ch: char) {
        self.offset += 1;
        if ch == '\n' {
            self.line += 1;
            self.column = 1;
        } else {
            self.column += 1;
        }
    }

    fn starts_with(&self, text: &str) -> bool {
        let wanted: Vec<char> = text.chars().collect();
        self.source.get(self.offset..self.offset + wanted.len()) == Some(wanted.as_slice())
    }

    fn text_from(&self, start: usize) -> String {
        self.source[start..self.offset].iter().collect()
    }

    /// Scan one token that is not whitespace or a comment.
    fn next_token(&mut self) -> Result<Token, DiagnosticError> {
        let start = Span::point(self.offset, self.line, self.column);
        let ch = self.source[self.offset];
        if ch.is_ascii_digit() {
            return self.number(start);
        }
        if ch.is_ascii_alphabetic() || ch == '_' {
            return Ok(self.identifier(start));
        }
        if ch == '"' {
            return self.string(start);
        }
        for symbol in MULTI_CHAR_SYMBOLS {
            if self.starts_with(symbol) {
                let length = symbol.chars().count();
                for _ in 0..length {
                    let current = self.source[self.offset];
                    self.advance(current);
                }
                return Ok(Token {
                    kind: "symbol",
                    text: symbol.to_string(),
                    span: self.span_from(start),
                    value: 0,
                });
            }
        }
        if SINGLE_CHAR_SYMBOLS.contains(ch) {
            self.advance(ch);
            return Ok(Token {
                kind: "symbol",
                text: ch.to_string(),
                span: self.span_from(start),
                value: 0,
            });
        }
        Err(fail(
            "E1001",
            &format!("unexpected character '{}'", ch),
            "Cairn source is ASCII: use a letter, a digit, or one of (){}:,;=+-*/%<>!&|^~.",
            Span::new(start.start, start.start + 1, start.line, start.column,
                      start.line, start.column + 1),
            &self.path,
        ))
    }

    /// A quoted literal with `\\\\`, `\\"`, `\\n` and `\\t` escapes; its text is decoded.
    fn string(&mut self, start: Span) -> Result<Token, DiagnosticError> {
        let quote = '"';
        self.advance(quote);
        let mut pieces = String::new();
        loop {
            if self.offset >= self.source.len() || self.source[self.offset] == '\n' {
                return Err(fail(
                    "E1004",
                    "unterminated string literal",
                    "Close the string with the matching double quote on the same line.",
                    self.span_from(start),
                    &self.path,
                ));
            }
            let ch = self.source[self.offset];
            if ch == '"' {
                self.advance(ch);
                return Ok(Token {
                    kind: "str",
                    text: pieces,
                    span: self.span_from(start),
                    value: 0,
                });
            }
            if ch == '\\' {
                self.advance(ch);
                if self.offset >= self.source.len() {
                    return Err(fail(
                        "E1004",
                        "unterminated string literal",
                        "Close the string with the matching double quote on the same line.",
                        self.span_from(start),
                        &self.path,
                    ));
                }
                let escaped = self.source[self.offset];
                let decoded = match escaped {
                    '\\' => Some('\\'),
                    '"' => Some('"'),
                    'n' => Some('\n'),
                    't' => Some('\t'),
                    _ => None,
                };
                let decoded = match decoded {
                    Some(value) => value,
                    None => {
                        return Err(fail(
                            "E1004",
                            &format!("unknown escape sequence '\\{}'", escaped),
                            "Use one of \\\\, \\\", \\n or \\t inside a string literal.",
                            self.span_from(start),
                            &self.path,
                        ));
                    }
                };
                self.advance(escaped);
                pieces.push(decoded);
                continue;
            }
            pieces.push(ch);
            self.advance(ch);
        }
    }

    /// The span between `start` and the current position.
    fn span_from(&self, start: Span) -> Span {
        Span::new(start.start, self.offset, start.line, start.column, self.line, self.column)
    }

    /// A decimal integer literal that must fit in a signed 64-bit integer.
    fn number(&mut self, start: Span) -> Result<Token, DiagnosticError> {
        while self.offset < self.source.len() && self.source[self.offset].is_ascii_digit() {
            let current = self.source[self.offset];
            self.advance(current);
        }
        let text = self.text_from(start.start);
        let significant = text.trim_start_matches('0');
        let significant = if significant.is_empty() { "0" } else { significant };
        let too_big = significant.len() > 19
            || text.parse::<i128>().map_or(true, |value| value > INT64_MAX + 1);
        if too_big {
            return Err(fail(
                "E1003",
                &format!("integer literal {} does not fit in i64", text),
                "Write a value between -9223372036854775808 and 9223372036854775807.",
                self.span_from(start),
                &self.path,
            ));
        }
        let value = text.parse::<i128>().unwrap_or(0);
        Ok(Token {
            kind: "int",
            text,
            span: self.span_from(start),
            value,
        })
    }

    /// A name, or one of the fixed keywords.
    fn identifier(&mut self, start: Span) -> Token {
        while self.offset < self.source.len() {
            let ch = self.source[self.offset];
            if !((ch.is_ascii_alphanumeric() || ch == '_')) {
                break;
            }
            self.advance(ch);
        }
        let text = self.text_from(start.start);
        let kind = if KEYWORDS.contains(&text.as_str()) { "keyword" } else { "ident" };
        Token { kind, text, span: self.span_from(start), value: 0 }
    }
}

/// Tokenize one source text.
pub fn tokenize(source: &str, path: &str) -> Result<Vec<Token>, DiagnosticError> {
    Lexer::new(source, path).tokenize()
}
