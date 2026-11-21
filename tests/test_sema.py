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
    def test_popcount_is_a_one_argument_builtin(self) -> None:
        analyze(parse("fn main() -> i64 { popcount(255); return 0; }"))

    def test_popcount_rejects_a_wrong_arity(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { popcount(); return 0; }"), ["E3004"])

    def test_clz_is_a_one_argument_builtin(self) -> None:
        analyze(parse("fn main() -> i64 { clz(255); return 0; }"))

    def test_clz_rejects_a_wrong_arity(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { clz(); return 0; }"), ["E3004"])

    def test_ctz_is_a_one_argument_builtin(self) -> None:
        analyze(parse("fn main() -> i64 { ctz(255); return 0; }"))

    def test_ctz_rejects_a_wrong_arity(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { ctz(); return 0; }"), ["E3004"])

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

    def test_string_builtins_have_their_arities(self) -> None:
        analyze(parse(
            "fn main() -> i64 { print_str(\"hi\"); var n: i64 = str_len(\"hi\");"
            " var b: i64 = str_byte(\"hi\", 0); return n + b; }"))
        self.assertEqual(codes("fn main() -> i64 { return str_len(\"hi\", 1); }"),
                         ["E3004"])
        self.assertEqual(codes("fn main() -> i64 { return print_str(); }"), ["E3004"])
        self.assertEqual(codes("fn main() -> i64 { return str_byte(\"hi\"); }"),
                         ["E3004"])

    def test_exit_takes_one_code_and_stops_the_program(self) -> None:
        analyze(parse(
            "fn f(c: i64) -> i64 { if c < 0 { exit(1); } return 0; }"
            "fn main() -> i64 { return f(-3); }"))
        self.assertEqual(codes("fn main() -> i64 { exit(); }"), ["E3004"])
        self.assertEqual(codes("fn main() -> i64 { exit(1, 2); }"), ["E3004"])

    def test_exit_is_never_a_value(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { return exit(1); }"), ["E3013"])
        self.assertEqual(codes(
            "fn main() -> i64 { var x: i64 = exit(1); return 0; }"), ["E3013"])
        self.assertEqual(codes("fn main() -> i64 { if exit(1) == 0 { return 0; } return 1; }"),
                         ["E3013"])
        analyze(parse("fn main() -> i64 { exit(0); }"))

    def test_an_exit_statement_ends_the_return_path(self) -> None:
        analyze(parse(
            "fn f(c: i64) -> i64 { if c == 0 { exit(3); } else { return 1; } }"
            "fn main() -> i64 { return f(0); }"))

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



class FlushBuiltinTest(unittest.TestCase):
    """flush() drains the shared stdout buffer: zero arguments, a real value."""

    def test_flush_takes_no_arguments_and_runs_as_a_statement(self) -> None:
        analyze(parse("fn main() -> i64 { flush(); return 0; }"))
        self.assertEqual(codes("fn main() -> i64 { flush(1); return 0; }"), ["E3004"])

    def test_flush_is_a_value_builtin_that_cannot_be_renamed(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { return flush; }"), ["E3012"])
        self.assertEqual(codes("fn main() -> i64 { flush = 1; return 0; }"), ["E3012"])


class ArraySemaTest(unittest.TestCase):
    """`[N]i64` bindings reserve N consecutive slots and index only within range."""

    def test_an_array_binding_reserves_consecutive_slots(self) -> None:
        program = analyze(parse(
            "fn main() -> i64 { var a: [8]i64 = [0; 8]; var j: i64 = 5; a[j] = j;"
            " return 0; }"))
        function = program.functions[0]
        self.assertEqual(function.slots, 10)
        self.assertEqual(function.body.statements[0].slot, 0)
        self.assertEqual(function.body.statements[1].slot, 8)
        store = function.body.statements[2]
        self.assertEqual((store.slot, store.index.slot), (0, 8))

    def test_a_non_literal_initializer_is_refused(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { var a: [3]i64 = 5; return 0; }"),
                         ["E3014"])

    def test_a_literal_length_mismatch_is_refused(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { var a: [3]i64 = [1, 2]; return 0; }"),
                         ["E3014"])

    def test_an_empty_or_oversized_array_is_refused(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { var a: [0]i64 = []; return 0; }"),
                         ["E3014"])
        self.assertEqual(codes("fn main() -> i64 { var a: [70000]i64 = [0; 70000]; return 0; }"), ["E3014"])

    def test_indexing_a_scalar_is_refused(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { var a: i64 = 0; return a[0]; }"),
                         ["E3015"])

    def test_an_array_name_is_not_a_value(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { var a: [2]i64 = [1, 1]; return a; }"),
                         ["E3016"])

    def test_a_static_out_of_range_index_is_refused(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { var a: [4]i64 = [0; 4]; return a[9]; }"), ["E3017"])

    def test_storing_into_an_immutable_array_element_is_refused(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { let a: [2]i64 = [1, 1]; a[0] = 1; return 0; }"), ["E3005"])

    def test_storing_into_an_undeclared_array_is_refused(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { b[0] = 1; return 0; }"), ["E3007"])