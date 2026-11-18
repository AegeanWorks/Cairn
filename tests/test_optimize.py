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

    def test_zero_fold_never_drops_a_division_trap(self) -> None:
        literal = optimized_body("print((1 / 0) * 0);").statements[0]
        dynamic = optimized_body("var zero: i64 = 0; print((10 / zero) * 0);").statements[1]
        remainder = optimized_body("print((1 % 0) & 0);").statements[0]
        for statement in (literal, dynamic, remainder):
            self.assertIsInstance(statement.expression.arguments[0], syntax.Binary)
        safe = optimized_body("print((8 / 2) * 0);").statements[0]
        self.assertIsInstance(safe.expression.arguments[0], syntax.IntLiteral)
        self.assertEqual(safe.expression.arguments[0].value, 0)

    def test_constant_condition_selects_the_taken_branch(self) -> None:
        body = optimized_body("if 1 { print(1); } else { print(2); }")
        self.assertEqual(len(body.statements), 1)
        self.assertEqual(body.statements[0].expression.arguments[0].value, 1)

    def test_constant_false_while_loop_disappears(self) -> None:
        body = optimized_body("while 0 { print(1); }")
        self.assertEqual(body.statements, [])

    def test_while_zero_counts_its_rewrite(self) -> None:
        folder = optimize.Optimizer()
        folder.run(analyze(parse("fn main() -> i64 { while 0 { print(1); } return 0; }")))
        self.assertGreaterEqual(folder.folded, 1)

    def test_dropped_unreachable_statements_count(self) -> None:
        folder = optimize.Optimizer()
        program = folder.run(analyze(parse(
            "fn main() -> i64 { while 1 { return 1; print(9); } return 0; }")))
        loop = program.functions[0].body.statements[0]
        self.assertEqual(len(loop.body.statements), 1)
        self.assertGreaterEqual(folder.folded, 1)

    def test_double_negation_folds_to_the_value(self) -> None:
        argument = optimized_body("print(--1);").statements[0].expression.arguments[0]
        self.assertIsInstance(argument, syntax.IntLiteral)
        self.assertEqual(argument.value, 1)

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


