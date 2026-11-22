//! Parser: recursive descent over the token list.
//!
//! Precedence follows C for the operators that exist, so an agent's existing intuition transfers
//! without a table lookup. The grammar has no expression that is also a declaration: `let` and
//! `var` introduce names, and every other expression statement must be a call.

use crate::diagnostics::{fail, DiagnosticError, Span};
use crate::lexer::{tokenize, Token};
use crate::syntax::{Block, Expr, Function, Parameter, Program, Stmt};

/// The smallest i64, spelled as negated `2**63` in source.
pub const INT64_MIN: i128 = -9_223_372_036_854_775_808;

/// Binding power of each binary operator; higher binds tighter.
pub const PRECEDENCE: &[(&str, u8)] = &[
    ("||", 1), ("&&", 2), ("|", 3), ("^", 4), ("&", 5), ("==", 6), ("!=", 6),
    ("<", 7), ("<=", 7), (">", 7), (">=", 7), ("<<", 8), (">>", 8), ("+", 9),
    ("-", 9), ("*", 10), ("/", 10), ("%", 10),
];

/// The unary operators Cairn accepts.
pub const UNARY_OPERATORS: &[&str] = &["-", "!", "~"];

/// The one type Cairn 0.1 has.
pub const TYPE_NAMES: &[&str] = &["i64"];

fn precedence(op: &str) -> Option<u8> {
    PRECEDENCE.iter().find(|(name, _)| *name == op).map(|(_, value)| *value)
}

/// Python's `repr` for the simple token texts the grammar deals with.
fn py_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') { '"' } else { '\'' };
    let mut out = String::new();
    out.push(quote);
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// A cursor over one token list; every parse method consumes what it matches.
pub struct Parser {
    tokens: Vec<Token>,
    path: String,
    index: usize,
}

impl Parser {
    pub fn new(tokens: Vec<Token>, path: &str) -> Self {
        Self { tokens, path: path.to_string(), index: 0 }
    }

    /// Parse top-level functions until end of file.
    pub fn parse_program(&mut self) -> Result<Program, DiagnosticError> {
        if self.tokens.last().map(|token| token.kind) != Some("eof") {
            let span = self
                .tokens
                .last()
                .map(|token| token.span)
                .unwrap_or_else(|| Span::point(0, 1, 1));
            return Err(fail(
                "E2001",
                "expected a complete token list ending in end of file, found a truncated list",
                "Parse with cairn.parser.parse so the input ends in an 'eof' token.",
                span,
                &self.path,
            ));
        }
        let mut functions = Vec::new();
        while !self.at_end() {
            if !self.peek().is_keyword("fn") {
                return Err(self.expected("a function, starting with 'fn'"));
            }
            functions.push(self.parse_function()?);
        }
        let span = if let (Some(first), Some(last)) = (functions.first(), functions.last()) {
            Span::join(first.span, last.span)
        } else {
            self.peek().span
        };
        Ok(Program { span, functions })
    }

    /// `fn name(a: i64, ...) -> i64 { ... }`.
    fn parse_function(&mut self) -> Result<Function, DiagnosticError> {
        let start = self.expect_keyword("fn")?;
        let name = self.expect_ident("a function name")?;
        self.expect_symbol("(")?;
        let mut parameters = Vec::new();
        if !self.peek().is_symbol(")") {
            parameters.push(self.parse_parameter()?);
            while self.peek().is_symbol(",") {
                self.advance();
                parameters.push(self.parse_parameter()?);
            }
        }
        self.expect_symbol(")")?;
        self.expect_symbol("->")?;
        let (_size, return_type) = self.parse_type(false)?;
        let body = self.parse_block()?;
        let span = Span::join(start.span, body.span);
        Ok(Function {
            span,
            name: name.text,
            parameters,
            return_type,
            body,
            slots: 0,
        })
    }

    /// One `name: i64` parameter.
    fn parse_parameter(&mut self) -> Result<Parameter, DiagnosticError> {
        let name = self.expect_ident("a parameter name")?;
        self.expect_symbol(":")?;
        let (_size, type_name) = self.parse_type(false)?;
        let previous = self.tokens[self.index - 1].span;
        Ok(Parameter {
            span: Span::join(name.span, previous),
            name: name.text,
            type_name,
            slot: -1,
        })
    }

