//! Code generation, first slice: the entry stub and frameless functions with simple expressions.
//!
//! The byte-level parity gate compares `.text` with the Python generator over a curated set of
//! simple sources. Wider lowering shapes are added in later slices.

use crate::codegen::function_label;
use crate::image::{iat_label, Artifact, ENTRY_LABEL, ReadOnlyData, WritableData};
use crate::machine::Assembler;
use crate::runtime::{emit_runtime, OUT_FLUSH_LABEL, PRINT_CHAR_LABEL, PRINT_HEX_LABEL, PRINT_LABEL};
use crate::syntax::{Expr, Program, Stmt};

/// The array spans declared in one function: `(base slot, element count)`.
fn collect_arrays(block: &crate::syntax::Block, found: &mut Vec<(i64, usize)>) {
    for statement in &block.statements {
        match statement {
            Stmt::Let { slot, array_size: Some(size), .. } => found.push((*slot, *size)),
            Stmt::If { then_block, else_block, .. } => {
                collect_arrays(then_block, found);
                if let Some(otherwise) = else_block {
                    collect_arrays(otherwise, found);
                }
            }
            Stmt::While { body, .. } | Stmt::Block(body) => collect_arrays(body, found),
            _ => {}
        }
    }
}

/// Slots whose first top-level touch is an assignment, so no entry clear is needed.
fn assigned_slots(block: &crate::syntax::Block) -> std::collections::BTreeSet<i64> {
    let mut found = std::collections::BTreeSet::new();
    for statement in &block.statements {
        match statement {
            Stmt::Let { slot, .. } | Stmt::Assign { slot, .. } => {
                found.insert(*slot);
            }
            _ => {}
        }
    }
    found
}

fn in_array(slot: i64, arrays: &[(i64, usize)]) -> bool {
    arrays.iter().any(|(base, size)| slot >= *base && slot < *base + *size as i64)
}

/// Whether a function reads stdin and every call it makes is a builtin.
fn promotes_stdin_cursor(block: &crate::syntax::Block) -> bool {
    fn expression(expr: &Expr, reads: &mut bool, builtins: &mut bool) {
        match expr {
            Expr::Call { name, arguments, .. } => {
                if name == "read_char" {
                    *reads = true;
                } else if !crate::sema::BUILTINS.iter().any(|(known, _)| *known == name) {
                    *builtins = false;
                }
                for argument in arguments {
                    expression(argument, reads, builtins);
                }
            }
            Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => {
                expression(inner, reads, builtins);
            }
            Expr::Binary { left, right, .. } | Expr::Index { operand: left, index: right, .. } => {
                expression(left, reads, builtins);
                expression(right, reads, builtins);
            }
            Expr::ArrayLiteral { elements, .. } => {
                for element in elements {
                    expression(element, reads, builtins);
                }
            }
            _ => {}
        }
    }
    fn statements(block: &crate::syntax::Block, reads: &mut bool, builtins: &mut bool) {
        for statement in &block.statements {
            match statement {
                Stmt::Let { value, .. } | Stmt::Assign { value, .. }
                | Stmt::Return { value, .. } | Stmt::ExprStmt { expression: value, .. } => {
                    expression(value, reads, builtins);
                }
                Stmt::If { condition, then_block, else_block, .. } => {
                    expression(condition, reads, builtins);
                    statements(then_block, reads, builtins);
                    if let Some(otherwise) = else_block {
                        statements(otherwise, reads, builtins);
                    }
                }
                Stmt::While { condition, body, .. } => {
                    expression(condition, reads, builtins);
                    statements(body, reads, builtins);
                }
                Stmt::Block(inner) => statements(inner, reads, builtins),
                _ => {}
            }
        }
    }
    let (mut reads, mut builtins) = (false, true);
    statements(block, &mut reads, &mut builtins);
    reads && builtins
}

/// The `(operand, count)` of `(a >> k) & (2**(64-k)-1)`, the logical-shift peephole.
/// The `(slot, log2(scale), addend)` of `slot * 2**k + addend`, when it fits one lea.
fn scaled_sum_terms(expression: &Expr) -> Option<(i64, u32, i128)> {
    let Expr::Binary { op, left, right, .. } =
        crate::optimize::peel_parens(Some(expression)).unwrap() else { return None };
    if op != "+" {
        return None;
    }
    for (literal, product) in [(left.as_ref(), right.as_ref()), (right.as_ref(), left.as_ref())] {
        let Expr::IntLiteral { value: addend, .. } =
            crate::optimize::peel_parens(Some(literal)).unwrap() else { continue };
        let Expr::Binary { op: product_op, left: factor, right: named, .. } =
            crate::optimize::peel_parens(Some(product)).unwrap() else { continue };
        if product_op != "*" {
            continue;
        }
        let (slot, scale) = match (crate::optimize::peel_parens(Some(factor)).unwrap(),
                                   crate::optimize::peel_parens(Some(named)).unwrap()) {
            (Expr::IntLiteral { value, .. }, Expr::Name { slot, .. }) => (*slot, *value),
            (Expr::Name { slot, .. }, Expr::IntLiteral { value, .. }) => (*slot, *value),
            _ => continue,
        };
        let log2 = match scale {
            3 => 1,
            5 => 2,
            9 => 3,
            _ => continue,
        };
        return Some((slot, log2, *addend));
    }
    None
}

fn is_plus_step(expression: &Expr, slot: i64, amount: i128) -> bool {
    let Expr::Binary { op, left, right, .. } =
        crate::optimize::peel_parens(Some(expression)).unwrap() else { return false };
    op == "+" && expr_slot(left) == Some(slot)
        && matches!(crate::optimize::peel_parens(Some(right)).unwrap(),
            Expr::IntLiteral { value, .. } if *value == amount)
}

fn body_is_copy_free(block: &crate::syntax::Block) -> bool {
    fn expression(expr: &Expr) -> bool {
        match expr {
            Expr::Call { .. } => true,
            Expr::Binary { op, left, right, .. } => {
                op == "/" || op == "%" || expression(left) || expression(right)
            }
            Expr::Unary { operand, .. } | Expr::Parens { inner: operand, .. } => expression(operand),
            Expr::Index { operand, index, .. } => expression(operand) || expression(index),
            Expr::ArrayLiteral { elements, .. } => elements.iter().any(expression),
            _ => false,
        }
    }
    fn statements(block: &crate::syntax::Block) -> bool {
        for statement in &block.statements {
            match statement {
                Stmt::Let { value, .. } | Stmt::Assign { value, .. }
                | Stmt::Return { value, .. } | Stmt::ExprStmt { expression: value, .. } => {
                    if expression(value) {
                        return false;
                    }
                }
                Stmt::If { condition, then_block, else_block, .. } => {
                    if expression(condition) || !statements(then_block)
                        || else_block.as_ref().is_some_and(|other| !statements(other)) {
                        return false;
                    }
                }
                Stmt::While { condition, body, .. } => {
                    if expression(condition) || !statements(body) {
                        return false;
                    }
                }
                Stmt::Block(inner) => {
                    if !statements(inner) {
                        return false;
                    }
                }
                _ => {}
            }
        }
        true
    }
    statements(block)
}

/// Whether a function is a two-parameter pure-text pair-inline candidate.
fn is_pair_shape(function: &crate::syntax::Function) -> bool {
    if function.slots != 2 || function.parameters.len() != 2 {
        return false;
    }
    let value_slot = function.parameters[1].slot;
    fn block_has_guard(block: &crate::syntax::Block, slot: i64) -> bool {
        for statement in &block.statements {
            if let Stmt::If { condition, .. } = statement {
                if let Expr::Binary { op, left, right, .. } =
                    crate::optimize::peel_parens(Some(condition)).unwrap() {
                    if op == "==" {
                        let names = [expr_slot(left), expr_slot(right)];
                        let zeros = [
                            matches!(crate::optimize::peel_parens(Some(left)).unwrap(),
                                Expr::IntLiteral { value, .. } if *value == 0),
                            matches!(crate::optimize::peel_parens(Some(right)).unwrap(),
                                Expr::IntLiteral { value, .. } if *value == 0),
                        ];
                        if names.contains(&Some(slot)) && zeros.contains(&true) {
                            return true;
                        }
                    }
                }
            }
        }
        false
    }
    block_has_guard(&function.body, value_slot)
}

fn rebinds_parameters(block: &crate::syntax::Block, count: usize) -> bool {
    for statement in &block.statements {
        match statement {
            Stmt::Assign { slot, .. } if *slot >= 0 && (*slot as usize) < count => return true,
            Stmt::If { then_block, else_block, .. } => {
                if rebinds_parameters(then_block, count)
                    || else_block.as_ref().is_some_and(|other| rebinds_parameters(other, count)) {
                    return true;
                }
            }
            Stmt::While { body, .. } | Stmt::Block(body) => {
                if rebinds_parameters(body, count) {
                    return true;
                }
            }
            _ => {}
        }
    }
    false
}

/// The `(operand, count)` of `(a >> k) << k`, the drop-then-restore mask.
fn shift_through_shape(expression: &Expr) -> Option<(&Expr, u8)> {
    let Expr::Binary { op, left, right, .. } =
        crate::optimize::peel_parens(Some(expression)).unwrap() else { return None };
    if op != "<<" {
        return None;
    }
    let count = match crate::optimize::peel_parens(Some(right)).unwrap() {
        Expr::IntLiteral { value, .. } if *value >= 1 && *value <= 63 => *value as u32,
        _ => return None,
    };
    let Expr::Binary { op: inner_op, left: operand, right: inner_count, .. } =
        crate::optimize::peel_parens(Some(left)).unwrap() else { return None };
    if inner_op != ">>" {
        return None;
    }
    if !matches!(crate::optimize::peel_parens(Some(inner_count)).unwrap(),
        Expr::IntLiteral { value, .. } if *value as u32 == count) {
        return None;
    }
    Some((crate::optimize::peel_parens(Some(operand)).unwrap(), count as u8))
}

fn logical_shift_shape(expression: &Expr) -> Option<(&Expr, u8)> {
    let Expr::Binary { op, left, right, .. } = expression else { return None };
    if op != "&" {
        return None;
    }
    let Expr::Binary { op: shift_op, left: operand, right: count, .. } =
        crate::optimize::peel_parens(Some(left)).unwrap() else { return None };
    if shift_op != ">>" {
        return None;
    }
    let shift = match crate::optimize::peel_parens(Some(count)).unwrap() {
        Expr::IntLiteral { value, .. } if *value >= 1 && *value <= 63 => *value as u32,
        _ => return None,
    };
    match crate::optimize::peel_parens(Some(right)).unwrap() {
        Expr::IntLiteral { value, .. } if *value == (1i128 << (64 - shift)) - 1 => {
            Some((crate::optimize::peel_parens(Some(operand)).unwrap(), shift as u8))
        }
        _ => None,
    }
}

/// Slots seeded with a non-negative literal and only grown by non-negative steps.
fn collect_nonnegative_slots(block: &crate::syntax::Block,
                              found: &mut std::collections::BTreeSet<i64>) {
    for statement in &block.statements {
        match statement {
            Stmt::Let { slot, value, .. } | Stmt::Assign { slot, value, .. } => {
                match crate::optimize::peel_parens(Some(value)).unwrap() {
                    Expr::IntLiteral { value, .. } if *value >= 0 => {
                        found.insert(*slot);
                    }
                    Expr::Binary { op, left, right, .. }
                        if op == "+" && expr_slot(left) == Some(*slot) => {
                        if matches!(crate::optimize::peel_parens(Some(right)).unwrap(),
                            Expr::IntLiteral { value, .. } if *value >= 0) {
                            found.insert(*slot);
                        } else {
                            found.remove(slot);
                        }
                    }
                    _ => {
                        found.remove(slot);
                    }
                }
            }
            Stmt::If { then_block, else_block, .. } => {
                collect_nonnegative_slots(then_block, found);
                if let Some(otherwise) = else_block {
                    collect_nonnegative_slots(otherwise, found);
                }
            }
            Stmt::While { body, .. } | Stmt::Block(body) => {
                collect_nonnegative_slots(body, found);
            }
            _ => {}
        }
    }
}

/// Whether `slot` is read exactly once in `body`, and that read is `slot << m`.
fn parameter_single_shift_use(block: &crate::syntax::Block, slot: i64, amount: i64) -> bool {
    fn expression(expr: &Expr, slot: i64, amount: i64, uses: &mut usize, shaped: &mut bool,
                  inside_shift: bool) {
        if *uses > 1 {
            return;
        }
        match expr {
            Expr::Name { slot: found, .. } => {
                if *found == slot {
                    *uses += 1;
                    *shaped |= inside_shift;
                }
            }
            Expr::Parens { inner, .. } => expression(inner, slot, amount, uses, shaped, inside_shift),
            Expr::Unary { operand, .. } => expression(operand, slot, amount, uses, shaped, false),
            Expr::Binary { op, left, right, .. } => {
                let fused_here = op == "<<" && expr_slot(left) == Some(slot)
                    && matches!(crate::optimize::peel_parens(Some(right)).unwrap(),
                        Expr::IntLiteral { value, .. } if *value == amount as i128);
                expression(left, slot, amount, uses, shaped, fused_here);
                expression(right, slot, amount, uses, shaped, false);
            }
            Expr::Index { operand, index, .. } => {
                expression(operand, slot, amount, uses, shaped, false);
                expression(index, slot, amount, uses, shaped, false);
            }
            Expr::Call { arguments, .. } | Expr::ArrayLiteral { elements: arguments, .. } => {
                for argument in arguments {
                    expression(argument, slot, amount, uses, shaped, false);
                }
            }
            _ => {}
        }
    }
    fn statements(block: &crate::syntax::Block, slot: i64, amount: i64,
                  uses: &mut usize, shaped: &mut bool) {
        for statement in &block.statements {
            match statement {
                Stmt::Let { value, .. } | Stmt::Assign { value, .. }
                | Stmt::Return { value, .. } | Stmt::ExprStmt { expression: value, .. } => {
                    expression(value, slot, amount, uses, shaped, false);
                }
                Stmt::If { condition, then_block, else_block, .. } => {
                    expression(condition, slot, amount, uses, shaped, false);
                    statements(then_block, slot, amount, uses, shaped);
                    if let Some(otherwise) = else_block {
                        statements(otherwise, slot, amount, uses, shaped);
                    }
                }
                Stmt::While { condition, body, .. } => {
                    expression(condition, slot, amount, uses, shaped, false);
                    statements(body, slot, amount, uses, shaped);
                }
                Stmt::Block(inner) => statements(inner, slot, amount, uses, shaped),
                _ => {}
            }
        }
    }
    let (mut uses, mut shaped) = (0, false);
    statements(block, slot, amount, &mut uses, &mut shaped);
    uses == 1 && shaped
}

fn collect_str_lens(block: &crate::syntax::Block,
                    found: &mut std::collections::BTreeMap<i64, i64>) {
    for statement in &block.statements {
        match statement {
            Stmt::Let { slot, value, .. } | Stmt::Assign { slot, value, .. } => {
                if let Expr::Call { name, arguments, .. } = value {
                    if name == "str_len" {
                        if let Some(pointer) = arguments.first().and_then(expr_slot) {
                            found.insert(*slot, pointer);
                        }
                    }
                }
            }
            Stmt::If { then_block, else_block, .. } => {
                collect_str_lens(then_block, found);
                if let Some(otherwise) = else_block {
                    collect_str_lens(otherwise, found);
                }
            }
            Stmt::While { body, .. } | Stmt::Block(body) => collect_str_lens(body, found),
            _ => {}
        }
    }
}

fn collect_string_bounds(block: &crate::syntax::Block,
                          str_lens: &std::collections::BTreeMap<i64, i64>,
                          bounds: &mut std::collections::BTreeMap<i64, i64>) {
    for statement in &block.statements {
        match statement {
            Stmt::While { condition, body, .. } => {
                if let Expr::Binary { op, left, right, .. } =
                    crate::optimize::peel_parens(Some(condition)).unwrap() {
                    let pair = match op.as_str() {
                        "<" => Some((expr_slot(left), expr_slot(right))),
                        ">" => Some((expr_slot(right), expr_slot(left))),
                        _ => None,
                    };
                    if let Some((Some(index), Some(length))) = pair {
                        if let Some(pointer) = str_lens.get(&length) {
                            bounds.insert(index, *pointer);
                        }
                    }
                }
                collect_string_bounds(body, str_lens, bounds);
            }
            Stmt::If { then_block, else_block, .. } => {
                collect_string_bounds(then_block, str_lens, bounds);
                if let Some(otherwise) = else_block {
                    collect_string_bounds(otherwise, str_lens, bounds);
                }
            }
            Stmt::Block(inner) => collect_string_bounds(inner, str_lens, bounds),
            _ => {}
        }
    }
}

fn expr_slot(expr: &Expr) -> Option<i64> {
    match crate::optimize::peel_parens(Some(expr)).unwrap() {
        Expr::Name { slot, .. } => Some(*slot),
        _ => None,
    }
}

fn expr_has_call(expr: &Expr) -> bool {
    match expr {
        Expr::Call { .. } => true,
        Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => expr_has_call(inner),
        Expr::Binary { left, right, .. } | Expr::Index { operand: left, index: right, .. } => {
            expr_has_call(left) || expr_has_call(right)
        }
        Expr::ArrayLiteral { elements, .. } => elements.iter().any(expr_has_call),
        _ => false,
    }
}

/// True when lowering this expression writes rcx (a division, a variable shift or a call).
/// A value that leaves rcx alone can be evaluated after rotl's count is set there.
fn expression_uses_rcx(expression: &Expr) -> bool {
    match expression {
        Expr::Call { .. } => true,
        Expr::Binary { op, left, right, .. } => {
            let variable_shift = matches!(op.as_str(), "<<" | ">>")
                && !matches!(crate::optimize::peel_parens(Some(right)).unwrap(),
                    Expr::IntLiteral { .. });
            matches!(op.as_str(), "/" | "%") || variable_shift
                || expression_uses_rcx(left) || expression_uses_rcx(right)
        }
        Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => {
            expression_uses_rcx(inner)
        }
        Expr::Index { operand, index, .. } => {
            expression_uses_rcx(operand) || expression_uses_rcx(index)
        }
        Expr::ArrayLiteral { elements, .. } => elements.iter().any(expression_uses_rcx),
        _ => false,
    }
}

