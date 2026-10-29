"""Optimiser tests: folding arithmetic, refusals and effect preservation."""

import unittest

from cairn import codegen, optimize, syntax
from cairn.diagnostics import Span
from cairn.parser import parse
from cairn.sema import analyze

SPAN = Span(0, 0, 1, 1, 1, 1)


def folded_value(op: str, left: int, right: int):
    """`fold_constant` for one pair, through the public entry point."""
    return optimize.fold_constant(op, left, right)


def optimized_body(source: str) -> syntax.Block:
    """Optimise a single-function body; a trailing `return 0;` is added and then hidden.

    Semantic analysis requires every function to end in a return, so the helper supplies one and
    removes it from the result, leaving the statements the source itself produced.
    """
    program = optimize.optimize(analyze(parse(f"fn main() -> i64 {{ {source} return 0; }}")))
    statements = program.functions[0].body.statements
    assert isinstance(statements[-1], syntax.Return)
    return syntax.Block(span=statements[-1].span, statements=statements[:-1])


class FoldTest(unittest.TestCase):
    """Folding must match the machine exactly, including wrapping and the division trap."""

    def test_arithmetic_wraps_at_the_i64_boundary(self) -> None:
        self.assertEqual(folded_value("+", 2 ** 63 - 1, 1), -(2 ** 63))
        self.assertEqual(folded_value("*", 2 ** 62, 4), 0)
        self.assertEqual(folded_value("-", -(2 ** 63), 1), 2 ** 63 - 1)

    def test_division_truncates_toward_zero_and_remainder_follows_the_dividend(self) -> None:
        self.assertEqual(folded_value("/", -7, 2), -3)
        self.assertEqual(folded_value("%", -7, 2), -1)
        self.assertEqual(folded_value("/", 7, -2), -3)
        self.assertEqual(folded_value("%", 7, -2), 1)

    def test_division_by_zero_is_never_folded(self) -> None:
        self.assertIsNone(folded_value("/", 1, 0))
        self.assertIsNone(folded_value("%", 1, 0))

    def test_the_one_overflowing_division_is_never_folded(self) -> None:
        self.assertIsNone(folded_value("/", -(2 ** 63), -1))
        self.assertIsNone(folded_value("%", -(2 ** 63), -1))

    def test_shifts_mask_the_count_the_way_the_hardware_does(self) -> None:
        self.assertEqual(folded_value("<<", 1, 64), 1)
        self.assertEqual(folded_value(">>", -16, 2), -4)
        self.assertEqual(folded_value("<<", 1, 63), -(2 ** 63))

    def test_comparisons_and_logical_operators_yield_zero_or_one(self) -> None:
        self.assertEqual(folded_value("<", 1, 2), 1)
        self.assertEqual(folded_value(">=", 1, 2), 0)
        self.assertEqual(folded_value("&&", 3, -1), 1)
        self.assertEqual(folded_value("||", 0, 0), 0)

    def test_bitwise_operators_keep_two_complement_meaning(self) -> None:
        self.assertEqual(folded_value("&", -1, 6), 6)
        self.assertEqual(folded_value("|", -2, 1), -1)
        self.assertEqual(folded_value("^", -1, 5), -6)

    def test_unary_folding(self) -> None:
        self.assertEqual(optimize.fold_unary("-", 5), -5)
        self.assertEqual(optimize.fold_unary("-", -(2 ** 63)), -(2 ** 63))
        self.assertEqual(optimize.fold_unary("~", 0), -1)
        self.assertEqual(optimize.fold_unary("!", 0), 1)


class RewriteTest(unittest.TestCase):
    """Whole-program rewrites: the tree the backend sees afterwards."""

    def test_constant_expression_becomes_one_literal(self) -> None:
        statement = optimized_body("print(1 + 2 * 3);").statements[0]
        argument = statement.expression.arguments[0]
        self.assertIsInstance(argument, syntax.IntLiteral)
        self.assertEqual(argument.value, 7)

    def test_neutral_right_operand_disappears(self) -> None:
        statement = optimized_body("let a: i64 = 4; print(a + 0);").statements[1]
        argument = statement.expression.arguments[0]
        self.assertIsInstance(argument, syntax.Name)
        self.assertEqual(argument.name, "a")

    def test_neutral_left_operand_disappears_except_for_subtraction(self) -> None:
        addition = optimized_body("let a: i64 = 4; print(0 + a);").statements[1]
        subtraction = optimized_body("let a: i64 = 4; print(0 - a);").statements[1]
        self.assertIsInstance(addition.expression.arguments[0], syntax.Name)
        negated = subtraction.expression.arguments[0]
        self.assertIsInstance(negated, syntax.Binary)
        self.assertEqual(negated.op, "-")

    def test_zero_multiply_is_dropped_only_when_nothing_calls(self) -> None:
        pure = optimized_body("let a: i64 = 4; print(a * 0);").statements[1]
        effectful = optimized_body("print(print(1) * 0);").statements[0]
        self.assertEqual(pure.expression.arguments[0].value, 0)
        self.assertIsInstance(effectful.expression.arguments[0], syntax.Binary)

    def test_constant_condition_selects_the_taken_branch(self) -> None:
        body = optimized_body("if 1 { print(1); } else { print(2); }")
        self.assertEqual(len(body.statements), 1)
        self.assertEqual(body.statements[0].expression.arguments[0].value, 1)

    def test_constant_false_while_loop_disappears(self) -> None:
        body = optimized_body("while 0 { print(1); }")
        self.assertEqual(body.statements, [])

    def test_statements_after_return_are_unreachable_and_dropped(self) -> None:
        body = optimized_body("while 1 { return 1; print(9); }")
        loop = body.statements[0]
        self.assertIsInstance(loop, syntax.While)
        self.assertEqual(len(loop.body.statements), 1)

    def test_a_call_is_never_removed(self) -> None:
        body = optimized_body("print(1);")
        self.assertEqual(len(body.statements), 1)
        self.assertIsInstance(body.statements[0], syntax.ExprStmt)

    def test_folded_count_reports_what_changed(self) -> None:
        folder = optimize.Optimizer()
        folder.run(analyze(parse("fn main() -> i64 { return 2 * 3 + 1; }")))
        self.assertGreaterEqual(folder.folded, 2)

    def test_constant_heavy_code_generates_fewer_instructions(self) -> None:
        source = ("fn main() -> i64 { var a: i64 = 0; "
                  "a = a + (2 * 3 + 4 * 5 - 6 / 2); return a; }")
        plain = analyze(parse(source))
        folded = optimize.optimize(analyze(parse(source)))
        self.assertLess(len(codegen.generate(folded).text), len(codegen.generate(plain).text))


if __name__ == "__main__":
    unittest.main()
