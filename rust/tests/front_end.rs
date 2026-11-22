//! Front-end tests ported from `tests/test_lexer.py` and `tests/test_parser.py`.
//!
//! Every assertion here mirrors a Python assertion against the same source text, so the Rust
//! compiler can be checked against the Python reference before any backend exists.

use cairn_rs::codes;
use cairn_rs::diagnostics::{Diagnostic, DiagnosticError, Span};
use cairn_rs::lexer::{tokenize, Token};
use cairn_rs::parser::{parse, Parser};
use cairn_rs::syntax::{Block, Expr, Stmt};
use cairn_rs::values;

fn tokens(source: &str) -> Vec<Token> {
    tokenize(source, "<source>").unwrap()
}

fn body(source: &str) -> Block {
    let program = parse(&format!("fn main() -> i64 {{ {} }}", source), "<source>").unwrap();
    program.functions[0].body.clone()
}

fn error_of<T>(result: Result<T, DiagnosticError>) -> Diagnostic {
    let error = result.err().expect("expected a diagnostic error");
    error.diagnostics[0].clone()
}

fn expr_stmt(statement: &Stmt) -> &Expr {
    match statement {
        Stmt::ExprStmt { expression, .. } => expression,
        _ => panic!("expected an expression statement"),
    }
}

fn as_call(expression: &Expr) -> (&str, &Vec<Expr>) {
    match expression {
        Expr::Call { name, arguments, .. } => (name, arguments),
        _ => panic!("expected a call"),
    }
}

fn as_binary(expression: &Expr) -> (&str, &Expr, &Expr) {
    match expression {
        Expr::Binary { op, left, right, .. } => (op, left, right),
        _ => panic!("expected a binary expression"),
    }
}

fn as_int(expression: &Expr) -> i128 {
    expression.as_int().expect("expected an integer literal")
}

// --- contracts -------------------------------------------------------------------------------

#[test]
fn every_registered_code_has_a_stage_and_a_summary() {
    assert_eq!(codes::REGISTRY.len(), 34);
    for entry in codes::REGISTRY {
        assert!(!entry.code.is_empty());
        assert!(!entry.stage.is_empty());
        assert!(!entry.summary.is_empty());
        assert_eq!(codes::by_code(entry.code), Some(entry));
    }
    assert_eq!(codes::by_code("E1002"), None);
    assert_eq!(codes::by_code("E3002"), None);
}

#[test]
fn codes_by_stage_preserves_registry_order() {
    let grouped = codes::codes_by_stage();
    assert_eq!(grouped[0].0, "lexer");
    assert_eq!(grouped[0].1, vec!["E1001", "E1004", "E1003"]);
    assert_eq!(grouped[1].1[0], "E2001");
}

#[test]
fn codes_in_source_reads_only_quoted_codes() {
    let found = codes::codes_in_source(&["raise fail(\"E1003\", 'V0004') E1001"]);
    assert!(found.contains("E1003"));
    assert!(found.contains("V0004"));
    assert!(!found.contains("E1001"));
}

#[test]
fn spans_point_and_join_exactly() {
    let first = Span::new(0, 3, 1, 1, 1, 4);
    let last = Span::point(8, 2, 3);
    assert_eq!(Span::join(first, last), Span::new(0, 8, 1, 1, 2, 3));
}

#[test]
fn a_diagnostic_has_the_contract_json_key_order() {
    let diagnostic = Diagnostic::new("E1001", "boom", "fix it", Span::point(0, 1, 1), "<test>");
    assert_eq!(
        diagnostic.to_json(),
        "{\"code\":\"E1001\",\"severity\":\"error\",\"path\":\"<test>\",\"line\":1,\
         \"column\":1,\"end_line\":1,\"end_column\":1,\"message\":\"boom\",\"fix\":\"fix it\"}"
    );
}

#[test]
fn the_value_list_is_frozen_and_every_guard_is_named() {
    assert_eq!(values::VALUES.len(), 6);
    assert_eq!(values::by_key("verifiable").map(|value| value.name), Some("Verifiable"));
    for value in values::VALUES {
        assert!(value.guard.contains("::test_"));
    }
    assert_eq!(values::values_manifest().len(), 6);
}

// --- lexer ------------------------------------------------------------------------------------

#[test]
fn tokens_carry_kind_text_value_and_span() {
    let result = tokens("fn main() -> i64 { return 42; }");
    assert_eq!(result[0].text, "fn");
    assert_eq!(result[0].kind, "keyword");
    assert_eq!(result[2].text, "(");
    assert_eq!(result[4].text, "->");
    let number = result.iter().find(|token| token.kind == "int").unwrap();
    assert_eq!(number.value, 42);
    assert_eq!((number.span.line, number.span.column), (1, 27));
    assert_eq!(result.last().unwrap().kind, "eof");
}

