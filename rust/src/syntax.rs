//! Syntax tree: one variant per construct, each carrying its source span.
//!
//! The tree is deliberately shallow. There is no expression that is also a declaration, no
//! operator overloading and no implicit conversion, so an agent can predict the meaning of any
//! node from its variant alone. `i128` holds literals because the lexer admits `2**63` so unary
//! minus can fold the smallest i64; later stages narrow it with the documented wrapping rules.

use crate::diagnostics::Span;

/// A value-producing node.
#[derive(Debug, Clone, PartialEq)]
pub enum Expr {
    /// A decimal integer literal, already range-checked by the lexer.
    IntLiteral { span: Span, value: i128 },
    /// A quoted string literal: read-only NUL-terminated bytes; `label` is filled in by codegen.
    StrLiteral { span: Span, text: String, label: String },
    /// A reference to a parameter or local; `slot` is filled in by semantic analysis.
    Name { span: Span, name: String, slot: i64 },
    /// `-x`, `!x` or `~x`.
    Unary { span: Span, op: String, operand: Box<Expr> },
    /// A binary operator; `op` is the source spelling.
    Binary { span: Span, op: String, left: Box<Expr>, right: Box<Expr> },
    /// Parentheses, kept so spans and grouping survive into code generation.
    Parens { span: Span, inner: Box<Expr> },
    /// `name(arg, ...)`.
    Call { span: Span, name: String, arguments: Vec<Expr> },
    /// `[e0, e1, ...]`, including a fill expanded to one node per element.
    ArrayLiteral { span: Span, elements: Vec<Expr> },
    /// `a[i]`; `slot` and `size` are filled in by semantic analysis.
    Index { span: Span, operand: Box<Expr>, index: Box<Expr>, slot: i64, size: usize },
}

impl Expr {
    /// The span this expression was parsed from.
    pub fn span(&self) -> Span {
        match self {
            Self::IntLiteral { span, .. }
            | Self::StrLiteral { span, .. }
            | Self::Name { span, .. }
            | Self::Unary { span, .. }
            | Self::Binary { span, .. }
            | Self::Parens { span, .. }
            | Self::Call { span, .. }
            | Self::ArrayLiteral { span, .. }
            | Self::Index { span, .. } => *span,
        }
    }

    /// The integer value when this is an `IntLiteral`.
    pub fn as_int(&self) -> Option<i128> {
        match self {
            Self::IntLiteral { value, .. } => Some(*value),
            _ => None,
        }
    }

    /// The name when this is a `Name`.
    pub fn as_name(&self) -> Option<&str> {
        match self {
            Self::Name { name, .. } => Some(name),
            _ => None,
        }
    }

    /// Peel parenthesised expressions, as the parser and semantic analysis both do.
    pub fn unwrap_parens(&self) -> &Self {
        let mut current = self;
        while let Self::Parens { inner, .. } = current {
            current = inner;
        }
        current
    }

    /// The mutable form of `unwrap_parens`, for stages that fill in slots.
    pub fn unwrap_parens_mut(&mut self) -> &mut Self {
        let mut current = self;
        while let Self::Parens { inner, .. } = current {
            current = inner;
        }
        current
    }
}

/// A brace-delimited statement sequence.
#[derive(Debug, Clone, PartialEq)]
pub struct Block {
    pub span: Span,
    pub statements: Vec<Stmt>,
}

/// A statement node.
#[derive(Debug, Clone, PartialEq)]
pub enum Stmt {
    /// A brace-delimited statement sequence.
    Block(Block),
    /// `let x: i64 = e;` or `var x: i64 = e;`; arrays reserve `array_size` frame slots.
    Let {
        span: Span,
        name: String,
        mutable: bool,
        type_name: String,
        value: Expr,
        slot: i64,
        array_size: Option<usize>,
    },
    /// `x = e;` or `a[i] = e;`.
    Assign {
        span: Span,
        name: String,
        value: Expr,
        slot: i64,
        index: Option<Expr>,
        temp_slot: Option<usize>,
        array_size: Option<usize>,
    },
    /// `if cond { ... } else { ... }`.
    If {
        span: Span,
        condition: Expr,
        then_block: Block,
        else_block: Option<Block>,
    },
    /// `while cond { ... }`; `countdown` marks a loop the optimiser converted.
    While {
        span: Span,
        condition: Expr,
        body: Block,
        countdown: bool,
    },
    /// `for name in start..end { ... }`; `..=` sets the inclusive bound.
    For {
        span: Span,
        name: String,
        start: Expr,
        end: Expr,
        inclusive: bool,
        body: Block,
        slot: i64,
    },
    /// `return e;`.
    Return { span: Span, value: Expr },
    /// Leave the innermost loop.
    Break { span: Span },
    /// Start the next iteration of the innermost loop.
    Continue { span: Span },
    /// An expression evaluated for its effect, which in Cairn means a call.
    ExprStmt { span: Span, expression: Expr },
}

impl Stmt {
    /// The span this statement was parsed from.
    pub fn span(&self) -> Span {
        match self {
            Self::Block(block) => block.span,
            Self::Let { span, .. }
            | Self::Assign { span, .. }
            | Self::If { span, .. }
            | Self::While { span, .. }
            | Self::For { span, .. }
            | Self::Return { span, .. }
            | Self::Break { span, .. }
            | Self::Continue { span, .. }
            | Self::ExprStmt { span, .. } => *span,
        }
    }
}

/// One parameter: a name and its declared type.
#[derive(Debug, Clone, PartialEq)]
pub struct Parameter {
    pub span: Span,
    pub name: String,
    pub type_name: String,
    pub slot: i64,
}

/// A top-level function; `slots` is the number of 8-byte frame slots it needs.
#[derive(Debug, Clone, PartialEq)]
pub struct Function {
    pub span: Span,
    pub name: String,
    pub parameters: Vec<Parameter>,
    pub return_type: String,
    pub body: Block,
    pub slots: usize,
}

/// A whole source file.
#[derive(Debug, Clone, PartialEq)]
pub struct Program {
    pub span: Span,
    pub functions: Vec<Function>,
}
