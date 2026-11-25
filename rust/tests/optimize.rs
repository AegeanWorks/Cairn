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
fn a_for_loop_lowers_to_a_let_and_a_while() {
    let program = analysed("fn main() -> i64 { var t: i64 = 0; for i in 0..3 { t = t + i; } return t; }");
    let lowered = optimize::lower_for_loops(&program);
    let statements = &lowered.functions[0].body.statements;
    assert!(matches!(statements[1], cairn_rs::syntax::Stmt::Let { mutable: true, .. }));
    assert!(matches!(statements[2], cairn_rs::syntax::Stmt::While { .. }));
    assert!(!statements.iter().any(|statement| matches!(statement, cairn_rs::syntax::Stmt::For { .. })));
}

#[test]
fn copy_propagation_removes_a_single_read_definition() {
    let mut program = analysed(
        "fn main() -> i64 { let a: i64 = 1 + 2; let b: i64 = a; return b; }",
    );
    let applied = optimize::apply_copy_propagation(&mut program);
    assert!(applied >= 1);
    let statements = &program.functions[0].body.statements;
    assert_eq!(statements.len(), 1, "both single-read bindings should be gone");
    let cairn_rs::syntax::Stmt::Return { value, .. } = &statements[0] else {
        panic!("expected the value to be forwarded into the return");
    };
    assert!(matches!(value, Expr::Binary { .. }));
}

#[test]
fn a_single_read_definition_is_forwarded_into_a_call_argument() {
    let mut program = analysed(
        "fn f(x: i64, y: i64) -> i64 { return x + y; } \
         fn main() -> i64 { var i: i64 = 3; var t: i64 = 0; \
         let v: i64 = (i * 7) ^ (i >> 1); t = t + f(v, i + 1); return t; }",
    );
    let applied = optimize::apply_copy_propagation(&mut program);
    assert!(applied >= 1, "the single-read definition was not forwarded");
    let body = &program.functions[1].body;
    assert!(body.statements.iter().all(|statement| {
        !matches!(statement, cairn_rs::syntax::Stmt::Let { name, .. } if name == "v")
    }), "v should no longer be bound");
    let call = body.statements.iter().find_map(|statement| {
        let cairn_rs::syntax::Stmt::Assign { value, .. } = statement else { return None };
        let Expr::Binary { right, .. } = optimize::peel_parens(Some(value)).unwrap() else {
            return None;
        };
        match optimize::peel_parens(Some(right)).unwrap() {
            Expr::Call { arguments, .. } => Some(arguments.clone()),
            _ => None,
        }
    }).expect("the call is still in the body");
    assert!(matches!(optimize::peel_parens(Some(&call[0])).unwrap(), Expr::Binary { .. }),
            "the call argument should now hold the forwarded expression");
}

#[test]
fn a_definition_is_not_forwarded_past_a_write_to_its_operand() {
    let mut program = analysed(
        "fn main() -> i64 { var i: i64 = 3; var t: i64 = 0; \
         let v: i64 = (i * 7) ^ (i >> 1); i = i + 100; t = t + v; return t; }",
    );
    optimize::apply_copy_propagation(&mut program);
    let body = &program.functions[0].body;
    assert!(body.statements.iter().any(|statement| {
        matches!(statement, cairn_rs::syntax::Stmt::Let { name, .. } if name == "v")
    }), "v reads i, which changes before its only use");
}

