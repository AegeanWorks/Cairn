//! Semantic tests ported from `tests/test_sema.py`: every reachable rule, one failing source each.

use cairn_rs::parser::parse;
use cairn_rs::sema::analyze;
use cairn_rs::syntax::{Expr, Stmt};

fn codes(source: &str) -> Vec<String> {
    // The Python helper wraps `analyze(parse(source))` in assertRaises, so a lexer or parser
    // diagnostic counts too (e.g. a literal above 2**63 fails before semantic analysis).
    let mut program = match parse(source, "<source>") {
        Ok(program) => program,
        Err(error) => return error.diagnostics.into_iter().map(|item| item.code).collect(),
    };
    match analyze(&mut program, "<source>") {
        Ok(()) => Vec::new(),
        Err(error) => error.diagnostics.into_iter().map(|item| item.code).collect(),
    }
}

#[test]
fn valid_program_takes_slots_in_declaration_order() {
    let mut program = parse(
        "fn f(a: i64, b: i64) -> i64 { var c: i64 = a; let d: i64 = c; return d + b; }\
         fn main() -> i64 { return f(1, 2); }",
        "<source>",
    )
    .unwrap();
    analyze(&mut program, "<source>").unwrap();
    let function = &program.functions[0];
    assert_eq!(
        function.parameters.iter().map(|p| p.slot).collect::<Vec<_>>(),
        vec![0, 1]
    );
    assert_eq!(function.slots, 4);
}

#[test]
fn names_resolve_to_their_slot() {
    let mut program = parse("fn main() -> i64 { let x: i64 = 7; return x; }", "<source>").unwrap();
    analyze(&mut program, "<source>").unwrap();
    let Stmt::Return { value, .. } = &program.functions[0].body.statements[1] else {
        panic!("expected a return");
    };
    let Expr::Name { slot, .. } = value else {
        panic!("expected a name");
    };
    assert_eq!(*slot, 0);
}

#[test]
fn entry_point_and_duplicate_function() {
    assert_eq!(codes("fn helper() -> i64 { return 1; }"), vec!["E3011"]);
    assert_eq!(
        codes("fn f() -> i64 { return 1; }fn f() -> i64 { return 2; }\
               fn main() -> i64 { return f(); }"),
        vec!["E3001"]
    );
}

#[test]
fn parameter_limit_and_basic_names() {
    assert!(parse(
        "fn f(a: i64, b: i64, c: i64, d: i64, e: i64, f2: i64, g: i64, h: i64) -> i64 \
         { return a + h; }fn main() -> i64 { return 0; }",
        "<source>",
    )
    .is_ok());
    assert_eq!(
        codes("fn f(a: i64, b: i64, c: i64, d: i64, e: i64, f2: i64, g: i64, h: i64, i2: i64) \
               -> i64 { return a; }fn main() -> i64 { return 0; }"),
        vec!["E3010"]
    );
    assert_eq!(codes("fn main() -> i64 { return missing; }"), vec!["E3007"]);
    assert_eq!(
        codes("fn main() -> i64 { let x: i64 = 1; x = 2; return x; }"),
        vec!["E3005"]
    );
    assert_eq!(
        codes("fn main() -> i64 { let x: i64 = 1; let x: i64 = 2; return x; }"),
        vec!["E3006"]
    );
}

#[test]
fn calls_and_builtin_names() {
    assert_eq!(codes("fn main() -> i64 { return nope(); }"), vec!["E3003"]);
    assert_eq!(
        codes("fn f(a: i64) -> i64 { return a; }fn main() -> i64 { return f(1, 2); }"),
        vec!["E3004"]
    );
    assert_eq!(codes("fn main() -> i64 { return print; }"), vec!["E3012"]);
    assert_eq!(
        codes("fn f(print: i64) -> i64 { return 0; }fn main() -> i64 { return f(1); }"),
        vec!["E3012"]
    );
    assert_eq!(codes("fn main() -> i64 { print = 1; return 0; }"), vec!["E3012"]);
}

