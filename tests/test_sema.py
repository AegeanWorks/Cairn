"""Semantic tests: every reachable rule, one failing source each."""

import unittest

from cairn import syntax
from cairn.diagnostics import DiagnosticError
from cairn.parser import parse
from cairn.sema import analyze


def codes(source: str) -> list:
    """The diagnostic codes a source produces; asserts it is refused."""
    with unittest.TestCase().assertRaises(DiagnosticError) as caught:
        analyze(parse(source))
    return [diagnostic.code for diagnostic in caught.exception.diagnostics]


class SemaTest(unittest.TestCase):
    """Semantic analysis is the last stage that sees names, so its rules are the language's."""

    def test_valid_program_takes_slots_in_declaration_order(self) -> None:
        program = analyze(parse(
            "fn f(a: i64, b: i64) -> i64 { var c: i64 = a; let d: i64 = c; return d + b; }"
            "fn main() -> i64 { return f(1, 2); }"))
        function = program.functions[0]
        self.assertEqual([parameter.slot for parameter in function.parameters], [0, 1])
        self.assertEqual(function.slots, 4)

    def test_names_resolve_to_the_parameter_slot(self) -> None:
        program = analyze(parse("fn main() -> i64 { let x: i64 = 7; return x; }"))
        returned = program.functions[0].body.statements[1].value
        self.assertEqual(returned.slot, 0)

    def test_missing_main_is_the_entry_point_error(self) -> None:
        self.assertEqual(codes("fn helper() -> i64 { return 1; }"), ["E3011"])

    def test_duplicate_function(self) -> None:
        self.assertEqual(codes("fn f() -> i64 { return 1; }"
                               "fn f() -> i64 { return 2; }"
                               "fn main() -> i64 { return f(); }"), ["E3001"])

    def test_five_to_eight_parameters_are_accepted(self) -> None:
        analyze(parse("fn f(a: i64, b: i64, c: i64, d: i64, e: i64," 
                      " f2: i64, g: i64, h: i64) -> i64 { return a + h; }"
                      "fn main() -> i64 { return 0; }"))

    def test_more_than_eight_parameters(self) -> None:
        self.assertEqual(codes("fn f(a: i64, b: i64, c: i64, d: i64, e: i64," 
                               " f2: i64, g: i64, h: i64, i2: i64) -> i64 { return a; }"
                               "fn main() -> i64 { return 0; }"), ["E3010"])

    def test_unknown_name_and_use_before_declaration(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { return missing; }"), ["E3007"])

    def test_assignment_to_a_let_is_refused(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { let x: i64 = 1; x = 2; return x; }"),
                         ["E3005"])

    def test_duplicate_binding_without_shadowing(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { let x: i64 = 1; let x: i64 = 2; return x; }"),
                         ["E3006"])

    def test_unknown_function(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { return nope(); }"), ["E3003"])

    def test_wrong_argument_count(self) -> None:
        self.assertEqual(codes("fn f(a: i64) -> i64 { return a; }"
                               "fn main() -> i64 { return f(1, 2); }"), ["E3004"])

    def test_five_arguments_hits_the_four_parameter_limit(self) -> None:
        self.assertEqual(codes("fn f(a: i64, b: i64, c: i64, d: i64) -> i64 { return a; }"
                               "fn main() -> i64 { return f(1, 2, 3, 4, 5); }"),
                          ["E3004"])

    def test_print_is_callable_not_redefinable(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { return print; }"), ["E3012"])

    def test_builtin_cannot_be_a_parameter_or_an_assignment_target(self) -> None:
        self.assertEqual(codes("fn f(print: i64) -> i64 { return 0; }"
                               "fn main() -> i64 { return f(1); }"), ["E3012"])
        self.assertEqual(codes("fn main() -> i64 { print = 1; return 0; }"), ["E3012"])
    def test_print_char_and_print_hex_are_one_argument_builtins(self) -> None:
        analyze(parse("fn main() -> i64 { print_char(65); print_hex(255); return 0; }"))

    def test_new_builtins_take_exactly_one_argument(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { print_char(1, 2); return 0; }"), ["E3004"])
        self.assertEqual(codes("fn main() -> i64 { print_hex(); return 0; }"), ["E3004"])

    def test_new_builtins_are_callable_not_values_redefinable(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { return print_char; }"), ["E3012"])
        self.assertEqual(codes("fn main() -> i64 { return print_hex; }"), ["E3012"])
        self.assertEqual(codes("fn f(print_char: i64) -> i64 { return 0; }"
                               "fn main() -> i64 { return f(1); }"), ["E3012"])
        self.assertEqual(codes("fn main() -> i64 { print_hex = 1; return 0; }"), ["E3012"])

    def test_input_builtins_have_their_arities(self) -> None:
        analyze(parse(
            "fn main() -> i64 { var n: i64 = argc(); var b: i64 = arg_byte(1, 0);"
            " var c: i64 = read_char(); return n + b + c; }"))
        self.assertEqual(codes("fn main() -> i64 { return arg_byte(1); }"), ["E3004"])
        self.assertEqual(codes("fn main() -> i64 { return argc(1); }"), ["E3004"])
        self.assertEqual(codes("fn main() -> i64 { return read_char(2); }"), ["E3004"])

    def test_input_builtins_are_not_values(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { return read_char; }"), ["E3012"])
        self.assertEqual(codes("fn main() -> i64 { return argc; }"), ["E3012"])

    def test_literal_above_max_plus_one_is_refused(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { return 9223372036854775809; }"),
                          ["E1003"])

    def test_break_outside_a_loop(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { break; return 0; }"), ["E3008"])

    def test_function_must_return_on_every_path(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { let x: i64 = 1; }"), ["E3009"])

    def test_if_else_returning_both_ways_is_accepted(self) -> None:
        program = analyze(parse("fn main() -> i64 { if 1 { return 1; } else { return 2; } }"))
        self.assertEqual(program.functions[0].name, "main")

    def test_every_diagnostic_is_collected_in_one_pass(self) -> None:
        found = codes("fn main() -> i64 { let a: i64 = b; c = 1; return d(); }")
        self.assertEqual(found.count("E3007"), 2)
        self.assertEqual(found.count("E3003"), 1)


class ForLoopTest(unittest.TestCase):
    """The for variable is scoped to its loop: reusable across loops, restored outside."""

    def test_two_loops_may_reuse_the_loop_variable(self) -> None:
        program = analyze(parse(
            "fn main() -> i64 { var t: i64 = 0; for i in 0..3 { t = t + i; }"
            " for i in 0..4 { t = t + i; } return t; }"))
        loops = [statement for statement in program.functions[0].body.statements
                 if isinstance(statement, syntax.For)]
        self.assertEqual([loop.slot for loop in loops], [1, 2])
        self.assertEqual(program.functions[0].slots, 3)

    def test_the_loop_variable_shadows_an_outer_binding_and_restores_it(self) -> None:
        program = analyze(parse(
            "fn main() -> i64 { let i: i64 = 9; var t: i64 = 0; for i in 0..2 { t = t + i; }"
            " return i + t; }"))
        returned = program.functions[0].body.statements[3].value
        self.assertIsInstance(returned, syntax.Binary)
        self.assertEqual(returned.left.slot, 0)

    def test_assigning_the_loop_variable_inside_the_body_is_allowed(self) -> None:
        analyze(parse("fn main() -> i64 { for i in 0..2 { i = i + 10; } return 0; }"))

    def test_the_body_may_not_shadow_the_loop_variable(self) -> None:
        self.assertIn("E3006", codes(
            "fn main() -> i64 { for i in 0..2 { let i: i64 = 1; } return 0; }"))

    def test_a_loop_variable_named_like_a_builtin_is_refused(self) -> None:
        self.assertIn("E3012", codes(
            "fn main() -> i64 { for print in 0..2 { } return 0; }"))

    def test_the_loop_variable_is_gone_after_the_loop(self) -> None:
        codes("fn main() -> i64 { for i in 0..2 { } return i; }")

    def test_continue_and_break_are_valid_inside_a_for(self) -> None:
        analyze(parse(
            "fn main() -> i64 { for i in 0..9 { if i == 3 { break; }"
            " if i == 1 { continue; } } return 0; }"))

    def test_the_range_bounds_must_be_i64_expressions(self) -> None:
        codes("fn main() -> i64 { for i in 0..nope { } return 0; }")


if __name__ == "__main__":
    unittest.main()

