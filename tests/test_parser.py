"""Parser tests: shapes, precedence and the grammar's positioned refusals."""

import unittest

from cairn import syntax
from cairn.diagnostics import DiagnosticError
from cairn.parser import Parser, parse


def body(source: str) -> syntax.Block:
    """The body of the single function in a tiny program."""
    return parse(f"fn main() -> i64 {{ {source} }}").functions[0].body


class ParserTest(unittest.TestCase):
    """The tree shape decides code shape, so these assertions are about generated code too."""

    def test_function_signature_and_body(self) -> None:
        program = parse("fn add(a: i64, b: i64) -> i64 { return a + b; }")
        function = program.functions[0]
        self.assertEqual(function.name, "add")
        self.assertEqual([parameter.name for parameter in function.parameters], ["a", "b"])
        self.assertEqual(function.return_type, "i64")
        self.assertEqual(len(function.body.statements), 1)

    def test_multiplication_binds_tighter_than_addition(self) -> None:
        statement = body("print(1 + 2 * 3);").statements[0]
        call = statement.expression
        self.assertEqual(call.name, "print")
        tree = call.arguments[0]
        self.assertEqual((tree.op, tree.left.value, tree.right.op), ("+", 1, "*"))

    def test_parentheses_override_precedence(self) -> None:
        statement = body("print((1 + 2) * 3);").statements[0]
        tree = statement.expression.arguments[0]
        self.assertEqual(tree.op, "*")
        self.assertIsInstance(tree.left, syntax.Parens)
        self.assertEqual(tree.left.inner.op, "+")

    def test_else_if_nests_as_a_block(self) -> None:
        program = body("if 1 { } else if 2 { } else { }")
        outer = program.statements[0]
        self.assertIsInstance(outer, syntax.If)
        self.assertIsInstance(outer.else_block.statements[0], syntax.If)

    def test_assignment_is_a_statement_only_for_a_name(self) -> None:
        statement = body("var x: i64 = 1; x = 2;").statements[1]
        self.assertIsInstance(statement, syntax.Assign)
        self.assertEqual(statement.name, "x")

    def test_assignment_to_a_literal_or_a_call_is_E2003(self) -> None:
        for source in ("fn main() -> i64 { 1 = 2; return 0; }",
                       "fn main() -> i64 { print(1) = 2; return 0; }"):
            with self.subTest(source=source):
                with self.assertRaises(DiagnosticError) as caught:
                    parse(source)
                self.assertEqual(caught.exception.diagnostics[0].code, "E2003")

    def test_bare_literal_is_E2004(self) -> None:
        with self.assertRaises(DiagnosticError) as caught:
            parse("fn main() -> i64 { 1; return 0; }")
        self.assertEqual(caught.exception.diagnostics[0].code, "E2004")

    def test_unknown_type_is_refused_with_the_one_type_cairn_has(self) -> None:
        with self.assertRaises(DiagnosticError) as caught:
            parse("fn main() -> f64 { return 0; }")
        self.assertEqual(caught.exception.diagnostics[0].code, "E2002")

    def test_missing_semicolon_reports_what_was_expected(self) -> None:
        with self.assertRaises(DiagnosticError) as caught:
            parse("fn main() -> i64 { return 0 }")
        diagnostic = caught.exception.diagnostics[0]
        self.assertEqual(diagnostic.code, "E2001")
        self.assertIn("';'", diagnostic.message)

    def test_expression_statement_must_be_a_call(self) -> None:
        with self.assertRaises(DiagnosticError) as caught:
            parse("fn main() -> i64 { 1 + 2; return 0; }")
        self.assertEqual(caught.exception.diagnostics[0].code, "E2004")

    def test_parens_call_statement_is_accepted(self) -> None:
        statement = body("(print(1));").statements[0]
        self.assertIsInstance(statement, syntax.ExprStmt)
        unwrapped = statement.expression
        while isinstance(unwrapped, syntax.Parens):
            unwrapped = unwrapped.inner
        self.assertIsInstance(unwrapped, syntax.Call)

    def test_bare_token_list_is_E2001_not_an_index_error(self) -> None:
        with self.assertRaises(DiagnosticError) as caught:
            Parser([], "<test>").parse_program()
        self.assertEqual(caught.exception.diagnostics[0].code, "E2001")

    def test_break_and_continue_spans_include_the_semicolon(self) -> None:
        source = "fn main() -> i64 { while 1 { break; continue; } return 0; }"
        loop = parse(source).functions[0].body.statements[0]
        broken, continued = loop.body.statements
        self.assertEqual(source[broken.span.start:broken.span.end], "break;")
        self.assertEqual(source[continued.span.start:continued.span.end], "continue;")

    def test_min_int_is_spellable_as_negated_two_to_the_63(self) -> None:
        program = parse("fn main() -> i64 { return -9223372036854775808; }")
        value = program.functions[0].body.statements[0].value
        self.assertIsInstance(value, syntax.IntLiteral)
        self.assertEqual(value.value, -(2 ** 63))

    def test_true_and_false_are_integers(self) -> None:
        values = [statement.expression.arguments[0].value
                  for statement in body("print(true); print(false);").statements]
        self.assertEqual(values, [1, 0])


if __name__ == "__main__":
    unittest.main()
