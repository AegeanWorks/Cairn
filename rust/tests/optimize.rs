//! Optimizer core tests ported from `tests/test_optimize.py` and `tests/test_magic_division.py`.

use cairn_rs::optimize::{self, Optimizer};
use cairn_rs::parser::parse;
use cairn_rs::sema::analyze;
use cairn_rs::syntax::Expr;

fn analysed(source: &str) -> cairn_rs::syntax::Program {
    let mut program = parse(source, "<source>").unwrap();
    analyze(&mut program, "<source>").unwrap();
    program
}

#[test]
fn wrapping_and_truncating_division() {
    assert_eq!(optimize::wrap(i128::from(optimize::INT64_MAX) + 1), optimize::INT64_MIN);
    assert_eq!(optimize::truncate_division(-7, 2), -3);
    assert_eq!(optimize::truncate_division(7, -2), -3);
    assert_eq!(optimize::truncate_division(-7, -2), 3);
}

#[test]
fn folds_constants_without_changing_behaviour() {
    assert_eq!(optimize::fold_constant("+", 1, 2), Some(3));
    assert_eq!(optimize::fold_constant("*", 9_223_372_036_854_775_807, 2), Some(-2));
    assert_eq!(optimize::fold_constant("/", -7, 2), Some(-3));
    assert_eq!(optimize::fold_constant("%", -7, 2), Some(-1));
    assert_eq!(optimize::fold_constant("/", 1, 0), None);
    assert_eq!(optimize::fold_constant("/", optimize::INT64_MIN, -1), None);
    assert_eq!(optimize::fold_constant("<<", 1, 64), Some(1));
    assert_eq!(optimize::fold_constant(">>", -8, 1), Some(-4));
    assert_eq!(optimize::fold_constant("&&", 2, 0), Some(0));
    assert_eq!(optimize::fold_constant("unknown", 1, 2), None);
}

#[test]
fn folds_unary_operators() {
    assert_eq!(optimize::fold_unary("-", 7), Some(-7));
    assert_eq!(optimize::fold_unary("~", 0), Some(-1));
    assert_eq!(optimize::fold_unary("!", 0), Some(1));
    assert_eq!(optimize::fold_unary("!", 7), Some(0));
    assert_eq!(optimize::fold_unary("unknown", 1), None);
}

#[test]
fn has_call_respects_inline_builtins() {
    let program = analysed("fn main() -> i64 { return rotl(1, 2) + print(1); }");
    let value = match &program.functions[0].body.statements[0] {
        cairn_rs::syntax::Stmt::Return { value, .. } => value,
        _ => panic!("expected a return"),
    };
    let Expr::Binary { left, right, .. } = value else {
        panic!("expected a binary expression");
    };
    assert!(!optimize::has_call(Some(left), &["rotl"]));
    assert!(optimize::has_call(Some(right), &["rotl"]));
    assert!(optimize::has_call(Some(value), &[]));
}

#[test]
fn may_trap_keeps_division_by_zero() {
    let program = analysed("fn main() -> i64 { return 8 / (1 - 1); }");
    let value = match &program.functions[0].body.statements[0] {
        cairn_rs::syntax::Stmt::Return { value, .. } => value,
        _ => panic!("expected a return"),
    };
    assert!(optimize::may_trap(Some(value)));
}

#[test]
fn folds_a_program_in_place() {
    let program = analysed("fn main() -> i64 { let x: i64 = 2 + 3; return x; }");
    let mut optimizer = Optimizer::new();
    let folded = optimizer.fold_program(&program);
    assert!(optimizer.folded >= 1);
    let cairn_rs::syntax::Stmt::Let { value, .. } = &folded.functions[0].body.statements[0] else {
        panic!("expected a binding");
    };
    assert_eq!(*value, Expr::IntLiteral {
        span: value.span(),
        value: 5,
    });
}

#[test]
fn a_constant_if_is_replaced_by_its_branch() {
    let program = analysed("fn main() -> i64 { if 0 { return 1; } else { return 2; } }");
    let folded = Optimizer::new().fold_program(&program);
    assert_eq!(folded.functions[0].body.statements.len(), 1);
    assert!(matches!(folded.functions[0].body.statements[0],
                     cairn_rs::syntax::Stmt::Return { .. }));
}