#[test]
fn a_single_bit_shift_loop_is_unrolled_by_two_with_a_one_shot_tail() {
    let mut program = analysed(
        "fn main() -> i64 { var x: i64 = 12; \
         while (x & 1) == 0 { x = x >> 1; } return x; }",
    );
    let changed = optimize::apply_shift_loop_unrolling(&mut program);
    assert_eq!(changed, 1, "the loop should be widened exactly once");
    let statements = &program.functions[0].body.statements;
    let cairn_rs::syntax::Stmt::While { condition, .. } = &statements[1] else {
        panic!("expected the widened loop");
    };
    assert!(format!("{:?}", condition).contains("IntLiteral"));
    let text = format!("{:?}", condition);
    assert!(text.contains("value: 3"), "the wide test should mask two bits");
    assert!(matches!(&statements[2], cairn_rs::syntax::Stmt::If { .. }),
            "the one-shot tail should be an if");
    let tail = format!("{:?}", statements[2]);
    assert!(tail.contains("value: 1"), "the tail should keep the one-bit test");
}

#[test]
fn the_common_even_loop_unrolls_both_shifts_and_the_counter() {
    let mut program = analysed(
        "fn main() -> i64 { var a: i64 = 8; var b: i64 = 4; var k: i64 = 0; \
         while (a & 1) == 0 && (b & 1) == 0 { a = a >> 1; b = b >> 1; k = k + 1; } \
         return a + b + k; }",
    );
    let changed = optimize::apply_shift_loop_unrolling(&mut program);
    assert_eq!(changed, 1);
    let statements = &program.functions[0].body.statements;
    let cairn_rs::syntax::Stmt::While { body, .. } = &statements[3] else {
        panic!("expected the widened loop");
    };
    let text = format!("{:?}", body);
    assert!(text.contains("value: 2"), "both shifts and the counter should step by two");
    assert!(!text.contains("value: 1"), "no one-bit step should remain in the wide body");
}

#[test]
fn a_shift_loop_that_accumulates_a_value_is_left_alone() {
    let mut program = analysed(
        "fn main() -> i64 { var x: i64 = 12; var t: i64 = 0; \
         while (x & 1) == 0 { x = x >> 1; t = t + x; } return t; }",
    );
    let changed = optimize::apply_shift_loop_unrolling(&mut program);
    assert_eq!(changed, 0, "a body with a second effect must not be unrolled");
}

#[test]
fn a_single_return_helper_is_inlined() {
    let mut program = analysed(
        "fn add(a: i64, b: i64) -> i64 { return a + b; } \
         fn main() -> i64 { return add(1, 2); }",
    );
    let changed = optimize::apply_pure_inlining(&mut program);
    assert!(changed >= 1);
    let cairn_rs::syntax::Stmt::Return { value, .. } = &program.functions[1].body.statements[0]
    else {
        panic!("expected a return");
    };
    assert!(matches!(value, Expr::Binary { op, .. } if op == "+"));
}

#[test]
fn a_short_self_recursive_loop_unrolls() {
    let mut program = analysed(
        "fn f(n: i64) -> i64 { var t: i64 = 0; var i: i64 = 0; \
         while i < 4 { t = t + f(i); i = i + 1; } return t; } \
         fn main() -> i64 { return f(1); }",
    );
    let changed = optimize::apply_constant_loop_unrolling(&mut program);
    assert!(changed >= 1);
    assert!(!program.functions[0].body.statements.iter().any(|statement| {
        matches!(statement, cairn_rs::syntax::Stmt::While { .. })
    }));
}

#[test]
fn a_dead_counter_loop_becomes_a_countdown_latch() {
    let mut program = analysed(
        "fn main() -> i64 { var i: i64 = 0; var t: i64 = 0; \
         while i < 10 { t = t + 1; i = i + 1; } return t; }",
    );
    let changed = optimize::apply_countdown_latches(&mut program);
    assert!(changed >= 1);
    let cairn_rs::syntax::Stmt::While { condition, body, countdown, .. } =
        &program.functions[0].body.statements[2] else {
        panic!("expected a while loop");
    };
    assert!(*countdown);
    assert!(matches!(condition, Expr::Binary { op, .. } if op == ">"));
    assert_eq!(body.statements.len(), 1);
}

