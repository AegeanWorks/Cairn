//! Optimiser core: constant folding and provably safe algebraic identities.
//!
//! A rewrite happens only when the result is identical to what the machine code would compute,
//! including wrapping arithmetic, arithmetic right shift and the divide-by-zero trap. `print` is
//! an effect, so no rewrite may drop or duplicate a call. The structural passes (algebraic
//! simplification, copy propagation and loop rewrites) are added after this core.

use crate::diagnostics::Span;
use crate::syntax::{Block, Expr, Program, Stmt};

/// The smallest i64, as an exact integer.
pub const INT64_MIN: i128 = -9_223_372_036_854_775_808;
/// The largest i64, as an exact integer.
pub const INT64_MAX: i128 = 9_223_372_036_854_775_807;
const MASK64: i128 = (1i128 << 64) - 1;

/// Reduce an exact integer to the i64 value the machine would hold.
pub fn wrap(value: i128) -> i128 {
    let masked = value & MASK64;
    if masked > INT64_MAX {
        masked - (1i128 << 64)
    } else {
        masked
    }
}

/// Signed division truncating toward zero, the way `idiv` does.
pub fn truncate_division(left: i128, right: i128) -> i128 {
    let quotient = left.abs() / right.abs();
    if (left < 0) == (right < 0) {
        quotient
    } else {
        -quotient
    }
}

/// The literal value of an expression, or None when it is not a constant.
pub fn constant(expression: Option<&Expr>) -> Option<i128> {
    match expression {
        Some(Expr::IntLiteral { value, .. }) => Some(*value),
        _ => None,
    }
}

/// Peel parenthesised expressions.
pub fn peel_parens(mut expression: Option<&Expr>) -> Option<&Expr> {
    while let Some(Expr::Parens { inner, .. }) = expression {
        expression = Some(inner);
    }
    expression
}

fn comparison(op: &str, left: i128, right: i128) -> Option<i128> {
    match op {
        "==" => Some((left == right) as i128),
        "!=" => Some((left != right) as i128),
        "<" => Some((left < right) as i128),
        "<=" => Some((left <= right) as i128),
        ">" => Some((left > right) as i128),
        ">=" => Some((left >= right) as i128),
        _ => None,
    }
}

/// The folded value of `left op right`, or None when folding would change behaviour.
pub fn fold_constant(op: &str, left: i128, right: i128) -> Option<i128> {
    if let Some(value) = comparison(op, left, right) {
        return Some(value);
    }
    match op {
        "/" | "%" => {
            if right == 0 || (left == INT64_MIN && right == -1) {
                return None;
            }
            let quotient = truncate_division(left, right);
            Some(if op == "/" { quotient } else { left - quotient * right })
        }
        "+" => Some(wrap(left + right)),
        "-" => Some(wrap(left - right)),
        "*" => Some(wrap(left * right)),
        "&" => Some(wrap(left & right)),
        "|" => Some(wrap(left | right)),
        "^" => Some(wrap(left ^ right)),
        "<<" => Some(wrap(left << (right & 63))),
        ">>" => Some(wrap(left >> (right & 63))),
        "&&" => Some((left != 0 && right != 0) as i128),
        "||" => Some((left != 0 || right != 0) as i128),
        _ => None,
    }
}

/// The folded value of a unary operator, or None when it must stay dynamic.
pub fn fold_unary(op: &str, value: i128) -> Option<i128> {
    match op {
        "-" => Some(wrap(-value)),
        "~" => Some(wrap(!value)),
        "!" => Some((value == 0) as i128),
        _ => None,
    }
}

/// Whether evaluating `expression` can run a call, and therefore an effect.
///
/// A name in `inline_builtins` lowers inline and never runs a callee (ADR-115).
pub fn has_call(expression: Option<&Expr>, inline_builtins: &[&str]) -> bool {
    match expression {
        None => false,
        Some(Expr::Call { name, arguments, .. }) if inline_builtins.contains(&name.as_str()) => {
            arguments.iter().any(|argument| has_call(Some(argument), inline_builtins))
        }
        Some(Expr::Call { .. }) => true,
        Some(Expr::Parens { inner, .. }) | Some(Expr::Unary { operand: inner, .. }) => {
            has_call(Some(inner), inline_builtins)
        }
        Some(Expr::Binary { left, right, .. }) => {
            has_call(Some(left), inline_builtins) || has_call(Some(right), inline_builtins)
        }
        Some(Expr::Index { operand, index, .. }) => {
            has_call(Some(operand), inline_builtins) || has_call(Some(index), inline_builtins)
        }
        Some(Expr::ArrayLiteral { elements, .. }) => {
            elements.iter().any(|element| has_call(Some(element), inline_builtins))
        }
        Some(_) => false,
    }
}

