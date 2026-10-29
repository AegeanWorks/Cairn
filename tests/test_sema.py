"""Semantic tests: every reachable rule, one failing source each."""

import unittest

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

    def test_more_than_four_parameters(self) -> None:
        self.assertEqual(codes("fn f(a: i64, b: i64, c: i64, d: i64, e: i64) -> i64 { return a; }"
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

    def test_print_is_callable_not_redefinable(self) -> None:
        self.assertEqual(codes("fn main() -> i64 { return print; }"), ["E3012"])

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


if __name__ == "__main__":
    unittest.main()