#[test]
fn algebraic_simplification_collects_like_terms() {
    let mut program = analysed(
        "fn main() -> i64 { let i: i64 = 3; return i * 31 + i * 37; }",
    );
    let changed = optimize::apply_algebraic_simplification(&mut program);
    assert!(changed >= 1);
    let cairn_rs::syntax::Stmt::Return { value, .. } = &program.functions[0].body.statements[1]
    else {
        panic!("expected a return");
    };
    assert!(matches!(value, Expr::Binary { op, .. } if op == "*"));
}

#[test]
fn a_constant_if_is_replaced_by_its_branch() {
    let program = analysed("fn main() -> i64 { if 0 { return 1; } else { return 2; } }");
    let folded = Optimizer::new().fold_program(&program);
    assert_eq!(folded.functions[0].body.statements.len(), 1);
    assert!(matches!(folded.functions[0].body.statements[0],
                     cairn_rs::syntax::Stmt::Return { .. }));
}

#[test]
fn a_counted_loop_body_is_duplicated_behind_a_shrunk_bound() {
    let mut program = analysed(
        "fn main() -> i64 { var t: i64 = 0; var i: i64 = 0; \
         while i < 10 { t = t + i; i = i + 1; } return t; }",
    );
    let changed = optimize::apply_counted_loop_duplication(&mut program);
    assert_eq!(changed, 1, "the loop should be duplicated exactly once");
    let statements = &program.functions[0].body.statements;
    let cairn_rs::syntax::Stmt::While { condition, body, .. } = &statements[2] else {
        panic!("expected the widened loop at index 2");
    };
    assert_eq!(format!("{}", condition.span().start) != "", true);
    let text = format!("{:?}", condition);
    assert!(text.contains("value: 9"), "the wide bound should be one less");
    assert_eq!(body.statements.len(), 4, "two copies of a two-statement body");
    assert!(matches!(&statements[3], cairn_rs::syntax::Stmt::While { .. }),
            "the original loop must follow as the one-shot tail");
    let tail = format!("{:?}", statements[3]);
    assert!(tail.contains("value: 10"), "the tail keeps the original bound");
}

#[test]
fn an_inclusive_bound_duplicates_with_the_same_literal() {
    let mut program = analysed(
        "fn main() -> i64 { var t: i64 = 0; var i: i64 = 0; \
         while i <= 10 { t = t + i; i = i + 1; } return t; }",
    );
    assert_eq!(optimize::apply_counted_loop_duplication(&mut program), 1);
    let statements = &program.functions[0].body.statements;
    let wide = format!("{:?}", statements[2]);
    assert!(wide.contains("value: 10"), "i <= 10 widens to i < 10");
}

#[test]
fn a_loop_whose_counter_moves_elsewhere_is_left_alone() {
    let mut program = analysed(
        "fn main() -> i64 { var t: i64 = 0; var i: i64 = 0; \
         while i < 10 { i = i + 1; t = t + i; i = i + 1; } return t; }",
    );
    assert_eq!(optimize::apply_counted_loop_duplication(&mut program), 0);
}

#[test]
fn only_the_inner_loop_is_duplicated_when_loops_nest() {
    let mut program = analysed(
        "fn main() -> i64 { var t: i64 = 0; var i: i64 = 0; var j: i64 = 0; \
         while i < 10 { j = 0; while j < 3 { j = j + 1; } t = t + j; i = i + 1; } return t; }",
    );
    assert_eq!(optimize::apply_counted_loop_duplication(&mut program), 1);
    let statements = &program.functions[0].body.statements;
    let outer = statements.iter().find_map(|statement| {
        let cairn_rs::syntax::Stmt::While { .. } = statement else { return None };
        Some(format!("{:?}", statement))
    }).expect("the outer loop is still in the body");
    assert!(outer.contains("value: 10"),
            "the outer loop keeps its bound because its body holds a loop");
    assert_eq!(outer.matches("value: 3").count(), 1,
               "just the inner loop is widened");
}