class CountdownLatchTest(unittest.TestCase):
    """`while v < C` loops whose counter is dead outside the body lower as countdown latches."""

    def test_a_counter_loop_counts_down_instead_of_up(self) -> None:
        body = optimized_body(
            "var i: i64 = 0; var total: i64 = 0;"
            "while i < 6 { total = total + 3; i = i + 1; } print(total);")
        loops = [s for s in body.statements if isinstance(s, syntax.While)]
        self.assertEqual(len(loops), 1)
        loop = loops[0]
        self.assertTrue(loop.countdown)
        self.assertEqual(loop.condition.op, ">")
        self.assertEqual(loop.condition.right.value, 0)
        init = body.statements[0]
        self.assertIsInstance(init, syntax.Let)
        self.assertEqual(init.value.value, 6)
        self.assertEqual(len(loop.body.statements), 1)

    def test_the_loop_condition_shape_survives_the_rewrite(self) -> None:
        body = optimized_body(
            "var i: i64 = 0; var total: i64 = 0;"
            "while i < 6 { total = total + 3; i = i + 1; } print(total);")
        loop = body.statements[2]
        self.assertIsInstance(loop.condition.left, syntax.Name)
        self.assertEqual(loop.condition.left.slot, body.statements[0].slot)

    def test_a_counter_read_after_the_loop_refuses_the_rewrite(self) -> None:
        body = optimized_body(
            "var i: i64 = 0; while i < 6 { print(3); i = i + 1; } print(i);")
        loop = body.statements[1]
        self.assertFalse(loop.countdown)
        self.assertTrue(body.statements[0].mutable)
        self.assertEqual(body.statements[0].value.value, 0)
        self.assertEqual(loop.condition.op, "<")

    def test_a_body_that_reads_the_counter_refuses_the_rewrite(self) -> None:
        body = optimized_body(
            "var i: i64 = 0; var total: i64 = 0;"
            "while i < 6 { total = total + i; i = i + 1; } print(total);")
        loop = body.statements[2]
        self.assertFalse(loop.countdown)
        self.assertEqual(loop.condition.op, "<")

    def test_a_parameter_counter_refuses_the_rewrite(self) -> None:
        program = analyze(parse(
            "fn main() -> i64 { print(run(0)); return 0; }\n"
            "fn run(n: i64) -> i64 { var t: i64 = 0;"
            " while n < 6 { t = t + 1; n = n + 1; } return t; }"))
        program = optimize.optimize(program)
        loop = program.functions[1].body.statements[1]
        self.assertFalse(loop.countdown)

    def test_a_loop_with_break_refuses_the_rewrite(self) -> None:
        body = optimized_body(
            "var i: i64 = 0; while i < 6 { break; i = i + 1; } print(1);")
        loop = body.statements[1]
        self.assertFalse(loop.countdown)

    def test_a_loop_with_continue_refuses_the_rewrite(self) -> None:
        body = optimized_body(
            "var i: i64 = 0; while i < 6 { continue; i = i + 1; } print(1);")
        loop = body.statements[1]
        self.assertFalse(loop.countdown)

    def test_a_countdown_loop_latches_with_a_bottom_dec_and_jnz(self) -> None:
        source = ("fn main() -> i64 { var i: i64 = 0; var t: i64 = 0;"
                  " while i < 6 { t = t + 3; i = i + 1; } print(t); return 0; }")
        artifact = codegen.generate(optimize.optimize(analyze(parse(source))))
        text = bytes(artifact.text)
        labels = artifact.text_labels
        main_start = labels[codegen.function_label("main")]
        runtime_start = min(offset for name, offset in labels.items()
                            if name.startswith("cairn.runtime."))
        main_code = text[main_start:runtime_start]
        self.assertIn(b"\x75", main_code)        # jnz rel8: the countdown latch, backward
        self.assertNotIn(b"\x0f\x8d", main_code)  # jge rel32: the count-up guard is gone
        self.assertNotIn(b"\x49\x83\xc4\x01", main_code)  # add r12,1: the old update fold


