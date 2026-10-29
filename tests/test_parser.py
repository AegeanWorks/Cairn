"""Parser tests: shapes, precedence and the grammar's positioned refusals."""

import unittest

from cairn import syntax
from cairn.diagnostics import DiagnosticError
from cairn.parser import parse


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
    def test_true_and_false_are_integers(self) -> None:
        values = [statement.expression.arguments[0].value
                  for statement in body("print(true); print(false);").statements]
        self.assertEqual(values, [1, 0])


if __name__ == "__main__":
    unittest.main()