#[test]
fn bit_builtins_have_their_arities() {
    assert!(parse("fn main() -> i64 { popcount(255); return 0; }", "<source>").is_ok());
    assert_eq!(codes("fn main() -> i64 { popcount(); return 0; }"), vec!["E3004"]);
    assert_eq!(codes("fn main() -> i64 { clz(); return 0; }"), vec!["E3004"]);
    assert_eq!(codes("fn main() -> i64 { ctz(); return 0; }"), vec!["E3004"]);
    assert_eq!(codes("fn main() -> i64 { bswap(); return 0; }"), vec!["E3004"]);
    assert_eq!(codes("fn main() -> i64 { rotl(255); return 0; }"), vec!["E3004"]);
    assert_eq!(codes("fn main() -> i64 { rotl(); return 0; }"), vec!["E3004"]);
    let mut program = parse("fn main() -> i64 { rotl(255, 4); return 0; }", "<source>").unwrap();
    analyze(&mut program, "<source>").unwrap();
}

#[test]
fn io_and_string_builtins_have_their_arities() {
    assert_eq!(codes("fn main() -> i64 { return arg_byte(1); }"), vec!["E3004"]);
    assert_eq!(codes("fn main() -> i64 { return argc(1); }"), vec!["E3004"]);
    assert_eq!(codes("fn main() -> i64 { return read_char(2); }"), vec!["E3004"]);
    assert_eq!(codes("fn main() -> i64 { return read_char; }"), vec!["E3012"]);
    assert_eq!(codes("fn main() -> i64 { return str_len(\"hi\", 1); }"), vec!["E3004"]);
    assert_eq!(codes("fn main() -> i64 { return print_str(); }"), vec!["E3004"]);
    assert_eq!(codes("fn main() -> i64 { return str_byte(\"hi\"); }"), vec!["E3004"]);
    assert_eq!(codes("fn main() -> i64 { flush(1); return 0; }"), vec!["E3004"]);
    let mut program = parse(
        "fn main() -> i64 { flush(); print_str(\"hi\"); return 0; }",
        "<source>",
    )
    .unwrap();
    analyze(&mut program, "<source>").unwrap();
}

#[test]
fn exit_is_a_statement_never_a_value() {
    assert_eq!(codes("fn main() -> i64 { exit(); }"), vec!["E3004"]);
    assert_eq!(codes("fn main() -> i64 { return exit(1); }"), vec!["E3013"]);
    assert_eq!(
        codes("fn main() -> i64 { var x: i64 = exit(1); return 0; }"),
        vec!["E3013"]
    );
    assert_eq!(
        codes("fn main() -> i64 { if exit(1) == 0 { return 0; } return 1; }"),
        vec!["E3013"]
    );
    let mut program = parse(
        "fn f(c: i64) -> i64 { if c == 0 { exit(3); } else { return 1; } }\
         fn main() -> i64 { return f(0); }",
        "<source>",
    )
    .unwrap();
    analyze(&mut program, "<source>").unwrap();
}

#[test]
fn return_paths_literals_and_loop_control() {
    assert_eq!(
        codes("fn main() -> i64 { return 9223372036854775809; }"),
        vec!["E1003"]
    );
    assert_eq!(codes("fn main() -> i64 { break; return 0; }"), vec!["E3008"]);
    assert_eq!(codes("fn main() -> i64 { let x: i64 = 1; }"), vec!["E3009"]);
    let mut program =
        parse("fn main() -> i64 { if 1 { return 1; } else { return 2; } }", "<source>").unwrap();
    analyze(&mut program, "<source>").unwrap();
}

#[test]
fn every_diagnostic_is_collected_in_one_pass() {
    let found = codes("fn main() -> i64 { let a: i64 = b; c = 1; return d(); }");
    assert_eq!(found.iter().filter(|code| *code == "E3007").count(), 2);
    assert_eq!(found.iter().filter(|code| *code == "E3003").count(), 1);
}