    /// One type: `i64`, or `[N]i64` where arrays are allowed; answers `(size, name)`.
    fn parse_type(&mut self, allow_array: bool) -> Result<(Option<usize>, String), DiagnosticError> {
        if self.peek().is_symbol("[") {
            let open_bracket = self.advance();
            let count = self.peek().clone();
            if count.kind != "int" {
                return Err(self.expected("an array length"));
            }
            self.advance();
            self.expect_symbol("]")?;
            let element = self.peek().clone();
            if element.kind != "ident" || !TYPE_NAMES.contains(&element.text.as_str()) {
                return Err(self.expected("an element type name"));
            }
            self.advance();
            if !allow_array {
                return Err(fail(
                    "E2002",
                    "an array type needs a local 'let' or 'var' binding",
                    "Arrays bind only to locals; parameters and return types stay 'i64'.",
                    open_bracket.span,
                    &self.path,
                ));
            }
            return Ok((Some(count.value as usize), element.text));
        }
        let token = self.peek().clone();
        if token.kind != "ident" {
            return Err(self.expected("a type name"));
        }
        if !TYPE_NAMES.contains(&token.text.as_str()) {
            return Err(fail(
                "E2002",
                &format!("unknown type '{}'", token.text),
                "Cairn 0.1 has exactly one type, i64; write 'i64'.",
                token.span,
                &self.path,
            ));
        }
        self.advance();
        Ok((None, token.text))
    }

    /// A `[` elements `]` literal; `[v; N]` expands to N copies evaluated in element order.
    fn parse_array_literal(&mut self) -> Result<Expr, DiagnosticError> {
        let open_bracket = self.expect_symbol("[")?;
        let mut span = open_bracket.span;
        if self.peek().is_symbol("]") {
            let close = self.advance();
            return Ok(Expr::ArrayLiteral {
                span: Span::join(span, close.span),
                elements: Vec::new(),
            });
        }
        let first = self.parse_expression(1)?;
        let elements = if self.peek().is_symbol(";") {
            self.advance();
            let count = self.peek().clone();
            if count.kind != "int" {
                return Err(self.expected("an integer fill count"));
            }
            self.advance();
            let close = self.expect_symbol("]")?;
            span = Span::join(span, close.span);
            let mut expanded = Vec::new();
            for _ in 0..count.value {
                expanded.push(clone_expression(&first, 1, |what| self.expected(what))?);
            }
            expanded
        } else {
            let mut values = vec![first];
            while self.peek().is_symbol(",") {
                self.advance();
                values.push(self.parse_expression(1)?);
            }
            let close = self.expect_symbol("]")?;
            span = Span::join(span, close.span);
            values
        };
        Ok(Expr::ArrayLiteral { span, elements })
    }

    /// `{ statement* }`.
    fn parse_block(&mut self) -> Result<Block, DiagnosticError> {
        let start = self.expect_symbol("{")?;
        let mut statements = Vec::new();
        while !self.peek().is_symbol("}") {
            if self.at_end() {
                return Err(self.expected("'}' to close the block"));
            }
            statements.push(self.parse_statement()?);
        }
        let end = self.expect_symbol("}")?;
        Ok(Block {
            span: Span::join(start.span, end.span),
            statements,
        })
    }

    /// Dispatch on the first token of one statement.
    fn parse_statement(&mut self) -> Result<Stmt, DiagnosticError> {
        let token = self.peek().clone();
        if token.is_keyword("let") || token.is_keyword("var") {
            return self.parse_binding();
        }
        if token.is_keyword("if") {
            return self.parse_if();
        }
        if token.is_keyword("while") {
            return self.parse_while();
        }
        if token.is_keyword("for") {
            return self.parse_for();
        }
        if token.is_keyword("return") {
            return self.parse_return();
        }
        if token.is_keyword("break") {
            self.advance();
            let semi = self.expect_symbol(";")?;
            return Ok(Stmt::Break { span: Span::join(token.span, semi.span) });
        }
        if token.is_keyword("continue") {
            self.advance();
            let semi = self.expect_symbol(";")?;
            return Ok(Stmt::Continue { span: Span::join(token.span, semi.span) });
        }
        if token.is_symbol("{") {
            return Ok(Stmt::Block(self.parse_block()?));
        }
        self.parse_expression_statement()
    }

