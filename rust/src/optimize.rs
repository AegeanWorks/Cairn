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

/// Apply copy propagation: replace `x = t` with t's definition when the copy is t's only read.
pub fn apply_copy_propagation(program: &mut Program) -> usize {
    let mut propagated = 0;
    for _ in 0..64 {
        let roots: Vec<Block> = program.functions.iter().map(|f| f.body.clone()).collect();
        let mut changed = false;
        for (function, root) in program.functions.iter_mut().zip(roots.iter()) {
            if propagate_once(root, &mut function.body) {
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