#[test]
fn comment_runs_to_end_of_line() {
    let result = tokens("// fn ignored() -> i64 {}\nlet\n");
    assert_eq!(result[0].text, "let");
    assert_eq!(result[0].span.line, 2);
}

#[test]
fn two_character_operators_win_over_one() {
    let texts: Vec<String> = tokens("<= >= == != && || << >>")
        .into_iter()
        .filter(|token| token.kind != "eof")
        .map(|token| token.text)
        .collect();
    assert_eq!(texts, vec!["<=", ">=", "==", "!=", "&&", "||", "<<", ">>"]);
}

#[test]
fn range_symbols_lex_before_the_number_boundary() {
    let texts: Vec<String> = tokens("1..10 1..=10")
        .into_iter()
        .filter(|token| token.kind != "eof")
        .map(|token| token.text)
        .collect();
    assert_eq!(texts, vec!["1", "..", "10", "1", "..=", "10"]);
}

#[test]
fn integer_beyond_i64_is_a_positioned_error() {
    let diagnostic = error_of(tokenize("let x: i64 = 9223372036854775809;", "<source>"));
    assert_eq!(diagnostic.code, "E1003");
    assert!(diagnostic.message.contains("i64"));
}

#[test]
fn two_to_the_63_lexes_so_unary_minus_can_fold_it() {
    let number = tokens("9223372036854775808;")
        .into_iter()
        .find(|token| token.kind == "int")
        .unwrap();
    assert_eq!(number.value, 1i128 << 63);
}

#[test]
fn unexpected_character_names_the_fix() {
    let diagnostic = error_of(tokenize("let x: i64 = 1; @", "<source>"));
    assert_eq!(diagnostic.code, "E1001");
    assert!(diagnostic.fix.contains("ASCII"));
    assert_eq!(diagnostic.span.column, 17);
}

#[test]
fn tokenize_reports_eof_at_the_end() {
    let result = tokens("");
    assert_eq!(result.len(), 1);
    assert_eq!(result[0].kind, "eof");
}

#[test]
fn a_string_literal_lexes_with_its_escapes_decoded() {
    let literal = tokens("print_str(\"a\\tb\\\"c\\\\d\\n\");")
        .into_iter()
        .find(|token| token.kind == "str")
        .unwrap();
    assert_eq!(literal.text, "a\tb\"c\\d\n");
}

#[test]
fn an_unterminated_string_is_a_positioned_error() {
    let diagnostic = error_of(tokenize("let x: i64 = 0; print_str(\"open", "<source>"));
    assert_eq!(diagnostic.code, "E1004");
    assert!(diagnostic.fix.to_lowercase().contains("quote"));
}

#[test]
fn an_unknown_escape_is_a_positioned_error() {
    let diagnostic = error_of(tokenize("print_str(\"bad\\q\");", "<source>"));
    assert_eq!(diagnostic.code, "E1004");
    assert!(diagnostic.message.contains("\\q"));
}

#[test]
fn a_string_token_carries_its_column() {
    let literal = tokens("let s: i64 = \"hi\";")
        .into_iter()
        .find(|token| token.kind == "str")
        .unwrap();
    assert_eq!((literal.span.line, literal.span.column), (1, 14));
}

#[test]
fn the_full_pipeline_places_a_string_on_its_span() {
    let source = "fn main() -> i64 { let s: i64 = \"hi\"; return 0; }";
    let program = parse(source, "<source>").unwrap();
    let statement = &program.functions[0].body.statements[0];
    let value = match statement {
        Stmt::Let { value, .. } => value,
        _ => panic!("expected a binding"),
    };
    assert_eq!((value.span().line, value.span().column), (1, 33));
}

#[test]
fn non_ascii_identifier_is_rejected_as_unexpected_character() {
    let diagnostic = error_of(tokenize("let café: i64 = 1;", "<source>"));
    assert_eq!(diagnostic.code, "E1001");
    assert!(diagnostic.fix.contains("ASCII"));
}

#[test]
fn long_digit_run_is_e1003_not_a_parse_failure() {
    let source = format!("{};", "9".repeat(5000));
    assert_eq!(error_of(tokenize(&source, "<source>")).code, "E1003");
}

#[test]
fn zero_padded_literal_fits_when_value_fits() {
    let source = format!("{};", "0".repeat(20));
    let number = tokens(&source).into_iter().find(|token| token.kind == "int").unwrap();
    assert_eq!(number.value, 0);
}

// --- parser ------------------------------------------------------------------------------------

#[test]
fn function_signature_and_body() {
    let program = parse("fn add(a: i64, b: i64) -> i64 { return a + b; }", "<source>").unwrap();
    let function = &program.functions[0];
    assert_eq!(function.name, "add");
    assert_eq!(
        function.parameters.iter().map(|p| p.name.as_str()).collect::<Vec<_>>(),
        vec!["a", "b"]
    );
    assert_eq!(function.return_type, "i64");
    assert_eq!(function.body.statements.len(), 1);
}