fn expression_may_hold(expr: &Expr) -> bool {
    match expr {
        Expr::Binary { op, right, .. } => {
            if op != "&&" && op != "||" && expr_has_call(right) {
                return true;
            }
            match expr {
                Expr::Binary { left, right, .. } => {
                    expression_may_hold(left) || expression_may_hold(right)
                }
                _ => false,
            }
        }
        Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => {
            expression_may_hold(inner)
        }
        Expr::Call { arguments, .. } => arguments.iter().any(expression_may_hold),
        _ => false,
    }
}

/// True when evaluating this expression can execute a `call` instruction.
/// Division and modulo count: their lowering may call a runtime helper.
fn expr_makes_calls(expression: &Expr) -> bool {
    match expression {
        Expr::Call { .. } => true,
        Expr::Binary { op, left, right, .. } => {
            op == "/" || op == "%" || expr_makes_calls(left) || expr_makes_calls(right)
        }
        Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => expr_makes_calls(inner),
        Expr::Index { operand, index, .. } => {
            expr_makes_calls(operand) || expr_makes_calls(index)
        }
        Expr::ArrayLiteral { elements, .. } => elements.iter().any(expr_makes_calls),
        _ => false,
    }
}

/// True when emitting this block can execute a `call` instruction.
fn block_makes_calls(block: &crate::syntax::Block) -> bool {
    block.statements.iter().any(|statement| match statement {
        crate::syntax::Stmt::Let { value, .. }
        | crate::syntax::Stmt::Assign { value, .. }
        | crate::syntax::Stmt::Return { value, .. } => expr_makes_calls(value),
        crate::syntax::Stmt::ExprStmt { expression, .. } => expr_makes_calls(expression),
        crate::syntax::Stmt::If { condition, then_block, else_block, .. } => {
            expr_makes_calls(condition)
                || block_makes_calls(then_block)
                || else_block.as_ref().is_some_and(|block| block_makes_calls(block))
        }
        crate::syntax::Stmt::While { condition, body, .. } => {
            expr_makes_calls(condition) || block_makes_calls(body)
        }
        crate::syntax::Stmt::Block(inner) => block_makes_calls(inner),
        _ => false,
    })
}

/// True when emitting this function can execute a `call` instruction. A leaf
/// function does not need its stack kept 16-byte aligned, so the one-slot pad
/// that exists only for call alignment can be omitted from its prologue.
fn function_makes_calls(function: &crate::syntax::Function) -> bool {
    block_makes_calls(&function.body)
}

/// A hoist held in a caller-saved register survives only while the loop runs;
/// a call anywhere in the loop would clobber it.
fn loop_makes_calls(condition: &Expr, body: &crate::syntax::Block) -> bool {
    expr_makes_calls(condition) || block_makes_calls(body)
}

fn body_may_hold(block: &crate::syntax::Block) -> bool {
    for statement in &block.statements {
        match statement {
            Stmt::Let { value, .. } | Stmt::Assign { value, .. }
            | Stmt::Return { value, .. } | Stmt::ExprStmt { expression: value, .. } => {
                if expression_may_hold(value) {
                    return true;
                }
            }
            Stmt::If { condition, then_block, else_block, .. } => {
                if expression_may_hold(condition) || body_may_hold(then_block) {
                    return true;
                }
                if let Some(otherwise) = else_block {
                    if body_may_hold(otherwise) {
                        return true;
                    }
                }
            }
            Stmt::While { condition, body, .. } => {
                if expression_may_hold(condition) || body_may_hold(body) {
                    return true;
                }
            }
            Stmt::Block(inner) => {
                if body_may_hold(inner) {
                    return true;
                }
            }
            _ => {}
        }
    }
    false
}