class ForLoweringTest(unittest.TestCase):
    """The for sugar lowers to Let + While before any other pass; no For survives lowering."""

    def lowered(self, source: str):
        program = analyze(parse(f"fn main() -> i64 {{ {source} return 0; }}"))
        return optimize.lower_for_loops(program).functions[0].body.statements

    def test_an_exclusive_for_becomes_let_plus_while(self) -> None:
        statements = self.lowered("var t: i64 = 0; for i in 0..4 { t = t + i; }")
        loop_let, while_stmt = statements[1], statements[2]
        self.assertIsInstance(loop_let, syntax.Let)
        self.assertTrue(loop_let.mutable)
        self.assertEqual(loop_let.slot, 1)
        self.assertIsInstance(while_stmt, syntax.While)
        condition = while_stmt.condition
        self.assertEqual(condition.op, "<")
        self.assertEqual(condition.left.slot, 1)
        self.assertEqual(condition.right.value, 4)
        increment = while_stmt.body.statements[-1]
        self.assertIsInstance(increment, syntax.Assign)
        self.assertEqual(increment.slot, 1)
        self.assertEqual(increment.value.op, "+")

    def test_an_inclusive_for_compares_less_equal(self) -> None:
        statements = self.lowered("for i in 0..=4 { break; }")
        self.assertEqual(statements[1].condition.op, "<=")

    def test_a_continue_inside_the_for_steps_before_jumping(self) -> None:
        block = self.lowered("var t: i64 = 0; for i in 0..4 { if i == 2 { continue; } t = t + 1; }")
        body = block[2].body.statements[0].then_block.statements
        replaced = body[0]
        self.assertIsInstance(replaced, syntax.Block)
        self.assertIsInstance(replaced.statements[0], syntax.Assign)
        self.assertEqual(replaced.statements[0].slot, 1)
        self.assertIsInstance(replaced.statements[1], syntax.Continue)

    def test_a_continue_in_a_nested_while_is_not_touched(self) -> None:
        block = self.lowered("var t: i64 = 0; for i in 0..4 {"
                             " while t < 8 { t = t + 1; continue; } }")
        inner = block[2].body.statements[0]
        self.assertIsInstance(inner, syntax.While)
        self.assertEqual([type(item).__name__ for item in inner.body.statements],
                         ["Assign", "Continue"])

    def test_nested_fors_bind_the_continue_to_the_inner_loop(self) -> None:
        block = self.lowered("var t: i64 = 0; for i in 0..3 { for j in 0..3 { continue; } }")
        outer_while = block[2]
        self.assertIsInstance(outer_while, syntax.While)
        self.assertEqual(outer_while.condition.left.slot, 1)
        inner_while = outer_while.body.statements[1]
        self.assertIsInstance(inner_while, syntax.While)
        replaced = inner_while.body.statements[0]
        self.assertIsInstance(replaced, syntax.Block)
        self.assertEqual(replaced.statements[0].slot, 2)
        self.assertIsInstance(replaced.statements[1], syntax.Continue)

    def test_no_for_survives_anywhere_in_a_mixed_program(self) -> None:
        program = analyze(parse(
            "fn side(n: i64) -> i64 { for k in 0..n { print(k); } return n; }"
            "fn main() -> i64 { for i in 0..side(2) { } return 0; }"))
        lowered = optimize.lower_for_loops(program)

        def walk(node) -> bool:
            if isinstance(node, (syntax.For,)):
                return True
            if isinstance(node, syntax.Program):
                return any(walk(item) for item in node.functions)
            if isinstance(node, syntax.Function):
                return walk(node.body)
            if isinstance(node, syntax.Block):
                return any(walk(item) for item in node.statements)
            if isinstance(node, syntax.If):
                return walk(node.then_block) or walk(node.else_block)
            if isinstance(node, syntax.While):
                return walk(node.body)
            return False

        self.assertFalse(walk(lowered))




class CopyPropagationTest(unittest.TestCase):
    """One read of a temp substitutes the definition; every second use refuses."""

    def test_a_single_read_temp_folds_into_its_reader(self) -> None:
        block = optimized_body(
            "var a: i64 = 2; var b: i64 = 3; var next: i64 = a + b;"
            " var x: i64 = next; x = x + x; print(x);")
        names = [statement.name for statement in block.statements
                 if isinstance(statement, syntax.Let)]
        self.assertNotIn("next", names)
        x_let = block.statements[2]
        self.assertIsInstance(x_let, syntax.Let)
        self.assertEqual(x_let.name, "x")
        self.assertEqual((x_let.value.left.name, x_let.value.right.name), ("a", "b"))

    def test_a_second_read_refuses(self) -> None:
        block = optimized_body(
            "var a: i64 = 2; var b: i64 = 3; var next: i64 = a + b;"
            " var x: i64 = next; print(next); x = x + next;")
        names = [statement.name for statement in block.statements
                 if isinstance(statement, syntax.Let)]
        self.assertIn("next", names)

    def test_a_reassignment_between_definition_and_copy_refuses(self) -> None:
        block = optimized_body(
            "var a: i64 = 2; var b: i64 = 3; var next: i64 = a + b;"
            " next = next + 1; var x: i64 = next; print(x);")
        names = [statement.name for statement in block.statements
                 if isinstance(statement, syntax.Let)]
        self.assertIn("next", names)

    def test_a_calling_definition_refuses(self) -> None:
        block = optimized_body(
            "var a: i64 = 2; var next: i64 = print(a); var x: i64 = next; print(x);")
        names = [statement.name for statement in block.statements
                 if isinstance(statement, syntax.Let)]
        self.assertIn("next", names)

    def test_a_trapping_definition_refuses(self) -> None:
        block = optimized_body(
            "var a: i64 = 2; var b: i64 = 0; var next: i64 = a / b;"
            " var x: i64 = next; print(x);")
        names = [statement.name for statement in block.statements
                 if isinstance(statement, syntax.Let)]
        self.assertIn("next", names)

    def test_a_clobbered_operand_between_definition_and_copy_refuses(self) -> None:
        block = optimized_body(
            "var a: i64 = 2; var b: i64 = 3; var next: i64 = a + b;"
            " b = 9; var x: i64 = next; print(x);")
        names = [statement.name for statement in block.statements
                 if isinstance(statement, syntax.Let)]
        self.assertIn("next", names)

    def test_a_loop_body_temp_propagates_per_iteration(self) -> None:
        block = optimized_body(
            "var a: i64 = 5; var x: i64 = 0; var i: i64 = 0;"
            " while i < 3 { var next: i64 = a + 1; x = next; i = i + 1; }"
            " print(x);")
        loop = block.statements[3]
        self.assertIsInstance(loop, syntax.While)
        print_stmt = block.statements[4]
        self.assertIsInstance(print_stmt, syntax.ExprStmt)
        lets = [statement for statement in loop.body.statements
                if isinstance(statement, syntax.Let)]
        self.assertEqual(lets, [])
        copy = loop.body.statements[0]
        self.assertEqual(copy.value.op, "+")
        self.assertEqual(copy.value.right.value, 1)



