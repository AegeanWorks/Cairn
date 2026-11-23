//! Code generation helpers: the pure layer before the `CodeGenerator` class.
//!
//! These rules decide labels, frame offsets and the magic-multiply parameters that the class uses.

use crate::optimize::has_call;
use crate::syntax::Expr;

/// The assembler label of a Cairn function.
pub fn function_label(name: &str) -> String {
    format!("fn.{}", name)
}

/// The rbp-relative offset of a frame slot; slot 0 is the first parameter.
pub fn slot_offset(slot: i64) -> i64 {
    -8 * (slot + 1)
}

/// The lowest rbp displacement of an array span; element `i` sits at `base + 8i`.
pub fn array_element_base(base: i64, size: i64) -> i64 {
    slot_offset(base + size - 1)
}

/// Round a frame size up to the 16-byte stack alignment the ABI requires.
pub fn align16(value: usize) -> usize {
    (value + 15) & !15
}

/// The slot a parenthesised name refers to, or None for every other expression.
pub fn slot_of(expression: Option<&Expr>) -> Option<i64> {
    match crate::optimize::peel_parens(expression).unwrap() {
        Expr::Name { slot, .. } => Some(*slot),
        _ => None,
    }
}

/// Whether evaluating the expression is a load or a literal move and nothing else.
pub fn is_leaf(expression: Option<&Expr>) -> bool {
    matches!(crate::optimize::peel_parens(expression).unwrap(),
             Expr::IntLiteral { .. } | Expr::Name { .. } | Expr::StrLiteral { .. })
}

/// Builtins the code generator lowers inline and never turns into a machine call.
pub const INLINE_ONLY_BUILTINS: &[&str] = &["clz", "ctz", "bswap", "rotl"];

/// `has_call`, but the always-inline builtins count as call-free.
pub fn has_lowering_call(expression: Option<&Expr>) -> bool {
    has_call(expression, INLINE_ONLY_BUILTINS)
}

/// Structural equality of two index sub-expressions, spans ignored.
pub fn shape_equal(left: Option<&Expr>, right: Option<&Expr>) -> bool {
    let (Some(left), Some(right)) = (left, right) else {
        return false;
    };
    let left = crate::optimize::peel_parens(Some(left)).unwrap();
    let right = crate::optimize::peel_parens(Some(right)).unwrap();
    match (left, right) {
        (Expr::Name { slot: a, .. }, Expr::Name { slot: b, .. }) => a == b,
        (Expr::IntLiteral { value: a, .. }, Expr::IntLiteral { value: b, .. }) => a == b,
        (Expr::Binary { op: a, left: l1, right: r1, .. },
         Expr::Binary { op: b, left: l2, right: r2, .. }) => {
            a == b && shape_equal(Some(l1), Some(l2)) && shape_equal(Some(r1), Some(r2))
        }
        (Expr::Unary { op: a, operand: l, .. }, Expr::Unary { op: b, operand: r, .. }) => {
            a == b && shape_equal(Some(l), Some(r))
        }
        (Expr::Index { slot: a, index: l, .. }, Expr::Index { slot: b, index: r, .. }) => {
            a == b && shape_equal(Some(l), Some(r))
        }
        _ => false,
    }
}

/// A rotate count with a redundant `& m` removed, where `m & 63 == 63`.
pub fn rotate_count(expression: &Expr) -> Expr {
    let peeled = crate::optimize::peel_parens(Some(expression)).unwrap();
    let Expr::Binary { op, left, right, .. } = peeled else {
        return expression.clone();
    };
    if op != "&" {
        return expression.clone();
    }
    for (kept, mask) in [(left, right), (right, left)] {
        let mask = crate::optimize::peel_parens(Some(mask)).unwrap();
        if let Expr::IntLiteral { value, .. } = mask {
            if value & 63 == 63 {
                return (**kept).clone();
            }
        }
    }
    expression.clone()
}

/// Whether the statement is `amount = amount + level` for its own slot.
pub fn is_plus_step(statement: &crate::syntax::Stmt, level: i128) -> bool {
    let crate::syntax::Stmt::Assign { slot, value, .. } = statement else {
        return false;
    };
    if *slot < 0 {
        return false;
    }
    let Expr::Binary { op, left, right, .. } =
        crate::optimize::peel_parens(Some(value)).unwrap() else {
        return false;
    };
    if op != "+" {
        return false;
    }
    let left = crate::optimize::peel_parens(Some(left)).unwrap();
    let right = crate::optimize::peel_parens(Some(right)).unwrap();
    let (left, right) = if matches!(right, Expr::IntLiteral { value, .. } if *value == level) {
        (right, left)
    } else {
        (left, right)
    };
    matches!(left, Expr::IntLiteral { value, .. } if *value == level)
        && matches!(right, Expr::Name { slot: found, .. } if *found == *slot)
}

/// A `(multiplier, shift)` so the magic-multiply chain divides by `divisor` exactly.
pub fn magic_division_parameters(divisor: i128) -> Option<(i128, u32)> {
    if divisor < 2 || (divisor & (divisor - 1)) == 0 {
        return None;
    }
    let top = (1i128 << 63) - 1;
    for shift in 1..64u32 {
        let exponents = 64 + shift;
        let scale = 1i128 << exponents;
        let multiplier = -((-scale).div_euclid(divisor));
        if multiplier >= (1i128 << 64) {
            continue;
        }
        let error = multiplier * divisor - scale;
        let n_wrap = top;
        let n_last = top - ((top - (divisor - 1)) % divisor);
        let positive = [n_wrap, n_last]
            .iter()
            .all(|n| n * error < scale * (divisor - (n % divisor)));
        if !positive {
            continue;
        }
        let negative = [n_wrap, n_last]
            .iter()
            .all(|n| n * error <= scale * (divisor - (n % divisor)));
        if !negative {
            continue;
        }
        if -((-(1i128 << 63) * multiplier).div_euclid(scale)) != (1i128 << 63) / divisor + 1 {
            continue;
        }
        return Some((multiplier - (1i128 << 64), shift));
    }
    None
}

pub const COMPARISON_CONDITIONS: &[(&str, &str)] = &[
    ("==", "e"), ("!=", "ne"), ("<", "l"), ("<=", "le"), (">", "g"), (">=", "ge"),
];

pub const TEMPORARY_REGISTERS: &[&str] = &["r11", "r10"];
pub const SCRATCH: &str = "rsi";
pub const HOME_REGISTERS: &[&str] = &["rbx", "r12", "r13", "r14", "r15", "rdi"];
pub const ARGUMENT_REGISTERS: &[&str] = &["rcx", "rdx", "r8", "r9"];
pub const EXTRA_HOME_REGISTERS: &[&str] = &["r10", "r11"];
pub const ARRAY_UNROLL_LIMIT: usize = 8;
pub const MAX_INLINE_DEPTH: usize = 7;