#[test]
fn multiplication_binds_tighter_than_addition() {
    let statement = &body("print(1 + 2 * 3);").statements[0];
    let (_, arguments) = as_call(expr_stmt(statement));
    let (op, left, right) = as_binary(&arguments[0]);
    assert_eq!((op, as_int(left), as_binary(right).0), ("+", 1, "*"));
}

#[test]
fn parentheses_override_precedence() {
    let statement = &body("print((1 + 2) * 3);").statements[0];
    let (_, arguments) = as_call(expr_stmt(statement));
    let (op, left, _) = as_binary(&arguments[0]);
    assert_eq!(op, "*");
    assert!(matches!(left, Expr::Parens { .. }));
}

#[test]
fn else_if_nests_as_a_block() {
    let statements = &body("if 1 { } else if 2 { } else { }").statements;
    let outer = &statements[0];
    let Stmt::If { else_block: Some(block), .. } = outer else {
        panic!("expected an if with an else");
    };
    assert!(matches!(&block.statements[0], Stmt::If { .. }));
}

#[test]
fn assignment_is_a_statement_only_for_a_name() {
    let statements = body("var x: i64 = 1; x = 2;").statements;
    assert!(matches!(&statements[1], Stmt::Assign { name, .. } if name == "x"));
}

#[test]
fn assignment_to_a_literal_or_a_call_is_e2003() {
    for source in [
        "fn main() -> i64 { 1 = 2; return 0; }",
        "fn main() -> i64 { print(1) = 2; return 0; }",
    ] {
        assert_eq!(error_of(parse(source, "<source>")).code, "E2003", "source: {}", source);
    }
}

#[test]
fn bare_literal_is_e2004() {
    assert_eq!(
        error_of(parse("fn main() -> i64 { 1; return 0; }", "<source>")).code,
        "E2004"
    );
}

#[test]
fn unknown_type_is_refused_with_the_one_type_cairn_has() {
    assert_eq!(
        error_of(parse("fn main() -> f64 { return 0; }", "<source>")).code,
        "E2002"
    );
}

#[test]
fn missing_semicolon_reports_what_was_expected() {
    let diagnostic = error_of(parse("fn main() -> i64 { return 0 }", "<source>"));
    assert_eq!(diagnostic.code, "E2001");
    assert!(diagnostic.message.contains("';'"));
}

#[test]
fn expression_statement_must_be_a_call() {
    assert_eq!(
        error_of(parse("fn main() -> i64 { 1 + 2; return 0; }", "<source>")).code,
        "E2004"
    );
}

#[test]
fn parens_call_statement_is_accepted() {
    let statement = &body("(print(1));").statements[0];
    assert!(matches!(statement, Stmt::ExprStmt { .. }));
    assert!(matches!(expr_stmt(statement).unwrap_parens(), Expr::Call { .. }));
}

#[test]
fn bare_token_list_is_e2001_not_an_index_error() {
    let diagnostic = error_of(Parser::new(Vec::new(), "<test>").parse_program());
    assert_eq!(diagnostic.code, "E2001");
}

#[test]
fn break_and_continue_spans_include_the_semicolon() {
    let source = "fn main() -> i64 { while 1 { break; continue; } return 0; }";
    let program = parse(source, "<source>").unwrap();
    let Stmt::While { body, .. } = &program.functions[0].body.statements[0] else {
        panic!("expected a while loop");
    };
    let broken = &body.statements[0];
    let continued = &body.statements[1];
    assert_eq!(&source[broken.span().start..broken.span().end], "break;");
    assert_eq!(&source[continued.span().start..continued.span().end], "continue;");
}

#[test]
fn min_int_is_spellable_as_negated_two_to_the_63() {
    let program = parse("fn main() -> i64 { return -9223372036854775808; }", "<source>").unwrap();
    let Stmt::Return { value, .. } = &program.functions[0].body.statements[0] else {
        panic!("expected a return");
    };
    assert_eq!(as_int(value), -(1i128 << 63));
}

#[test]
fn parenthesised_min_int_folds_like_the_bare_form() {
    let program = parse("fn main() -> i64 { return -(9223372036854775808); }", "<source>").unwrap();
    let Stmt::Return { value, .. } = &program.functions[0].body.statements[0] else {
        panic!("expected a return");
    };
    assert_eq!(as_int(value), -(1i128 << 63));
}

#[test]
fn true_and_false_are_integers() {
    let statements = body("print(true); print(false);").statements;
    let values: Vec<i128> = statements
        .iter()
        .map(|statement| as_int(&as_call(expr_stmt(statement)).1[0]))
        .collect();
    assert_eq!(values, vec![1, 0]);
}