#[test]
fn the_for_variable_is_scoped_to_its_loop() {
    let mut program = parse(
        "fn main() -> i64 { var t: i64 = 0; for i in 0..3 { t = t + i; }\
         for i in 0..4 { t = t + i; } return t; }",
        "<source>",
    )
    .unwrap();
    analyze(&mut program, "<source>").unwrap();
    let loops: Vec<i64> = program.functions[0]
        .body
        .statements
        .iter()
        .filter_map(|statement| match statement {
            Stmt::For { slot, .. } => Some(*slot),
            _ => None,
        })
        .collect();
    assert_eq!(loops, vec![1, 2]);
    assert_eq!(program.functions[0].slots, 3);
}

#[test]
fn for_loop_shadowing_and_restore() {
    let mut program = parse(
        "fn main() -> i64 { let i: i64 = 9; var t: i64 = 0; for i in 0..2 { t = t + i; }\
         return i + t; }",
        "<source>",
    )
    .unwrap();
    analyze(&mut program, "<source>").unwrap();
    let Stmt::Return { value, .. } = &program.functions[0].body.statements[3] else {
        panic!("expected a return");
    };
    let Expr::Binary { left, .. } = value else {
        panic!("expected a binary expression");
    };
    assert!(matches!(left.as_ref(), Expr::Name { slot: 0, .. }));
}

#[test]
fn for_loop_refusals() {
    assert_eq!(
        codes("fn main() -> i64 { for i in 0..2 { let i: i64 = 1; } return 0; }"),
        vec!["E3006"]
    );
    assert_eq!(
        codes("fn main() -> i64 { for print in 0..2 { } return 0; }"),
        vec!["E3012"]
    );
    assert_eq!(
        codes("fn main() -> i64 { for i in 0..2 { } return i; }"),
        vec!["E3007"]
    );
    let mut program = parse(
        "fn main() -> i64 { for i in 0..9 { if i == 3 { break; } if i == 1 { continue; } }\
         return 0; }",
        "<source>",
    )
    .unwrap();
    analyze(&mut program, "<source>").unwrap();
}

#[test]
fn arrays_reserve_consecutive_slots_and_check_indices() {
    let mut program = parse(
        "fn main() -> i64 { var a: [8]i64 = [0; 8]; var j: i64 = 5; a[j] = j; return 0; }",
        "<source>",
    )
    .unwrap();
    analyze(&mut program, "<source>").unwrap();
    let function = &program.functions[0];
    assert_eq!(function.slots, 10);
    let Stmt::Let { slot, .. } = &function.body.statements[0] else {
        panic!("expected a binding");
    };
    assert_eq!(*slot, 0);
    let Stmt::Assign { slot, index, .. } = &function.body.statements[2] else {
        panic!("expected an assignment");
    };
    assert_eq!(*slot, 0);
    let Expr::Name { slot, .. } = index.as_ref().unwrap() else {
        panic!("expected an index name");
    };
    assert_eq!(*slot, 8);
}

#[test]
fn array_refusals() {
    assert_eq!(
        codes("fn main() -> i64 { var a: [3]i64 = 5; return 0; }"),
        vec!["E3014"]
    );
    assert_eq!(
        codes("fn main() -> i64 { var a: [3]i64 = [1, 2]; return 0; }"),
        vec!["E3014"]
    );
    assert_eq!(
        codes("fn main() -> i64 { var a: [0]i64 = []; return 0; }"),
        vec!["E3014"]
    );
    assert_eq!(
        codes("fn main() -> i64 { var a: [70000]i64 = [0; 70000]; return 0; }"),
        vec!["E3014"]
    );
    assert_eq!(
        codes("fn main() -> i64 { var a: i64 = 0; return a[0]; }"),
        vec!["E3015"]
    );
    assert_eq!(
        codes("fn main() -> i64 { var a: [2]i64 = [1, 1]; return a; }"),
        vec!["E3016"]
    );
    assert_eq!(
        codes("fn main() -> i64 { var a: [4]i64 = [0; 4]; return a[9]; }"),
        vec!["E3017"]
    );
    assert_eq!(
        codes("fn main() -> i64 { let a: [2]i64 = [1, 1]; a[0] = 1; return 0; }"),
        vec!["E3005"]
    );
    assert_eq!(codes("fn main() -> i64 { b[0] = 1; return 0; }"), vec!["E3007"]);
}