def count_calls(expression):
    """Every Call node under one expression."""
    if expression is None:
        return []
    if isinstance(expression, syntax.Call):
        found = [expression]
    else:
        found = []
    if isinstance(expression, syntax.Parens):
        found += count_calls(expression.inner)
    if isinstance(expression, syntax.Unary):
        found += count_calls(expression.operand)
    if isinstance(expression, syntax.Binary):
        found += count_calls(expression.left) + count_calls(expression.right)
    if isinstance(expression, syntax.Call):
        for argument in expression.arguments:
            found += count_calls(argument)
    return found


class InliningTest(unittest.TestCase):
    """Single-return pure functions inline into their call sites."""

    MIX = ('fn mix(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64) -> i64 '
           '{ return a * 31 + b * 37 + c * 41 + d * 43 + e * 47 + f * 53; }')

    def test_pure_single_return_call_inlines_into_a_literal(self) -> None:
        program = analyze(parse(self.MIX + ' fn main() -> i64 '
                                '{ var t: i64 = mix(3, 4, 5, 6, 7, 8); return t; }'))
        result = optimize.optimize(program)
        statement = result.functions[1].body.statements[0]
        self.assertIsInstance(statement, syntax.Let)
        self.assertEqual(statement.value.value, 3 * 31 + 4 * 37 + 5 * 41 + 6 * 43 + 7 * 47 + 8 * 53)

    def test_multi_use_call_argument_keeps_the_call(self) -> None:
        # A parameter read twice must not duplicate a call argument: the call stays.
        source = ('fn twice(x: i64) -> i64 { return x + x; } '
                  'fn main() -> i64 { var t: i64 = twice(read_char()); return t; }')
        result = optimize.optimize(analyze(parse(source)))
        statement = result.functions[1].body.statements[0]
        self.assertIsInstance(statement.value, syntax.Call)
        self.assertEqual(statement.value.name, 'twice')
        # the surviving tree is the outer call plus its single read_char argument
        self.assertEqual(len(count_calls(statement.value)), 2)

    def test_multi_use_pure_argument_inlines_one_clone_per_read(self) -> None:
        source = ('fn twice(x: i64) -> i64 { return x + x; } '
                  'fn main() -> i64 { var i: i64 = 5; var t: i64 = twice(i); return t; }')
        result = optimize.optimize(analyze(parse(source)))
        statement = result.functions[1].body.statements[1]
        self.assertEqual(len(count_calls(statement.value)), 0)
        self.assertIsInstance(statement.value, syntax.Binary)
        # twice(i) inlines as i + i, and the algebraic pass collects it as i * 2
        pair = [statement.value.left, statement.value.right]
        names = [operand for operand in pair if isinstance(operand, syntax.Name)]
        literals = [operand for operand in pair if isinstance(operand, syntax.IntLiteral)]
        self.assertEqual(len(names), 1)
        self.assertEqual(len(literals), 1)
        self.assertEqual(literals[0].value, 2)

    def test_self_recursion_is_never_inlined(self) -> None:
        source = ('fn f(n: i64) -> i64 { return f(n - 1) + n; } '
                  'fn main() -> i64 { var t: i64 = f(4); return t; }')
        result = optimize.optimize(analyze(parse(source)))
        statement = result.functions[1].body.statements[0]
        self.assertEqual(len(count_calls(statement.value)), 1)
        self.assertEqual(statement.value.name, 'f')

    def test_multi_statement_functions_are_never_inlined(self) -> None:
        source = ('fn probe(k: i64) -> i64 { var t: i64 = k; return t + t + 1; } '
                  'fn main() -> i64 { var t: i64 = probe(7); return t; }')
        result = optimize.optimize(analyze(parse(source)))
        statement = result.functions[1].body.statements[0]
        self.assertIsInstance(statement.value, syntax.Call)
        self.assertEqual(statement.value.name, 'probe')