/// The register home of a slot, when the slot is kept in a register.
fn home_for<'a>(slot: i64, homes: &'a [(i64, &'a str)]) -> Option<&'a str> {
    homes.iter().find(|(found, _)| *found == slot).map(|(_, register)| *register)
}

/// The constant shift a self-call's argument adds to the inlined parameter.
fn delta_argument(expression: &Expr, slot: i64) -> Option<i64> {
    match crate::optimize::peel_parens(Some(expression)).unwrap() {
        Expr::Name { slot: found, .. } if *found == slot => Some(0),
        Expr::Binary { op, left, right, .. } if op == "+" || op == "-" => {
            let left = crate::optimize::peel_parens(Some(left)).unwrap();
            let right = crate::optimize::peel_parens(Some(right)).unwrap();
            if let (Expr::Name { slot: found, .. }, Expr::IntLiteral { value, .. }) = (left, right) {
                if *found == slot {
                    return Some(if op == "+" { *value as i64 } else { -(*value as i64) });
                }
            }
            None
        }
        _ => None,
    }
}

fn body_assigns(block: &crate::syntax::Block) -> bool {
    block.statements.iter().any(|statement| match statement {
        Stmt::Let { .. } | Stmt::Assign { .. } => true,
        Stmt::If { then_block, else_block, .. } => {
            body_assigns(then_block)
                || else_block.as_ref().map_or(false, body_assigns)
        }
        Stmt::While { body, .. } | Stmt::Block(body) => body_assigns(body),
        _ => false,
    })
}

fn has_delta_self_call(block: &crate::syntax::Block, name: &str, slot: i64) -> bool {
    fn in_expr(expression: &Expr, name: &str, slot: i64) -> bool {
        if let Expr::Call { name: found, arguments, .. } = expression {
            if found == name && arguments.len() == 1 && delta_argument(&arguments[0], slot).is_some() {
                return true;
            }
            return arguments.iter().any(|argument| in_expr(argument, name, slot));
        }
        match expression {
            Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => in_expr(inner, name, slot),
            Expr::Binary { left, right, .. } | Expr::Index { operand: left, index: right, .. } => {
                in_expr(left, name, slot) || in_expr(right, name, slot)
            }
            Expr::ArrayLiteral { elements, .. } => elements.iter().any(|element| in_expr(element, name, slot)),
            _ => false,
        }
    }
    block.statements.iter().any(|statement| match statement {
        Stmt::Let { value, .. } | Stmt::Assign { value, .. } | Stmt::Return { value, .. } => {
            in_expr(value, name, slot)
        }
        Stmt::ExprStmt { expression, .. } => in_expr(expression, name, slot),
        Stmt::If { condition, then_block, else_block, .. } => {
            in_expr(condition, name, slot)
                || has_delta_self_call(then_block, name, slot)
                || else_block.as_ref().map_or(false, |block| has_delta_self_call(block, name, slot))
        }
        Stmt::While { condition, body, .. } => {
            in_expr(condition, name, slot) || has_delta_self_call(body, name, slot)
        }
        Stmt::Block(inner) => has_delta_self_call(inner, name, slot),
        _ => false,
    })
}

/// One lowering pass over a checked program.
pub struct CodeGenerator {
    pub assembler: Assembler,
    pub rodata: ReadOnlyData,
    pub writable: WritableData,
    str_labels: std::collections::BTreeMap<String, String>,
    counter: usize,
    depth: i64,
    epilogue: String,
    delta_slot: i64,
    delta_value: i64,
    inline_depth: usize,
    current_name: String,
    current_body: crate::syntax::Block,
    arrays: Vec<(i64, usize)>,
    /// Per-loop invariant big-literal hoists, keyed by the While statement address.
    hoist_plans: std::collections::BTreeMap<usize, Vec<(i128, String)>>,
    /// Active value -> spare home map while a hoisted loop body lowers.
    hoist_imm: std::collections::BTreeMap<i128, String>,
    /// Spare homes saved by the frameless prologue for hoisted constants.
    hoist_homes: Vec<String>,
    /// Index slot -> string pointer slot for `str_byte` calls inside a str_len guard.
    string_bounds: std::collections::BTreeMap<i64, i64>,
    /// Spare homes promoted to hold the stdin buffer base, cursor and count word.
    stdin_base: Option<String>,
    stdin_cursor: Option<String>,
    stdin_count: Option<String>,
    /// Jump targets of the innermost loop, for break and continue.
    break_targets: Vec<String>,
    continue_targets: Vec<String>,
    /// All functions in the program, for code-generation-time foreign inlining.
    functions: Vec<crate::syntax::Function>,
    /// Slots provably non-negative by literal seeding and non-negative increments.
    nonnegative_slots: std::collections::BTreeSet<i64>,
    /// The second parameter of a pair-mode function, rebound across inline copies.
    value_slot: i64,
    /// Nesting depth of pair chain loops, for parking the counter register.
    chain_loop_depth: usize,
    /// Number of temporary registers currently held across a right operand.
    temporaries: usize,
    /// Slots whose sole use in an inline copy is a forwarded shift: (source home, count).
    shift_through: std::collections::BTreeMap<i64, (String, u32)>,
    /// Destination home a foreign-inline copy may write its result into directly.
    copy_home: Option<String>,
    /// Whether the last foreign-inline copy honored `copy_home`.
    copy_home_used: bool,
    /// Destination home for a tail return inside the current inline copy.
    copy_return: Option<String>,
    /// Whether the current frame reserved a pad slot usable as a call left-hold.
    pad_slot_active: bool,
    /// Caller-saved extra homes that must be preserved across machine calls.
    extra_homes: Vec<String>,
}

impl CodeGenerator {
    pub fn new() -> Self {
        Self {
            assembler: Assembler::new(),
            rodata: ReadOnlyData::default(),
            writable: WritableData::default(),
            str_labels: std::collections::BTreeMap::new(),
            counter: 0,
            depth: 0,
            epilogue: String::new(),
            delta_slot: -1,
            delta_value: 0,
            inline_depth: 0,
            current_name: String::new(),
            current_body: crate::syntax::Block {
                span: crate::diagnostics::Span::point(0, 1, 1),
                statements: Vec::new(),
            },
            arrays: Vec::new(),
            hoist_plans: std::collections::BTreeMap::new(),
            hoist_imm: std::collections::BTreeMap::new(),
            hoist_homes: Vec::new(),
            string_bounds: std::collections::BTreeMap::new(),
            stdin_base: None,
            stdin_cursor: None,
            stdin_count: None,
            break_targets: Vec::new(),
            continue_targets: Vec::new(),
            functions: Vec::new(),
            nonnegative_slots: std::collections::BTreeSet::new(),
            value_slot: -1,
            chain_loop_depth: 0,
            temporaries: 0,
            shift_through: std::collections::BTreeMap::new(),
            copy_home: None,
            copy_home_used: false,
            copy_return: None,
            pad_slot_active: false,
            extra_homes: Vec::new(),
        }
    }

    fn fresh(&mut self, stem: &str) -> String {
        self.counter += 1;
        format!("{}.{}", stem, self.counter)
    }

    fn loops_within(block: &crate::syntax::Block, found: &mut Vec<*const Stmt>) {
        for statement in &block.statements {
            match statement {
                Stmt::While { body, .. } => {
                    found.push(statement as *const Stmt);
                    Self::loops_within(body, found);
                }
                Stmt::If { then_block, else_block, .. } => {
                    Self::loops_within(then_block, found);
                    if let Some(otherwise) = else_block {
                        Self::loops_within(otherwise, found);
                    }
                }
                _ => {}
            }
        }
    }

    fn count_add_raw(counts: &mut Vec<(i128, usize)>, value: i128) {
        if let Some(entry) = counts.iter_mut().find(|(found, _)| *found == value) {
            entry.1 += 1;
        } else {
            counts.push((value, 1));
        }
    }

    fn count_add(counts: &mut Vec<(i128, usize)>, value: i128) {
        if value < -0x80000000 || value > 0x7FFFFFFF {
            Self::count_add_raw(counts, value);
        }
    }

    fn count_literals(expr: &Expr, counts: &mut Vec<(i128, usize)>) {
        match expr {
            Expr::IntLiteral { value, .. } => Self::count_add(counts, *value),
            Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => {
                Self::count_literals(inner, counts);
            }
            Expr::Binary { op, left, right, .. } => {
                if op == "/" || op == "%" {
                    if let Expr::IntLiteral { value: divisor, .. } =
                        crate::optimize::peel_parens(Some(right)).unwrap() {
                        if *divisor >= 2 && *divisor & (*divisor - 1) != 0 {
                            Self::count_add_raw(counts, *divisor);
                            if let Some((multiplier, _)) =
                                crate::codegen::magic_division_parameters(*divisor) {
                                Self::count_add_raw(counts, multiplier);
                            }
                        }
                    }
                }
                if let Some((operand, _)) = logical_shift_shape(expr) {
                    Self::count_literals(operand, counts);
                } else {
                    Self::count_literals(left, counts);
                    Self::count_literals(right, counts);
                }
            }
            Expr::Index { operand: left, index: right, .. } => {
                if let Some((operand, _)) = logical_shift_shape(expr) {
                    Self::count_literals(operand, counts);
                } else {
                    Self::count_literals(left, counts);
                    Self::count_literals(right, counts);
                }
            }
            Expr::Call { arguments, .. } | Expr::ArrayLiteral { elements: arguments, .. } => {
                for argument in arguments {
                    Self::count_literals(argument, counts);
                }
            }
            _ => {}
        }
    }

    fn count_literals_block(block: &crate::syntax::Block, counts: &mut Vec<(i128, usize)>) {
        for statement in &block.statements {
            match statement {
                Stmt::Let { value, .. } | Stmt::Assign { value, .. } => {
                    Self::count_literals(value, counts);
                }
                Stmt::If { condition, then_block, else_block, .. } => {
                    Self::count_literals(condition, counts);
                    Self::count_literals_block(then_block, counts);
                    if let Some(otherwise) = else_block {
                        Self::count_literals_block(otherwise, counts);
                    }
                }
                Stmt::While { condition, body, .. } => {
                    Self::count_literals(condition, counts);
                    Self::count_literals_block(body, counts);
                }
                Stmt::Return { value, .. } | Stmt::ExprStmt { expression: value, .. } => {
                    Self::count_literals(value, counts);
                }
                _ => {}
            }
        }
    }

    /// Reserve spare homes for each loop's most-used big literals, outer loops first.
    fn plan_constant_hoists(&mut self, body: &crate::syntax::Block, homes_len: usize) {
        self.hoist_plans.clear();
        self.hoist_imm.clear();
        self.hoist_homes.clear();
        let spare: Vec<String> = crate::codegen::HOME_REGISTERS
            .iter().skip(homes_len).map(|name| name.to_string()).collect();
        if spare.is_empty() {
            return;
        }
        let mut loops = Vec::new();
        Self::loops_within(body, &mut loops);
        let mut cursor = 0usize;
        let mut caller_saved_used = false;
        for statement in loops {
            let Stmt::While { condition, body: loop_body, .. } =
                (unsafe { &*statement }) else { continue };
            let mut counts = Vec::new();
            Self::count_literals(condition, &mut counts);
            Self::count_literals_block(loop_body, &mut counts);
            if counts.is_empty() {
                continue;
            }
            // r8/r9 are caller-saved argument registers: free after the prologue
            // and never written by call-free lowering, so they can hold a third
            // constant without a spare HOME register.
            let mut available: Vec<String> = spare[cursor.min(spare.len())..].to_vec();
            if !caller_saved_used && !loop_makes_calls(condition, loop_body) {
                available.push("r8".to_string());
                available.push("r9".to_string());
            }
            if available.is_empty() {
                continue;
            }
            counts.sort_by(|a, b| b.1.cmp(&a.1)); // stable: ties keep first-seen order
            let take = 3.min(available.len());
            let mut plan = Vec::new();
            for ((value, _), home) in counts.into_iter().take(take)
                .zip(available[..take].iter()) {
                plan.push((value, home.clone()));
                self.hoist_homes.push(home.clone());
            }
            let home_taken = take.min(spare.len().saturating_sub(cursor));
            cursor += home_taken;
            caller_saved_used |= take > home_taken;
            self.hoist_plans.insert(statement as usize, plan);
            if cursor >= spare.len() && caller_saved_used {
                break;
            }
        }
    }

    /// Compute an expression directly into `destination`, using home-offset lea where possible.
    fn emit_into(&mut self, expression: &Expr, destination: &str,
                 homes: &[(i64, &str)]) {
        let expr = crate::optimize::peel_parens(Some(expression)).unwrap();
        if let Expr::Binary { op, left, right, .. } = expr {
            if op == "+" || op == "-" {
                let (target, value) = match (left.as_ref(), right.as_ref()) {
                    (_, Expr::IntLiteral { value, .. }) => (left.as_ref(), *value),
                    (Expr::IntLiteral { value, .. }, _) if op == "+" => (right.as_ref(), *value),
                    _ => (expr, 0),
                };
                if let Expr::Name { slot, .. } = crate::optimize::peel_parens(Some(target)).unwrap() {
                    if let Some(home) = home_for(*slot, homes) {
                        if value >= -0x80000000 && value < 0x80000000 {
                            let mut effective = if op == "-" { -value } else { value };
                            if *slot == self.delta_slot && self.delta_value != 0 {
                                effective -= self.delta_value as i128;
                            }
                            if home != destination || effective != 0 {
                                self.assembler.lea_imm(destination, home, effective as i64);
                                return;
                            }
                        }
                    }
                }
            }
        }
        if self.try_emit_binary_into(expr, destination, homes) {
            return;
        }
        match expr {
            Expr::IntLiteral { value, .. } => self.emit_literal(*value, destination),
            Expr::Name { slot, .. } => {
                if let Some(home) = home_for(*slot, homes) {
                    if home != destination {
                        self.assembler.mov_reg_reg(destination, home);
                    }
                } else {
                    self.load_slot(*slot, destination, homes);
                }
            }
            _ => {
                self.emit_expression(expr, homes);
                if destination != "rax" {
                    self.assembler.mov_reg_reg(destination, "rax");
                }
            }
        }
    }

    /// Materialise a literal from its hoisted spare home when one is active.
    fn emit_literal(&mut self, value: i128, destination: &str) {
        if let Some(home) = self.hoist_imm.get(&value).cloned() {
            if home != destination {
                self.assembler.mov_reg_reg(destination, &home);
            }
        } else {
            self.assembler.mov_imm64(destination, value as i64);
        }
    }

    fn string_label(&mut self, text: &str) -> String {
        if let Some(label) = self.str_labels.get(text) {
            return label.clone();
        }
        let label = format!("cairn.str.{}", self.str_labels.len());
        let mut bytes = text.as_bytes().to_vec();
        bytes.push(0);
        self.rodata.add(&label, &bytes);
        self.str_labels.insert(text.to_string(), label.clone());
        label
    }

    /// A call padded so rsp stays 16-byte aligned at the ABI boundary.
    fn call_site(&mut self, target: &str) {
        for home in &self.extra_homes {
            self.assembler.push(home);
            self.depth += 8;
        }
        let padding = if self.depth % 16 != 0 { 8 } else { 0 };
        if padding > 0 {
            self.assembler.sub_rsp(padding);
        }
        self.assembler.call(target);
        if padding > 0 {
            self.assembler.add_rsp(padding);
        }
        for home in self.extra_homes.iter().rev() {
            self.assembler.pop(home);
            self.depth -= 8;
        }
    }

    fn direct_indirect_call(&mut self, import: &str) {
        let padding = if self.depth % 16 != 0 { 8 } else { 0 };
        if padding > 0 {
            self.assembler.sub_rsp(padding);
        }
        let name = iat_label(import);
        self.assembler.call_indirect(&name);
        if padding > 0 {
            self.assembler.add_rsp(padding);
        }
    }

    fn emit_entry(&mut self) {
        self.assembler.label(ENTRY_LABEL);
        self.assembler.sub_rsp(40);
        self.assembler.call(&function_label("main"));
        self.assembler.mov_reg_reg("rcx", "rax");
        self.assembler.push("rcx");
        self.depth += 8;
        let flush = OUT_FLUSH_LABEL.to_string();
        self.call_site(&flush);
        self.depth -= 8;
        self.assembler.pop("rcx");
        self.direct_indirect_call("ExitProcess");
        self.assembler.hlt();
    }

    /// Slots that keep a register home, in register order (frame functions rank by loop depth,
    /// ties by descending slot; all slots at depth zero here).
    fn home_slots(function: &crate::syntax::Function, arrays: &[(i64, usize)]) -> Vec<i64> {
        if (function.slots as usize) <= crate::codegen::HOME_REGISTERS.len() {
            return (0..function.slots as i64)
                .filter(|slot| !in_array(*slot, arrays))
                .collect();
        }
        let mut ranks = std::collections::BTreeMap::new();
        fn touch(slot: i64, depth: usize, ranks: &mut std::collections::BTreeMap<i64, usize>) {
            ranks.entry(slot).and_modify(|value| *value = (*value).max(depth)).or_insert(depth);
        }
        fn expression(expr: &Expr, depth: usize, ranks: &mut std::collections::BTreeMap<i64, usize>) {
            match expr {
                Expr::Name { slot, .. } => touch(*slot, depth, ranks),
                Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => {
                    expression(inner, depth, ranks);
                }
                Expr::Binary { left, right, .. } | Expr::Index { operand: left, index: right, .. } => {
                    expression(left, depth, ranks);
                    expression(right, depth, ranks);
                }
                Expr::Call { arguments, .. } | Expr::ArrayLiteral { elements: arguments, .. } => {
                    for argument in arguments { expression(argument, depth, ranks); }
                }
                _ => {}
            }
        }
        fn block(body: &crate::syntax::Block, depth: usize,
                 ranks: &mut std::collections::BTreeMap<i64, usize>) {
            for statement in &body.statements {
                match statement {
                    Stmt::Let { value, slot, .. } | Stmt::Assign { value, slot, .. } => {
                        expression(value, depth, ranks);
                        touch(*slot, depth, ranks);
                    }
                    Stmt::If { condition, then_block, else_block, .. } => {
                        expression(condition, depth, ranks);
                        block(then_block, depth, ranks);
                        if let Some(otherwise) = else_block { block(otherwise, depth, ranks); }
                    }
                    Stmt::While { condition, body, .. } => {
                        expression(condition, depth, ranks);
                        block(body, depth + 1, ranks);
                    }
                    Stmt::Return { value, .. } | Stmt::ExprStmt { expression: value, .. } => {
                        expression(value, depth, ranks);
                    }
                    Stmt::Block(inner) => block(inner, depth, ranks),
                    _ => {}
                }
            }
        }
        block(&function.body, 0, &mut ranks);
        let mut ranked: Vec<i64> = (0..function.slots as i64).collect();
        ranked.sort_by(|a, b| {
            let ra = ranks.get(a).copied().unwrap_or(0);
            let rb = ranks.get(b).copied().unwrap_or(0);
            rb.cmp(&ra).then_with(|| b.cmp(a))
        });
        ranked.retain(|slot| !in_array(*slot, arrays));
        ranked
    }

    /// Emit one function: frameless when every slot has a home, otherwise a frame function.
    fn emit_function(&mut self, function: &crate::syntax::Function) {
        self.epilogue = format!("{}.epilogue", function_label(&function.name));
        self.assembler.label(&function_label(&function.name));
        let mut array_spans = Vec::new();
        collect_arrays(&function.body, &mut array_spans);
        let ranked = Self::home_slots(function, &array_spans);
        let callee_homes = crate::codegen::HOME_REGISTERS.len();
        let callee_count = ranked.len().min(callee_homes);
        let extra_count = ranked.len().saturating_sub(callee_homes);
        let mut homes: Vec<(i64, &str)> = ranked[..callee_count]
            .iter()
            .zip(crate::codegen::HOME_REGISTERS.iter().take(callee_count))
            .map(|(slot, register)| (*slot, *register))
            .collect();
        self.extra_homes = crate::codegen::EXTRA_HOME_REGISTERS.iter()
            .take(extra_count).map(|name| name.to_string()).collect();
        homes.extend(
            ranked[callee_count..callee_count + extra_count]
                .iter()
                .zip(crate::codegen::EXTRA_HOME_REGISTERS.iter().take(extra_count))
                .map(|(slot, register)| (*slot, *register)),
        );
        let frame_function = function.slots > callee_homes
            || function.parameters.len() > 4
            || !array_spans.is_empty();
        let delta_eligible = function.slots == 1
            && function.parameters.len() == 1
            && !body_assigns(&function.body)
            && has_delta_self_call(&function.body, &function.name, function.parameters[0].slot);
        let pair_mode = !frame_function && !delta_eligible && is_pair_shape(function);
        self.delta_slot = if delta_eligible || pair_mode {
            function.parameters[0].slot
        } else {
            -1
        };
        self.value_slot = if pair_mode { function.parameters[1].slot } else { -1 };
        self.delta_value = 0;
        self.inline_depth = 0;
        self.current_name = function.name.clone();
        self.current_body = function.body.clone();
        self.arrays = array_spans.clone();
        let callee_registers: Vec<&str> = homes[..callee_count]
            .iter()
            .map(|(_, register)| *register)
            .collect();
        let body = function.body.clone();
        let mut str_lens = std::collections::BTreeMap::new();
        collect_str_lens(&body, &mut str_lens);
        self.string_bounds.clear();
        collect_string_bounds(&body, &str_lens, &mut self.string_bounds);
        self.nonnegative_slots.clear();
        collect_nonnegative_slots(&body, &mut self.nonnegative_slots);
        if !frame_function && !delta_eligible && self.inline_depth == 0 {
            self.plan_constant_hoists(&body, callee_count);
        } else {
            self.hoist_plans.clear();
            self.hoist_imm.clear();
            self.hoist_homes.clear();
        }
        self.stdin_base = None;
        self.stdin_cursor = None;
        self.stdin_count = None;
        let mut pushed: Vec<String> = callee_registers.iter().map(|name| name.to_string()).collect();
        if !frame_function && !delta_eligible && promotes_stdin_cursor(&body) {
            let spare: Vec<String> = crate::codegen::HOME_REGISTERS
                .iter().skip(callee_count).take(3)
                .filter(|name| !self.hoist_homes.iter().any(|home| home == **name))
                .map(|name| name.to_string()).collect();
            if spare.len() >= 2 {
                self.stdin_base = Some(spare[0].clone());
                self.stdin_cursor = Some(spare[1].clone());
                self.stdin_count = spare.get(2).cloned();
                for register in &spare {
                    pushed.push(register.clone());
                }
            }
        }
        let saved_units = pushed.len() + self.hoist_homes.len();
        let merging = !frame_function && !delta_eligible && body_may_hold(&body);
        // Only a function that issues a call needs the stack 16-byte aligned.
        // A leaf can skip the pad even when it has an even number of saved units.
        let leaf = !frame_function && !function_makes_calls(function);
        let pad = !leaf && (frame_function || saved_units % 2 == 0 || merging);
        let filler = merging && saved_units % 2 == 1;
        self.pad_slot_active = !frame_function && pad && !filler && !delta_eligible;
        if frame_function {
            let frame = crate::codegen::align16((function.slots as usize + callee_count) * 8);
            self.assembler.push("rbp");
            self.assembler.mov_reg_reg("rbp", "rsp");
            self.assembler.sub_rsp(frame as i64);
            for (index, register) in callee_registers.iter().enumerate() {
                self.assembler.mov_store_rbp(
                    crate::codegen::slot_offset(function.slots as i64 + index as i64),
                    register,
                );
            }
            let mut assigned = assigned_slots(&function.body);
            for (base, size) in &array_spans {
                if assigned.contains(base) {
                    for slot in *base..*base + *size as i64 {
                        assigned.insert(slot);
                    }
                }
            }
            let homed: std::collections::BTreeSet<i64> =
                homes.iter().map(|(slot, _)| *slot).collect();
            let frame_slots: Vec<i64> = (0..function.slots as i64)
                .filter(|slot| !homed.contains(slot) && !assigned.contains(slot))
                .collect();
            if !frame_slots.is_empty() {
                self.assembler.xor_zero("rax");
                for slot in frame_slots {
                    self.assembler.mov_store_rbp(
                        crate::codegen::slot_offset(slot), "rax");
                }
            }
            for (slot, register) in &homes {
                if *slot as usize >= function.parameters.len() && !assigned.contains(slot) {
                    self.assembler.xor_zero(register);
                }
            }
        } else {
            for register in &pushed {
                self.assembler.push(register);
            }
            for register in &self.hoist_homes {
                self.assembler.push(register);
            }
            if pair_mode {
                self.assembler.push("r13");
            }
            if delta_eligible {
                self.assembler.sub_rsp(8 * 8);
            } else {
                if filler {
                    self.assembler.push(crate::codegen::HOME_REGISTERS.last().unwrap());
                }
                if pad {
                    self.assembler.sub_rsp(8);
                }
            }
            let assigned = assigned_slots(&body);
            for (slot, register) in &homes {
                if *slot as usize >= function.parameters.len() && !assigned.contains(slot) {
                    self.assembler.xor_zero(register);
                }
            }
        }
        self.depth = 0;
        for (index, parameter) in function.parameters.iter().enumerate() {
            if index < 4 {
                let source = ["rcx", "rdx", "r8", "r9"][index];
                self.store_slot(parameter.slot, source, &homes);
            } else if frame_function {
                self.assembler.mov_load_rbp("rax", 16 + 8 * (index - 4) as i64);
                self.store_slot(parameter.slot, "rax", &homes);
            }
        }
        if let (Some(base), Some(cursor)) = (self.stdin_base.clone(), self.stdin_cursor.clone()) {
            self.assembler.lea_rip(&base, crate::runtime::STDIN_BUFFER_LABEL);
            self.assembler.mov_load_rip(&cursor, crate::runtime::STDIN_CURSOR_LABEL);
            if let Some(count) = self.stdin_count.clone() {
                self.assembler.mov_load_rip(&count, crate::runtime::STDIN_COUNT_LABEL);
            }
        }
        let ends_in_return = matches!(body.statements.last(), Some(Stmt::Return { .. }));
        if !ends_in_return {
            self.assembler.mov_imm64("rax", 0);
        }
        for (index, statement) in body.statements.iter().enumerate() {
            self.emit_statement(statement, index + 1 == body.statements.len(), &homes);
        }
        self.assembler.label(&self.epilogue.clone());
        if let Some(cursor) = self.stdin_cursor.clone() {
            self.assembler.mov_store_rip(&cursor, crate::runtime::STDIN_CURSOR_LABEL);
        }
        if frame_function {
            for (index, register) in callee_registers.iter().enumerate() {
                self.assembler.mov_load_rbp(
                    register,
                    crate::codegen::slot_offset(function.slots as i64 + index as i64),
                );
            }
            self.assembler.leave_ret();
        } else {
            if delta_eligible {
                self.assembler.add_rsp(8 * 8);
            } else {
                if pad {
                    self.assembler.add_rsp(8);
                }
                if filler {
                    self.assembler.pop(crate::codegen::HOME_REGISTERS.last().unwrap());
                }
            }
            if pair_mode {
                self.assembler.pop("r13");
            }
            for register in self.hoist_homes.iter().rev() {
                self.assembler.pop(register);
            }
            for register in pushed.iter().rev() {
                self.assembler.pop(register);
            }
            self.assembler.ret();
        }
        self.delta_slot = -1;
        self.delta_value = 0;
        self.value_slot = -1;
        self.inline_depth = 0;
        self.current_name.clear();
    }



    fn load_slot(&mut self, slot: i64, destination: &str, homes: &[(i64, &str)]) {
        if let Some((source, count)) = self.shift_through.get(&slot).cloned() {
            self.assembler.mov_reg_reg(destination, &source);
            self.assembler.arith_imm(4, destination, -(1i64 << count));
            return;
        }
        if let Some(home) = home_for(slot, homes) {
            if slot == self.delta_slot && self.delta_value != 0 {
                self.assembler.lea_imm(destination, home, -self.delta_value);
            } else if home != destination {
                self.assembler.mov_reg_reg(destination, home);
            }
        } else {
            self.assembler.mov_load_rbp(destination, crate::codegen::slot_offset(slot));
        }
    }

    fn store_slot(&mut self, slot: i64, source: &str, homes: &[(i64, &str)]) {
        if let Some(home) = home_for(slot, homes) {
            if home != source {
                self.assembler.mov_reg_reg(home, source);
            }
        } else {
            self.assembler.mov_store_rbp(crate::codegen::slot_offset(slot), source);
        }
    }

    /// Leave array element `a[index]` in `rax`, matching Python's `_emit_index_load`.
    fn emit_index_load(&mut self, index: &Expr, slot: i64, size: usize,
                       homes: &[(i64, &str)]) -> bool {
        match index {
            Expr::IntLiteral { value, .. } => {
                self.assembler.mov_load_rbp(
                    "rax", crate::codegen::slot_offset(slot + size as i64 - 1 - *value as i64));
                true
            }
            Expr::Name { slot: index_slot, .. } => {
                match home_for(*index_slot, homes) {
                    Some(index_home) => {
                        self.assembler.mov_load_indexed(
                            "rax", "rbp", index_home, 3,
                            crate::codegen::array_element_base(slot, size as i64));
                        true
                    }
                    None => false,
                }
            }
            Expr::Parens { inner, .. } => self.emit_index_load(inner, slot, size, homes),
            _ => false,
        }
    }

    fn emit_left_into(&mut self, expression: &Expr, destination: &str,
                      homes: &[(i64, &str)]) -> bool {
        let expr = crate::optimize::peel_parens(Some(expression)).unwrap();
        match expr {
            Expr::IntLiteral { value, .. } => {
                self.emit_literal(*value, destination);
                true
            }
            Expr::Name { slot, .. } => match home_for(*slot, homes) {
                Some(home) if home == destination => true,
                Some(home) if destination == "rax" => {
                    self.assembler.mov_reg_reg("rax", home);
                    true
                }
                Some(home) => {
                    self.assembler.mov_reg_reg(destination, home);
                    true
                }
                None => {
                    self.load_slot(*slot, destination, homes);
                    true
                }
            },
            Expr::Binary { .. } => self.try_emit_binary_into(expr, destination, homes),
            _ => false,
        }
    }

    fn combines_in_rax_only(&self, expression: &Expr, destination: &str,
                            parked_noop: bool, homes: &[(i64, &str)]) -> bool {
        let expr = crate::optimize::peel_parens(Some(expression)).unwrap();
        match expr {
            Expr::IntLiteral { .. } => true,
            Expr::Name { slot, .. } => match home_for(*slot, homes) {
                Some(home) => home != destination || parked_noop,
                None => true,
            },
            Expr::Binary { op, left, right, .. } => {
                if op == "*" {
                    let product = match (crate::optimize::peel_parens(Some(left)).unwrap(),
                                         crate::optimize::peel_parens(Some(right)).unwrap()) {
                        (Expr::Name { slot, .. }, Expr::IntLiteral { .. }) => Some(*slot),
                        (Expr::IntLiteral { .. }, Expr::Name { slot, .. }) => Some(*slot),
                        _ => None,
                    };
                    if let Some(slot) = product {
                        return match home_for(slot, homes) {
                            Some(home) if home == destination => false,
                            Some(_) => true,
                            None => false,
                        };
                    }
                    return false;
                }
                if matches!(op.as_str(), "/" | "%" | "&&" | "||"
                    | "==" | "!=" | "<" | "<=" | ">" | ">=") {
                    return false;
                }
                if op == "&" {
                    if let Some((operand, _)) = logical_shift_shape(expr) {
                        return self.combines_in_rax_only(operand, destination, parked_noop, homes);
                    }
                }
                let right = crate::optimize::peel_parens(Some(right)).unwrap();
                let Expr::IntLiteral { value, .. } = right else { return false };
                let in_range = *value >= -0x80000000 && *value <= 0x7FFFFFFF;
                if matches!(op.as_str(), "+" | "-" | "&" | "|" | "^") && in_range {
                    return self.combines_in_rax_only(left, destination, parked_noop, homes);
                }
                if matches!(op.as_str(), "<<" | ">>") && *value >= 0 && *value <= 63 {
                    return self.combines_in_rax_only(left, destination, parked_noop, homes);
                }
                false
            }
            _ => false,
        }
    }

    /// Compute a binary expression straight into `destination`, matching Python's
    /// `_emit_binary_into`; refuses shapes it cannot keep destination untouched.
    fn try_emit_binary_into(&mut self, expression: &Expr, destination: &str,
                            homes: &[(i64, &str)]) -> bool {
        if destination == "rax" {
            return false;
        }
        let Expr::Binary { op, left, right, .. } =
            crate::optimize::peel_parens(Some(expression)).unwrap() else { return false };
        if op == "&" {
            if let Some((operand, shift)) = logical_shift_shape(expression) {
                if self.emit_left_into(operand, destination, homes) {
                    self.assembler.shift_imm(5, destination, shift);
                    return true;
                }
            }
            if self.delta_value == 0 {
                let mask_and_slot = match (crate::optimize::peel_parens(Some(left)).unwrap(),
                                           crate::optimize::peel_parens(Some(right)).unwrap()) {
                    (Expr::IntLiteral { value, .. }, other) => Some((*value, other)),
                    (other, Expr::IntLiteral { value, .. }) => Some((*value, other)),
                    _ => None,
                };
                if let Some((mask, other)) = mask_and_slot {
                    if matches!(mask, 255 | 65535) {
                        if let Some(slot) = expr_slot(other) {
                            if slot != self.delta_slot && slot != self.value_slot {
                                if let Some(home) = home_for(slot, homes) {
                                    if home != destination {
                                        if mask == 255 {
                                            self.assembler.movzx_from8(destination, home);
                                        } else {
                                            self.assembler.movzx_from16(destination, home);
                                        }
                                        return true;
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        let right = crate::optimize::peel_parens(Some(right)).unwrap();
        if let Expr::IntLiteral { value, .. } = right {
            let ext = match op.as_str() {
                "+" => Some(0), "-" => Some(5), "&" => Some(4), "|" => Some(1), "^" => Some(6),
                _ => None,
            };
            if let Some(ext) = ext {
                if *value >= -0x80000000 && *value <= 0x7FFFFFFF
                    && self.emit_left_into(left, destination, homes) {
                    self.assembler.arith_imm(ext, destination, *value as i64);
                    return true;
                }
            }
        }
        if op == "-" {
            if let Expr::Name { slot, .. } = right {
                if let Some(right_home) = home_for(*slot, homes) {
                    if self.emit_left_into(left, destination, homes) {
                        self.assembler.binary(0x29, destination, right_home);
                        return true;
                    }
                }
            }
            return false;
        }
        if op == "*" {
            let peeled_left = crate::optimize::peel_parens(Some(left)).unwrap();
            if let Expr::Name { slot, .. } = peeled_left {
                if let (Some(home), Expr::IntLiteral { value, .. }) =
                    (home_for(*slot, homes), right) {
                    let scale = match *value { 3 => Some(1u8), 5 => Some(2), 9 => Some(3), _ => None };
                    if let Some(scale) = scale {
                        self.assembler.lea_scale(destination, home, home, scale, 0);
                        return true;
                    }
                    if *value >= -2147483648 && *value <= 2147483647 {
                        self.assembler.imul_imm(destination, home, *value as i64);
                        return true;
                    }
                }
            }
            if self.emit_left_into(left, destination, homes) {
                if let Expr::Name { slot, .. } = right {
                    if let Some(home) = home_for(*slot, homes) {
                        self.assembler.imul(destination, home);
                        return true;
                    }
                }
                if let Expr::IntLiteral { value, .. } = right {
                    if let Some(home) = self.hoist_imm.get(value).cloned() {
                        self.assembler.imul(destination, &home);
                        return true;
                    }
                }
                self.emit_expression(right, homes);
                self.assembler.imul(destination, "rax");
                return true;
            }
            return false;
        }
        if op == "<<" || op == ">>" {
            if let Expr::IntLiteral { value, .. } = right {
                if *value >= 0 && *value <= 63 && self.emit_left_into(left, destination, homes) {
                    self.assembler.shift_imm(if op == "<<" { 4 } else { 7 },
                                             destination, *value as u8);
                    return true;
                }
            }
            return false;
        }
        if let Some(opcode) = match op.as_str() {
            "+" => Some(0x01), "&" => Some(0x21), "|" => Some(0x09), "^" => Some(0x31),
            _ => None,
        } {
            let peeled_left = crate::optimize::peel_parens(Some(left)).unwrap();
            let parked_noop = matches!(peeled_left, Expr::Name { slot, .. }
                if home_for(*slot, homes) == Some(destination));
            if self.combines_in_rax_only(right, destination, parked_noop, homes)
                && self.emit_left_into(left, destination, homes) {
                if let Expr::Name { slot, .. } = right {
                    if let Some(home) = home_for(*slot, homes) {
                        self.assembler.binary(opcode, destination, home);
                        return true;
                    }
                }
                self.emit_expression(right, homes);
                self.assembler.binary(opcode, destination, "rax");
                return true;
            }
        }
        false
    }

    /// Emit `slot = slot op value` as one memory-operand instruction on a frame slot.
    fn try_fold_frame_update(&mut self, slot: i64, expression: &Expr,
                             homes: &[(i64, &str)]) -> bool {
        if home_for(slot, homes).is_some() {
            return false;
        }
        let Expr::Binary { op, left, right, .. } =
            crate::optimize::peel_parens(Some(expression)).unwrap() else { return false };
        let opcode = match op.as_str() {
            "+" => Some(0x01), "-" => Some(0x29), "&" => Some(0x21),
            "|" => Some(0x09), "^" => Some(0x31), _ => None,
        };
        let Some(opcode) = opcode else { return false };
        let commutative = op != "-";
        let other = if commutative {
            if expr_slot(left) == Some(slot) {
                right.as_ref()
            } else if expr_slot(right) == Some(slot) {
                left.as_ref()
            } else {
                return false;
            }
        } else if expr_slot(left) == Some(slot) {
            right.as_ref()
        } else {
            return false;
        };
        let offset = crate::codegen::slot_offset(slot);
        let other = crate::optimize::peel_parens(Some(other)).unwrap();
        if op == "+" || op == "-" {
            if let Expr::IntLiteral { value, .. } = other {
                if *value >= -0x80000000 && *value <= 0x7FFFFFFF {
                    let extension = if op == "+" { 0 } else { 5 };
                    self.assembler.group1_store_rbp_imm(extension, offset, *value as i64);
                    return true;
                }
            }
        }
        if let Expr::Name { slot: other_slot, .. } = other {
            if let Some(home) = home_for(*other_slot, homes) {
                self.assembler.binary_store_rbp(opcode, offset, home);
                return true;
            }
        }
        self.emit_into(other, "rax", homes);
        self.assembler.binary_store_rbp(opcode, offset, "rax");
        true
    }

    /// Emit `home = home op value` directly when the expression updates its own slot.
    fn try_fold_update(&mut self, slot: i64, expression: &Expr, home: &str,
                       homes: &[(i64, &str)]) -> bool {
        let Expr::Binary { op, left, right, .. } =
            crate::optimize::peel_parens(Some(expression)).unwrap() else { return false };
        let commutative = matches!(op.as_str(), "+" | "*" | "&" | "|" | "^");
        let other = if commutative {
            if expr_slot(left) == Some(slot) {
                Some(right.as_ref())
            } else if expr_slot(right) == Some(slot) {
                Some(left.as_ref())
            } else {
                None
            }
        } else if op == "-" && expr_slot(left) == Some(slot) {
            Some(right.as_ref())
        } else {
            None
        };
        let Some(other) = other else { return false };
        let opcode = match op.as_str() {
            "+" => Some(0x01), "-" => Some(0x29), "&" => Some(0x21),
            "|" => Some(0x09), "^" => Some(0x31), _ => None,
        };
        let other = crate::optimize::peel_parens(Some(other)).unwrap();
        if matches!(op.as_str(), "+" | "-") {
            if let Expr::IntLiteral { value, .. } = other {
                if *value >= -0x80000000 && *value <= 0x7FFFFFFF {
                    if op == "+" {
                        self.assembler.add_imm(home, *value as i64);
                    } else {
                        self.assembler.sub_imm(home, *value as i64);
                    }
                    return true;
                }
            }
        }
        if let Expr::Name { slot: other_slot, .. } = other {
            if let Some(other_home) = home_for(*other_slot, homes) {
                if op == "*" {
                    self.assembler.imul(home, other_home);
                } else {
                    self.assembler.binary(opcode.unwrap(), home, other_home);
                }
                return true;
            }
        }
        // A hoisted literal already lives in a register; combine with that home
        // directly instead of copying it through rax first.
        let hoisted = if let Expr::IntLiteral { value, .. } = other {
            self.hoist_imm.get(value).cloned()
        } else {
            None
        };
        if let Some(hoisted) = hoisted {
            if op == "*" {
                self.assembler.imul(home, &hoisted);
            } else {
                self.assembler.binary(opcode.unwrap(), home, &hoisted);
            }
            return true;
        }
        self.emit_expression(other, homes);
        if op == "*" {
            self.assembler.imul(home, "rax");
        } else {
            self.assembler.binary(opcode.unwrap(), home, "rax");
        }
        true
    }

    fn emit_value(&mut self, slot: i64, expression: &Expr, homes: &[(i64, &str)]) {
        if let Some(home) = home_for(slot, homes) {
            if self.try_fold_update(slot, expression, home, homes) {
                return;
            }
        }
        match expression {
            Expr::IntLiteral { value, .. } => {
                if *value == 0 {
                    if let Some(home) = home_for(slot, homes) {
                        self.assembler.xor_zero(home);
                        return;
                    }
                }
                self.emit_literal(*value, "rax");
                self.store_slot(slot, "rax", homes);
            }
            Expr::Name { slot: source, .. } => {
                if let Some(home) = home_for(slot, homes) {
                    self.load_slot(*source, home, homes);
                } else {
                    self.load_slot(*source, "rax", homes);
                    self.store_slot(slot, "rax", homes);
                }
            }
            Expr::Parens { inner, .. } => self.emit_value(slot, inner, homes),
            Expr::ArrayLiteral { elements, .. } => {
                let fill = if elements.len() > crate::codegen::ARRAY_UNROLL_LIMIT {
                    match elements.first() {
                        Some(Expr::IntLiteral { value, .. })
                            if elements.iter().all(|element| matches!(element,
                                Expr::IntLiteral { value: found, .. } if found == value)) =>
                        {
                            Some(*value)
                        }
                        _ => None,
                    }
                } else {
                    None
                };
                if let Some(value) = fill {
                    let loop_label = self.fresh("array_fill");
                    self.assembler.mov_imm64("rdx", elements.len() as i64);
                    self.assembler.mov_imm64("rax", value as i64);
                    let base = crate::codegen::array_element_base(slot, elements.len() as i64);
                    self.assembler.lea_rbp("rcx", base);
                    let label = loop_label.clone();
                    self.assembler.label(&label);
                    self.assembler.mov_store_reg("rcx", "rax");
                    self.assembler.add_imm("rcx", 8);
                    self.assembler.sub_imm("rdx", 1);
                    let again = loop_label.clone();
                    self.assembler.jcc("nz", &again);
                } else {
                    for (index, element) in elements.iter().enumerate() {
                        self.emit_expression(element, homes);
                        self.assembler.mov_store_rbp(
                            crate::codegen::slot_offset(
                                slot + elements.len() as i64 - 1 - index as i64),
                            "rax");
                    }
                }
            }
            Expr::Binary { op, left, right, .. }
                if op == "+"
                    && matches!(left.as_ref(), Expr::Name { slot: src, .. } if *src == slot)
                    && matches!(right.as_ref(), Expr::IntLiteral { .. }) =>
            {
                let Expr::IntLiteral { value, .. } = right.as_ref() else { unreachable!() };
                if *value >= -0x80000000 && *value <= 0x7FFFFFFF {
                    if let Some(home) = home_for(slot, homes) {
                        self.assembler.add_imm(home, *value as i64);
                        return;
                    }
                }
                if let Some(home) = home_for(slot, homes) {
                    self.emit_literal(*value, "rax");
                    self.assembler.binary(0x01, home, "rax");
                } else {
                    self.load_slot(slot, "rax", homes);
                    self.emit_literal(*value, "rcx");
                    self.assembler.binary(0x01, "rax", "rcx");
                    self.store_slot(slot, "rax", homes);
                }
            }
            Expr::Binary { op, left, right, .. }
                if op == "+" && matches!(left.as_ref(), Expr::Name { slot: src, .. } if *src == slot)
                    && matches!(right.as_ref(), Expr::Index { .. }) =>
            {
                let Expr::Index { slot: array_slot, size, index, .. } = right.as_ref()
                    else { unreachable!() };
                let home = home_for(slot, homes);
                if let Some(home) = home {
                    if self.emit_index_load(index, *array_slot, *size, homes) {
                        self.assembler.binary(0x01, home, "rax");
                        return;
                    }
                }
            }
            Expr::Binary { op, left, right, .. }
                if op == "+" && matches!(right.as_ref(), Expr::Name { slot: src, .. } if *src == slot)
                    && matches!(left.as_ref(), Expr::Index { .. }) =>
            {
                let Expr::Index { slot: array_slot, size, index, .. } = left.as_ref()
                    else { unreachable!() };
                let home = home_for(slot, homes);
                if let Some(home) = home {
                    if self.emit_index_load(index, *array_slot, *size, homes) {
                        self.assembler.binary(0x01, home, "rax");
                        return;
                    }
                }
            }
            other => {
                if let Some(home) = home_for(slot, homes) {
                    if self.try_emit_binary_into(other, home, homes) {
                        return;
                    }
                }
                let saved_copy_home = self.copy_home.take();
                if matches!(other, Expr::Call { .. }) {
                    self.copy_home = home_for(slot, homes).map(str::to_string);
                    self.copy_home_used = false;
                }
                self.emit_expression(other, homes);
                let used = self.copy_home_used;
                self.copy_home = saved_copy_home;
                self.copy_home_used = false;
                if !used {
                    self.store_slot(slot, "rax", homes);
                }
            }
        }
    }

    fn emit_statement(&mut self, statement: &Stmt, tail: bool, homes: &[(i64, &str)]) {
        match statement {
            Stmt::Return { value, .. } => {
                if tail {
                    if let Some(destination) = self.copy_return.clone() {
                        self.emit_into(value, &destination, homes);
                    } else {
                        self.emit_expression(value, homes);
                    }
                } else {
                    self.emit_expression(value, homes);
                }
                if !tail {
                    let epilogue = self.epilogue.clone();
                    self.assembler.jmp(&epilogue);
                }
            }
            Stmt::ExprStmt { expression, .. } => self.emit_expression(expression, homes),
            Stmt::Assign { slot, value, index: Some(index), array_size: Some(size), .. }
                if matches!(value, Expr::Name { .. })
                    && matches!(index, Expr::Name { .. }) =>
            {
                let Expr::Name { slot: index_slot, .. } = index else { unreachable!() };
                let Expr::Name { slot: value_slot, .. } = value else { unreachable!() };
                match (home_for(*index_slot, homes), home_for(*value_slot, homes)) {
                    (Some(index_home), Some(value_home)) if index_home == value_home => {
                        let displacement = crate::codegen::array_element_base(*slot, *size as i64);
                        self.assembler.mov_store_indexed(
                            "rbp", index_home, 3, displacement, value_home);
                    }
                    _ => self.emit_value(*slot, value, homes),
                }
            }
            Stmt::Assign { slot, value, index: Some(index), array_size: Some(size), .. }
                if matches!(value, Expr::IntLiteral { .. })
                    && matches!(index, Expr::Name { .. }) =>
            {
                let Expr::IntLiteral { value: number, .. } = value else { unreachable!() };
                let Expr::Name { slot: index_slot, .. } = index else { unreachable!() };
                match home_for(*index_slot, homes) {
                    Some(index_home) if *number >= -0x80000000 && *number <= 0x7FFFFFFF => {
                        let displacement = crate::codegen::array_element_base(*slot, *size as i64);
                        self.assembler.mov_store_indexed_imm(
                            "rbp", index_home, 3, displacement, *number as i64);
                    }
                    _ => self.emit_value(*slot, value, homes),
                }
            }
            Stmt::Assign { slot, value, index: Some(index), array_size: Some(size), .. } => {
                // Any other element store: `a[e] = value`. The index is settled first so
                // a value that reads the array or calls cannot disturb it, then the value
                // is evaluated into rax and stored through the element address.
                let displacement = crate::codegen::array_element_base(*slot, *size as i64);
                enum Anchor<'a> { Home(&'a str), Constant(i128), Pushed }
                let anchor = match crate::optimize::peel_parens(Some(index)).unwrap() {
                    Expr::Name { slot: index_slot, .. }
                        if home_for(*index_slot, homes).is_some() =>
                    {
                        Anchor::Home(home_for(*index_slot, homes).unwrap())
                    }
                    Expr::IntLiteral { value, .. } => Anchor::Constant(*value),
                    other => {
                        self.emit_into(other, "rax", homes);
                        self.assembler.push("rax");
                        self.depth += 8;
                        Anchor::Pushed
                    }
                };
                self.emit_expression(value, homes);
                match anchor {
                    Anchor::Home(index_home) => self.assembler.mov_store_indexed(
                        "rbp", index_home, 3, displacement, "rax"),
                    Anchor::Constant(constant) => {
                        let offset = displacement + 8 * constant as i64;
                        self.assembler.binary_store_rbp(0x89, offset, "rax");
                    }
                    Anchor::Pushed => {
                        self.assembler.pop("rcx");
                        self.depth -= 8;
                        self.assembler.mov_store_indexed("rbp", "rcx", 3, displacement, "rax");
                    }
                }
            }
            Stmt::Let { slot, value, .. } | Stmt::Assign { slot, value, .. } => {
                if !self.try_fold_frame_update(*slot, value, homes) {
                    self.emit_value(*slot, value, homes);
                }
            }
            Stmt::Block(block) => {
                for (index, inner) in block.statements.iter().enumerate() {
                    self.emit_statement(inner, tail && index + 1 == block.statements.len(), homes);
                }
            }
            Stmt::If { condition, then_block, else_block, .. } => {
                if else_block.is_none() && then_block.statements.len() == 1 {
                    let target = match &then_block.statements[0] {
                        Stmt::Break { .. } => self.break_targets.last(),
                        Stmt::Continue { .. } => self.continue_targets.last(),
                        _ => None,
                    };
                    if let Some(target) = target.cloned() {
                        self.emit_condition_polarity(condition, &target, homes, true);
                        return;
                    }
                }
                if !self.try_emit_select_if(condition, then_block, else_block.as_ref(), homes)
                    && !self.try_emit_cmov_swap(condition, then_block,
                                                else_block.as_ref(), homes) {
                    self.emit_if(condition, then_block, else_block.as_ref(), tail, homes);
                }
            }
            Stmt::While { condition, body, countdown, .. } => {
                let plan = self.hoist_plans.get(&(statement as *const Stmt as usize)).cloned();
                let saved = self.hoist_imm.clone();
                if let Some(plan) = plan {
                    for (value, home) in &plan {
                        self.assembler.mov_imm64(home, *value as i64);
                    }
                    for (value, home) in &plan {
                        self.hoist_imm.insert(*value, home.clone());
                    }
                }
                if !self.try_emit_rmw_vector(condition, body, homes)
                    && !self.try_emit_stdin_scan(condition, body, homes)
                    && !self.try_emit_halving_loop(condition, body, homes) {
                    self.emit_while(condition, body, *countdown, homes);
                }
                self.hoist_imm = saved;
            }
            Stmt::Break { .. } => {
                if let Some(target) = self.break_targets.last().cloned() {
                    self.assembler.jmp(&target);
                }
            }
            Stmt::Continue { .. } => {
                if let Some(target) = self.continue_targets.last().cloned() {
                    self.assembler.jmp(&target);
                }
            }
            // Wider shapes are added in later slices; refusing beats emitting wrong bytes.
            Stmt::For { .. } => {}
        }
    }

    fn emit_block(&mut self, block: &crate::syntax::Block, tail: bool, homes: &[(i64, &str)]) {
        let statements = &block.statements;
        let mut index = 0;
        while index < statements.len() {
            let fused = if index + 1 < statements.len() {
                let hex_arg = match &statements[index] {
                    Stmt::ExprStmt { expression: Expr::Call { name, arguments, .. }, .. }
                        if name == "print_hex" && arguments.len() == 1 => Some(&arguments[0]),
                    _ => None,
                };
                hex_arg.filter(|_| match &statements[index + 1] {
                    Stmt::ExprStmt { expression: Expr::Call { name, arguments, .. }, .. } =>
                        name == "print_char" && arguments.len() == 1
                            && matches!(&arguments[0],
                                Expr::IntLiteral { value, .. } if *value == 10),
                    _ => false,
                })
            } else {
                None
            };
            if let Some(argument) = fused {
                self.emit_expression(argument, homes);
                self.assembler.push("rax");
                self.depth += 8;
                self.call_site(crate::runtime::PRINT_HEX_LINE_LABEL);
                self.depth -= 8;
                self.assembler.pop("rax");
                index += 2;
                continue;
            }
            self.emit_statement(&statements[index], tail && index + 1 == statements.len(), homes);
            index += 1;
        }
    }

    /// Lower `while i < N { a[i] = a[i] + r; i = i + 1; }` with two SSE2 lanes.
    /// Emit a fused odd-pair Collatz loop as one unconditional half-step.
    fn try_emit_halving_loop(&mut self, condition: &Expr, body: &crate::syntax::Block,
                             homes: &[(i64, &str)]) -> bool {
        if body.statements.len() != 1 {
            return false;
        }
        let Stmt::If { condition: test, then_block: then_body,
                       else_block: else_body, .. } = &body.statements[0]
            else { return false };
        let Some(else_body) = else_body.as_ref() else { return false };
        let Expr::Binary { op, left, right, .. } =
            crate::optimize::peel_parens(Some(condition)).unwrap() else { return false };
        if op != ">" && op != ">=" {
            return false;
        }
        let Expr::IntLiteral { value: bound, .. } =
            crate::optimize::peel_parens(Some(right)).unwrap() else { return false };
        if if op == ">" { *bound < 0 } else { *bound < 1 } {
            return false;
        }
        let Some(k_slot) = expr_slot(left) else { return false };
        let Expr::Binary { op: test_op, left: parity, right: zero, .. } =
            crate::optimize::peel_parens(Some(test)).unwrap() else { return false };
        if test_op != "==" && test_op != "!=" {
            return false;
        }
        if !matches!(crate::optimize::peel_parens(Some(zero)).unwrap(),
            Expr::IntLiteral { value, .. } if *value == 0) {
            return false;
        }
        let Expr::Binary { op: mod_op, left: modulo_slot, right: divisor, .. } =
            crate::optimize::peel_parens(Some(parity)).unwrap() else { return false };
        if mod_op != "%" || expr_slot(modulo_slot) != Some(k_slot) {
            return false;
        }
        if !matches!(crate::optimize::peel_parens(Some(divisor)).unwrap(),
            Expr::IntLiteral { value, .. } if *value == 2) {
            return false;
        }
        let (even, odd) = if test_op == "==" {
            (then_body, else_body)
        } else {
            (else_body, then_body)
        };
        if even.statements.len() != 2 || odd.statements.len() != 2 {
            return false;
        }
        let Stmt::Assign { slot: even_slot, value: even_value, .. } = &even.statements[0]
            else { return false };
        if *even_slot != k_slot {
            return false;
        }
        let Expr::Binary { op: division, left: dividend, right: divisor_two, .. } =
            crate::optimize::peel_parens(Some(&even_value)).unwrap() else { return false };
        if division != "/" || expr_slot(dividend) != Some(k_slot) {
            return false;
        }
        if !matches!(crate::optimize::peel_parens(Some(divisor_two)).unwrap(),
            Expr::IntLiteral { value, .. } if *value == 2) {
            return false;
        }
        let Stmt::Assign { slot: odd_slot, value: odd_value, .. } = &odd.statements[0]
            else { return false };
        if *odd_slot != k_slot {
            return false;
        }
        let Expr::Binary { op: fused_div, left: sum, right: fused_two, .. } =
            crate::optimize::peel_parens(Some(&odd_value)).unwrap() else { return false };
        if fused_div != "/" {
            return false;
        }
        if !matches!(crate::optimize::peel_parens(Some(fused_two)).unwrap(),
            Expr::IntLiteral { value, .. } if *value == 2) {
            return false;
        }
        let Some((operand, scale_log2, addend)) = scaled_sum_terms(sum) else { return false };
        if operand != k_slot || !matches!(scale_log2, 1 | 2 | 3) {
            return false;
        }
        let multiplier = (1i128 << scale_log2) + 1;
        if addend % 2 == 0 || multiplier + addend < 4
            || (multiplier + addend) / 2 < -0x80000000
            || (multiplier + addend) / 2 > 0x7FFFFFFF {
            return false;
        }
        let Stmt::Assign { slot: step_slot, value: even_step, .. } = &even.statements[1]
            else { return false };
        let Stmt::Assign { slot: odd_step_slot, value: odd_step, .. } = &odd.statements[1]
            else { return false };
        if step_slot != odd_step_slot || *step_slot == k_slot {
            return false;
        }
        if !is_plus_step(&even_step, *step_slot, 1) || !is_plus_step(&odd_step, *step_slot, 2) {
            return false;
        }
        let (Some(k_home), Some(step_home)) =
            (home_for(k_slot, homes), home_for(*step_slot, homes)) else { return false };
        // The generic loop head reserves start/end before the body specialiser runs.
        let _ = self.fresh("while.start");
        let _ = self.fresh("while.end");
        let end = self.fresh("while.end");
        let top = self.fresh("while.half");
        let latch = self.fresh("while.latch");
        self.emit_condition_polarity(condition, &end, homes, false);
        let padding = (16 - self.assembler.here() % 16) % 16;
        if padding > 0 {
            self.assembler.nop_pad(padding);
        }
        let t = top.clone();
        self.assembler.label(&t);
        self.assembler.shift_imm(5, k_home, 1);
        self.assembler.lea_scale("rsi", k_home, k_home, scale_log2 as u8,
                                 ((multiplier + addend) / 2) as i64);
        self.assembler.cmov("c", k_home, "rsi");
        self.assembler.adc_imm(step_home, 1);
        let l = latch.clone();
        self.assembler.label(&l);
        self.emit_condition_polarity(condition, &top, homes, true);
        self.assembler.jmp(&end);
        let e = end.clone();
        self.assembler.label(&e);
        true
    }

    /// Rotate `while true { c = read_char(); if c < 0 break; total += c }`.
    fn try_emit_stdin_scan(&mut self, condition: &Expr, body: &crate::syntax::Block,
                           homes: &[(i64, &str)]) -> bool {
        if !matches!(condition, Expr::IntLiteral { value, .. } if *value != 0) {
            return false;
        }
        if body.statements.len() != 3 {
            return false;
        }
        let Stmt::Let { slot: read_slot, value: read_value, .. } = &body.statements[0]
            else { return false };
        if !matches!(read_value, Expr::Call { name, arguments, .. }
            if name == "read_char" && arguments.is_empty()) {
            return false;
        }
        let Stmt::If { condition: guard, then_block, else_block, .. } = &body.statements[1]
            else { return false };
        if else_block.is_some() || then_block.statements.len() != 1 {
            return false;
        }
        if !matches!(&then_block.statements[0], Stmt::Break { .. }) {
            return false;
        }
        let Expr::Binary { op, left, right, .. } =
            crate::optimize::peel_parens(Some(guard)).unwrap() else { return false };
        if op != "<" || expr_slot(left) != Some(*read_slot) {
            return false;
        }
        if !matches!(crate::optimize::peel_parens(Some(right)).unwrap(),
            Expr::IntLiteral { value, .. } if *value == 0) {
            return false;
        }
        let Stmt::Assign { slot: total_slot, value: update, .. } = &body.statements[2]
            else { return false };
        let Expr::Binary { op: update_op, left, right, .. } = update else { return false };
        if update_op != "+" {
            return false;
        }
        let pair = [expr_slot(left), expr_slot(right)];
        if !pair.contains(&Some(*total_slot)) || !pair.contains(&Some(*read_slot)) {
            return false;
        }
        let Some(total_home) = home_for(*total_slot, homes) else { return false };
        if total_home == "rax" || self.stdin_cursor.is_none() {
            return false;
        }
        let test = self.fresh("stdin_scan.test");
        let add = self.fresh("stdin_scan.add");
        let fetch = self.fresh("stdin_scan.fetch");
        let end = self.fresh("stdin_scan.end");
        self.assembler.jmp(&fetch);
        let a = add.clone();
        self.assembler.label(&a);
        self.assembler.binary(0x01, total_home, "rax");
        let f = fetch.clone();
        self.assembler.label(&f);
        if !self.emit_read_char_into(Some(&test)) {
            return false;
        }
        let t = test.clone();
        self.assembler.label(&t);
        self.assembler.cmp_imm("rax", 0);
        self.assembler.jcc("ge", &add);
        let e = end.clone();
        self.assembler.label(&e);
        true
    }

    fn try_emit_rmw_vector(&mut self, condition: &Expr, body: &crate::syntax::Block,
                           homes: &[(i64, &str)]) -> bool {
        let Expr::Binary { op, left, right, .. } = condition else { return false };
        if op != "<" { return false; }
        let (index_slot, bound) = match (left.as_ref(), right.as_ref()) {
            (Expr::Name { slot, .. }, Expr::IntLiteral { value, .. }) => (*slot, *value),
            _ => return false,
        };
        if bound < 2 || bound % 2 != 0 || body.statements.len() != 2 { return false; }
        let Stmt::Assign { slot: store_slot, index: Some(store_index), value: store_value,
                           array_size, .. } = &body.statements[0] else { return false; };
        let Some(array_size) = array_size else { return false; };
        let Expr::Binary { op: add_op, left: add_left, right: add_right, .. } = store_value else {
            return false;
        };
        if add_op != "+" { return false; }
        let element = match (add_left.as_ref(), add_right.as_ref()) {
            (Expr::Index { .. }, _) => Some(add_left.as_ref()),
            (_, Expr::Index { .. }) => Some(add_right.as_ref()),
            _ => None,
        };
        let Some(Expr::Index { operand, index, .. }) = element else { return false; };
        if !matches!(operand.as_ref(), Expr::Name { .. }) { return false; }
        if !matches!(index.as_ref(), Expr::Name { slot, .. } if *slot == index_slot) { return false; }
        if !matches!(store_index, Expr::Name { slot, .. } if *slot == index_slot) { return false; }
        let addend = if matches!(add_left.as_ref(), Expr::Index { .. }) {
            add_right.as_ref()
        } else {
            add_left.as_ref()
        };
        let Expr::Name { slot: addend_slot, .. } = addend else { return false; };
        if *addend_slot == index_slot { return false; }
        let Stmt::Assign { slot: step_slot, value: step_value, .. } = &body.statements[1] else {
            return false;
        };
        if *step_slot != index_slot { return false; }
        let Expr::Binary { op: step_op, left: step_left, right: step_right, .. } = step_value else {
            return false;
        };
        if step_op != "+" { return false; }
        if !matches!(step_left.as_ref(), Expr::Name { slot, .. } if *slot == index_slot) { return false; }
        if !matches!(step_right.as_ref(), Expr::IntLiteral { value, .. } if *value == 1) { return false; }
        let (Some(index_home), Some(addend_home)) =
            (home_for(index_slot, homes), home_for(*addend_slot, homes)) else { return false };
        if index_home == addend_home { return false; }

        let start = self.fresh("vec_rmw.start");
        let end = self.fresh("vec_rmw.end");
        let wide_start = self.fresh("vec_rmw.wide");
        let wide_done = self.fresh("vec_rmw.wide.done");
        let displacement = crate::codegen::array_element_base(*store_slot, *array_size as i64);
        self.assembler.movq_from_reg("xmm1", addend_home);
        self.assembler.punpcklqdq("xmm1", "xmm1");
        // Four elements per iteration while at least four remain. The second
        // pair sits 16 bytes past the first; both use the same broadcast addend.
        let wide_label = wide_start.clone();
        self.assembler.label(&wide_label);
        self.assembler.cmp_imm(index_home, bound as i64 - 4);
        let wide_end = wide_done.clone();
        self.assembler.jcc("g", &wide_end);
        self.assembler.movdqu_load_indexed("xmm0", "rbp", index_home, 3, displacement);
        self.assembler.movdqu_load_indexed("xmm2", "rbp", index_home, 3, displacement + 16);
        self.assembler.paddq_reg("xmm0", "xmm1");
        self.assembler.paddq_reg("xmm2", "xmm1");
        self.assembler.movdqu_store_indexed("rbp", index_home, 3, displacement, "xmm0");
        self.assembler.movdqu_store_indexed("rbp", index_home, 3, displacement + 16, "xmm2");
        self.assembler.add_imm(index_home, 4);
        let wide_again = wide_start.clone();
        self.assembler.jmp(&wide_again);
        let wide_done_label = wide_done.clone();
        self.assembler.label(&wide_done_label);
        // Two-element pass for the remaining pair; the guard is `i <= bound - 2` so it
        // never reads past the bound however the loop's start lines up with its bound.
        let start_label = start.clone();
        self.assembler.label(&start_label);
        self.assembler.cmp_imm(index_home, bound as i64 - 2);
        let done = end.clone();
        self.assembler.jcc("g", &done);
        self.assembler.movdqu_load_indexed("xmm0", "rbp", index_home, 3, displacement);
        self.assembler.paddq_reg("xmm0", "xmm1");
        self.assembler.movdqu_store_indexed("rbp", index_home, 3, displacement, "xmm0");
        self.assembler.add_imm(index_home, 2);
        let again = start.clone();
        self.assembler.jmp(&again);
        let end_label = end.clone();
        self.assembler.label(&end_label);
        // A single scalar element when the pair passes leave one behind (an odd start or
        // an odd bound). At most one element can remain, so no loop is needed.
        self.assembler.cmp_imm(index_home, bound as i64);
        let scalar_done = self.fresh("vec_rmw.scalar.done");
        self.assembler.jcc("ge", &scalar_done);
        self.assembler.mov_load_indexed("rax", "rbp", index_home, 3, displacement);
        self.assembler.binary(0x01, "rax", addend_home);
        self.assembler.mov_store_indexed("rbp", index_home, 3, displacement, "rax");
        self.assembler.add_imm(index_home, 1);
        let scalar_label = scalar_done.clone();
        self.assembler.label(&scalar_label);
        true
    }

    fn emit_while(&mut self, condition: &Expr, body: &crate::syntax::Block,
                  countdown: bool, homes: &[(i64, &str)]) {
        let start = self.fresh("while.start");
        let end = self.fresh("while.end");
        self.emit_condition_polarity(condition, &end, homes, false);
        let pad = (16 - self.assembler.here() % 16) % 16;
        if pad > 0 {
            self.assembler.nop_pad(pad);
        }
        let start_label = start.clone();
        self.assembler.label(&start_label);
        if countdown {
            self.break_targets.push(end.clone());
            self.emit_block(body, false, homes);
            self.break_targets.pop();
            let counter = match crate::optimize::peel_parens(Some(condition)).unwrap() {
                Expr::Binary { left, right, .. } => {
                    expr_slot(left).or_else(|| expr_slot(right))
                }
                _ => None,
            };
            if let Some(slot) = counter {
                if let Some(home) = home_for(slot, homes) {
                    self.assembler.sub_imm(home, 1);
                } else {
                    self.load_slot(slot, "rax", homes);
                    self.assembler.sub_imm("rax", 1);
                    self.store_slot(slot, "rax", homes);
                }
            }
            let again = start.clone();
            self.assembler.jcc("nz", &again);
            let end_label = end.clone();
            self.assembler.label(&end_label);
            return;
        }
        let latch = self.fresh("while.latch");
        self.break_targets.push(end.clone());
        self.continue_targets.push(latch.clone());
        self.emit_block(body, false, homes);
        self.break_targets.pop();
        self.continue_targets.pop();
        let latch_label = latch.clone();
        self.assembler.label(&latch_label);
        self.emit_condition_polarity(condition, &start, homes, true);
        let end_label = end.clone();
        self.assembler.label(&end_label);
    }

    /// Emit a comparison that jumps to `target` when the relation holds.
    fn emit_condition(&mut self, expression: &Expr, target: &str, homes: &[(i64, &str)]) {
        self.emit_condition_polarity(expression, target, homes, true);
    }

    fn emit_condition_polarity(&mut self, expression: &Expr, target: &str, homes: &[(i64, &str)],
                               when_true: bool) {
        let peeled = crate::optimize::peel_parens(Some(expression)).unwrap();
        if let Expr::IntLiteral { value, .. } = peeled {
            if (*value == 0 || *value == 1) && (*value != 0) == when_true {
                self.assembler.jmp(target);
            }
            return;
        }
        let Expr::Binary { op, left, right, .. } = peeled else {
            return;
        };
        if op == "&&" || op == "||" {
            if op == "&&" {
                if when_true {
                    let skipped = self.fresh("and.short");
                    self.emit_condition_polarity(left, &skipped, homes, false);
                    self.emit_condition_polarity(right, target, homes, true);
                    let s = skipped.clone();
                    self.assembler.label(&s);
                } else {
                    self.emit_condition_polarity(left, target, homes, false);
                    self.emit_condition_polarity(right, target, homes, false);
                }
            } else {
                let skipped = self.fresh("or.short");
                self.emit_condition_polarity(left, &skipped, homes, true);
                self.emit_condition_polarity(right, target, homes, when_true);
                let s = skipped.clone();
                self.assembler.label(&s);
            }
            return;
        }
        let base_condition = match op.as_str() {
            "==" => "e", "!=" => "ne", "<" => "l", "<=" => "le", ">" => "g", ">=" => "ge",
            _ => return,
        };
        let condition = if when_true {
            base_condition
        } else {
            match base_condition {
                "e" => "ne", "ne" => "e", "l" => "ge", "ge" => "l", "le" => "g", "g" => "le",
                other => other,
            }
        };
        // `(x & m) == 0` / `x % 2**b == 0` reads the low bits directly with test.
        if matches!(op.as_str(), "==" | "!=") {
            if let Expr::IntLiteral { value: zero, .. } = right.as_ref() {
                if *zero == 0 {
                    if let Expr::Binary { op: inner_op, left: inner_left, right: inner_right, .. } =
                        crate::optimize::peel_parens(Some(left)).unwrap() {
                        let bit_test = if inner_op == "%" {
                            match crate::optimize::peel_parens(Some(inner_right)).unwrap() {
                                Expr::IntLiteral { value: divisor, .. } if *divisor >= 2 => {
                                    let power = (*divisor as u64).trailing_zeros() as u32;
                                    if (1i128 << power) == *divisor {
                                        Some((inner_left.as_ref(), (1i128 << power) - 1))
                                    } else { None }
                                }
                                _ => None,
                            }
                        } else if inner_op == "&" {
                            match (crate::optimize::peel_parens(Some(inner_left)).unwrap(),
                                   crate::optimize::peel_parens(Some(inner_right)).unwrap()) {
                                (Expr::IntLiteral { value: mask, .. }, other)
                                    if *mask > 0 => Some((other, *mask)),
                                (other, Expr::IntLiteral { value: mask, .. })
                                    if *mask > 0 => Some((other, *mask)),
                                _ => None,
                            }
                        } else { None };
                        if let Some((value, mask)) = bit_test {
                            let parity = if op == "==" { "e" } else { "ne" };
                            let parity = if when_true { parity } else {
                                match parity { "e" => "ne", "ne" => "e", other => other }
                            };
                            if mask <= 127 {
                                if let Some(slot) = expr_slot(value) {
                                    if let Some(home) = home_for(slot, homes) {
                                        self.assembler.test_imm(home, mask as i64);
                                        self.assembler.jcc(parity, target);
                                        return;
                                    }
                                }
                            }
                            self.emit_expression(value, homes);
                            if mask <= 127 {
                                self.assembler.test_imm("rax", mask as i64);
                            } else if mask <= 0x7FFFFFFF {
                                self.assembler.arith_imm(4, "rax", mask as i64);
                            } else {
                                self.assembler.mov_imm64("rsi", mask as i64);
                                self.assembler.binary(0x21, "rax", "rsi");
                            }
                            self.assembler.jcc(parity, target);
                            return;
                        }
                    }
                }
            }
        }
        if let (Expr::Name { slot: left_slot, .. }, Expr::Name { slot: right_slot, .. }) =
            (left.as_ref(), right.as_ref()) {
            if let (Some(left_home), Some(right_home)) =
                (home_for(*left_slot, homes), home_for(*right_slot, homes)) {
                self.assembler.cmp_reg_reg(left_home, right_home);
                self.assembler.jcc(condition, target);
                return;
            }
        }
        match (left.as_ref(), right.as_ref()) {
            (Expr::Binary { .. }, Expr::IntLiteral { value, .. }) => {
                self.emit_expression(left.as_ref(), homes);
                self.assembler.cmp_imm("rax", *value as i64);
            }
            (Expr::Binary { .. }, Expr::Name { slot, .. }) => {
                self.emit_into(left.as_ref(), "rcx", homes);
                self.load_slot(*slot, "rax", homes);
                self.assembler.cmp_reg_reg("rcx", "rax");
            }
            (Expr::Index { .. }, Expr::IntLiteral { value, .. }) => {
                self.emit_expression(left.as_ref(), homes);
                self.assembler.cmp_imm("rax", *value as i64);
            }
            (Expr::IntLiteral { value, .. }, Expr::Index { .. }) => {
                self.emit_expression(right.as_ref(), homes);
                self.assembler.cmp_imm("rax", *value as i64);
            }
            _ => {
                let (name_slot, literal) = match (left.as_ref(), right.as_ref()) {
                    (Expr::Name { slot, .. }, Expr::IntLiteral { value, .. }) => {
                        (*slot, *value as i64)
                    }
                    (Expr::IntLiteral { value, .. }, Expr::Name { slot, .. }) => {
                        (*slot, *value as i64)
                    }
                    _ => return,
                };
                let literal = if name_slot == self.delta_slot {
                    literal + self.delta_value
                } else {
                    literal
                };
                if let Some(home) = home_for(name_slot, homes) {
                    if literal == 0 && matches!(base_condition, "e" | "ne")
                        && self.assembler.zf_status.as_deref() == Some(home) {
                        self.assembler.jcc(condition, target);
                        return;
                    }
                    self.assembler.cmp_imm(home, literal);
                } else {
                    self.load_slot(name_slot, "rax", homes);
                    self.assembler.cmp_imm("rax", literal);
                }
            }
        }
        self.assembler.jcc(condition, target);
    }

    /// Lower `if t > c { t = t - c; } else { t = t + c; }` as lea, sub, cmov.
    fn try_emit_select_if(&mut self, condition: &Expr, then_block: &crate::syntax::Block,
                          else_block: Option<&crate::syntax::Block>,
                          homes: &[(i64, &str)]) -> bool {
        let Some(else_block) = else_block else { return false };
        if then_block.statements.len() != 1 || else_block.statements.len() != 1 {
            return false;
        }
        let Stmt::Assign { slot, value: then_value, .. } = &then_block.statements[0]
            else { return false };
        let Stmt::Assign { slot: else_slot, value: else_value, .. } = &else_block.statements[0]
            else { return false };
        if slot != else_slot || self.delta_value != 0 {
            return false;
        }
        let Some(home) = home_for(*slot, homes) else { return false };
        let Expr::Binary { op, left, right, .. } =
            crate::optimize::peel_parens(Some(condition)).unwrap() else { return false };
        let base = match op.as_str() {
            "==" => "e", "!=" => "ne", "<" => "l", "<=" => "le", ">" => "g", ">=" => "ge",
            _ => return false,
        };
        if expr_slot(left) != Some(*slot) {
            return false;
        }
        let Some(other_slot) = expr_slot(right) else { return false };
        if other_slot == *slot {
            return false;
        }
        let Some(other_home) = home_for(other_slot, homes) else { return false };
        let side = |expression: &Expr, operator: &str| {
            let Expr::Binary { op, left, right, .. } =
                crate::optimize::peel_parens(Some(expression)).unwrap() else { return false };
            op == operator && expr_slot(left) == Some(*slot) && expr_slot(right) == Some(other_slot)
        };
        let move_condition = if side(then_value, "-") && side(else_value, "+") {
            match base {
                "e" => "ne", "ne" => "e", "l" => "ge", "ge" => "l", "le" => "g", "g" => "le",
                other => other,
            }
        } else if side(then_value, "+") && side(else_value, "-") {
            base
        } else {
            return false;
        };
        self.assembler.lea_scale("rax", home, other_home, 0, 0);
        self.assembler.binary(0x29, home, other_home);
        self.assembler.cmov(move_condition, home, "rax");
        true
    }

    /// Lower `if a > b { var t = a; a = b; b = t; }` as a flag-driven pair of cmovs.
    fn try_emit_cmov_swap(&mut self, condition: &Expr, then_block: &crate::syntax::Block,
                          else_block: Option<&crate::syntax::Block>,
                          homes: &[(i64, &str)]) -> bool {
        if else_block.is_some() || then_block.statements.len() != 3 {
            return false;
        }
        let Stmt::Let { slot: temp_slot, value: temp_value, .. } = &then_block.statements[0]
            else { return false };
        let Stmt::Assign { slot: a_slot, value: a_value, .. } = &then_block.statements[1]
            else { return false };
        let Stmt::Assign { slot: b_slot, value: b_value, .. } = &then_block.statements[2]
            else { return false };
        if temp_slot == a_slot || temp_slot == b_slot || a_slot == b_slot {
            return false;
        }
        if expr_slot(temp_value) != Some(*a_slot)
            || expr_slot(a_value) != Some(*b_slot)
            || expr_slot(b_value) != Some(*temp_slot) {
            return false;
        }
        let Expr::Binary { op, left, right, .. } =
            crate::optimize::peel_parens(Some(condition)).unwrap() else { return false };
        let (left_slot, right_slot) = (expr_slot(left), expr_slot(right));
        if !matches!(left_slot, Some(slot) if slot == *a_slot || slot == *b_slot) {
            return false;
        }
        if !matches!(right_slot, Some(slot) if slot == *a_slot || slot == *b_slot) {
            return false;
        }
        if left_slot == right_slot {
            return false;
        }
        let (Some(a_home), Some(b_home)) = (home_for(*a_slot, homes), home_for(*b_slot, homes))
            else { return false };
        let base = match op.as_str() {
            "==" => "e", "!=" => "ne", "<" => "l", "<=" => "le", ">" => "g", ">=" => "ge",
            _ => return false,
        };
        let move_condition = if left_slot == Some(*a_slot) {
            base
        } else {
            match base {
                "l" => "g", "g" => "l", "le" => "ge", "ge" => "le", other => other,
            }
        };
        let (flags_left, flags_right) = if left_slot == Some(*a_slot) {
            (a_home, b_home)
        } else {
            (b_home, a_home)
        };
        self.assembler.cmp_reg_reg(flags_left, flags_right);
        self.assembler.mov_reg_reg("rax", a_home);
        self.assembler.cmov(move_condition, a_home, b_home);
        self.assembler.cmov(move_condition, b_home, "rax");
        true
    }

    fn emit_if(&mut self, condition: &Expr, then_block: &crate::syntax::Block,
               else_block: Option<&crate::syntax::Block>, tail: bool, homes: &[(i64, &str)]) {
        if let Some(otherwise_block) = else_block {
            let then_label = self.fresh("if.then");
            let end_label = self.fresh("if.end");
            self.emit_condition(condition, &then_label, homes);
            self.emit_block(otherwise_block, tail, homes);
            let end = end_label.clone();
            self.assembler.jmp(&end);
            let then = then_label.clone();
            self.assembler.label(&then);
            self.emit_block(then_block, tail, homes);
            let end = end_label.clone();
            self.assembler.label(&end);
        } else {
            let otherwise = self.fresh("if.else");
            self.emit_condition_polarity(condition, &otherwise, homes, false);
            self.emit_block(then_block, tail, homes);
            let label = otherwise.clone();
            self.assembler.label(&label);
        }
    }

    fn emit_expression(&mut self, expression: &Expr, homes: &[(i64, &str)]) {
        match expression {
            Expr::IntLiteral { value, .. } => self.emit_literal(*value, "rax"),
            Expr::Parens { inner, .. } => self.emit_expression(inner, homes),
            Expr::StrLiteral { text, .. } => {
                let label = self.string_label(text);
                self.assembler.lea_rip("rax", &label);
            }
            Expr::Name { slot, .. } => self.load_slot(*slot, "rax", homes),
            Expr::Call { name, arguments, .. } => self.emit_call(name, arguments, homes),
            Expr::Binary { op, left, right, .. }
                if op == "+" && matches!(left.as_ref(), Expr::Name { .. })
                    && matches!(right.as_ref(), Expr::Name { .. }) =>
            {
                let Expr::Name { slot: left_slot, .. } = left.as_ref() else { unreachable!() };
                let Expr::Name { slot: right_slot, .. } = right.as_ref() else { unreachable!() };
                match (home_for(*left_slot, homes), home_for(*right_slot, homes)) {
                    (Some(base), Some(index)) => {
                        self.assembler.lea_scale("rax", base, index, 0, 0);
                    }
                    _ => {}
                }
            }
            Expr::Index { operand, index, slot: base, .. }
                if matches!(operand.as_ref(), Expr::Name { .. })
                    && matches!(index.as_ref(), Expr::IntLiteral { .. }) =>
            {
                let Expr::IntLiteral { value, .. } = index.as_ref() else { unreachable!() };
                let size = self.arrays.iter()
                    .find(|(span_base, _)| span_base == base)
                    .map(|(_, size)| *size)
                    .unwrap_or(1);
                self.assembler.mov_load_rbp(
                    "rax", crate::codegen::slot_offset(*base + size as i64 - 1 - *value as i64));
            }
            Expr::Index { index, slot, size, .. }
                if matches!(index.as_ref(), Expr::Name { .. }) =>
            {
                let Expr::Name { slot: index_slot, .. } = index.as_ref() else { unreachable!() };
                if let Some(index_home) = home_for(*index_slot, homes) {
                    self.assembler.mov_load_indexed(
                        "rax", "rbp", index_home, 3,
                        crate::codegen::array_element_base(*slot, *size as i64));
                }
            }
            Expr::Binary { op, left, right, .. } => {
                if op == "<<" {
                    if let Expr::Name { slot, .. } =
                        crate::optimize::peel_parens(Some(left)).unwrap() {
                        if let Some((source, forwarded)) = self.shift_through.get(slot).cloned() {
                            if matches!(crate::optimize::peel_parens(Some(right)).unwrap(),
                                Expr::IntLiteral { value, .. } if *value as u32 == forwarded) {
                                self.assembler.mov_reg_reg("rax", &source);
                                self.assembler.arith_imm(4, "rax", -(1i64 << forwarded));
                                return;
                            }
                        }
                    }
                    if let Some((operand, count)) = shift_through_shape(expression) {
                        if matches!(operand, Expr::Name { .. } | Expr::IntLiteral { .. }) {
                            self.emit_expression(operand, homes);
                            self.assembler.arith_imm(4, "rax", -(1i64 << count));
                            return;
                        }
                    }
                }
                if self.try_emit_power_of_two_division(op, left, right, homes)
                    || self.try_emit_magic_division(op, left, right, homes) {
                    return;
                }
                if op == "&" {
                    if let Some((operand, shift)) = logical_shift_shape(expression) {
                        if matches!(operand, Expr::Name { .. } | Expr::IntLiteral { .. }) {
                            self.emit_expression(operand, homes);
                            self.assembler.shift_imm(5, "rax", shift);
                            return;
                        }
                    }
                }
                self.emit_binary(op, left, right, homes);
            }
            Expr::Unary { .. } | Expr::Index { .. } | Expr::ArrayLiteral { .. } => {}
        }
    }

    /// Lower division by a positive power of two as a sign-fixup shift chain.
    fn try_emit_power_of_two_division(&mut self, op: &str, left: &Expr, right: &Expr,
                                      homes: &[(i64, &str)]) -> bool {
        if op != "/" && op != "%" {
            return false;
        }
        let Expr::IntLiteral { value: divisor, .. } =
            crate::optimize::peel_parens(Some(right)).unwrap() else { return false };
        if *divisor <= 0 {
            return false;
        }
        let power = (127 - (*divisor as u128).leading_zeros()) as u32;
        if power < 1 || power > 63 || (1i128 << power) != *divisor {
            return false;
        }
        self.emit_expression(left, homes);
        self.assembler.mov_reg_reg("rcx", "rax");
        self.assembler.shift_imm(7, "rcx", 63);
        self.assembler.shift_imm(5, "rcx", (64 - power) as u8);
        self.assembler.binary(0x01, "rax", "rcx");
        if op == "/" {
            self.assembler.shift_imm(7, "rax", power as u8);
        } else {
            let mask = (1i128 << power) - 1;
            if mask <= 0x7FFFFFFF {
                self.assembler.arith_imm(4, "rax", mask as i64);
            } else {
                self.assembler.mov_imm64("rsi", mask as i64);
                self.assembler.binary(0x21, "rax", "rsi");
            }
            self.assembler.binary(0x29, "rax", "rcx");
        }
        true
    }

    /// Signed variable division with a guarded 32-bit fast path and a zero trap.
    /// On entry `rcx` holds the dividend and `rax` the divisor.
    fn emit_variable_division(&mut self, remainder: bool, nonnegative_divisor: bool) {
        self.assembler.test("rax");
        self.assembler.jcc("z", crate::runtime::DIV_ZERO_LABEL);
        let wide = self.fresh("div.wide");
        let done = self.fresh("div.wide.done");
        self.assembler.cmp_imm("rax", 2147483647);
        self.assembler.jcc("a", &wide);
        self.assembler.cmp_imm("rcx", 2147483647);
        self.assembler.jcc("a", &wide);
        self.assembler.mov_reg_reg("rsi", "rax");
        self.assembler.xor_zero("rdx");
        self.assembler.mov_reg_reg("rax", "rcx");
        self.assembler.divide32("rsi");
        if remainder {
            self.assembler.mov_reg_reg("rax", "rdx");
        }
        self.assembler.jmp(&done);
        let w = wide.clone();
        self.assembler.label(&w);
        if !nonnegative_divisor {
            let no_overflow = self.fresh("div.no_overflow");
            self.assembler.cmp_imm("rax", -1);
            self.assembler.jcc("nz", &no_overflow);
            self.assembler.mov_imm64("rsi", i64::MIN);
            self.assembler.cmp_reg_reg("rcx", "rsi");
            self.assembler.jcc("e", crate::runtime::DIV_ZERO_LABEL);
            let n = no_overflow.clone();
            self.assembler.label(&n);
        }
        self.assembler.mov_reg_reg("rsi", "rax");
        self.assembler.mov_reg_reg("rax", "rcx");
        self.assembler.cqo();
        self.assembler.divide("rsi", true);
        if remainder {
            self.assembler.mov_reg_reg("rax", "rdx");
        }
        let d = done.clone();
        self.assembler.label(&d);
    }

    /// Lower `x / d` or `x % d` for a positive literal divisor as a magic multiply.
    fn try_emit_magic_division(&mut self, op: &str, left: &Expr, right: &Expr,
                               homes: &[(i64, &str)]) -> bool {
        if op != "/" && op != "%" {
            return false;
        }
        let Expr::IntLiteral { value: divisor, .. } =
            crate::optimize::peel_parens(Some(right)).unwrap() else { return false };
        if *divisor < 2 {
            return false;
        }
        let Some((multiplier, shift)) = crate::codegen::magic_division_parameters(*divisor)
            else { return false };
        self.emit_expression(left, homes);
        self.assembler.mov_reg_reg("rcx", "rax");
        match self.hoist_imm.get(&multiplier).cloned() {
            Some(home) => self.assembler.mov_reg_reg("rsi", &home),
            None => self.assembler.mov_imm64("rsi", multiplier as i64),
        }
        self.assembler.imul_high("rsi");
        self.assembler.lea_scale("rax", "rdx", "rcx", 0, 0);
        self.assembler.shift_imm(7, "rax", shift as u8);
        self.assembler.mov_reg_reg("rdx", "rcx");
        self.assembler.shift_imm(7, "rdx", 63);
        self.assembler.binary(0x29, "rax", "rdx");
        if op == "/" {
            return true;
        }
        self.assembler.mov_reg_reg("rdx", "rax");
        match self.hoist_imm.get(divisor).cloned() {
            Some(home) => self.assembler.imul("rdx", &home),
            None => {
                self.assembler.mov_imm64("rsi", *divisor as i64);
                self.assembler.imul("rdx", "rsi");
            }
        }
        self.assembler.mov_reg_reg("rax", "rcx");
        self.assembler.binary(0x29, "rax", "rdx");
        true
    }

    /// General binary lowering into `rax`, mirroring Python's `_emit_binary` core.
    fn emit_binary(&mut self, op: &str, left: &Expr, right: &Expr,
                   homes: &[(i64, &str)]) {
        let arith: Option<u8> = match op {
            "+" => Some(0x01), "&" => Some(0x21), "|" => Some(0x09), "^" => Some(0x31),
            _ => None,
        };
        let imm_ext: Option<u8> = match op {
            "+" => Some(0), "-" => Some(5), "&" => Some(4), "|" => Some(1), "^" => Some(6),
            _ => None,
        };
        if op == "&" && self.delta_value == 0 {
            let mask_and_slot = match (crate::optimize::peel_parens(Some(left)).unwrap(),
                                       crate::optimize::peel_parens(Some(right)).unwrap()) {
                (Expr::IntLiteral { value, .. }, other) => Some((*value, other)),
                (other, Expr::IntLiteral { value, .. }) => Some((*value, other)),
                _ => None,
            };
            if let Some((mask, other)) = mask_and_slot {
                if matches!(mask, 255 | 65535) {
                    if let Some(slot) = expr_slot(other) {
                        if slot != self.delta_slot && slot != self.value_slot {
                            if let Some(home) = home_for(slot, homes) {
                                if home != "rax" {
                                    if mask == 255 {
                                        self.assembler.movzx_from8("rax", home);
                                    } else {
                                        self.assembler.movzx_from16("rax", home);
                                    }
                                    return;
                                }
                            }
                        }
                    }
                }
            }
        }
        if let Expr::IntLiteral { value, .. } = right {
            if (op == "+" || op == "-") && *value >= -0x80000000 && *value <= 0x7FFFFFFF {
                if let Expr::Name { slot, .. } =
                    crate::optimize::peel_parens(Some(left)).unwrap() {
                    if let Some(home) = home_for(*slot, homes) {
                        let mut effective = if op == "-" { -*value } else { *value };
                        if *slot == self.delta_slot && self.delta_value != 0 {
                            effective -= self.delta_value as i128;
                        }
                        self.assembler.lea_imm("rax", home, effective as i64);
                        return;
                    }
                }
            }
            if *value >= -0x80000000 && *value <= 0x7FFFFFFF {
                if let Some(ext) = imm_ext {
                    self.emit_expression(left, homes);
                    self.assembler.arith_imm(ext, "rax", *value as i64);
                    return;
                }
            }
            if *value >= 0 && *value <= 63 {
                let ext = match op { "<<" => Some(4), ">>" => Some(7), _ => None };
                if let Some(ext) = ext {
                    self.emit_expression(left, homes);
                    self.assembler.shift_imm(ext, "rax", *value as u8);
                    return;
                }
            }
        }
        if op == "*" {
            if let Expr::Name { slot, .. } = left {
                if let (Some(home), Expr::IntLiteral { value, .. }) =
                    (home_for(*slot, homes), right) {
                    let scale = match *value { 3 => Some(1u8), 5 => Some(2), 9 => Some(3), _ => None };
                    if let Some(scale) = scale {
                        self.assembler.lea_scale("rax", home, home, scale, 0);
                        return;
                    }
                    if *value >= -2147483648 && *value <= 2147483647 {
                        self.assembler.imul_imm("rax", home, *value as i64);
                        return;
                    }
                }
            }
        }
        if op == "*" || arith.is_some() {
            if let Expr::Name { slot, .. } = crate::optimize::peel_parens(Some(left)).unwrap() {
                if let Some(left_home) = home_for(*slot, homes) {
                    if op != "*" || !expr_has_call(right) {
                        self.emit_expression(right, homes);
                        if op == "*" {
                            self.assembler.imul("rax", left_home);
                        } else {
                            self.assembler.binary(arith.unwrap(), "rax", left_home);
                        }
                        return;
                    }
                }
            }
        }
        let peeled_right = crate::optimize::peel_parens(Some(right)).unwrap();
        let mut right_home: Option<String> = None;
        if arith.is_some() || op == "-" || op == "*" {
            if let Expr::Name { slot, .. } = peeled_right {
                right_home = home_for(*slot, homes).map(str::to_string);
            } else if let Expr::IntLiteral { value, .. } = peeled_right {
                right_home = self.hoist_imm.get(value).cloned();
            }
        }
        if let Some(right_home) = right_home {
            self.emit_expression(left, homes);
            if op == "-" {
                self.assembler.binary(0x29, "rax", &right_home);
            } else if op == "*" {
                self.assembler.imul("rax", &right_home);
            } else if let Some(opcode) = arith {
                self.assembler.binary(opcode, "rax", &right_home);
            }
            return;
        }
        if op == "/" || op == "%" {
            let nonnegative = expr_slot(right)
                .is_some_and(|slot| self.nonnegative_slots.contains(&slot));
            self.emit_into(left, "rcx", homes);
            self.emit_expression(right, homes);
            self.emit_variable_division(op == "%", nonnegative);
            return;
        }
        if op == "<<" || op == ">>" {
            if !expr_has_call(right) && self.emit_left_into(left, "rcx", homes) {
                self.emit_expression(right, homes);
            } else {
                self.emit_expression(left, homes);
                self.assembler.mov_reg_reg("rcx", "rax");
                self.emit_expression(right, homes);
            }
            self.assembler.mov_reg_reg("rsi", "rax");
            self.assembler.mov_reg_reg("rax", "rcx");
            self.assembler.mov_reg_reg("rcx", "rsi");
            self.assembler.shift_cl(if op == "<<" { 4 } else { 7 });
            return;
        }
        let mut hold = "rcx".to_string();
        if expr_has_call(right) {
            self.emit_expression(left, homes);
            if self.delta_slot >= 0 {
                let offset = 8 * self.inline_depth as i64 + self.depth;
                self.assembler.mov_store_rsp_disp(offset, "rax");
                self.emit_expression(right, homes);
                self.assembler.mov_load_rsp_disp(offset, "rcx");
            } else if self.pad_slot_active {
                self.assembler.mov_store_rsp("rax");
                self.emit_expression(right, homes);
                self.assembler.mov_load_rsp("rcx");
            } else {
                self.assembler.push("rax");
                self.depth += 8;
                self.emit_expression(right, homes);
                self.assembler.pop("rcx");
                self.depth -= 8;
            }
        } else if !matches!(crate::optimize::peel_parens(Some(right)).unwrap(),
            Expr::Name { .. } | Expr::IntLiteral { .. }) && self.temporaries < 2 {
            hold = ["r11", "r10"][self.temporaries].to_string();
            self.emit_into(left, &hold, homes);
            self.temporaries += 1;
            self.emit_expression(right, homes);
            self.temporaries -= 1;
        } else {
            self.emit_expression(left, homes);
            self.assembler.mov_reg_reg("rcx", "rax");
            self.emit_expression(right, homes);
        }
        match op {
            "+" | "&" | "|" | "^" => self.assembler.binary(arith.unwrap(), "rax", &hold),
            "-" => {
                if hold != "rcx" {
                    self.assembler.mov_reg_reg("rcx", &hold);
                }
                self.assembler.binary(0x29, "rcx", "rax");
                self.assembler.mov_reg_reg("rax", "rcx");
            }
            "*" => self.assembler.imul("rax", &hold),
            _ => {}
        }
    }

    /// Lower a constant-delta self-call as a pure-text copy of the current body.
    /// Emit one inlined copy of the pair-mode body with an accumulated first-parameter delta.
    fn emit_pair_body_copy(&mut self, delta: i64, homes: &[(i64, &str)]) {
        let saved_epilogue = self.epilogue.clone();
        let saved_delta = self.delta_value;
        let saved_depth = self.inline_depth;
        self.epilogue = self.fresh(&format!(
            "{}.pair", crate::codegen::function_label(&self.current_name)));
        self.delta_value -= delta;
        self.inline_depth += 1;
        let body = self.current_body.clone();
        for (index, statement) in body.statements.iter().enumerate() {
            self.emit_statement(statement, index + 1 == body.statements.len(), homes);
        }
        let end = self.epilogue.clone();
        self.assembler.label(&end);
        self.epilogue = saved_epilogue;
        self.delta_value = saved_delta;
        self.inline_depth = saved_depth;
    }

    /// Lower `f(m - delta, n - 1)` as a counted chain loop around one pair copy.
    fn emit_pair_chain_loop(&mut self, homes: &[(i64, &str)]) {
        let Some(value_home) = home_for(self.value_slot, homes).map(str::to_string) else { return };
        let top = self.fresh("pair.loop");
        let exit = self.fresh("pair.exit");
        let nested = self.chain_loop_depth > 0;
        if nested {
            self.assembler.push("r13");
        }
        self.chain_loop_depth += 1;
        self.assembler.mov_reg_reg("r13", &value_home);
        self.assembler.mov_imm64(&value_home, 1);
        let padding = (16 - self.assembler.here() % 16) % 16;
        if padding > 0 {
            self.assembler.nop_pad(padding);
        }
        let t = top.clone();
        self.assembler.label(&t);
        self.assembler.cmp_imm("r13", 0);
        self.assembler.jcc("e", &exit);
        self.emit_pair_body_copy(-1, homes);
        self.assembler.mov_reg_reg(&value_home, "rax");
        self.assembler.sub_imm("r13", 1);
        let again = top.clone();
        self.assembler.jcc("nz", &again);
        let e = exit.clone();
        self.assembler.label(&e);
        self.assembler.mov_reg_reg("rax", &value_home);
        self.chain_loop_depth -= 1;
        if nested {
            self.assembler.pop("r13");
        }
    }

    /// Lower a two-parameter self-call as a pure-text pair copy.
    fn emit_pair_self_call(&mut self, delta: i64, value: &Expr, homes: &[(i64, &str)]) {
        let Some(value_home) = home_for(self.value_slot, homes).map(str::to_string) else { return };
        let value = crate::optimize::peel_parens(Some(value)).unwrap();
        // A step of exactly `n - 1` is the chain loop's counted iteration.
        if let Expr::Binary { op, left, right, .. } = value {
            if op == "-" && expr_slot(left) == Some(self.value_slot)
                && matches!(crate::optimize::peel_parens(Some(right)).unwrap(),
                    Expr::IntLiteral { value, .. } if *value == 1) {
                self.emit_pair_chain_loop(homes);
                return;
            }
            if op == "+" || op == "-" {
                if let Expr::IntLiteral { value: amount, .. } =
                    crate::optimize::peel_parens(Some(right)).unwrap() {
                    let effective = if op == "-" { -*amount } else { *amount };
                    self.assembler.lea_imm(&value_home, &value_home, effective as i64);
                    self.emit_pair_body_copy(delta, homes);
                    return;
                }
            }
        }
        if let Expr::Call { .. } = value {
            self.emit_expression(value, homes);
            self.assembler.mov_reg_reg(&value_home, "rax");
        }
        self.emit_pair_body_copy(delta, homes);
    }

    fn emit_delta_self_call(&mut self, delta: i64, homes: &[(i64, &str)]) {
        let saved_epilogue = self.epilogue.clone();
        let saved_value = self.delta_value;
        let saved_depth = self.inline_depth;
        let label = self.fresh(&format!("{}.delta", function_label(&self.current_name)));
        self.epilogue = label;
        self.inline_depth += 1;
        self.delta_value -= delta;
        let body = self.current_body.clone();
        self.emit_block(&body, true, homes);
        let end = self.epilogue.clone();
        self.assembler.label(&end);
        self.epilogue = saved_epilogue;
        self.delta_value = saved_value;
        self.inline_depth = saved_depth;
    }

    /// Lower a promoted `read_char` into `rax`; `done` lets a caller own the join label.
    fn emit_read_char_into(&mut self, done: Option<&str>) -> bool {
        let (Some(base), Some(cursor)) = (self.stdin_base.clone(), self.stdin_cursor.clone()) else {
            return false;
        };
        let have = self.fresh("readchar.cursor.have");
        let done = done.map(str::to_string).unwrap_or_else(|| self.fresh("readchar.cursor.done"));
        if let Some(count) = self.stdin_count.clone() {
            self.assembler.cmp_reg_reg(&cursor, &count);
        } else {
            self.assembler.cmp_reg_rip(&cursor, crate::runtime::STDIN_COUNT_LABEL);
        }
        self.assembler.jcc("b", &have);
        self.assembler.mov_store_rip(&cursor, crate::runtime::STDIN_CURSOR_LABEL);
        self.call_site(crate::runtime::READ_CHAR_LABEL);
        self.assembler.mov_load_rip(&cursor, crate::runtime::STDIN_CURSOR_LABEL);
        if let Some(count) = self.stdin_count.clone() {
            self.assembler.mov_load_rip(&count, crate::runtime::STDIN_COUNT_LABEL);
        }
        let d = done.clone();
        self.assembler.jmp(&d);
        let h = have.clone();
        self.assembler.label(&h);
        self.assembler.movzx_load8_sib("rax", &base, &cursor);
        self.assembler.add_imm(&cursor, 1);
        if done.starts_with("readchar.cursor.done") {
            self.assembler.label(&done);
        }
        true
    }

    /// Copy a copy-free callee into this call site instead of emitting a machine call.
    fn try_emit_foreign_inline(&mut self, name: &str, arguments: &[Expr], homes: &[(i64, &str)]) -> bool {
        if self.inline_depth >= 7 || name == self.current_name {
            return false;
        }
        let Some(callee) = self.functions.iter().find(|function| function.name == name).cloned()
            else { return false };
        let parameters = callee.parameters.len();
        if parameters == 0 || parameters > 4 || callee.slots > crate::codegen::HOME_REGISTERS.len() {
            return false;
        }
        if !body_is_copy_free(&callee.body) {
            return false;
        }
        let rebinds = rebinds_parameters(&callee.body, parameters);
        let caller_homes: Vec<&str> = homes.iter().map(|(_, register)| *register).collect();
        let reserved: std::collections::BTreeSet<&str> = caller_homes.iter().copied()
            .chain(self.hoist_homes.iter().map(String::as_str))
            .collect();
        let mut guest: Vec<&str>;
        let mut spill: Vec<&str> = Vec::new();
        if rebinds {
            let free: Vec<&str> = crate::codegen::HOME_REGISTERS.iter()
                .filter(|register| !reserved.contains(**register)).copied().collect();
            let mut pool = free;
            pool.extend(caller_homes.iter().copied());
            guest = pool.into_iter().take(callee.slots).collect();
            spill = caller_homes.iter().copied().filter(|home| guest.contains(home)).collect();
        } else {
            let entry: Vec<&str> = crate::codegen::ARGUMENT_REGISTERS
                .iter().take(parameters).copied().collect();
            let spare: Vec<&str> = crate::codegen::HOME_REGISTERS.iter()
                .filter(|register| !reserved.contains(**register) && !entry.contains(register))
                .take(callee.slots - parameters).copied().collect();
            guest = entry;
            guest.extend(spare);
        }
        if guest.len() < callee.slots {
            return false;
        }
        let copy_home = self.copy_home.clone();
        let copy_return = if !rebinds
            && callee.body.statements.last().is_some_and(|s| matches!(s, Stmt::Return { .. }))
            && copy_home.as_ref().is_some_and(|home| !spill.iter().any(|h| *h == home)) {
            copy_home.clone()
        } else {
            None
        };
        let mut return_slot: Option<i64> = None;
        if let Some(copy_home) = &copy_home {
            if let Some(Stmt::Return { value, .. }) = callee.body.statements.last() {
                if let Expr::Name { slot, .. } =
                    crate::optimize::peel_parens(Some(value)).unwrap() {
                    let slot = *slot;
                    if slot >= parameters as i64 && slot >= 0 && (slot as usize) < guest.len()
                        && guest[slot as usize] != copy_home
                        && assigned_slots(&callee.body).contains(&slot) {
                        return_slot = Some(slot);
                    }
                }
            }
        }
        if let (Some(slot), Some(copy_home)) = (return_slot, &copy_home) {
            guest[slot as usize] = copy_home.as_str();
            self.copy_home_used = true;
        }
        let mut shift_plan: std::collections::BTreeMap<i64, (String, u32)> =
            std::collections::BTreeMap::new();
        if !rebinds {
            for (index, argument) in arguments.iter().take(parameters).enumerate() {
                let Expr::Binary { op, left, right, .. } =
                    crate::optimize::peel_parens(Some(argument)).unwrap() else { continue };
                if op != ">>" {
                    continue;
                }
                let count = match crate::optimize::peel_parens(Some(right)).unwrap() {
                    Expr::IntLiteral { value, .. } if *value >= 1 && *value <= 31 => *value as u32,
                    _ => continue,
                };
                let Some(source_slot) = expr_slot(left) else { continue };
                let Some(source_home) = home_for(source_slot, homes).map(str::to_string)
                    else { continue };
                if spill.iter().any(|home| *home == source_home) {
                    continue;
                }
                if parameter_single_shift_use(&callee.body, index as i64, count as i64) {
                    shift_plan.insert(index as i64, (source_home, count));
                }
            }
        }
        let mut aliases: std::collections::BTreeMap<i64, String> =
            std::collections::BTreeMap::new();
        if !rebinds && self.delta_value == 0 && self.value_slot < 0 {
            for (index, argument) in arguments.iter().take(parameters).enumerate() {
                if shift_plan.contains_key(&(index as i64)) {
                    continue;
                }
                if let Expr::Name { slot, .. } =
                    crate::optimize::peel_parens(Some(argument)).unwrap() {
                    if let Some(home) = home_for(*slot, homes) {
                        if !spill.iter().any(|borrowed| *borrowed == home) {
                            aliases.insert(index as i64, home.to_string());
                        }
                    }
                }
            }
        }
        for (index, home) in &aliases {
            guest[*index as usize] = home.as_str();
        }
        let saved = (
            self.current_name.clone(), self.current_body.clone(), self.epilogue.clone(),
            self.inline_depth, self.delta_slot, self.delta_value,
            self.hoist_plans.clone(), self.hoist_imm.clone(), self.hoist_homes.clone(),
            self.string_bounds.clone(), self.stdin_base.clone(), self.stdin_cursor.clone(),
            self.stdin_count.clone(), self.arrays.clone(), self.shift_through.clone(),
            self.copy_return.clone(),
        );
        self.current_name = callee.name.clone();
        self.current_body = callee.body.clone();
        self.epilogue = self.fresh(&format!("{}.copy", crate::codegen::function_label(name)));
        self.inline_depth += 1;
        self.delta_slot = -1;
        self.delta_value = 0;
        self.hoist_plans.clear();
        self.hoist_imm.clear();
        self.hoist_homes.clear();
        self.string_bounds.clear();
        self.stdin_base = None;
        self.stdin_cursor = None;
        self.stdin_count = None;
        self.arrays.clear();
        collect_arrays(&callee.body, &mut self.arrays);
        self.shift_through = shift_plan.clone();
        self.copy_return = copy_return;
        for home in &spill {
            self.assembler.push(home);
        }
        let guest_homes: Vec<(i64, &str)> = guest.iter().enumerate()
            .map(|(slot, register)| (slot as i64, *register)).collect();
        if rebinds {
            for (index, register) in guest.iter().take(parameters).enumerate() {
                if shift_plan.contains_key(&(index as i64)) {
                    continue;
                }
                self.assembler.mov_reg_reg(register, crate::codegen::ARGUMENT_REGISTERS[index]);
            }
        }
        let assigned = assigned_slots(&callee.body);
        for slot in parameters..callee.slots {
            if !assigned.contains(&(slot as i64)) {
                self.assembler.xor_zero(guest[slot]);
            }
        }
        let statements = &callee.body.statements;
        if !statements.last().is_some_and(|last| matches!(last, Stmt::Return { .. })) {
            self.assembler.mov_imm64("rax", 0);
        }
        for (index, statement) in statements.iter().enumerate() {
            self.emit_statement(statement, index + 1 == statements.len(), &guest_homes);
        }
        let epilogue = self.epilogue.clone();
        self.assembler.label(&epilogue);
        for home in spill.iter().rev() {
            self.assembler.pop(home);
        }
        self.current_name = saved.0;
        self.current_body = saved.1;
        self.epilogue = saved.2;
        self.inline_depth = saved.3;
        self.delta_slot = saved.4;
        self.delta_value = saved.5;
        self.hoist_plans = saved.6;
        self.hoist_imm = saved.7;
        self.hoist_homes = saved.8;
        self.string_bounds = saved.9;
        self.stdin_base = saved.10;
        self.stdin_cursor = saved.11;
        self.stdin_count = saved.12;
        self.arrays = saved.13;
        self.shift_through = saved.14;
        self.copy_return = saved.15;
        true
    }

    /// Arguments whose value is fused into a parameter's sole `<<` use in the copy.
    fn shift_through_skips(&self, name: &str, arguments: &[Expr],
                           homes: &[(i64, &str)]) -> std::collections::BTreeSet<usize> {
        let mut skips = std::collections::BTreeSet::new();
        let Some(callee) = self.functions.iter().find(|function| function.name == name)
            else { return skips };
        if rebinds_parameters(&callee.body, callee.parameters.len())
            || !body_is_copy_free(&callee.body)
            || callee.parameters.len() != arguments.len() {
            return skips;
        }
        let aliasable = self.delta_value == 0 && self.value_slot < 0;
        for (index, argument) in arguments.iter().enumerate() {
            if aliasable {
                if let Expr::Name { slot, .. } =
                    crate::optimize::peel_parens(Some(argument)).unwrap() {
                    if home_for(*slot, homes).is_some() {
                        skips.insert(index);
                    }
                    continue;
                }
            }
            let Expr::Binary { op, left, right, .. } =
                crate::optimize::peel_parens(Some(argument)).unwrap() else { continue };
            if op != ">>" {
                continue;
            }
            let count = match crate::optimize::peel_parens(Some(right)).unwrap() {
                Expr::IntLiteral { value, .. } if *value >= 1 && *value <= 31 => *value as u32,
                _ => continue,
            };
            let Some(source_slot) = expr_slot(left) else { continue };
            if home_for(source_slot, homes).is_none() {
                continue;
            }
            if parameter_single_shift_use(&callee.body, index as i64, count as i64) {
                skips.insert(index);
            }
        }
        skips
    }

    fn emit_call(&mut self, name: &str, arguments: &[Expr], homes: &[(i64, &str)]) {
        match name {
            "clz" => {
                self.emit_expression(&arguments[0], homes);
                self.assembler.lzcnt("rax", "rax");
                return;
            }
            "ctz" => {
                self.emit_expression(&arguments[0], homes);
                self.assembler.tzcnt("rax", "rax");
                return;
            }
            "bswap" => {
                self.emit_expression(&arguments[0], homes);
                self.assembler.bswap("rax");
                return;
            }
            "popcount" => {
                self.emit_expression(&arguments[0], homes);
                self.call_site(crate::runtime::POPCOUNT_LABEL);
                return;
            }
            "str_len" => {
                self.emit_expression(&arguments[0], homes);
                self.call_site(crate::runtime::STR_LEN_LABEL);
                return;
            }
            "arg_byte" => {
                let index = crate::optimize::peel_parens(Some(&arguments[1])).unwrap();
                if self.temporaries == 0
                    && matches!(&arguments[0], Expr::IntLiteral { value, .. } if *value == 0)
                    && !expr_has_call(index)
                    && !self.extra_homes.iter().any(|home| home == "r10" || home == "r11") {
                    let miss = self.fresh("arg_byte.miss");
                    let done = self.fresh("arg_byte.done");
                    self.emit_expression(index, homes);
                    self.assembler.mov_load_rip("r10", crate::runtime::ARGV_PTR0_LABEL);
                    self.assembler.mov_load_rip("r11", crate::runtime::ARGV_LEN0_LABEL);
                    self.assembler.cmp_reg_reg("rax", "r11");
                    self.assembler.jcc("ge", &miss);
                    let masked_nonnegative = matches!(index,
                        Expr::Binary { op, right, .. }
                            if op == "&"
                                && matches!(crate::optimize::peel_parens(Some(right)).unwrap(),
                                    Expr::IntLiteral { value, .. } if *value >= 0));
                    let known_nonnegative = masked_nonnegative
                        || match index {
                            Expr::Name { slot, .. } => self.nonnegative_slots.contains(slot),
                            Expr::IntLiteral { value, .. } => *value >= 0,
                            _ => false,
                        };
                    if !known_nonnegative {
                        self.assembler.test("rax");
                        self.assembler.jcc("s", &miss);
                    }
                    self.assembler.movzx_load8_sib("rax", "r10", "rax");
                    self.assembler.jmp(&done);
                    let m = miss.clone();
                    self.assembler.label(&m);
                    self.assembler.mov_reg_reg("rdx", "rax");
                    self.assembler.xor_zero("rcx");
                    self.call_site(crate::runtime::ARG_BYTE_LABEL);
                    let d = done.clone();
                    self.assembler.label(&d);
                    return;
                }
            }
            "read_char" => {
                if self.emit_read_char_into(None) {
                    return;
                }
                self.call_site(crate::runtime::READ_CHAR_LABEL);
                return;
            }
            "str_byte" => {
                let pointer = crate::optimize::peel_parens(Some(&arguments[0])).unwrap();
                let index = crate::optimize::peel_parens(Some(&arguments[1])).unwrap();
                if let (Expr::Name { slot: pointer_slot, .. },
                        Expr::Name { slot: index_slot, .. }) = (pointer, index) {
                    if let (Some(base), Some(index_home)) =
                        (home_for(*pointer_slot, homes), home_for(*index_slot, homes)) {
                        if self.string_bounds.get(index_slot) == Some(pointer_slot) {
                            self.assembler.movzx_load8_sib("rax", base, index_home);
                            return;
                        }
                        let miss = self.fresh("str_byte.inline.miss");
                        let done = self.fresh("str_byte.inline.done");
                        self.assembler.test(index_home);
                        let m = miss.clone();
                        self.assembler.jcc("s", &m);
                        self.assembler.movzx_load8_sib("rax", base, index_home);
                        self.assembler.test("rax");
                        let d = done.clone();
                        self.assembler.jcc("nz", &d);
                        let mi = miss.clone();
                        self.assembler.label(&mi);
                        self.assembler.mov_imm64("rax", -1);
                        let dn = done.clone();
                        self.assembler.label(&dn);
                        return;
                    }
                }
            }
            "rotl" => {
                let value = &arguments[0];
                let mut count = crate::optimize::peel_parens(Some(&arguments[1])).unwrap();
                if let Expr::Binary { op, left, right, .. } = count {
                    if op == "&" {
                        if let Expr::IntLiteral { value: mask, .. } =
                            crate::optimize::peel_parens(Some(right)).unwrap() {
                            if *mask & 63 == 63 {
                                count = crate::optimize::peel_parens(Some(left)).unwrap();
                            }
                        }
                    }
                }
                let simple_count = matches!(count, Expr::Name { .. } | Expr::IntLiteral { .. });
                // The count lives in rcx, so it can be set before the value is evaluated
                // whenever that evaluation cannot touch rcx; this avoids a stack round trip
                // for a value that is itself an expression.
                if simple_count && !expression_uses_rcx(value) {
                    match count {
                        Expr::IntLiteral { value: number, .. } => {
                            self.emit_literal(*number, "rcx");
                        }
                        Expr::Name { slot, .. } => {
                            if let Some(home) = home_for(*slot, homes) {
                                if home != "rcx" {
                                    self.assembler.mov_reg_reg("rcx", home);
                                }
                            } else {
                                self.load_slot(*slot, "rcx", homes);
                            }
                        }
                        _ => {}
                    }
                    self.emit_expression(value, homes);
                    self.assembler.rotate_cl(0);
                    return;
                }
                self.emit_expression(value, homes);
                self.assembler.push("rax");
                self.depth += 8;
                self.emit_expression(count, homes);
                self.assembler.mov_reg_reg("rcx", "rax");
                self.assembler.pop("rax");
                self.depth -= 8;
                self.assembler.rotate_cl(0);
                return;
            }
            "argc" => {
                let label = crate::runtime::ARGC_LABEL.to_string();
                self.call_site(&label);
            }
            "flush" => {
                let label = crate::runtime::OUT_FLUSH_LABEL.to_string();
                self.call_site(&label);
            }
            "print" | "print_char" | "print_hex" | "print_str" => {
                self.emit_expression(&arguments[0], homes);
                self.assembler.push("rax");
                self.depth += 8;
                let label = match name {
                    "print" => PRINT_LABEL,
                    "print_char" => PRINT_CHAR_LABEL,
                    "print_hex" => PRINT_HEX_LABEL,
                    _ => crate::runtime::PRINT_STR_LABEL,
                };
                self.call_site(label);
                self.depth -= 8;
                self.assembler.pop("rax");
            }
            _ => {
                if name == &self.current_name
                    && self.delta_slot >= 0
                    && self.inline_depth < 7
                    && arguments.len() == 1
                {
                    if let Some(delta) = delta_argument(&arguments[0], self.delta_slot) {
                        self.emit_delta_self_call(delta, homes);
                        return;
                    }
                }
                if name == &self.current_name
                    && self.value_slot >= 0
                    && self.inline_depth < 6
                    && arguments.len() == 2 {
                    let second = crate::optimize::peel_parens(Some(&arguments[1])).unwrap();
                    if !matches!(second, Expr::IntLiteral { .. } | Expr::Call { .. }) {
                        if let Some(delta) = delta_argument(&arguments[0], self.delta_slot) {
                            self.emit_pair_self_call(delta, second, homes);
                            return;
                        }
                    }
                }
                let skips = self.shift_through_skips(name, arguments, homes);
                // Arguments bind in reverse order so a later evaluation cannot disturb an
                // earlier binding. An argument whose lowering writes rcx/rdx/r8/r9 (a call, a
                // division or a variable shift) clobbers registers that may already hold a
                // higher-index argument, so it is evaluated first and parked on the stack, then
                // popped once every direct binding is in place. Everything else binds straight
                // into its own register, exactly as before.
                let registers = ["rcx", "rdx", "r8", "r9"];
                let parkable: Vec<usize> = (0..arguments.len())
                    .filter(|index| !skips.contains(index)
                        && delta_argument(&arguments[*index], self.delta_slot).is_none()
                        && !matches!(crate::optimize::peel_parens(Some(&arguments[*index])).unwrap(),
                            Expr::IntLiteral { .. } | Expr::Name { .. })
                        && expression_uses_rcx(&arguments[*index]))
                    .collect();
                for index in (0..parkable.len()).rev() {
                    self.emit_expression(&arguments[parkable[index]], homes);
                    self.assembler.push("rax");
                    self.depth += 8;
                }
                if arguments.len() <= 4 {
                    for index in (0..arguments.len()).rev() {
                        if skips.contains(&index) || parkable.contains(&index) {
                            continue;
                        }
                        let argument = &arguments[index];
                        let register = registers[index];
                        if let Expr::IntLiteral { value, .. } = argument {
                            self.assembler.mov_imm64(register, *value as i64);
                        } else if let Expr::Name { slot, .. } = argument {
                            if let Some(home) = home_for(*slot, homes) {
                                if *slot == self.delta_slot && self.delta_value != 0 {
                                    self.assembler.lea_imm(register, home, -self.delta_value);
                                } else {
                                    self.assembler.mov_reg_reg(register, home);
                                }
                            } else {
                                self.load_slot(*slot, "rax", homes);
                                self.assembler.mov_reg_reg(register, "rax");
                            }
                        } else if let Some(offset) = delta_argument(argument, self.delta_slot) {
                            if self.delta_slot >= 0 {
                                if let Some(home) = home_for(self.delta_slot, homes) {
                                    let effective = -(self.delta_value - offset);
                                    self.assembler.lea_imm(register, home, effective);
                                }
                            }
                        } else {
                            self.emit_into(argument, register, homes);
                        }
                    }
                }
                for index in &parkable {
                    self.assembler.pop(registers[*index]);
                    self.depth -= 8;
                }
                if arguments.len() <= 4 {
                    if self.try_emit_foreign_inline(name, arguments, homes) {
                        return;
                    }
                    let label = function_label(name);
                    self.call_site(&label);
                }
            }
        }
    }

    /// Emit the entry stub, every function, then the runtime helpers.
    pub fn generate(mut self, program: &Program) -> Artifact {
        self.functions = program.functions.clone();
        self.emit_entry();
        for function in &program.functions {
            self.emit_function(function);
        }
        emit_runtime(&mut self.assembler, &mut self.rodata, &mut self.writable);
        let _ = self.fresh("");
        Artifact {
            text: self.assembler.code,
            text_labels: self.assembler.labels,
            text_label_order: self.assembler.label_order,
            fixups: self.assembler.fixups,
            rodata: self.rodata,
            entry: ENTRY_LABEL.to_string(),
            imports: crate::image::IMPORTS.iter().map(|name| name.to_string()).collect(),
            writable: self.writable.data,
            writable_labels: self.writable.labels,
        }
    }
}

impl Default for CodeGenerator {
    fn default() -> Self {
        Self::new()
    }
}