    /// `let|var name: i64 = expr;` or `let|var name: [N]i64 = literal;`.
    fn parse_binding(&mut self) -> Result<Stmt, DiagnosticError> {
        let keyword = self.advance();
        let name = self.expect_ident("a binding name")?;
        self.expect_symbol(":")?;
        let (size, type_name) = self.parse_type(true)?;
        self.expect_symbol("=")?;
        let value = if size.is_some() && self.peek().is_symbol("[") {
            self.parse_array_literal()?
        } else {
            self.parse_expression(1)?
        };
        let semi = self.expect_symbol(";")?;
        Ok(Stmt::Let {
            span: Span::join(keyword.span, semi.span),
            name: name.text,
            mutable: keyword.text == "var",
            type_name,
            value,
            slot: -1,
            array_size: size,
        })
    }

    /// `if expr block [else (if | block)]`.
    fn parse_if(&mut self) -> Result<Stmt, DiagnosticError> {
        let start = self.expect_keyword("if")?;
        let condition = self.parse_expression(1)?;
        let then_block = self.parse_block()?;
        let mut end_span = then_block.span;
        let else_block = if self.peek().is_keyword("else") {
            self.advance();
            let block = if self.peek().is_keyword("if") {
                let nested = self.parse_if()?;
                let span = nested.span();
                Block { span, statements: vec![nested] }
            } else {
                self.parse_block()?
            };
            end_span = block.span;
            Some(block)
        } else {
            None
        };
        Ok(Stmt::If {
            span: Span::join(start.span, end_span),
            condition,
            then_block,
            else_block,
        })
    }

    /// `while expr block`.
    fn parse_while(&mut self) -> Result<Stmt, DiagnosticError> {
        let start = self.expect_keyword("while")?;
        let condition = self.parse_expression(1)?;
        let body = self.parse_block()?;
        Ok(Stmt::While {
            span: Span::join(start.span, body.span),
            condition,
            body,
            countdown: false,
        })
    }

    /// `for name in start..end block`, with `..=` for the inclusive upper bound.
    fn parse_for(&mut self) -> Result<Stmt, DiagnosticError> {
        let start = self.expect_keyword("for")?;
        let name = self.expect_ident("a loop variable name")?;
        self.expect_keyword("in")?;
        let lower = self.parse_expression(1)?;
        let token = self.peek().clone();
        let inclusive = if token.is_symbol("..") {
            self.advance();
            false
        } else if token.is_symbol("..=") {
            self.advance();
            true
        } else {
            return Err(fail(
                "E2005",
                "expected '..' or '..=' in the for range",
                "Write 'for i in 0..10 { ... }' or 'for i in 0..=10 { ... }'.",
                token.span,
                &self.path,
            ));
        };
        let upper = self.parse_expression(1)?;
        let body = self.parse_block()?;
        Ok(Stmt::For {
            span: Span::join(start.span, body.span),
            name: name.text,
            start: lower,
            end: upper,
            inclusive,
            body,
            slot: -1,
        })
    }

    /// `return expr;`.
    fn parse_return(&mut self) -> Result<Stmt, DiagnosticError> {
        let start = self.expect_keyword("return")?;
        let value = self.parse_expression(1)?;
        let semi = self.expect_symbol(";")?;
        Ok(Stmt::Return { span: Span::join(start.span, semi.span), value })
    }

    /// An assignment to a name (or an array element), or a call whose value is discarded.
    fn parse_expression_statement(&mut self) -> Result<Stmt, DiagnosticError> {
        let start_span = self.peek().span;
        let expression = self.parse_expression(1)?;
        if self.peek().is_symbol("=") {
            if let Expr::Index { operand, index, .. } = &expression {
                let Some(name) = operand.as_name() else {
                    return Err(fail(
                        "E2003",
                        "only an array element can be index-assigned",
                        "Write 'a[expression] = value;' with `a` a 'var' array.",
                        operand.span(),
                        &self.path,
                    ));
                };
                let name = name.to_string();
                let index = (**index).clone();
                self.advance();
                let value = self.parse_expression(1)?;
                let semi = self.expect_symbol(";")?;
                return Ok(Stmt::Assign {
                    span: Span::join(start_span, semi.span),
                    name,
                    value,
                    slot: -1,
                    index: Some(index),
                    temp_slot: None,
                    array_size: None,
                });
            }
            let Some(name) = expression.as_name() else {
                return Err(fail(
                    "E2003",
                    "only a variable name can be assigned",
                    "Write 'name = expression;' with a name declared by 'var'.",
                    expression.span(),
                    &self.path,
                ));
            };
            let name = name.to_string();
            self.advance();
            let value = self.parse_expression(1)?;
            let semi = self.expect_symbol(";")?;
            return Ok(Stmt::Assign {
                span: Span::join(start_span, semi.span),
                name,
                value,
                slot: -1,
                index: None,
                temp_slot: None,
                array_size: None,
            });
        }
        let semi = self.expect_symbol(";")?;
        if !matches!(expression.unwrap_parens(), Expr::Call { .. }) {
            return Err(fail(
                "E2004",
                "this expression has no effect",
                "Assign it to a 'let' or 'var', or call a function instead.",
                expression.span(),
                &self.path,
            ));
        }
        Ok(Stmt::ExprStmt {
            span: Span::join(start_span, semi.span),
            expression,
        })
    }