class AlgebraicTest(unittest.TestCase):
    """Distributive expansion and like-term collection must be ring-exact."""

    def test_like_terms_collect(self) -> None:
        body = optimized_body('var i: i64 = 5; var r: i64 = i * 31 + i * 37;')
        value = body.statements[1].value
        self.assertEqual(len(count_calls(value)), 0)
        operands = [value.left, value.right]
        multipliers = [side.value for side in operands
                       if isinstance(side, syntax.IntLiteral)]
        self.assertEqual(multipliers, [68])
        names = [side for side in operands
                 if isinstance(_peel_name_or_product(side), syntax.Name)]
        self.assertEqual(len(names), 1)

    def test_distribution_expands_and_collects(self) -> None:
        body = optimized_body('var i: i64 = 5; var total: i64 = 0;'
                              ' total = (i + 2) * 31 + i * 37;')
        value = body.statements[2].value
        # (i + 2) * 31 + i * 37 == 68 * i + 62: one constant and one product remain.
        def terms(expression):
            expression = expression
            while isinstance(expression, syntax.Parens):
                expression = expression.inner
            if isinstance(expression, syntax.Binary) and expression.op == '+':
                return terms(expression.left) + terms(expression.right)
            return [expression]
        pieces = terms(value)
        constants = sorted(piece.value for piece in pieces
                           if isinstance(piece, syntax.IntLiteral))
        products = [piece for piece in pieces if isinstance(piece, syntax.Binary)]
        self.assertEqual(constants, [62])
        self.assertEqual(len(products), 1)
        multipliers = [side.value for side in (products[0].left, products[0].right)
                       if isinstance(side, syntax.IntLiteral)]
        self.assertEqual(multipliers, [68])

    def test_cancelling_terms_leave_zero(self) -> None:
        body = optimized_body('var i: i64 = 5; var r: i64 = i * 7 - i * 7;')
        value = body.statements[1].value
        self.assertEqual(value.value, 0)

    def test_calls_are_never_grouped_or_duplicated(self) -> None:
        source = ('fn probe(k: i64) -> i64 { var t: i64 = k; return t + t; } '
                  'fn main() -> i64 { var x: i64 = 5; '
                  'var r: i64 = probe(x) * 3 + probe(x) * 4; return r; }')
        result = optimize.optimize(analyze(parse(source)))
        statement = result.functions[1].body.statements[1]
        self.assertEqual(len(count_calls(statement.value)), 2)


def _peel_name_or_product(expression):
    while isinstance(expression, syntax.Parens):
        expression = expression.inner
    if isinstance(expression, syntax.Binary) and expression.op == '*':
        for side in (expression.left, expression.right):
            peeled = _peel_name_or_product(side)
            if peeled is not None:
                return peeled
        return None
    return expression


if __name__ == "__main__":
    unittest.main()