// --- for loops ---------------------------------------------------------------------------------

#[test]
fn an_exclusive_range_parses() {
    let statement = &body("for i in 0..10 { print(i); }").statements[0];
    let Stmt::For { name, start, end, inclusive, body, .. } = statement else {
        panic!("expected a for loop");
    };
    assert_eq!(name, "i");
    assert!(!inclusive);
    assert_eq!(as_int(start), 0);
    assert_eq!(as_int(end), 10);
    assert_eq!(body.statements.len(), 1);
}

#[test]
fn an_inclusive_range_parses() {
    let statement = &body("var n: i64 = 2; for i in n..=n + 5 { n = n + 1; }").statements[1];
    let Stmt::For { inclusive, start, end, .. } = statement else {
        panic!("expected a for loop");
    };
    assert!(inclusive);
    assert!(matches!(start, Expr::Name { .. }));
    assert!(matches!(end, Expr::Binary { .. }));
}

#[test]
fn a_missing_range_symbol_is_a_positioned_refusal() {
    let diagnostic = error_of(parse(
        "fn main() -> i64 { for i in 0 10 { } return 0; }",
        "<source>",
    ));
    assert_eq!(diagnostic.code, "E2005");
    assert!(diagnostic.message.contains(".."));
}

#[test]
fn the_loop_variable_must_be_a_name() {
    assert_eq!(
        error_of(parse("fn main() -> i64 { for 5 in 0..3 { } return 0; }", "<source>")).code,
        "E2001"
    );
}

#[test]
fn the_loop_needs_the_in_keyword() {
    assert_eq!(
        error_of(parse("fn main() -> i64 { for i 0..3 { } return 0; }", "<source>")).code,
        "E2001"
    );
}

// --- arrays ------------------------------------------------------------------------------------

#[test]
fn a_fill_literal_expands_to_one_node_per_element() {
    let program = parse("fn main() -> i64 { var a: [4]i64 = [0; 4]; return 0; }", "<source>")
        .unwrap();
    let Stmt::Let { value, array_size, .. } = &program.functions[0].body.statements[0] else {
        panic!("expected a binding");
    };
    assert_eq!(*array_size, Some(4));
    let Expr::ArrayLiteral { elements, .. } = value else {
        panic!("expected an array literal");
    };
    assert_eq!(elements.len(), 4);
    for element in elements {
        assert_eq!(as_int(element), 0);
    }
}

#[test]
fn an_aggregate_literal_keeps_its_element_expressions() {
    let program = parse(
        "fn main() -> i64 { let a: [3]i64 = [1, 2, 3]; return a[0]; }",
        "<source>",
    )
    .unwrap();
    let Stmt::Let { value, array_size, .. } = &program.functions[0].body.statements[0] else {
        panic!("expected a binding");
    };
    assert_eq!(*array_size, Some(3));
    let Expr::ArrayLiteral { elements, .. } = value else {
        panic!("expected an array literal");
    };
    assert_eq!(elements.iter().map(as_int).collect::<Vec<_>>(), vec![1, 2, 3]);
    let Stmt::Return { value, .. } = &program.functions[0].body.statements[1] else {
        panic!("expected a return");
    };
    let Expr::Index { operand, index, .. } = value else {
        panic!("expected an index");
    };
    assert_eq!(operand.as_name(), Some("a"));
    assert_eq!(as_int(index), 0);
}

#[test]
fn index_store_targets_the_binding_with_its_index() {
    let program = parse(
        "fn main() -> i64 { var a: [4]i64 = [0; 4]; a[1] = 7; return 0; }",
        "<source>",
    )
    .unwrap();
    let Stmt::Assign { name, value, index, .. } = &program.functions[0].body.statements[1] else {
        panic!("expected an assignment");
    };
    assert_eq!(name, "a");
    assert_eq!(as_int(index.as_ref().unwrap()), 1);
    assert_eq!(as_int(value), 7);
}

#[test]
fn fill_counts_must_be_integer_literals() {
    assert_eq!(
        error_of(parse(
            "fn main() -> i64 { var n: i64 = 4; var a: [4]i64 = [0; n]; return 0; }",
            "<source>",
        ))
        .code,
        "E2001"
    );
}

#[test]
fn array_types_stay_local_bindings() {
    assert_eq!(
        error_of(parse("fn f(a: [2]i64) -> i64 { return a[0]; }", "<source>")).code,
        "E2002"
    );
}

#[test]
fn an_unclosed_index_is_a_positioned_refusal() {
    assert_eq!(
        error_of(parse(
            "fn main() -> i64 { var a: [4]i64 = [0; 4]; return a[2; }",
            "<source>",
        ))
        .code,
        "E2001"
    );
}