/// Whether an element load sits anywhere under one expression tree.
pub fn contains_index(expression: &Expr) -> bool {
    match expression {
        Expr::Index { .. } => true,
        Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => contains_index(inner),
        Expr::Binary { left, right, .. } => contains_index(left) || contains_index(right),
        Expr::Call { arguments, .. } | Expr::ArrayLiteral { elements: arguments, .. } => {
            arguments.iter().any(contains_index)
        }
        _ => false,
    }
}

/// Whether evaluating `expression` can trap on `/` or `%`.
pub fn may_trap(expression: Option<&Expr>) -> bool {
    match expression {
        None => false,
        Some(Expr::Binary { op, left, right, .. }) if op == "/" || op == "%" => {
            let left_value = constant(Some(left));
            let right_value = constant(Some(right));
            if let Some(right_value) = right_value {
                if right_value != 0
                    && !(right_value == -1 && (left_value.is_none() || left_value == Some(INT64_MIN)))
                {
                    return may_trap(Some(left)) || may_trap(Some(right));
                }
            }
            true
        }
        Some(Expr::Binary { left, right, .. }) => {
            may_trap(Some(left)) || may_trap(Some(right))
        }
        Some(Expr::Parens { inner, .. }) | Some(Expr::Unary { operand: inner, .. }) => {
            may_trap(Some(inner))
        }
        Some(Expr::Call { arguments, .. }) | Some(Expr::ArrayLiteral { elements: arguments, .. }) => {
            arguments.iter().any(|argument| may_trap(Some(argument)))
        }
        Some(Expr::Index { operand, index, .. }) => {
            may_trap(Some(operand)) || may_trap(Some(index))
        }
        Some(_) => false,
    }
}

/// Splice a branch selected by folding into its parent, since Cairn has one flat scope.
pub fn flatten(statements: Vec<Stmt>) -> Vec<Stmt> {
    let mut flattened = Vec::new();
    for statement in statements {
        if let Stmt::Block(block) = statement {
            flattened.extend(flatten(block.statements));
        } else {
            flattened.push(statement);
        }
    }
    flattened
}

/// Drop statements after one that cannot fall through: `return`, `break`, `continue`.
pub fn reachable(statements: Vec<Stmt>) -> Vec<Stmt> {
    let end = statements
        .iter()
        .position(|statement| matches!(statement, Stmt::Return { .. } | Stmt::Break { .. } | Stmt::Continue { .. }))
        .map(|index| index + 1)
        .unwrap_or(statements.len());
    statements[..end].to_vec()
}

/// Where each operator has a neutral right operand, and (separately) a neutral left operand.
fn right_neutral(op: &str) -> Option<i128> {
    match op {
        "+" | "-" | "|" | "^" | "<<" | ">>" => Some(0),
        "*" => Some(1),
        _ => None,
    }
}

fn left_neutral(op: &str) -> Option<i128> {
    match op {
        "+" | "|" | "^" => Some(0),
        "*" => Some(1),
        _ => None,
    }
}

/// One folding pass, rewriting expressions and returning replacement nodes.
#[derive(Debug, Default)]
pub struct Optimizer {
    pub folded: usize,
}

impl Optimizer {
    pub fn new() -> Self {
        Self::default()
    }

    /// Fold every function body in a program.
    pub fn fold_program(&mut self, program: &Program) -> Program {
        let mut result = program.clone();
        for function in &mut result.functions {
            function.body = self.block(&function.body);
        }
        result
    }

    fn block(&mut self, block: &Block) -> Block {
        let statements = block.statements.iter().map(|statement| self.statement(statement)).collect();
        let flattened = flatten(statements);
        let kept = reachable(flattened.clone());
        self.folded += flattened.len() - kept.len();
        Block { span: block.span, statements: kept }
    }

    fn statement(&mut self, statement: &Stmt) -> Stmt {
        match statement {
            Stmt::Let { name, mutable, type_name, value, slot, array_size, span } => Stmt::Let {
                span: *span, name: name.clone(), mutable: *mutable, type_name: type_name.clone(),
                value: self.expression(value), slot: *slot, array_size: *array_size,
            },
            Stmt::Assign { name, value, slot, index, temp_slot, array_size, span } => Stmt::Assign {
                span: *span, name: name.clone(), value: self.expression(value), slot: *slot,
                index: index.as_ref().map(|index| self.expression(index)),
                temp_slot: *temp_slot, array_size: *array_size,
            },
            Stmt::Return { span, value } => Stmt::Return { span: *span, value: self.expression(value) },
            Stmt::ExprStmt { span, expression } => Stmt::ExprStmt {
                span: *span,
                expression: self.expression(expression),
            },
            Stmt::If { span, condition, then_block, else_block } => {
                let folded_condition = self.expression(condition);
                let then = self.block(then_block);
                let otherwise = else_block.as_ref().map(|block| self.block(block));
                self.choose_branch(*span, folded_condition, then, otherwise)
            }
            Stmt::While { span, condition, body, countdown } => {
                let folded_condition = self.expression(condition);
                if constant(Some(&folded_condition)) == Some(0) {
                    self.folded += 1;
                    return Stmt::Block(Block { span: *span, statements: Vec::new() });
                }
                Stmt::While {
                    span: *span,
                    condition: folded_condition,
                    body: self.block(body),
                    countdown: *countdown,
                }
            }
            Stmt::Block(block) => Stmt::Block(self.block(block)),
            other => other.clone(),
        }
    }

