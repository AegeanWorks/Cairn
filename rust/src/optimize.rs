//! Optimiser core: constant folding and provably safe algebraic identities.
//!
//! A rewrite happens only when the result is identical to what the machine code would compute,
//! including wrapping arithmetic, arithmetic right shift and the divide-by-zero trap. `print` is
//! an effect, so no rewrite may drop or duplicate a call. The structural passes (algebraic
//! simplification, copy propagation and loop rewrites) are added after this core.

use std::collections::{BTreeMap, BTreeSet};

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

    fn fold_string_builtin(&self, _span: crate::diagnostics::Span, name: &str,
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

/// Add every slot an expression reads to `acc`.
fn read_slots(expression: &Expr, acc: &mut BTreeSet<i64>) {
    match expression {
        Expr::Name { slot, .. } => {
            acc.insert(*slot);
        }
        Expr::Unary { operand, .. } | Expr::Parens { inner: operand, .. } => {
            read_slots(operand, acc);
        }
        Expr::Binary { left, right, .. } => {
            read_slots(left, acc);
            read_slots(right, acc);
        }
        Expr::Call { arguments, .. } | Expr::ArrayLiteral { elements: arguments, .. } => {
            for argument in arguments {
                read_slots(argument, acc);
            }
        }
        Expr::Index { operand, index, .. } => {
            if let Expr::Name { slot, .. } = operand.as_ref() {
                acc.insert(*slot);
            }
            read_slots(index, acc);
        }
        Expr::IntLiteral { .. } | Expr::StrLiteral { .. } => {}
    }
}

/// Whether a statement or expression reads or writes the variable in `slot`.
fn mentions_slot_expr(expression: &Expr, slot: i64) -> bool {
    match expression {
        Expr::Name { slot: found, .. } => *found == slot,
        Expr::Unary { operand, .. } | Expr::Parens { inner: operand, .. } => {
            mentions_slot_expr(operand, slot)
        }
        Expr::Binary { left, right, .. } => {
            mentions_slot_expr(left, slot) || mentions_slot_expr(right, slot)
        }
        Expr::Call { arguments, .. } | Expr::ArrayLiteral { elements: arguments, .. } => {
            arguments.iter().any(|argument| mentions_slot_expr(argument, slot))
        }
        Expr::Index { operand, index, .. } => {
            mentions_slot_expr(operand, slot) || mentions_slot_expr(index, slot)
        }
        Expr::IntLiteral { .. } | Expr::StrLiteral { .. } => false,
    }
}

fn mentions_slot_block(block: &Block, slot: i64) -> bool {
    block.statements.iter().any(|statement| mentions_slot_stmt(statement, slot))
}

fn mentions_slot_stmt(statement: &Stmt, slot: i64) -> bool {
    match statement {
        Stmt::Let { value, slot: found, .. } | Stmt::Assign { value, slot: found, .. } => {
            *found == slot || mentions_slot_expr(value, slot)
        }
        Stmt::If { condition, then_block, else_block, .. } => {
            mentions_slot_expr(condition, slot)
                || mentions_slot_block(then_block, slot)
                || else_block.as_ref().map_or(false, |block| mentions_slot_block(block, slot))
        }
        Stmt::While { condition, body, .. } => {
            mentions_slot_expr(condition, slot) || mentions_slot_block(body, slot)
        }
        Stmt::Block(block) => mentions_slot_block(block, slot),
        Stmt::Return { value, .. } | Stmt::ExprStmt { expression: value, .. } => {
            mentions_slot_expr(value, slot)
        }
        Stmt::For { start, end, body, .. } => {
            mentions_slot_expr(start, slot)
                || mentions_slot_expr(end, slot)
                || mentions_slot_block(body, slot)
        }
        Stmt::Break { .. } | Stmt::Continue { .. } => false,
    }
}

fn count_reads_expr(expression: &Expr, slot: i64) -> usize {
    match expression {
        Expr::Name { slot: found, .. } => usize::from(*found == slot),
        Expr::Unary { operand, .. } | Expr::Parens { inner: operand, .. } => {
            count_reads_expr(operand, slot)
        }
        Expr::Binary { left, right, .. } => {
            count_reads_expr(left, slot) + count_reads_expr(right, slot)
        }
        Expr::Call { arguments, .. } | Expr::ArrayLiteral { elements: arguments, .. } => {
            arguments.iter().map(|argument| count_reads_expr(argument, slot)).sum()
        }
        Expr::Index { operand, index, .. } => {
            count_reads_expr(operand, slot) + count_reads_expr(index, slot)
        }
        Expr::IntLiteral { .. } | Expr::StrLiteral { .. } => 0,
    }
}

fn count_reads_block(block: &Block, slot: i64) -> usize {
    block.statements.iter().map(|statement| count_reads_stmt(statement, slot)).sum()
}

fn count_reads_stmt(statement: &Stmt, slot: i64) -> usize {
    match statement {
        Stmt::Let { value, .. } | Stmt::Return { value, .. }
        | Stmt::ExprStmt { expression: value, .. } => count_reads_expr(value, slot),
        Stmt::Assign { value, index, .. } => {
            count_reads_expr(value, slot)
                + index.as_ref().map_or(0, |index| count_reads_expr(index, slot))
        }
        Stmt::If { condition, then_block, else_block, .. } => {
            count_reads_expr(condition, slot)
                + count_reads_block(then_block, slot)
                + else_block.as_ref().map_or(0, |block| count_reads_block(block, slot))
        }
        Stmt::While { condition, body, .. } => {
            count_reads_expr(condition, slot) + count_reads_block(body, slot)
        }
        Stmt::Block(block) => count_reads_block(block, slot),
        Stmt::For { start, end, body, .. } => {
            count_reads_expr(start, slot)
                + count_reads_expr(end, slot)
                + count_reads_block(body, slot)
        }
        Stmt::Break { .. } | Stmt::Continue { .. } => 0,
    }
}

/// Apply copy propagation: replace `x = t` with t's definition when the copy is t's only read,
/// and forward-substitute a pure definition into its single later use.
pub fn apply_copy_propagation(program: &mut Program) -> usize {
    let mut propagated = 0;
    for _ in 0..64 {
        let roots: Vec<Block> = program.functions.iter().map(|f| f.body.clone()).collect();
        let mut changed = false;
        for (function, root) in program.functions.iter_mut().zip(roots.iter()) {
            if propagate_once(root, &mut function.body) {
                changed = true;
                propagated += 1;
            } else if substitute_once(root, &mut function.body) {
                changed = true;
                propagated += 1;
            }
        }
        if !changed {
            break;
        }
    }
    propagated
}

fn propagate_once(root: &Block, block: &mut Block) -> bool {
    for index in 0..block.statements.len() {
        match &block.statements[index] {
            Stmt::Break { .. } | Stmt::Continue { .. } | Stmt::Return { .. }
            | Stmt::ExprStmt { .. } => continue,
            Stmt::If { then_block, else_block, condition, span } => {
                let mut then = then_block.clone();
                let mut otherwise = else_block.clone();
                if propagate_once(root, &mut then)
                    || otherwise.as_mut().map_or(false, |block| propagate_once(root, block))
                {
                    block.statements[index] = Stmt::If {
                        span: *span, condition: condition.clone(), then_block: then,
                        else_block: otherwise,
                    };
                    return true;
                }
                continue;
            }
            Stmt::While { body, condition, span, countdown } => {
                let mut inner = body.clone();
                if propagate_once(root, &mut inner) {
                    block.statements[index] = Stmt::While {
                        span: *span, condition: condition.clone(), body: inner, countdown: *countdown,
                    };
                    return true;
                }
                continue;
            }
            Stmt::Block(inner) => {
                let mut nested = inner.clone();
                if propagate_once(root, &mut nested) {
                    block.statements[index] = Stmt::Block(nested);
                    return true;
                }
                continue;
            }
            Stmt::Let { value, slot, .. } => {
                if has_call(Some(value), &[]) || may_trap(Some(value))
                    || mentions_slot_expr(value, *slot) || count_reads_block(root, *slot) != 1
                {
                    continue;
                }
                let mut operands = BTreeSet::new();
                read_slots(value, &mut operands);
                let definition = value.clone();
                let slot = *slot;
                let mut target = None;
                let mut blocked = false;
                for cursor in index + 1..block.statements.len() {
                    let current = &block.statements[cursor];
                    let copy = match current {
                        Stmt::Assign { value, slot: destination, .. }
                        | Stmt::Let { value, slot: destination, .. } => {
                            matches!(value, Expr::Name { slot: found, .. }
                                     if *found == slot && *destination != slot)
                        }
                        _ => false,
                    };
                    if copy {
                        if target.is_some() {
                            blocked = true;
                            break;
                        }
                        target = Some(cursor);
                        continue;
                    }
                    if mentions_slot_stmt(current, slot) {
                        blocked = true;
                        break;
                    }
                    if target.is_none() && operands.iter().any(|operand| {
                        mentions_slot_stmt(current, *operand)
                    }) {
                        blocked = true;
                        break;
                    }
                }
                if blocked {
                    continue;
                }
                if let Some(cursor) = target {
                    match &mut block.statements[cursor] {
                        Stmt::Assign { value, .. } | Stmt::Let { value, .. } => {
                            *value = definition;
                        }
                        _ => unreachable!(),
                    }
                    block.statements.remove(index);
                    return true;
                }
            }
            Stmt::For { .. } | Stmt::Assign { .. } => continue,
        }
    }
    false
}

/// Forward-substitute a pure definition into its single later use and drop the `let`.
///
/// This is the shape a bit-builtin benchmark takes: `let v = <arithmetic>; total = f(v)`.
/// Keeping the `let` costs a register round trip because the code generator must park the
/// value in v's home and then copy it back to rax for the builtin. The substitution is only
/// made when the value has no call, no trap, no array read and no self-reference, the slot
/// is read exactly once in the whole function, that read is a plain statement of this same
/// block, and nothing between writes any slot the value reads.
fn substitute_once(root: &Block, block: &mut Block) -> bool {
    for index in 0..block.statements.len() {
        match &block.statements[index] {
            Stmt::Break { .. } | Stmt::Continue { .. } | Stmt::Return { .. }
            | Stmt::ExprStmt { .. } => continue,
            Stmt::If { then_block, else_block, condition, span } => {
                let mut then = then_block.clone();
                let mut otherwise = else_block.clone();
                if substitute_once(root, &mut then)
                    || otherwise.as_mut().map_or(false, |block| substitute_once(root, block))
                {
                    block.statements[index] = Stmt::If {
                        span: *span, condition: condition.clone(), then_block: then,
                        else_block: otherwise,
                    };
                    return true;
                }
                continue;
            }
            Stmt::While { body, condition, span, countdown } => {
                let mut inner = body.clone();
                if substitute_once(root, &mut inner) {
                    block.statements[index] = Stmt::While {
                        span: *span, condition: condition.clone(), body: inner, countdown: *countdown,
                    };
                    return true;
                }
                continue;
            }
            Stmt::Block(inner) => {
                let mut nested = inner.clone();
                if substitute_once(root, &mut nested) {
                    block.statements[index] = Stmt::Block(nested);
                    return true;
                }
                continue;
            }
            Stmt::Let { value, slot, .. } => {
                let peel = peel_parens(Some(value)).unwrap();
                let substitutable = !matches!(peel, Expr::Name { .. } | Expr::IntLiteral { .. })
                    && !has_call(Some(value), &[])
                    && !may_trap(Some(value))
                    && !contains_index(value)
                    && !mentions_slot_expr(value, *slot);
                if !substitutable || count_reads_block(root, *slot) != 1 {
                    continue;
                }
                let definition = value.clone();
                let slot = *slot;
                let mut operands = BTreeSet::new();
                read_slots(value, &mut operands);
                let mut use_at = None;
                let mut blocked = false;
                for cursor in index + 1..block.statements.len() {
                    let current = &block.statements[cursor];
                    let reads = count_reads_stmt(current, slot);
                    if reads > 0 {
                        let plain = matches!(current, Stmt::Let { .. } | Stmt::Assign { .. }
                            | Stmt::Return { .. } | Stmt::ExprStmt { .. });
                        if plain && reads == 1 {
                            use_at = Some(cursor);
                        } else {
                            blocked = true;
                        }
                        break;
                    }
                    if mentions_slot_stmt(current, slot)
                        || operands.iter().any(|operand| {
                            writes_slot(std::slice::from_ref(current), *operand)
                        })
                    {
                        blocked = true;
                        break;
                    }
                }
                if blocked {
                    continue;
                }
                let Some(cursor) = use_at else { continue };
                match &mut block.statements[cursor] {
                    Stmt::Let { value, .. } | Stmt::Return { value, .. }
                    | Stmt::ExprStmt { expression: value, .. } => {
                        replace_slot_expr(value, slot, &definition);
                    }
                    Stmt::Assign { value, index, .. } => {
                        replace_slot_expr(value, slot, &definition);
                        if let Some(index) = index {
                            replace_slot_expr(index, slot, &definition);
                        }
                    }
                    _ => unreachable!(),
                }
                block.statements.remove(index);
                return true;
            }
            Stmt::For { .. } | Stmt::Assign { .. } => continue,
        }
    }
    false
}

/// Replace every read of `slot` inside `expression` with `replacement`.
fn replace_slot_expr(expression: &mut Expr, slot: i64, replacement: &Expr) {
    match expression {
        Expr::Name { slot: found, .. } if *found == slot => *expression = replacement.clone(),
        Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => {
            replace_slot_expr(inner, slot, replacement);
        }
        Expr::Binary { left, right, .. } => {
            replace_slot_expr(left, slot, replacement);
            replace_slot_expr(right, slot, replacement);
        }
        Expr::Call { arguments, .. } | Expr::ArrayLiteral { elements: arguments, .. } => {
            for argument in arguments {
                replace_slot_expr(argument, slot, replacement);
            }
        }
        Expr::Index { operand, index, .. } => {
            replace_slot_expr(operand, slot, replacement);
            replace_slot_expr(index, slot, replacement);
        }
        Expr::IntLiteral { .. } | Expr::StrLiteral { .. } | Expr::Name { .. } => {}
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
enum Signature {
    Int(i128),
    Slot(i64),
    Str(String),
    Unary(String, Box<Signature>),
    Bin(String, Box<Signature>, Box<Signature>),
    Call(String, Vec<Signature>),
    Other(Span),
}

fn signature(expression: &Expr) -> Signature {
    match peel_parens(Some(expression)).unwrap() {
        Expr::IntLiteral { value, .. } => Signature::Int(*value),
        Expr::Name { slot, .. } => Signature::Slot(*slot),
        Expr::StrLiteral { text, .. } => Signature::Str(text.clone()),
        Expr::Unary { op, operand, .. } => {
            Signature::Unary(op.clone(), Box::new(signature(operand)))
        }
        Expr::Binary { op, left, right, .. } => Signature::Bin(
            op.clone(),
            Box::new(signature(left)),
            Box::new(signature(right)),
        ),
        Expr::Call { name, arguments, .. } => {
            Signature::Call(name.clone(), arguments.iter().map(signature).collect())
        }
        other => Signature::Other(other.span()),
    }
}

#[derive(Debug, Clone)]
struct Term {
    sign: i128,
    constant: Option<i128>,
    expression: Option<Expr>,
    span: Span,
}

fn product_term(expression: &Expr, sign: i128) -> Vec<Term> {
    let span = expression.span();
    let peeled = peel_parens(Some(expression)).unwrap();
    if let Expr::Unary { op, operand, .. } = peeled {
        if op == "-" {
            return product_term(operand, wrap(-sign));
        }
    }
    if let Expr::IntLiteral { value, .. } = peeled {
        return vec![Term { sign: wrap(sign * value), constant: Some(*value), expression: None, span }];
    }
    if let Expr::Binary { op, left, right, .. } = peeled {
        if op == "*" {
            let left = peel_parens(Some(left)).unwrap();
            let right = peel_parens(Some(right)).unwrap();
            let literal = match (left, right) {
                (Expr::IntLiteral { value, .. }, other)
                    if !matches!(other, Expr::IntLiteral { .. }) => Some((*value, other)),
                (other, Expr::IntLiteral { value, .. })
                    if !matches!(other, Expr::IntLiteral { .. }) => Some((*value, other)),
                _ => None,
            };
            if let Some((literal, base)) = literal {
                return chain_terms(base, wrap(sign * literal))
                    .into_iter()
                    .map(|term| Term { span, ..term })
                    .collect();
            }
        }
    }
    vec![Term { sign, constant: None, expression: Some(peeled.clone()), span }]
}

fn chain_terms(expression: &Expr, sign: i128) -> Vec<Term> {
    let peeled = peel_parens(Some(expression)).unwrap();
    if let Expr::Binary { op, left, right, .. } = peeled {
        if op == "+" || op == "-" {
            let right_sign = if op == "+" { sign } else { wrap(-sign) };
            let mut terms = chain_terms(left, sign);
            terms.extend(chain_terms(right, right_sign));
            return terms;
        }
    }
    product_term(peeled, sign)
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
enum ChainKey {
    Const,
    Effect(Span),
    Shape(Signature),
}

fn collect_chain(expression: &Expr) -> Option<Expr> {
    let (op, _, _) = match expression {
        Expr::Binary { op, left, right, .. } if op == "+" || op == "-" => (op, left, right),
        _ => return None,
    };
    let _ = op;
    let mut order: Vec<ChainKey> = Vec::new();
    let mut table: Vec<(ChainKey, i128, Option<Expr>, Span, usize)> = Vec::new();
    for term in chain_terms(expression, 1) {
        let key = if let Some(value) = term.constant {
            if value == 0 {
                continue;
            }
            ChainKey::Const
        } else if has_call(term.expression.as_ref(), &[]) {
            ChainKey::Effect(term.expression.as_ref().unwrap().span())
        } else {
            ChainKey::Shape(signature(term.expression.as_ref().unwrap()))
        };
        let position = match table.iter().position(|entry| entry.0 == key) {
            Some(position) => position,
            None => {
                order.push(key.clone());
                table.push((key, 0, term.expression.clone(), term.span, 0));
                table.len() - 1
            }
        };
        let entry = &mut table[position];
        entry.1 = wrap(entry.1 + term.sign);
        entry.4 += 1;
    }
    if !table.iter().any(|entry| entry.0 != ChainKey::Const && entry.4 >= 2) {
        return None;
    }
    let span = expression.span();
    let mut rebuilt: Option<Expr> = None;
    for key in order {
        let (_, sign_total, base, term_span, _) =
            table.iter().find(|entry| entry.0 == key).unwrap().clone();
        let node = if key == ChainKey::Const {
            if sign_total == 0 {
                continue;
            }
            Expr::IntLiteral { span: term_span, value: sign_total }
        } else if sign_total == 0 {
            let base = base.unwrap();
            if has_call(Some(&base), &[]) || may_trap(Some(&base)) {
                Expr::Binary {
                    span: term_span,
                    op: "*".to_string(),
                    left: Box::new(Expr::IntLiteral { span: term_span, value: 0 }),
                    right: Box::new(base),
                }
            } else {
                continue;
            }
        } else if sign_total == 1 {
            base.unwrap()
        } else {
            Expr::Binary {
                span: term_span,
                op: "*".to_string(),
                left: Box::new(base.unwrap()),
                right: Box::new(Expr::IntLiteral { span: term_span, value: sign_total }),
            }
        };
        rebuilt = Some(match rebuilt {
            None => node,
            Some(previous) => Expr::Binary {
                span,
                op: "+".to_string(),
                left: Box::new(previous),
                right: Box::new(node),
            },
        });
    }
    Some(rebuilt.unwrap_or(Expr::IntLiteral { span, value: 0 }))
}

fn algebraic_expression(expression: &Expr) -> Option<Expr> {
    match expression {
        Expr::IntLiteral { .. } | Expr::Name { .. } | Expr::StrLiteral { .. } => None,
        Expr::Parens { span, inner } => algebraic_expression(inner).map(|new| Expr::Parens {
            span: *span, inner: Box::new(new),
        }),
        Expr::Unary { span, op, operand } => algebraic_expression(operand).map(|new| Expr::Unary {
            span: *span, op: op.clone(), operand: Box::new(new),
        }),
        Expr::Call { span, name, arguments } => {
            let mut changed = false;
            let mut new_arguments = Vec::new();
            for argument in arguments {
                match algebraic_expression(argument) {
                    Some(new) => {
                        new_arguments.push(new);
                        changed = true;
                    }
                    None => new_arguments.push(argument.clone()),
                }
            }
            changed.then(|| Expr::Call { span: *span, name: name.clone(), arguments: new_arguments })
        }
        Expr::Binary { span, op, left, right } => {
            let new_left = algebraic_expression(left);
            let new_right = algebraic_expression(right);
            let mut node = Expr::Binary {
                span: *span,
                op: op.clone(),
                left: Box::new(new_left.clone().unwrap_or_else(|| (**left).clone())),
                right: Box::new(new_right.clone().unwrap_or_else(|| (**right).clone())),
            };
            if op == "+" || op == "-" {
                if let Some(collected) = collect_chain(&node) {
                    return Some(collected);
                }
            }
            if new_left.is_some() || new_right.is_some() {
                node = Expr::Binary {
                    span: *span,
                    op: op.clone(),
                    left: Box::new(new_left.unwrap_or_else(|| (**left).clone())),
                    right: Box::new(new_right.unwrap_or_else(|| (**right).clone())),
                };
                Some(node)
            } else {
                None
            }
        }
        Expr::ArrayLiteral { .. } | Expr::Index { .. } => None,
    }
}

/// Expand constant coefficients over add-chains and collect like terms.
pub fn apply_algebraic_simplification(program: &mut Program) -> usize {
    let mut changed = 0;
    for function in &mut program.functions {
        changed += algebraic_block(&mut function.body);
    }
    changed
}

fn algebraic_block(block: &mut Block) -> usize {
    let mut changed = 0;
    for statement in &mut block.statements {
        match statement {
            Stmt::Let { value, .. } | Stmt::Assign { value, .. } | Stmt::Return { value, .. }
            | Stmt::ExprStmt { expression: value, .. } => {
                if let Some(new) = algebraic_expression(value) {
                    *value = new;
                    changed += 1;
                }
            }
            Stmt::If { condition, then_block, else_block, .. } => {
                if let Some(new) = algebraic_expression(condition) {
                    *condition = new;
                    changed += 1;
                }
                changed += algebraic_block(then_block);
                if let Some(otherwise) = else_block {
                    changed += algebraic_block(otherwise);
                }
            }
            Stmt::While { condition, body, .. } => {
                if let Some(new) = algebraic_expression(condition) {
                    *condition = new;
                    changed += 1;
                }
                changed += algebraic_block(body);
            }
            Stmt::Block(inner) => changed += algebraic_block(inner),
            Stmt::For { start, end, body, .. } => {
                if let Some(new) = algebraic_expression(start) {
                    *start = new;
                    changed += 1;
                }
                if let Some(new) = algebraic_expression(end) {
                    *end = new;
                    changed += 1;
                }
                changed += algebraic_block(body);
            }
            Stmt::Break { .. } | Stmt::Continue { .. } => {}
        }
    }
    changed
}

fn contains_break_or_continue(block: &Block) -> bool {
    block.statements.iter().any(|statement| match statement {
        Stmt::Break { .. } | Stmt::Continue { .. } => true,
        Stmt::If { then_block, else_block, .. } => {
            contains_break_or_continue(then_block)
                || else_block.as_ref().map_or(false, contains_break_or_continue)
        }
        Stmt::While { body, .. } | Stmt::Block(body) => contains_break_or_continue(body),
        _ => false,
    })
}

/// Remove the first `slot = slot + 1` found under a statement list.
fn remove_increment(statements: &mut Vec<Stmt>, slot: i64) -> bool {
    for index in 0..statements.len() {
        let is_increment = match &statements[index] {
            Stmt::Assign { slot: found, value, .. } if *found == slot => {
                let peeled = peel_parens(Some(value)).unwrap();
                matches!(peeled, Expr::Binary { op, left, right, .. }
                    if op == "+"
                        && matches!(peel_parens(Some(left)).unwrap(), Expr::Name { slot: found, .. } if *found == slot)
                        && matches!(peel_parens(Some(right)).unwrap(), Expr::IntLiteral { value, .. } if *value == 1))
            }
            _ => false,
        };
        if is_increment {
            statements.remove(index);
            return true;
        }
        let nested = match &mut statements[index] {
            Stmt::If { then_block, else_block, .. } => {
                remove_increment(&mut then_block.statements, slot)
                    || else_block.as_mut().map_or(false, |block| remove_increment(&mut block.statements, slot))
            }
            Stmt::While { body, .. } | Stmt::Block(body) => {
                remove_increment(&mut body.statements, slot)
            }
            _ => false,
        };
        if nested {
            return true;
        }
    }
    false
}

/// Rewrite one `while v < C` as a countdown latch, or refuse.
fn try_countdown_latch(statement: &Stmt, init: &Stmt, count: i128, trailing: &[Stmt])
    -> Option<(Stmt, Stmt)> {
    let Stmt::While { span, condition, body, countdown } = statement else {
        return None;
    };
    let binary = peel_parens(Some(condition)).unwrap();
    let (left, right) = match binary {
        Expr::Binary { op, left, right, .. } if op == "<" => (left, right),
        _ => return None,
    };
    if !matches!(peel_parens(Some(right)).unwrap(), Expr::IntLiteral { .. }) {
        return None;
    }
    let counter = peel_parens(Some(left)).unwrap();
    let slot = match counter {
        Expr::Name { slot, .. } if *slot >= 0 => *slot,
        _ => return None,
    };
    if body.statements.is_empty() || *countdown || contains_break_or_continue(body)
        || count_reads_block(body, slot) != 1
        || trailing.iter().any(|statement| mentions_slot_stmt(statement, slot))
    {
        return None;
    }
    let mut new_body = body.clone();
    if !remove_increment(&mut new_body.statements, slot) {
        return None;
    }
    let right_span = right.span();
    let new_condition = Expr::Binary {
        span: condition.span(),
        op: ">".to_string(),
        left: Box::new(counter.clone()),
        right: Box::new(Expr::IntLiteral { span: right_span, value: 0 }),
    };
    let new_while = Stmt::While {
        span: *span,
        condition: new_condition,
        body: new_body,
        countdown: true,
    };
    let mut new_init = init.clone();
    if let Stmt::Let { value, .. } | Stmt::Assign { value, .. } = &mut new_init {
        let init_span = value.span();
        *value = Expr::IntLiteral { span: init_span, value: count };
    } else {
        return None;
    }
    Some((new_while, new_init))
}

fn countdown_block(block: &mut Block, trailing: &[Stmt], parameters: &BTreeSet<i64>,
                   changed: &mut usize) {
    let mut literal_writes: BTreeMap<i64, (usize, i128)> = BTreeMap::new();
    for index in 0..block.statements.len() {
        let mut rest: Vec<Stmt> = block.statements[index + 1..].to_vec();
        rest.extend_from_slice(trailing);
        match &mut block.statements[index] {
            Stmt::Let { value, slot, .. } | Stmt::Assign { value, slot, .. } => {
                match constant(Some(value)) {
                    None => {
                        literal_writes.remove(slot);
                    }
                    Some(literal) => {
                        literal_writes.insert(*slot, (index, literal));
                    }
                }
            }
            Stmt::If { then_block, else_block, .. } => {
                countdown_block(then_block, &rest, parameters, changed);
                if let Some(otherwise) = else_block {
                    countdown_block(otherwise, &rest, parameters, changed);
                }
                for slot in literal_writes.keys().copied().collect::<Vec<_>>() {
                    if mentions_slot_block(then_block, slot)
                        || else_block.as_ref().map_or(false, |block| mentions_slot_block(block, slot))
                    {
                        literal_writes.remove(&slot);
                    }
                }
            }
            Stmt::Block(inner) => {
                countdown_block(inner, &rest, parameters, changed);
                for slot in literal_writes.keys().copied().collect::<Vec<_>>() {
                    if mentions_slot_block(inner, slot) {
                        literal_writes.remove(&slot);
                    }
                }
            }
            Stmt::While { .. } => {
                {
                    let Stmt::While { body, .. } = &mut block.statements[index] else {
                        unreachable!()
                    };
                    countdown_block(body, &rest, parameters, changed);
                }
                let current = block.statements[index].clone();
                let body_slots: Vec<i64> = match &current {
                    Stmt::While { body, .. } => literal_writes
                        .keys()
                        .copied()
                        .filter(|slot| mentions_slot_block(body, *slot))
                        .collect(),
                    _ => Vec::new(),
                };
                if let Stmt::While { condition, .. } = &current {
                    let binary = peel_parens(Some(condition)).unwrap();
                    if let Expr::Binary { op, left, right, .. } = binary {
                        if op == "<" {
                            if let Expr::Name { slot, .. } = peel_parens(Some(left)).unwrap() {
                                if !parameters.contains(slot) {
                                    if let Some((init_index, initial)) = literal_writes.get(slot).copied() {
                                        if let Some(bound) = constant(Some(right)) {
                                            let count = wrap(bound - initial);
                                            if let Some((new_while, new_init)) = try_countdown_latch(
                                                &current,
                                                &block.statements[init_index],
                                                count,
                                                &rest,
                                            ) {
                                                block.statements[index] = new_while;
                                                block.statements[init_index] = new_init;
                                                *changed += 1;
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                for slot in body_slots {
                    literal_writes.remove(&slot);
                }
            }
            Stmt::For { .. } | Stmt::Return { .. } | Stmt::ExprStmt { .. }
            | Stmt::Break { .. } | Stmt::Continue { .. } => {}
        }
    }
}

/// Convert `while v < C` loops with a dead counter into countdown latches.
pub fn apply_countdown_latches(program: &mut Program) -> usize {
    let mut changed = 0;
    for function in &mut program.functions {
        let parameters: BTreeSet<i64> = function.parameters.iter().map(|p| p.slot).collect();
        countdown_block(&mut function.body, &[], &parameters, &mut changed);
    }
    changed
}

const UNROLL_TRIP_LIMIT: usize = 16;
const UNROLL_BODY_STATEMENTS: usize = 8;

#[derive(Debug, Clone, Copy)]
enum Step {
    Add(i128),
    Mul(i128),
    Shl(i128),
}

impl Step {
    fn apply(self, current: i128) -> i128 {
        match self {
            Step::Add(value) => wrap(current + value),
            Step::Mul(value) => wrap(current * value),
            Step::Shl(value) => wrap(current << (value & 63)),
        }
    }
}

fn counter_step(statement: &Stmt, slot: i64) -> Option<Step> {
    let Stmt::Assign { slot: found, value, index: None, .. } = statement else {
        return None;
    };
    if *found != slot {
        return None;
    }
    let Expr::Binary { op, left, right, .. } = peel_parens(Some(value)).unwrap() else {
        return None;
    };
    let Expr::Name { slot: left_slot, .. } = peel_parens(Some(left)).unwrap() else {
        return None;
    };
    if *left_slot != slot {
        return None;
    }
    let Expr::IntLiteral { value: constant, .. } = peel_parens(Some(right)).unwrap() else {
        return None;
    };
    match op.as_str() {
        "+" => Some(Step::Add(*constant)),
        "*" => Some(Step::Mul(*constant)),
        "<<" => Some(Step::Shl(*constant)),
        _ => None,
    }
}

fn counter_bound(expression: &Expr) -> Option<(i128, bool)> {
    let Expr::Binary { op, left, right, .. } = peel_parens(Some(expression)).unwrap() else {
        return None;
    };
    if op != "<" && op != "<=" {
        return None;
    }
    if !matches!(peel_parens(Some(left)).unwrap(), Expr::Name { .. }) {
        return None;
    }
    let Expr::IntLiteral { value, .. } = peel_parens(Some(right)).unwrap() else {
        return None;
    };
    Some((*value, op == "<="))
}

fn writes_slot(statements: &[Stmt], slot: i64) -> bool {
    statements.iter().any(|statement| match statement {
        Stmt::Assign { slot: found, .. } | Stmt::Let { slot: found, .. } => *found == slot,
        Stmt::If { then_block, else_block, .. } => {
            writes_slot(&then_block.statements, slot)
                || else_block.as_ref().map_or(false, |block| writes_slot(&block.statements, slot))
        }
        Stmt::While { body, .. } => writes_slot(&body.statements, slot),
        _ => false,
    })
}

fn calls_named(expression: &Expr, name: &str) -> bool {
    match expression {
        Expr::Call { name: found, arguments, .. } => {
            found == name || arguments.iter().any(|argument| calls_named(argument, name))
        }
        Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => {
            calls_named(inner, name)
        }
        Expr::Binary { left, right, .. } => calls_named(left, name) || calls_named(right, name),
        Expr::Index { operand, index, .. } => {
            calls_named(operand, name) || calls_named(index, name)
        }
        Expr::ArrayLiteral { elements, .. } => elements.iter().any(|element| calls_named(element, name)),
        _ => false,
    }
}

fn calls_self(statements: &[Stmt], name: &str) -> bool {
    statements.iter().any(|statement| match statement {
        Stmt::ExprStmt { expression, .. } => calls_named(expression, name),
        Stmt::Let { value, .. } | Stmt::Return { value, .. } => calls_named(value, name),
        Stmt::Assign { value, index, .. } => {
            calls_named(value, name) || index.as_ref().map_or(false, |index| calls_named(index, name))
        }
        Stmt::If { condition, then_block, else_block, .. } => {
            calls_named(condition, name)
                || calls_self(&then_block.statements, name)
                || else_block.as_ref().map_or(false, |block| calls_self(&block.statements, name))
        }
        Stmt::While { condition, body, .. } => {
            calls_named(condition, name) || calls_self(&body.statements, name)
        }
        _ => false,
    })
}

fn jumps(statements: &[Stmt]) -> bool {
    statements.iter().any(|statement| match statement {
        Stmt::Break { .. } | Stmt::Continue { .. } => true,
        Stmt::If { then_block, else_block, .. } => {
            jumps(&then_block.statements)
                || else_block.as_ref().map_or(false, |block| jumps(&block.statements))
        }
        Stmt::While { body, .. } => jumps(&body.statements),
        _ => false,
    })
}

fn clone_with_literal(expression: &Expr, slot: i64, value: i128) -> Expr {
    match expression {
        Expr::Name { span, name, slot: found } if *found == slot => {
            Expr::IntLiteral { span: *span, value }
        }
        Expr::Name { span, name, slot } => Expr::Name {
            span: *span, name: name.clone(), slot: *slot,
        },
        Expr::IntLiteral { span, value } => Expr::IntLiteral { span: *span, value: *value },
        Expr::StrLiteral { span, text, label } => Expr::StrLiteral {
            span: *span, text: text.clone(), label: label.clone(),
        },
        Expr::Parens { span, inner } => Expr::Parens {
            span: *span,
            inner: Box::new(clone_with_literal(inner, slot, value)),
        },
        Expr::Unary { span, op, operand } => Expr::Unary {
            span: *span,
            op: op.clone(),
            operand: Box::new(clone_with_literal(operand, slot, value)),
        },
        Expr::Binary { span, op, left, right } => Expr::Binary {
            span: *span,
            op: op.clone(),
            left: Box::new(clone_with_literal(left, slot, value)),
            right: Box::new(clone_with_literal(right, slot, value)),
        },
        Expr::Call { span, name, arguments } => Expr::Call {
            span: *span,
            name: name.clone(),
            arguments: arguments.iter().map(|argument| clone_with_literal(argument, slot, value)).collect(),
        },
        Expr::Index { span, operand, index, slot: base, size } => Expr::Index {
            span: *span,
            operand: Box::new(clone_with_literal(operand, slot, value)),
            index: Box::new(clone_with_literal(index, slot, value)),
            slot: *base,
            size: *size,
        },
        Expr::ArrayLiteral { span, elements } => Expr::ArrayLiteral {
            span: *span,
            elements: elements.iter().map(|element| clone_with_literal(element, slot, value)).collect(),
        },
    }
}

fn clone_statement(statement: &Stmt, slot: i64, value: i128) -> Stmt {
    match statement {
        Stmt::Let { span, name, mutable, type_name, value: expr, slot: found, array_size } =>
            Stmt::Let {
                span: *span, name: name.clone(), mutable: *mutable, type_name: type_name.clone(),
                value: clone_with_literal(expr, slot, value), slot: *found, array_size: *array_size,
            },
        Stmt::Assign { span, name, value: expr, slot: found, index, temp_slot, array_size } =>
            Stmt::Assign {
                span: *span, name: name.clone(), value: clone_with_literal(expr, slot, value),
                slot: *found,
                index: index.as_ref().map(|index| clone_with_literal(index, slot, value)),
                temp_slot: *temp_slot, array_size: *array_size,
            },
        Stmt::Return { span, value: expr } => Stmt::Return {
            span: *span, value: clone_with_literal(expr, slot, value),
        },
        Stmt::ExprStmt { span, expression } => Stmt::ExprStmt {
            span: *span, expression: clone_with_literal(expression, slot, value),
        },
        Stmt::If { span, condition, then_block, else_block } => Stmt::If {
            span: *span,
            condition: clone_with_literal(condition, slot, value),
            then_block: clone_block(then_block, slot, value),
            else_block: else_block.as_ref().map(|block| clone_block(block, slot, value)),
        },
        Stmt::While { span, condition, body, countdown } => Stmt::While {
            span: *span,
            condition: clone_with_literal(condition, slot, value),
            body: clone_block(body, slot, value),
            countdown: *countdown,
        },
        Stmt::Block(block) => Stmt::Block(clone_block(block, slot, value)),
        Stmt::For { .. } | Stmt::Break { .. } | Stmt::Continue { .. } => statement.clone(),
    }
}

fn clone_block(block: &Block, slot: i64, value: i128) -> Block {
    Block {
        span: block.span,
        statements: block.statements.iter()
            .map(|statement| clone_statement(statement, slot, value))
            .collect(),
    }
}

fn unrolled_loop(statement: &Stmt, loop_statement: Option<&Stmt>, function_name: &str)
    -> Option<Vec<Stmt>> {
    let Stmt::While { span: loop_span, condition, body, .. } = loop_statement? else {
        return None;
    };
    if body.statements.is_empty() {
        return None;
    }
    let (Stmt::Let { value, slot, name, .. } | Stmt::Assign { value, slot, name, .. }) = statement else {
        return None;
    };
    let Expr::IntLiteral { value: initial, .. } = peel_parens(Some(value)).unwrap() else {
        return None;
    };
    if body.statements.len() > UNROLL_BODY_STATEMENTS || jumps(&body.statements)
        || !calls_self(&body.statements, function_name)
    {
        return None;
    }
    let step = counter_step(body.statements.last().unwrap(), *slot)?;
    let (limit, inclusive) = counter_bound(condition)?;
    if writes_slot(&body.statements[..body.statements.len() - 1], *slot) {
        return None;
    }
    let mut values = Vec::new();
    let mut current = *initial;
    while if inclusive { current <= limit } else { current < limit } {
        values.push(current);
        if values.len() > UNROLL_TRIP_LIMIT {
            return None;
        }
        current = step.apply(current);
    }
    let mut steps = vec![statement.clone()];
    for counter in values {
        for body_statement in &body.statements[..body.statements.len() - 1] {
            steps.push(clone_statement(body_statement, *slot, counter));
        }
    }
    steps.push(Stmt::Assign {
        span: *loop_span,
        name: name.clone(),
        value: Expr::IntLiteral { span: *loop_span, value: current },
        slot: *slot,
        index: None,
        temp_slot: None,
        array_size: None,
    });
    Some(steps)
}

/// Unroll a short constant-trip while into clones with literal counter values.
pub fn apply_constant_loop_unrolling(program: &mut Program) -> usize {
    let mut unrolled = 0;
    for function in &mut program.functions {
        let name = function.name.clone();
        unrolled += unroll_block(&mut function.body, &name);
    }
    unrolled
}

fn unroll_block(block: &mut Block, function_name: &str) -> usize {
    let mut unrolled = 0;
    for statement in &mut block.statements {
        match statement {
            Stmt::If { then_block, else_block, .. } => {
                unrolled += unroll_block(then_block, function_name);
                if let Some(otherwise) = else_block {
                    unrolled += unroll_block(otherwise, function_name);
                }
            }
            Stmt::While { body, .. } => unrolled += unroll_block(body, function_name),
            _ => {}
        }
    }
    let mut statements = Vec::new();
    let original = std::mem::take(&mut block.statements);
    let mut index = 0;
    while index < original.len() {
        let following = original.get(index + 1);
        match unrolled_loop(&original[index], following, function_name) {
            Some(replacement) => {
                statements.extend(replacement);
                index += 2;
                unrolled += 1;
            }
            None => {
                statements.push(original[index].clone());
                index += 1;
            }
        }
    }
    block.statements = statements;
    unrolled
}

const INLINE_EXPANSION_NODES: usize = 512;
const INLINE_ROUNDS: usize = 8;

fn node_count(expression: &Expr) -> usize {
    match expression {
        Expr::IntLiteral { .. } | Expr::Name { .. } | Expr::StrLiteral { .. } => 1,
        Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => {
            1 + node_count(inner)
        }
        Expr::Binary { left, right, .. } | Expr::Index { operand: left, index: right, .. } => {
            1 + node_count(left) + node_count(right)
        }
        Expr::Call { arguments, .. } | Expr::ArrayLiteral { elements: arguments, .. } => {
            1 + arguments.iter().map(node_count).sum::<usize>()
        }
    }
}

type InlineBodies = BTreeMap<String, (Vec<crate::syntax::Parameter>, Expr)>;

fn single_return_bodies(program: &Program) -> InlineBodies {
    let mut bodies = InlineBodies::new();
    for function in &program.functions {
        if function.body.statements.len() == 1 {
            if let Stmt::Return { value, .. } = &function.body.statements[0] {
                if node_count(value) <= INLINE_EXPANSION_NODES && !contains_index(value) {
                    bodies.insert(
                        function.name.clone(),
                        (function.parameters.clone(), value.clone()),
                    );
                }
            }
        }
    }
    bodies
}

fn substitute_parameters(expression: &Expr, substitutions: &BTreeMap<i64, Expr>) -> Expr {
    match expression {
        Expr::Name { slot, .. } if substitutions.contains_key(slot) => {
            substitutions[slot].clone()
        }
        Expr::Parens { span, inner } => Expr::Parens {
            span: *span,
            inner: Box::new(substitute_parameters(inner, substitutions)),
        },
        Expr::Unary { span, op, operand } => Expr::Unary {
            span: *span,
            op: op.clone(),
            operand: Box::new(substitute_parameters(operand, substitutions)),
        },
        Expr::Binary { span, op, left, right } => Expr::Binary {
            span: *span,
            op: op.clone(),
            left: Box::new(substitute_parameters(left, substitutions)),
            right: Box::new(substitute_parameters(right, substitutions)),
        },
        Expr::Call { span, name, arguments } => Expr::Call {
            span: *span,
            name: name.clone(),
            arguments: arguments.iter()
                .map(|argument| substitute_parameters(argument, substitutions))
                .collect(),
        },
        Expr::Index { span, operand, index, slot, size } => Expr::Index {
            span: *span,
            operand: Box::new(substitute_parameters(operand, substitutions)),
            index: Box::new(substitute_parameters(index, substitutions)),
            slot: *slot,
            size: *size,
        },
        other => other.clone(),
    }
}

fn calls_in(expression: &Expr, found: &mut Vec<String>) {
    if let Expr::Call { name, arguments, .. } = expression {
        found.push(name.clone());
        for argument in arguments {
            calls_in(argument, found);
        }
        return;
    }
    match expression {
        Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => calls_in(inner, found),
        Expr::Binary { left, right, .. } => {
            calls_in(left, found);
            calls_in(right, found);
        }
        Expr::Index { operand, index, .. } => {
            calls_in(operand, found);
            calls_in(index, found);
        }
        Expr::ArrayLiteral { elements, .. } => {
            for element in elements {
                calls_in(element, found);
            }
        }
        _ => {}
    }
}

fn inlined_body(name: &str, arguments: &[Expr], bodies: &InlineBodies,
                active: &BTreeSet<String>) -> Option<Expr> {
    let (parameters, value) = bodies.get(name)?;
    let mut substitutions = BTreeMap::new();
    for (parameter, argument) in parameters.iter().zip(arguments.iter()) {
        let count = count_reads_expr(value, parameter.slot);
        if count == 0 && (has_call(Some(argument), &[]) || may_trap(Some(argument))) {
            return None;
        }
        if count > 1 && has_call(Some(argument), &[]) {
            return None;
        }
        if count > 0 {
            substitutions.insert(parameter.slot, argument.clone());
        }
    }
    if substitutions.is_empty() {
        return None;
    }
    let substituted = substitute_parameters(value, &substitutions);
    if node_count(&substituted) > INLINE_EXPANSION_NODES * 2 {
        return None;
    }
    let mut calls = Vec::new();
    calls_in(&substituted, &mut calls);
    if calls.iter().any(|call| bodies.contains_key(call) && active.contains(call)) {
        return None;
    }
    Some(substituted)
}

fn inline_expression(expression: &Expr, bodies: &InlineBodies,
                     active: &BTreeSet<String>) -> Option<Expr> {
    match expression {
        Expr::Call { span, name, arguments } => {
            let mut changed = false;
            let mut new_arguments = Vec::new();
            for argument in arguments {
                match inline_expression(argument, bodies, active) {
                    Some(new) => {
                        new_arguments.push(new);
                        changed = true;
                    }
                    None => new_arguments.push(argument.clone()),
                }
            }
            if bodies.contains_key(name) && !active.contains(name) {
                if let Some(substituted) = inlined_body(name, &new_arguments, bodies, active) {
                    return Some(substituted);
                }
            }
            changed.then(|| Expr::Call {
                span: *span, name: name.clone(), arguments: new_arguments,
            })
        }
        Expr::Parens { span, inner } => inline_expression(inner, bodies, active).map(|new| {
            Expr::Parens { span: *span, inner: Box::new(new) }
        }),
        Expr::Unary { span, op, operand } => inline_expression(operand, bodies, active).map(|new| {
            Expr::Unary { span: *span, op: op.clone(), operand: Box::new(new) }
        }),
        Expr::Binary { span, op, left, right } => {
            let new_left = inline_expression(left, bodies, active);
            let new_right = inline_expression(right, bodies, active);
            if new_left.is_none() && new_right.is_none() {
                None
            } else {
                Some(Expr::Binary {
                    span: *span,
                    op: op.clone(),
                    left: Box::new(new_left.unwrap_or_else(|| (**left).clone())),
                    right: Box::new(new_right.unwrap_or_else(|| (**right).clone())),
                })
            }
        }
        _ => None,
    }
}

fn inline_attribute(expression: &mut Expr, bodies: &InlineBodies,
                    active: &BTreeSet<String>) -> usize {
    match inline_expression(expression, bodies, active) {
        Some(new) => {
            *expression = new;
            1
        }
        None => 0,
    }
}

fn inline_statements(block: &mut Block, bodies: &InlineBodies,
                     active: &BTreeSet<String>) -> usize {
    let mut inlined = 0;
    for statement in &mut block.statements {
        match statement {
            Stmt::If { condition, then_block, else_block, .. } => {
                inlined += inline_attribute(condition, bodies, active);
                inlined += inline_statements(then_block, bodies, active);
                if let Some(otherwise) = else_block {
                    inlined += inline_statements(otherwise, bodies, active);
                }
            }
            Stmt::While { condition, body, .. } => {
                inlined += inline_attribute(condition, bodies, active);
                inlined += inline_statements(body, bodies, active);
            }
            Stmt::Block(inner) => inlined += inline_statements(inner, bodies, active),
            Stmt::For { start, end, body, .. } => {
                inlined += inline_attribute(start, bodies, active);
                inlined += inline_attribute(end, bodies, active);
                inlined += inline_statements(body, bodies, active);
            }
            Stmt::Let { value, .. } | Stmt::Assign { value, .. } | Stmt::Return { value, .. } => {
                inlined += inline_attribute(value, bodies, active);
            }
            Stmt::ExprStmt { expression, .. } => {
                inlined += inline_attribute(expression, bodies, active);
            }
            Stmt::Break { .. } | Stmt::Continue { .. } => {}
        }
    }
    inlined
}

/// Inline every call to a single-`return` function, substituting its arguments.
pub fn apply_pure_inlining(program: &mut Program) -> usize {
    let bodies = single_return_bodies(program);
    if bodies.is_empty() {
        return 0;
    }
    let mut inlined = 0;
    for _ in 0..INLINE_ROUNDS {
        let mut changed = 0;
        for function in &mut program.functions {
            changed += inline_statements(&mut function.body, &bodies, &BTreeSet::new());
        }
        if changed == 0 {
            break;
        }
        inlined += 1;
    }
    inlined
}

fn slot_of_name(expression: Option<&Expr>) -> Option<i64> {
    match peel_parens(expression).unwrap() {
        Expr::Name { slot, .. } => Some(*slot),
        _ => None,
    }
}

fn is_plus_one(statement: &Stmt) -> bool {
    let Stmt::Assign { slot, value, .. } = statement else {
        return false;
    };
    if *slot < 0 {
        return false;
    }
    let Expr::Binary { op, left, right, .. } = peel_parens(Some(value)).unwrap() else {
        return false;
    };
    if op != "+" {
        return false;
    }
    let left = peel_parens(Some(left)).unwrap();
    let right = peel_parens(Some(right)).unwrap();
    let (left, right) = if matches!(right, Expr::IntLiteral { value, .. } if *value == 1) {
        (right, left)
    } else {
        (left, right)
    };
    matches!(left, Expr::IntLiteral { value, .. } if *value == 1)
        && matches!(right, Expr::Name { slot: found, .. } if *found == *slot)
}

fn scaled_odd_term(value: Option<&Expr>, slot: i64) -> Option<i128> {
    let Expr::Binary { op, left, right, .. } = peel_parens(value).unwrap() else {
        return None;
    };
    if op != "*" {
        return None;
    }
    let left = peel_parens(Some(left)).unwrap();
    let right = peel_parens(Some(right)).unwrap();
    if let (Expr::Name { slot: found, .. }, Expr::IntLiteral { value, .. }) = (left, right) {
        if *found == slot && value % 2 != 0 {
            return Some(*value);
        }
    }
    if let (Expr::IntLiteral { value, .. }, Expr::Name { slot: found, .. }) = (left, right) {
        if *found == slot && value % 2 != 0 {
            return Some(*value);
        }
    }
    None
}

fn odd_step_value(value: Option<&Expr>, slot: i64) -> Option<(i128, i128)> {
    let Expr::Binary { op, left, right, .. } = peel_parens(value).unwrap() else {
        return None;
    };
    if op != "+" {
        return None;
    }
    for (term, addend) in [
        (peel_parens(Some(left)).unwrap(), peel_parens(Some(right)).unwrap()),
        (peel_parens(Some(right)).unwrap(), peel_parens(Some(left)).unwrap()),
    ] {
        if let Some(multiplier) = scaled_odd_term(Some(term), slot) {
            if let Expr::IntLiteral { value, .. } = addend {
                if value % 2 != 0 {
                    return Some((multiplier, *value));
                }
            }
        }
    }
    None
}

fn halving_value(value: Option<&Expr>, slot: i64) -> bool {
    let Expr::Binary { op, left, right, .. } = peel_parens(value).unwrap() else {
        return false;
    };
    if op != "/" {
        return false;
    }
    let right = peel_parens(Some(right)).unwrap();
    slot_of_name(Some(left)) == Some(slot)
        && matches!(right, Expr::IntLiteral { value, .. } if *value == 2)
}

fn fuse_odd_pair(statement: &mut Stmt) -> bool {
    let Stmt::While { countdown, body, .. } = statement else {
        return false;
    };
    if *countdown || contains_break_or_continue(body) {
        return false;
    }
    for index in 0..body.statements.len().saturating_sub(1) {
        let (branch, increment) = {
            let branch = &body.statements[index];
            let increment = &body.statements[index + 1];
            (branch.clone(), increment.clone())
        };
        let Stmt::If { condition, then_block, else_block, .. } = &branch else {
            continue;
        };
        let Some(otherwise) = else_block else { continue };
        if !is_plus_one(&increment) {
            continue;
        }
        let condition = peel_parens(Some(condition)).unwrap();
        let Expr::Binary { op, left, right, .. } = condition else { continue };
        if op != "==" && op != "!=" {
            continue;
        }
        let zero = peel_parens(Some(right)).unwrap();
        if !matches!(zero, Expr::IntLiteral { value, .. } if *value == 0) {
            continue;
        }
        let parity = peel_parens(Some(left)).unwrap();
        let Expr::Binary { op: parity_op, left: dividend, right: modulus, .. } = parity else {
            continue;
        };
        if parity_op != "%" {
            continue;
        }
        let modulus = peel_parens(Some(modulus)).unwrap();
        if !matches!(modulus, Expr::IntLiteral { value, .. } if *value == 2) {
            continue;
        }
        let slot = match slot_of_name(Some(dividend)) {
            Some(slot) if slot >= 0 => slot,
            _ => continue,
        };
        if then_block.statements.len() != 1 || otherwise.statements.len() != 1 {
            continue;
        }
        let mut even_side = then_block.statements[0].clone();
        let mut odd_side = otherwise.statements[0].clone();
        if op == "!=" {
            std::mem::swap(&mut even_side, &mut odd_side);
        }
        let Stmt::Assign { slot: even_slot, value: even_value, .. } = &even_side else {
            continue;
        };
        if *even_slot != slot || !halving_value(Some(even_value), slot) {
            continue;
        }
        let Stmt::Assign { slot: odd_slot, name, value: odd_value, span: odd_span, .. } = &odd_side
        else {
            continue;
        };
        if *odd_slot != slot || odd_step_value(Some(odd_value), slot).is_none() {
            continue;
        }
        let fused = Stmt::Assign {
            span: *odd_span,
            name: name.clone(),
            slot,
            value: Expr::Binary {
                span: odd_value.span(),
                op: "/".to_string(),
                left: Box::new(odd_value.clone()),
                right: Box::new(Expr::IntLiteral { span: zero.span(), value: 2 }),
            },
            index: None,
            temp_slot: None,
            array_size: None,
        };
        let counted = |level: i128| Stmt::Assign {
            span: increment.span(),
            name: match &increment {
                Stmt::Assign { name, .. } => name.clone(),
                _ => unreachable!(),
            },
            value: Expr::Binary {
                span: match &increment {
                    Stmt::Assign { value, .. } => value.span(),
                    _ => unreachable!(),
                },
                op: "+".to_string(),
                left: Box::new(Expr::Name {
                    span: increment.span(),
                    name: match &increment {
                        Stmt::Assign { name, .. } => name.clone(),
                        _ => unreachable!(),
                    },
                    slot: match &increment {
                        Stmt::Assign { slot, .. } => *slot,
                        _ => unreachable!(),
                    },
                }),
                right: Box::new(Expr::IntLiteral { span: increment.span(), value: level }),
            },
            slot: match &increment {
                Stmt::Assign { slot, .. } => *slot,
                _ => unreachable!(),
            },
            index: None,
            temp_slot: None,
            array_size: None,
        };
        let Stmt::If { then_block: target_then, else_block: target_else, .. } =
            &mut body.statements[index]
        else {
            unreachable!();
        };
        if op == "==" {
            target_then.statements = vec![even_side, counted(1)];
            target_else.as_mut().unwrap().statements = vec![fused, counted(2)];
        } else {
            target_then.statements = vec![fused, counted(2)];
            target_else.as_mut().unwrap().statements = vec![even_side, counted(1)];
        }
        body.statements.remove(index + 1);
        return true;
    }
    false
}

/// Fuse collatz-shaped odd steps with the halving that follows them.
pub fn apply_odd_pair_fusion(program: &mut Program) -> usize {
    fn visit(block: &mut Block) -> usize {
        let mut changed = 0;
        for statement in &mut block.statements {
            match statement {
                Stmt::While { body, .. } => {
                    changed += visit(body);
                    if fuse_odd_pair(statement) {
                        changed += 1;
                    }
                }
                Stmt::If { then_block, else_block, .. } => {
                    changed += visit(then_block);
                    if let Some(otherwise) = else_block {
                        changed += visit(otherwise);
                    }
                }
                Stmt::Block(inner) => changed += visit(inner),
                _ => {}
            }
        }
        changed
    }
    let mut changed = 0;
    for function in &mut program.functions {
        changed += visit(&mut function.body);
    }
    changed
}

fn shift_lowbit_clear(expression: &Expr, slot: i64) -> bool {
    let Expr::Binary { op, left, right, .. } = peel_parens(Some(expression)).unwrap() else {
        return false;
    };
    if op != "==" {
        return false;
    }
    let left = peel_parens(Some(left)).unwrap();
    let right = peel_parens(Some(right)).unwrap();
    let target = if matches!(right, Expr::IntLiteral { value, .. } if *value == 0) {
        left
    } else if matches!(left, Expr::IntLiteral { value, .. } if *value == 0) {
        right
    } else {
        return false;
    };
    let Expr::Binary { op, left, right, .. } = peel_parens(Some(target)).unwrap() else {
        return false;
    };
    if op != "&" {
        return false;
    }
    let first = peel_parens(Some(left)).unwrap();
    let second = peel_parens(Some(right)).unwrap();
    (matches!(first, Expr::Name { slot: found, .. } if *found == slot)
        && matches!(second, Expr::IntLiteral { value, .. } if *value == 1))
        || (matches!(second, Expr::Name { slot: found, .. } if *found == slot)
            && matches!(first, Expr::IntLiteral { value, .. } if *value == 1))
}

fn is_shift_loop(statement: &Stmt, slot: i64) -> bool {
    let Stmt::While { condition, body, countdown, .. } = statement else {
        return false;
    };
    if *countdown || !shift_lowbit_clear(condition, slot) || body.statements.len() != 1 {
        return false;
    }
    let Stmt::Assign { slot: found, value, index: None, temp_slot: None, .. } =
        &body.statements[0] else {
        return false;
    };
    if *found != slot {
        return false;
    }
    let Expr::Binary { op, left, right, .. } = peel_parens(Some(value)).unwrap() else {
        return false;
    };
    if op != ">>" {
        return false;
    }
    slot_of_name(Some(left)) == Some(slot)
        && matches!(peel_parens(Some(right)).unwrap(), Expr::IntLiteral { value, .. } if *value == 1)
}

fn zero_comparison(expression: &Expr, slot: i64, op: &str) -> bool {
    let Expr::Binary { op: found, left, right, .. } = peel_parens(Some(expression)).unwrap() else {
        return false;
    };
    if found != op {
        return false;
    }
    let left = peel_parens(Some(left)).unwrap();
    let right = peel_parens(Some(right)).unwrap();
    (matches!(left, Expr::Name { slot: found, .. } if *found == slot)
        && matches!(right, Expr::IntLiteral { value, .. } if *value == 0))
        || (matches!(right, Expr::Name { slot: found, .. } if *found == slot)
            && matches!(left, Expr::IntLiteral { value, .. } if *value == 0))
}

fn count_break(statements: &[Stmt]) -> usize {
    statements.iter().map(|statement| match statement {
        Stmt::Break { .. } => 1,
        Stmt::If { then_block, else_block, .. } => {
            count_break(&then_block.statements)
                + else_block.as_ref().map_or(0, |block| count_break(&block.statements))
        }
        Stmt::While { body, .. } | Stmt::Block(body) => count_break(&body.statements),
        _ => 0,
    }).sum()
}

fn count_continue_return(statements: &[Stmt]) -> usize {
    statements.iter().map(|statement| match statement {
        Stmt::Continue { .. } | Stmt::Return { .. } => 1,
        Stmt::If { then_block, else_block, .. } => {
            count_continue_return(&then_block.statements)
                + else_block.as_ref().map_or(0, |block| count_continue_return(&block.statements))
        }
        Stmt::While { body, .. } | Stmt::Block(body) => count_continue_return(&body.statements),
        _ => 0,
    }).sum()
}

/// Rotate a guarded bottom-test loop so its redundant condition re-test disappears.
pub fn apply_bottom_test_rotation(program: &mut Program) -> usize {
    let mut rotated = 0;
    for function in &mut program.functions {
        let statements = &mut function.body.statements;
        let mut index = 1;
        while index < statements.len() {
            let is_candidate = matches!(&statements[index], Stmt::While { countdown: false, .. });
            if !is_candidate {
                index += 1;
                continue;
            }
            let cloned = statements[index].clone();
            let Stmt::While { ref condition, ref body, .. } = cloned else {
                unreachable!()
            };
            let Expr::Binary { op, left, right, .. } = peel_parens(Some(condition)).unwrap() else {
                index += 1;
                continue;
            };
            if op != "!=" {
                index += 1;
                continue;
            }
            let left = peel_parens(Some(left)).unwrap();
            let right = peel_parens(Some(right)).unwrap();
            let slot = if let (Expr::Name { slot, .. }, Expr::IntLiteral { value, .. }) = (left, right) {
                if *value == 0 { *slot } else { index += 1; continue }
            } else if let (Expr::IntLiteral { value, .. }, Expr::Name { slot, .. }) = (left, right) {
                if *value == 0 { *slot } else { index += 1; continue }
            } else {
                index += 1;
                continue;
            };
            if !is_shift_loop(&statements[index - 1], slot)
                || body.statements.len() < 3
                || !is_shift_loop(body.statements.last().unwrap(), slot)
            {
                index += 1;
                continue;
            }
            let prefix = &body.statements[..body.statements.len() - 1];
            let guards = prefix.iter().enumerate().filter(|(_, statement)| {
                if let Stmt::If { condition, then_block, else_block, .. } = statement {
                    zero_comparison(condition, slot, "==")
                        && else_block.is_none()
                        && then_block.statements.len() == 1
                        && matches!(then_block.statements[0], Stmt::Break { .. })
                } else {
                    false
                }
            }).count();
            if guards != 1 || count_continue_return(prefix) > 0
                || count_break(&body.statements) != 1
            {
                index += 1;
                continue;
            }
            let trailing = body.statements.last().unwrap().clone();
            let mut new_body = vec![trailing];
            new_body.extend_from_slice(prefix);
            let span = condition.span();
            if let Stmt::While { body: target_body, condition: target_condition, .. } =
                &mut statements[index]
            {
                target_body.statements = new_body;
                *target_condition = Expr::IntLiteral { span, value: 1 };
            }
            statements.remove(index - 1);
            rotated += 1;
        }
    }
    rotated
}

fn linear_product(expression: &Expr, slot: i64) -> Option<i128> {
    let Expr::Binary { op, left, right, .. } = peel_parens(Some(expression)).unwrap() else {
        return None;
    };
    if op != "*" {
        return None;
    }
    let left = peel_parens(Some(left)).unwrap();
    let right = peel_parens(Some(right)).unwrap();
    if let (Expr::Name { slot: found, .. }, Expr::IntLiteral { value, .. }) = (left, right) {
        if *found == slot {
            return Some(*value);
        }
    }
    if let (Expr::IntLiteral { value, .. }, Expr::Name { slot: found, .. }) = (left, right) {
        if *found == slot {
            return Some(*value);
        }
    }
    None
}

fn linear_in_index(expression: &Expr, slot: i64) -> Option<(i128, i128)> {
    let value = peel_parens(Some(expression)).unwrap();
    let Expr::Binary { op, left, right, .. } = value else {
        return None;
    };
    let left = peel_parens(Some(left)).unwrap();
    let right = peel_parens(Some(right)).unwrap();
    if op == "+" {
        if let Expr::IntLiteral { value, .. } = right {
            if let Some(multiplier) = linear_product(left, slot) {
                return Some((multiplier, *value));
            }
        }
        if let Expr::IntLiteral { value, .. } = left {
            if let Some(multiplier) = linear_product(right, slot) {
                return Some((multiplier, *value));
            }
        }
    }
    if op == "*" {
        if let Some(multiplier) = linear_product(value, slot) {
            return Some((multiplier, 0));
        }
    }
    None
}

type LinearReplacements = BTreeMap<(i128, i128), i64>;

fn rewrite_linear(expression: &Expr, slot: i64, replacements: &LinearReplacements)
    -> (Expr, usize) {
    if let Expr::Parens { span, inner } = expression {
        let (new_inner, count) = rewrite_linear(inner, slot, replacements);
        return (Expr::Parens { span: *span, inner: Box::new(new_inner) }, count);
    }
    if let Some(term) = linear_in_index(expression, slot) {
        if let Some(induction_slot) = replacements.get(&term) {
            return (Expr::Name {
                span: expression.span(),
                name: "__induction".to_string(),
                slot: *induction_slot,
            }, 1);
        }
    }
    match expression {
        Expr::Binary { span, op, left, right } => {
            let (new_left, first) = rewrite_linear(left, slot, replacements);
            let (new_right, second) = rewrite_linear(right, slot, replacements);
            if first + second > 0 {
                (Expr::Binary {
                    span: *span, op: op.clone(),
                    left: Box::new(new_left), right: Box::new(new_right),
                }, first + second)
            } else {
                (expression.clone(), 0)
            }
        }
        Expr::Unary { span, op, operand } => {
            let (new_operand, count) = rewrite_linear(operand, slot, replacements);
            if count > 0 {
                (Expr::Unary { span: *span, op: op.clone(), operand: Box::new(new_operand) }, count)
            } else {
                (expression.clone(), 0)
            }
        }
        Expr::Call { span, name, arguments } => {
            let mut new_arguments = Vec::new();
            let mut total = 0;
            for argument in arguments {
                let (new_argument, count) = rewrite_linear(argument, slot, replacements);
                new_arguments.push(new_argument);
                total += count;
            }
            if total > 0 {
                (Expr::Call { span: *span, name: name.clone(), arguments: new_arguments }, total)
            } else {
                (expression.clone(), 0)
            }
        }
        Expr::Index { span, operand, index, slot: base, size } => {
            let (new_operand, first) = rewrite_linear(operand, slot, replacements);
            let (new_index, second) = rewrite_linear(index, slot, replacements);
            if first + second > 0 {
                (Expr::Index {
                    span: *span, operand: Box::new(new_operand), index: Box::new(new_index),
                    slot: *base, size: *size,
                }, first + second)
            } else {
                (expression.clone(), 0)
            }
        }
        other => (other.clone(), 0),
    }
}

fn collect_linear(expression: &Expr, slot: i64, found: &mut BTreeSet<(i128, i128)>) {
    if let Some(term) = linear_in_index(expression, slot) {
        found.insert(term);
        return;
    }
    match expression {
        Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => {
            collect_linear(inner, slot, found);
        }
        Expr::Binary { left, right, .. } | Expr::Index { operand: left, index: right, .. } => {
            collect_linear(left, slot, found);
            collect_linear(right, slot, found);
        }
        Expr::Call { arguments, .. } | Expr::ArrayLiteral { elements: arguments, .. } => {
            for argument in arguments {
                collect_linear(argument, slot, found);
            }
        }
        _ => {}
    }
}

fn statement_expression(statement: &Stmt) -> Option<&Expr> {
    match statement {
        Stmt::Let { value, .. } | Stmt::Assign { value, .. } | Stmt::ExprStmt { expression: value, .. }
        | Stmt::If { condition: value, .. } => Some(value),
        _ => None,
    }
}

fn strength_reduction_plan(statement: &Stmt) -> Option<(i64, Vec<(i128, i128)>)> {
    let Stmt::While { condition, body, .. } = statement else {
        return None;
    };
    let Expr::Binary { op, left, .. } = peel_parens(Some(condition)).unwrap() else {
        return None;
    };
    if op != "<" {
        return None;
    }
    let Expr::Name { slot: index_slot, .. } = peel_parens(Some(left)).unwrap() else {
        return None;
    };
    if body.statements.len() < 2 || contains_break_or_continue(body) {
        return None;
    }
    let last = body.statements.last().unwrap();
    let Stmt::Assign { slot: found, value, .. } = last else {
        return None;
    };
    if *found != *index_slot {
        return None;
    }
    let Expr::Binary { op, right, .. } = peel_parens(Some(value)).unwrap() else {
        return None;
    };
    if op != "+" {
        return None;
    }
    if !matches!(peel_parens(Some(right)).unwrap(), Expr::IntLiteral { value, .. } if *value == 1) {
        return None;
    }
    let mut terms = BTreeSet::new();
    for inner in &body.statements[..body.statements.len() - 1] {
        if matches!(inner, Stmt::If { .. }) {
            return None;
        }
        if let Some(expression) = statement_expression(inner) {
            collect_linear(expression, *index_slot, &mut terms);
        }
    }
    if terms.is_empty() {
        return None;
    }
    Some((*index_slot, terms.into_iter().collect()))
}

fn induction_block(block: &mut Block, next_slot: &mut usize) -> usize {
    let mut changed = 0;
    let mut output = Vec::new();
    for statement in std::mem::take(&mut block.statements) {
        let plan = if matches!(statement, Stmt::While { .. }) {
            strength_reduction_plan(&statement)
        } else {
            None
        };
        if let Some((index_slot, terms)) = plan {
            let mut initializers = Vec::new();
            let mut increments = Vec::new();
            let mut replacements = LinearReplacements::new();
            for (multiplier, offset) in terms {
                let slot = *next_slot as i64;
                *next_slot += 1;
                let name = format!("__induction_{}", slot);
                replacements.insert((multiplier, offset), slot);
                initializers.push(Stmt::Let {
                    span: statement.span(), name: name.clone(), mutable: true,
                    type_name: "i64".to_string(),
                    value: Expr::IntLiteral { span: statement.span(), value: offset },
                    slot, array_size: None,
                });
                increments.push(Stmt::Assign {
                    span: statement.span(), name, slot,
                    value: Expr::Binary {
                        span: statement.span(), op: "+".to_string(),
                        left: Box::new(Expr::Name {
                            span: statement.span(),
                            name: format!("__induction_{}", slot), slot,
                        }),
                        right: Box::new(Expr::IntLiteral {
                            span: statement.span(), value: multiplier,
                        }),
                    },
                    index: None, temp_slot: None, array_size: None,
                });
            }
            let mut rewritten = Vec::new();
            let Stmt::While { ref body, .. } = statement else { unreachable!() };
            for inner in &body.statements[..body.statements.len() - 1] {
                match inner {
                    Stmt::Let { value, .. } | Stmt::Assign { value, .. } => {
                        let (new_value, _) = rewrite_linear(value, index_slot, &replacements);
                        let mut copy = inner.clone();
                        if let Stmt::Let { value, .. } | Stmt::Assign { value, .. } = &mut copy {
                            *value = new_value;
                        }
                        rewritten.push(copy);
                    }
                    Stmt::ExprStmt { expression, .. } => {
                        let (new_expression, _) =
                            rewrite_linear(expression, index_slot, &replacements);
                        rewritten.push(Stmt::ExprStmt {
                            span: inner.span(), expression: new_expression,
                        });
                    }
                    other => rewritten.push(other.clone()),
                }
            }
            rewritten.push(body.statements.last().unwrap().clone());
            rewritten.extend(increments);
            let mut new_statement = statement;
            if let Stmt::While { body: target, .. } = &mut new_statement {
                target.statements = rewritten;
            }
            output.extend(initializers);
            output.push(new_statement);
            changed += 1;
            continue;
        }
        output.push(statement);
    }
    block.statements = output;
    for statement in &mut block.statements {
        match statement {
            Stmt::If { then_block, else_block, .. } => {
                changed += induction_block(then_block, next_slot);
                if let Some(otherwise) = else_block {
                    changed += induction_block(otherwise, next_slot);
                }
            }
            Stmt::While { body, .. } | Stmt::Block(body) => {
                changed += induction_block(body, next_slot);
            }
            _ => {}
        }
    }
    changed
}

/// Keep linear functions of a counted index in one incremented local each.
pub fn apply_induction_strength_reduction(program: &mut Program) -> usize {
    let mut changed = 0;
    for function in &mut program.functions {
        changed += induction_block(&mut function.body, &mut function.slots);
    }
    changed
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