    /// Precedence climbing: parse operators at least as tight as `minimum`.
    fn parse_expression(&mut self, minimum: u8) -> Result<Expr, DiagnosticError> {
        let mut left = self.parse_unary()?;
        loop {
            let token = self.peek().clone();
            let power = match precedence(&token.text) {
                Some(value) if token.kind == "symbol" => value,
                _ => return Ok(left),
            };
            if power < minimum {
                return Ok(left);
            }
            self.advance();
            let right = self.parse_expression(power + 1)?;
            left = Expr::Binary {
                span: Span::join(left.span(), right.span()),
                op: token.text,
                left: Box::new(left),
                right: Box::new(right),
            };
        }
    }

    /// `-x`, `!x`, `~x`, otherwise a primary expression.
    fn parse_unary(&mut self) -> Result<Expr, DiagnosticError> {
        let token = self.peek().clone();
        if token.kind == "symbol" && UNARY_OPERATORS.contains(&token.text.as_str()) {
            self.advance();
            let operand = self.parse_unary()?;
            let unwrapped = operand.unwrap_parens();
            if token.text == "-" {
                if let Expr::IntLiteral { value, .. } = unwrapped {
                    if *value == -INT64_MIN {
                        return Ok(Expr::IntLiteral {
                            span: Span::join(token.span, operand.span()),
                            value: INT64_MIN,
                        });
                    }
                }
            }
            return Ok(Expr::Unary {
                span: Span::join(token.span, operand.span()),
                op: token.text,
                operand: Box::new(operand),
            });
        }
        self.parse_primary()
    }

    /// An integer, a string, a name, a call, a parenthesised expression, or `true`/`false`.
    fn parse_primary(&mut self) -> Result<Expr, DiagnosticError> {
        let token = self.peek().clone();
        match token.kind {
            "int" => {
                self.advance();
                Ok(Expr::IntLiteral { span: token.span, value: token.value })
            }
            "str" => {
                self.advance();
                Ok(Expr::StrLiteral { span: token.span, text: token.text, label: String::new() })
            }
            "keyword" if token.text == "true" || token.text == "false" => {
                self.advance();
                Ok(Expr::IntLiteral {
                    span: token.span,
                    value: if token.text == "true" { 1 } else { 0 },
                })
            }
            "ident" => {
                self.advance();
                if self.peek().is_symbol("(") {
                    self.parse_call(token)
                } else if self.peek().is_symbol("[") {
                    let name = Expr::Name { span: token.span, name: token.text, slot: -1 };
                    self.parse_index(name)
                } else {
                    Ok(Expr::Name { span: token.span, name: token.text, slot: -1 })
                }
            }
            _ if token.is_symbol("(") => {
                self.advance();
                let inner = self.parse_expression(1)?;
                let close = self.expect_symbol(")")?;
                Ok(Expr::Parens {
                    span: Span::join(token.span, close.span),
                    inner: Box::new(inner),
                })
            }
            _ => Err(self.expected("an expression")),
        }
    }

    /// An `operand[index]` load; the opening bracket is the current token.
    fn parse_index(&mut self, operand: Expr) -> Result<Expr, DiagnosticError> {
        let operand_span = operand.span();
        self.expect_symbol("[")?;
        let index = self.parse_expression(1)?;
        let close = self.expect_symbol("]")?;
        Ok(Expr::Index {
            span: Span::join(operand_span, close.span),
            operand: Box::new(operand),
            index: Box::new(index),
            slot: -1,
            size: 0,
        })
    }