    fn choose_branch(&mut self, span: crate::diagnostics::Span, condition: Expr,
                     then_block: Block, else_block: Option<Block>) -> Stmt {
        match constant(Some(&condition)) {
            None => Stmt::If { span, condition, then_block, else_block },
            Some(value) => {
                self.folded += 1;
                if value != 0 {
                    Stmt::Block(then_block)
                } else {
                    Stmt::Block(else_block.unwrap_or(Block { span, statements: Vec::new() }))
                }
            }
        }
    }

    fn expression(&mut self, expression: &Expr) -> Expr {
        match expression {
            Expr::Parens { span, inner } => {
                let folded = self.expression(inner);
                if let Expr::IntLiteral { .. } = folded {
                    self.folded += 1;
                    folded
                } else {
                    Expr::Parens { span: *span, inner: Box::new(folded) }
                }
            }
            Expr::Unary { span, op, operand } => {
                let folded = self.expression(operand);
                self.fold_unary_node(*span, op, folded)
            }
            Expr::Binary { span, op, left, right } => {
                let folded_left = self.expression(left);
                let folded_right = self.expression(right);
                self.fold_binary_node(*span, op, folded_left, folded_right)
            }
            Expr::Call { span, name, arguments } => {
                let args: Vec<Expr> = arguments.iter().map(|argument| self.expression(argument)).collect();
                let folded = self.fold_string_builtin(*span, name, &args);
                match folded {
                    Some(value) => {
                        self.folded += 1;
                        Expr::IntLiteral { span: *span, value }
                    }
                    None => Expr::Call { span: *span, name: name.clone(), arguments: args },
                }
            }
            other => other.clone(),
        }
    }

    fn fold_string_builtin(&self, span: crate::diagnostics::Span, name: &str,
                           arguments: &[Expr]) -> Option<i128> {
        if name != "str_len" && name != "str_byte" {
            return None;
        }
        let Expr::StrLiteral { text, .. } = arguments.first()? else {
            return None;
        };
        let length = text.chars().count() as i128;
        if name == "str_len" {
            return Some(length);
        }
        if arguments.len() != 2 {
            return None;
        }
        let Expr::IntLiteral { value: position, .. } = &arguments[1] else {
            return None;
        };
        if *position < 0 || *position == length {
            return Some(-1);
        }
        if *position > 0 && *position < length {
            return text.chars().nth(*position as usize).map(|ch| ch as i128);
        }
        if *position == 0 {
            return text.chars().next().map(|ch| ch as i128).or(Some(-1));
        }
        None
    }

    fn fold_unary_node(&mut self, span: crate::diagnostics::Span, op: &str,
                       operand: Expr) -> Expr {
        let value = constant(Some(&operand));
        let folded = value.and_then(|value| fold_unary(op, value));
        match folded {
            Some(value) => {
                self.folded += 1;
                Expr::IntLiteral { span, value }
            }
            None => Expr::Unary { span, op: op.to_string(), operand: Box::new(operand) },
        }
    }

    fn fold_binary_node(&mut self, span: crate::diagnostics::Span, op: &str,
                        left: Expr, right: Expr) -> Expr {
        let left_value = constant(Some(&left));
        let right_value = constant(Some(&right));
        if let (Some(left_value), Some(right_value)) = (left_value, right_value) {
            if let Some(folded) = fold_constant(op, left_value, right_value) {
                self.folded += 1;
                return Expr::IntLiteral { span, value: folded };
            }
            return Expr::Binary { span, op: op.to_string(), left: Box::new(left), right: Box::new(right) };
        }
        self.identity(span, op, left, right, left_value, right_value)
    }