    /// `name(arg, ...)`; the opening parenthesis is already the current token.
    fn parse_call(&mut self, name: Token) -> Result<Expr, DiagnosticError> {
        self.expect_symbol("(")?;
        let mut arguments = Vec::new();
        if !self.peek().is_symbol(")") {
            arguments.push(self.parse_expression(1)?);
            while self.peek().is_symbol(",") {
                self.advance();
                arguments.push(self.parse_expression(1)?);
            }
        }
        let close = self.expect_symbol(")")?;
        Ok(Expr::Call {
            span: Span::join(name.span, close.span),
            name: name.text,
            arguments,
        })
    }

    /// The current token without consuming it.
    fn peek(&self) -> &Token {
        &self.tokens[self.index]
    }

    /// Consume and return the current token; `eof` stays put.
    fn advance(&mut self) -> Token {
        let token = self.tokens[self.index].clone();
        if token.kind != "eof" {
            self.index += 1;
        }
        token
    }

    /// Whether the cursor reached the `eof` token.
    fn at_end(&self) -> bool {
        self.peek().kind == "eof"
    }

    /// Consume the given punctuation, or report where it should have been.
    fn expect_symbol(&mut self, text: &str) -> Result<Token, DiagnosticError> {
        if !self.peek().is_symbol(text) {
            return Err(self.expected(&format!("'{}'", text)));
        }
        Ok(self.advance())
    }

    /// Consume the given keyword, or report where it should have been.
    fn expect_keyword(&mut self, text: &str) -> Result<Token, DiagnosticError> {
        if !self.peek().is_keyword(text) {
            return Err(self.expected(&format!("'{}'", text)));
        }
        Ok(self.advance())
    }

    /// Consume an identifier, or report where it should have been.
    fn expect_ident(&mut self, what: &str) -> Result<Token, DiagnosticError> {
        if self.peek().kind != "ident" {
            return Err(self.expected(what));
        }
        Ok(self.advance())
    }

    /// A positioned `E2001`: what the grammar wanted and what it found instead.
    fn expected(&self, what: &str) -> DiagnosticError {
        let token = self.peek();
        let found = if token.kind == "eof" {
            "end of file".to_string()
        } else {
            py_repr(&token.text)
        };
        fail(
            "E2001",
            &format!("expected {}, found {}", what, found),
            &format!("Insert {} here, or remove the token before it.", what),
            token.span,
            &self.path,
        )
    }
}

/// A deep copy of one expression; fill expansion needs one node per element.
fn clone_expression<F>(expression: &Expr, depth: usize, expected: F) -> Result<Expr, DiagnosticError>
where
    F: Fn(&str) -> DiagnosticError + Copy,
{
    if depth > 64 {
        return Err(expected("a fill expression shallower than 64 nested operators"));
    }
    match expression {
        Expr::IntLiteral { .. } | Expr::Name { .. } | Expr::StrLiteral { .. } => {
            Ok(expression.clone())
        }
        Expr::Parens { span, inner } => Ok(Expr::Parens {
            span: *span,
            inner: Box::new(clone_expression(inner, depth + 1, expected)?),
        }),
        Expr::Unary { span, op, operand } => Ok(Expr::Unary {
            span: *span,
            op: op.clone(),
            operand: Box::new(clone_expression(operand, depth + 1, expected)?),
        }),
        Expr::Binary { span, op, left, right } => Ok(Expr::Binary {
            span: *span,
            op: op.clone(),
            left: Box::new(clone_expression(left, depth + 1, expected)?),
            right: Box::new(clone_expression(right, depth + 1, expected)?),
        }),
        Expr::Call { span, name, arguments } => Ok(Expr::Call {
            span: *span,
            name: name.clone(),
            arguments: arguments
                .iter()
                .map(|argument| clone_expression(argument, depth + 1, expected))
                .collect::<Result<Vec<_>, _>>()?,
        }),
        _ => Err(expected("a fill expression the parser can copy")),
    }
}

/// Tokenize and parse one source text.
pub fn parse(source: &str, path: &str) -> Result<Program, DiagnosticError> {
    Parser::new(tokenize(source, path)?, path).parse_program()
}