    fn identity(&mut self, span: crate::diagnostics::Span, op: &str, left: Expr, right: Expr,
                left_value: Option<i128>, right_value: Option<i128>) -> Expr {
        if left_value.is_some() && right_value.is_some() {
            return Expr::Binary { span, op: op.to_string(), left: Box::new(left), right: Box::new(right) };
        }
        if let Some(value) = right_value {
            if right_neutral(op) == Some(value) {
                self.folded += 1;
                return left;
            }
        }
        if let Some(value) = left_value {
            if left_neutral(op) == Some(value) {
                self.folded += 1;
                return right;
            }
        }
        if (op == "*" || op == "&") && (right_value == Some(0) || left_value == Some(0)) {
            let (dropped, kept) = if right_value == Some(0) {
                (&right, &left)
            } else {
                (&left, &right)
            };
            if !has_call(Some(dropped), &[]) && !has_call(Some(kept), &[])
                && !may_trap(Some(dropped)) && !may_trap(Some(kept))
            {
                self.folded += 1;
                return Expr::IntLiteral { span, value: 0 };
            }
        }
        Expr::Binary { span, op: op.to_string(), left: Box::new(left), right: Box::new(right) }
    }
}

/// Fold a program with a fresh optimizer.
pub fn fold(program: &Program) -> Program {
    Optimizer::new().fold_program(program)
}

/// Lower every `for` into a mutable `let` plus a `while`; runs before any folding.
///
/// Innermost loops lower first, so a `continue` in a nested `for` was already bound to the inner
/// increment before the outer rewrite looks at the block.
pub fn lower_for_loops(program: &Program) -> Program {
    let mut result = program.clone();
    for function in &mut result.functions {
        lower_block(&mut function.body);
    }
    result
}

fn lower_block(block: &mut Block) {
    let mut lowered = Vec::new();
    for statement in std::mem::take(&mut block.statements) {
        match statement {
            Stmt::For { span, name, start, end, inclusive, mut body, slot } => {
                lower_block(&mut body);
                rewrite_continues(&mut body, &name, slot);
                let increment = increment(&name, slot, span);
                body.statements.push(increment);
                lowered.extend(for_to_while(span, &name, slot, start, end, inclusive, body));
            }
            Stmt::If { condition, then_block, else_block, span } => {
                let mut then_block = then_block;
                let mut else_block = else_block;
                lower_block(&mut then_block);
                if let Some(otherwise) = else_block.as_mut() {
                    lower_block(otherwise);
                }
                lowered.push(Stmt::If { span, condition, then_block, else_block });
            }
            Stmt::While { span, condition, mut body, countdown } => {
                lower_block(&mut body);
                lowered.push(Stmt::While { span, condition, body, countdown });
            }
            Stmt::Block(mut inner) => {
                lower_block(&mut inner);
                lowered.push(Stmt::Block(inner));
            }
            other => lowered.push(other),
        }
    }
    block.statements = lowered;
}

fn for_to_while(span: crate::diagnostics::Span, name: &str, slot: i64, start: Expr,
                end: Expr, inclusive: bool, body: Block) -> Vec<Stmt> {
    let op = if inclusive { "<=" } else { "<" };
    let condition_span = Span::join(start.span(), end.span());
    let condition = Expr::Binary {
        span: condition_span,
        op: op.to_string(),
        left: Box::new(Expr::Name { span: start.span(), name: name.to_string(), slot }),
        right: Box::new(end),
    };
    let initialiser = Stmt::Let {
        span,
        name: name.to_string(),
        mutable: true,
        type_name: "i64".to_string(),
        value: start,
        slot,
        array_size: None,
    };
    vec![initialiser, Stmt::While { span, condition, body, countdown: false }]
}

fn increment(name: &str, slot: i64, span: crate::diagnostics::Span) -> Stmt {
    Stmt::Assign {
        span,
        name: name.to_string(),
        value: Expr::Binary {
            span,
            op: "+".to_string(),
            left: Box::new(Expr::Name { span, name: name.to_string(), slot }),
            right: Box::new(Expr::IntLiteral { span, value: 1 }),
        },
        slot,
        index: None,
        temp_slot: None,
        array_size: None,
    }
}

fn rewrite_continues(block: &mut Block, name: &str, slot: i64) {
    for statement in &mut block.statements {
        match statement {
            Stmt::Continue { span } => {
                let mut replacement = Block {
                    span: *span,
                    statements: vec![increment(name, slot, *span)],
                };
                replacement.statements.push(statement.clone());
                *statement = Stmt::Block(replacement);
            }
            Stmt::If { then_block, else_block, .. } => {
                rewrite_continues(then_block, name, slot);
                if let Some(otherwise) = else_block {
                    rewrite_continues(otherwise, name, slot);
                }
            }
            Stmt::While { .. } => {}
            Stmt::Block(inner) => rewrite_continues(inner, name, slot),
            _ => {}
        }
    }
}
