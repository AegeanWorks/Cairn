"""End-to-end tests: build real executables, run them, and assert what they print.

These are the tests that prove the claim: no external toolchain is involved, and the bytes the
compiler writes are a program Windows runs.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cairn.builder import compile_source, write_executable

WINDOWS_ONLY = unittest.skipUnless(sys.platform == "win32",
                                   "produced executables are Windows PE images")
TIMEOUT_SECONDS = 20

ARITHMETIC = """
fn main() -> i64 {
    print(-7 / 2);
    print(-7 % 2);
    print(1 << 40);
    print(0);
    return 3;
}
"""

LOOPS = """
fn fib(n: i64) -> i64 {
    if n < 2 { return n; }
    return fib(n - 1) + fib(n - 2);
}

fn count() -> i64 {
    var i: i64 = 0;
    var hits: i64 = 0;
    while i < 20 {
        i = i + 1;
        if i % 3 == 0 { continue; }
        if i > 15 { break; }
        hits = hits + 1;
    }
    return hits;
}

fn main() -> i64 {
    print(fib(10));
    print(count());
    return 0;
}
"""

COUNTDOWN = """
fn main() -> i64 {
    var i: i64 = 0;
    var total: i64 = 0;
    while i < 7 {
        total = total + 5;
        i = i + 1;
    }
    print(total);
    var j: i64 = 10;
    var seen: i64 = 0;
    while j < 3 {
        seen = seen + 1;
        j = j + 1;
    }
    print(seen);
    var k: i64 = 17;
    var neg: i64 = 0;
    while k < 7 {
        neg = neg + 1;
        k = k + 1;
    }
    print(neg);
    return 0;
}
"""

POWER_DIVISION = """
fn main() -> i64 {
    var a: i64 = -9;
    var b: i64 = 7;
    var c: i64 = -1;
    var d: i64 = -20;
    var lo: i64 = -9223372036854775807 - 1;
    print(a / 4);
    print(a % 4);
    print(b / 4);
    print(b % 4);
    print(c / 2);
    print(c % 2);
    print(d / 4);
    print(d % 4);
    print(lo / 2);
    print(lo % 8);
    print(a / 2);
    print(a % 2);
    return 0;
}
"""

DIVIDE_BY_ZERO = """
fn main() -> i64 {
    var zero: i64 = 0;
    print(10 / zero);
    return 0;
}
"""

EVEN_SLOTS_PAD = """
fn probe(n: i64) -> i64 {
    if n < 1 { return 100; }
    var a: i64 = probe(n - 1);
    return n + a;
}

fn main() -> i64 {
    print(probe(5));
    return 0;
}
"""

CALLS_AROUND_LOCALS = """
fn bump(x: i64) -> i64 {
    return x + 1;
}

fn main() -> i64 {
    var a: i64 = 1;
    var b: i64 = 2;
    var c: i64 = 3;
    a = bump(a);
    b = bump(b);
    c = bump(c);
    print(a * 100 + b * 10 + c);
    return 0;
}
"""


FOR_LOOPS = """
fn main() -> i64 {
    var t: i64 = 0;
    var j: i64 = 0;
    let i: i64 = 9;
    for i in 1..5 { t = t + i; }
    print(t);
    t = 0;
    for i in 1..=5 { t = t + i; }
    print(t);
    t = 0;
    for i in 5..5 { t = t + 1; }
    print(t);
    for i in 5..0 { t = t + 1; }
    print(t);
    t = 0;
    for i in 0..10 {
        if i % 3 == 0 { continue; }
        t = t + 1;
    }
    print(t);
    t = 0;
    for i in 0..10 {
        if i == 4 { break; }
        t = t + 1;
    }
    print(t);
    for i in 0..3 {
        for j in 0..4 { t = t + 1; }
    }
    print(t);
    print(j);
    print(i);
    return 0;
}
"""

FOR_END_REEVALUATED = """
fn edge(n: i64) -> i64 {
    print(n);
    return n;
}

fn main() -> i64 {
    for i in 0..edge(3) { print(i); }
    return 0;
}
"""


@WINDOWS_ONLY
class EndToEndTest(unittest.TestCase):
    """Each test compiles a source, writes it to a temporary folder and executes it."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="cairn-e2e-"))
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        """Remove the built executable and its folder."""
        for child in sorted(self.folder.rglob("*"), reverse=True):
            child.unlink(missing_ok=True)
        self.folder.rmdir()

    def build(self, source: str) -> Path:
        """Compile source text and return the executable path."""
        executable = self.folder / "program.exe"
        write_executable(executable, compile_source(source).image)
        return executable

    def execute(self, source: str) -> subprocess.CompletedProcess:
        """Run a compiled program, capturing both streams."""
        return subprocess.run([str(self.build(source))], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)

    def test_hello_prints_an_expression(self) -> None:
        completed = self.execute("fn main() -> i64 { print(40 + 2); return 0; }")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "42\n")

    def test_arithmetic_truncates_shifts_and_returns_the_exit_code(self) -> None:
        completed = self.execute(ARITHMETIC)
        self.assertEqual(completed.returncode, 3)
        self.assertEqual(completed.stdout.splitlines(), ["-3", "-1", "1099511627776", "0"])

    def test_recursion_and_loop_control(self) -> None:
        completed = self.execute(LOOPS)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "55\n10\n")

    def test_countdown_latches_match_count_up_semantics(self) -> None:
        completed = self.execute(COUNTDOWN)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "35\n0\n0\n")

    def test_power_of_two_division_truncates_like_the_machine_would(self) -> None:
        completed = self.execute(POWER_DIVISION)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "\n".join([
            "-2", "-1",       # -9 / 4, -9 % 4
            "1", "3",         # 7 / 4, 7 % 4
            "0", "-1",        # -1 / 2, -1 % 2
            "-5", "0",        # -20 / 4, -20 % 4
            "-4611686018427387904", "0",  # INT64_MIN / 2, INT64_MIN % 8
            "-4", "-1",       # -9 / 2, -9 % 2
        ]) + "\n")

    def test_division_by_zero_is_a_trap_with_a_message_and_exit_code_3(self) -> None:
        completed = self.execute(DIVIDE_BY_ZERO)
        self.assertEqual(completed.returncode, 3)
        self.assertIn("divide by zero", completed.stderr)

    def test_traps_behave_identically_without_the_folding_pass(self) -> None:
        executable = self.folder / "plain.exe"
        write_executable(executable, compile_source(DIVIDE_BY_ZERO, optimise=False).image)
        completed = subprocess.run([str(executable)], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)
        folded = self.execute(DIVIDE_BY_ZERO)
        self.assertEqual(folded.returncode, 3)
        self.assertEqual(completed.returncode, folded.returncode)
        self.assertIn("divide by zero", completed.stderr)

    def test_run_forwards_the_programs_own_exit_code(self) -> None:
        completed = self.execute("fn main() -> i64 { return 7; }")
        self.assertEqual(completed.returncode, 7)

    def test_shift_count_beyond_63_wraps_on_the_machine(self) -> None:
        completed = self.execute("fn main() -> i64 { var s: i64 = 70; print(1 << s); "
                                 "print(-8 >> s); return 0; }")
        self.assertEqual(completed.stdout.splitlines(), ["64", "-1"])

    def test_more_slots_than_registers_spill_to_the_stack(self) -> None:
        completed = self.execute("fn main() -> i64 { var a: i64 = 1; var b: i64 = 2; "
                                 "var c: i64 = 3; var d: i64 = 4; var e: i64 = 5; "
                                 "var f: i64 = 6; var g: i64 = 7; var h: i64 = 8; "
                                 "print(a + b + c + d + e + f + g + h); return 0; }")
        self.assertEqual(completed.stdout, "36\n")

    def test_min_int_divided_by_minus_one_traps_like_division_by_zero(self) -> None:
        source = ("fn main() -> i64 { var lo: i64 = -9223372036854775807 - 1; "
                  "var neg: i64 = -1; print(lo / neg); return 0; }")
        completed = self.execute(source)
        self.assertEqual(completed.returncode, 3)
        self.assertIn("divide by zero", completed.stderr)
        remainder = ("fn main() -> i64 { var lo: i64 = -9223372036854775807 - 1; "
                     "var neg: i64 = -1; print(lo % neg); return 0; }")
        completed = self.execute(remainder)
        self.assertEqual(completed.returncode, 3)
        self.assertIn("divide by zero", completed.stderr)

    def test_min_int64_prints_with_its_sign(self) -> None:
        completed = self.execute("fn main() -> i64 { print(-9223372036854775807 - 1); return 0; }")
        self.assertEqual(completed.stdout, "-9223372036854775808\n")
        direct = self.execute("fn main() -> i64 { print(-9223372036854775808); return 0; }")
        self.assertEqual(direct.returncode, 0)
        self.assertEqual(direct.stdout, "-9223372036854775808\n")

    def test_comparison_and_logical_operators_yield_one_or_zero(self) -> None:
        source = ("fn main() -> i64 { print(2 < 3); print(3 < 2); print(!0); "
                  "print(0 && 1); print(0 || 5); return 0; }")
        completed = self.execute(source)
        self.assertEqual(completed.stdout.splitlines(), ["1", "0", "1", "0", "1"])

    def test_four_argument_call_uses_every_argument_register(self) -> None:
        source = ("fn quad(a: i64, b: i64, c: i64, d: i64) -> i64 { "
                  "return a * 1000 + b * 100 + c * 10 + d; } "
                  "fn main() -> i64 { print(quad(1, 2, 3, 4)); return 0; }")
        completed = self.execute(source)
        self.assertEqual(completed.stdout, "1234\n")

    def test_print_inside_an_argument_list_evaluates_left_to_right(self) -> None:
        source = ("fn sub(a: i64, b: i64, c: i64) -> i64 { return a - b - c; } "
                  "fn main() -> i64 { print(sub(1, print(2), 3)); return 0; }")
        completed = self.execute(source)
        self.assertEqual(completed.stdout, "2\n-4\n")

    def test_calls_preserve_locals_held_in_callee_saved_registers(self) -> None:
        completed = self.execute(CALLS_AROUND_LOCALS)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "234\n")

    def test_even_slot_frames_restore_homes_across_calls(self) -> None:
        completed = self.execute(EVEN_SLOTS_PAD)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "115\n")

    def test_forty_locals_use_wide_frame_displacements(self) -> None:
        declarations = "".join(f"    let v{index}: i64 = {index};\n" for index in range(40))
        source = (f"fn main() -> i64 {{\n{declarations}"
                  "    print(v39 + v20);\n    return 0;\n}\n")
        completed = self.execute(source)
        self.assertEqual(completed.stdout, "59\n")

    def test_a_binding_from_a_branch_that_never_ran_reads_as_zero(self) -> None:
        source = ("fn main() -> i64 { var y: i64 = 0; "
                  "if y { let hidden: i64 = 7; } print(hidden); return 0; }")
        completed = self.execute(source)
        self.assertEqual(completed.stdout, "0\n")

    def test_optimised_and_unoptimised_builds_behave_identically(self) -> None:
        source = """
fn main() -> i64 {
    print(0 - 5);
    print(7 / 2 + 7 % 2);
    print(9223372036854775807 + 1);
    print(1 << 64);
    print(12 * 0 + 3);
    var zero: i64 = 0;
    print(zero * 3);
    print(print(4) * 0);
    return 0;
}
"""
        folded = self.execute(source).stdout
        executable = self.folder / "plain.exe"
        write_executable(executable, compile_source(source, optimise=False).image)
        completed = subprocess.run([str(executable)], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, folded)
        self.assertEqual(folded.splitlines(),
                         ["-5", "4", "-9223372036854775808", "1", "3", "0", "4", "0"])


    def test_print_char_writes_single_bytes_without_a_newline(self) -> None:
        source = "fn main() -> i64 { print_char(72); print_char(105); print_char(10); return 0; }"
        completed = self.execute(source)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "Hi\n")

    def test_print_char_used_as_a_value_returns_its_argument(self) -> None:
        completed = self.execute("fn main() -> i64 { print(print_char(65) + 1); return 0; }")
        self.assertEqual(completed.stdout, "A66\n")

    def test_print_char_wraps_to_the_low_byte(self) -> None:
        completed = self.execute("fn main() -> i64 { print_char(321); return 0; }")
        self.assertEqual(completed.stdout, "A")

    def test_print_hex_prints_lowercase_hex_without_leading_zeros(self) -> None:
        source = ("fn main() -> i64 { print_hex(255); print_char(32); print_hex(0);"
                  " print_char(32); print_hex(43981); print_char(10); return 0; }")
        completed = self.execute(source)
        self.assertEqual(completed.stdout, "ff 0 abcd\n")

    def test_print_hex_wraps_to_the_unsigned_sixty_four_bit_value(self) -> None:
        completed = self.execute("fn main() -> i64 { print_hex(-1); print_char(10); return 0; }")
        self.assertEqual(completed.stdout, "ffffffffffffffff\n")

    def test_print_hex_used_as_a_value_returns_its_argument(self) -> None:
        completed = self.execute("fn main() -> i64 { print_hex(15); print(print_hex(15));"
                                 " print_char(10); return 0; }")
        self.assertEqual(completed.stdout, "ff15\n\n")

    def test_print_builtins_compose_in_one_program(self) -> None:
        source = ("fn main() -> i64 { for i in 0..3 { print_char(48 + i); print_hex(i * 43981);"
                  " print_char(32); } print(7); return 0; }")
        completed = self.execute(source)
        self.assertEqual(completed.stdout, "00 1abcd 21579a 7\n")

STACK_ARGS_PROGRAM = """
fn combine(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64) -> i64 {
    return a * 31 + b * 37 + c * 41 + d * 43 + e * 47 + f * 53;
}

fn main() -> i64 {
    print(combine(1, 2, 3, 4, 5, 6));
    print(combine(6, 5, 4, 3, 2, 1));
    var i: i64 = 0;
    var total: i64 = 0;
    while i < 3 {
        total = total + combine(i, i * 2, 5 - i, i + 7, -i, i * 100);
        i = i + 1;
    }
    print(total);
    return 0;
}
"""

class StackParametersTest(EndToEndTest):
    """Six-argument calls: four in registers, two through the outgoing stack area."""

    def test_stack_arguments_reach_the_callee_and_rotate(self) -> None:
        completed = self.execute(STACK_ARGS_PROGRAM)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "953\n811\n17598\n")

    def test_arguments_past_the_registers_evaluate_left_to_right(self) -> None:
        source = ("fn take(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64) -> i64"
                  " { return a + b + c + d + e + f; }"
                  "fn main() -> i64 { print(take(print(1), print(2), 3, 4,"
                  " print(5), 6)); return 0; }")
        completed = self.execute(source)
        self.assertEqual(completed.stdout, "1\n2\n5\n21\n")

    def test_stack_parameters_are_mutable_locals(self) -> None:
        source = ("fn bump(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64) -> i64 {"
                  " var total: i64 = 0;"
                  " for k in 0..3 { e = e + f; total = total + a + b + c + d + e; }"
                  " return e * 1000 + total; }"
                  "fn main() -> i64 { print(bump(1, 2, 3, 4, 5, 6)); return 0; }")
        completed = self.execute(source)
        # e walks 11, 17, 23; the round totals are 21 + 27 + 33 = 81.
        self.assertEqual(completed.stdout, "23081\n")

    def test_eight_parameters_pass(self) -> None:
        source = ("fn sum8(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64, g: i64,"
                  " h: i64) -> i64 { return a + b + c + d + e + f + g + h; }"
                  "fn main() -> i64 { print(sum8(1, 2, 3, 4, 5, 6, 7, 8)); return 0; }")
        completed = self.execute(source)
        self.assertEqual(completed.stdout, "36\n")

    def test_deep_recursion_rotates_its_stack_parameters(self) -> None:
        source = ("fn rot(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64) -> i64 {"
                  " if a == 0 { return e; }"
                  " return rot(a - 1, f, a, b, c, d); }"
                  "fn main() -> i64 { print(rot(9, 1, 2, 3, 4, 5)); return 0; }")
        completed = self.execute(source)
        # Nine (a-1, f, a, b, c, d) steps from (9,1,2,3,4,5): the model says 2.
        self.assertEqual(completed.stdout, "2\n")

class FusedCollatzLoopTest(EndToEndTest):
    """The fused odd-pair loop counts every reference step exactly, on every path.

    The cmov lowering commits one halving per pass and adds the pass's steps with one
    `adc` (1 + carry), so the tests pin the totals: the odd path pays two, the even path
    one, and shapes the specialized emitter refuses keep the generic shrink-chain's
    identical arithmetic.
    """

    def _steps_source(self, k: str, odd_value: str, even_test: str = "== 0") -> str:
        return ("fn main() -> i64 {"
                f" var k: i64 = {k}; var s: i64 = 0;"
                " while k > 1 {"
                f" if k % 2 {even_test} {{ k = k / 2; s = s + 1; }}"
                f" else {{ k = ({odd_value}) / 2; s = s + 2; }} }} "
                " print(s); return 0; }")

    def test_a_probe_starting_odd_counts_both_steps_of_the_pair(self) -> None:
        # 27's total stopping time is 111 steps; the first pass sees odd k.
        completed = self.execute(self._steps_source(27, "k * 3 + 1"))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "111\n")

    def test_a_probe_starting_even_takes_the_halving_path(self) -> None:
        completed = self.execute(self._steps_source(6, "k * 3 + 1"))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "8\n")

    def test_a_five_k_pair_counts_the_same_way(self) -> None:
        # 5k + 1 with odd k: (5k + 1)/2 = 5*(k >> 1) + 3; 3 -> 8, 4, 2, 1.
        completed = self.execute(self._steps_source(3, "k * 5 + 1"))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "5\n")

    def test_a_refused_pair_keeps_the_generic_shrink_chain(self) -> None:
        # m + c = 2 < 4 refuses the cmov form; the arithmetic must not change.
        # 3 -> 4 (odd pair), 2, 1.
        completed = self.execute(self._steps_source(3, "k * 3 - 1"))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "4\n")

    def test_the_swapped_parity_test_counts_identically(self) -> None:
        source = ("fn main() -> i64 { var k: i64 = 3; var s: i64 = 0;"
                  " while k > 1 {"
                  " if k % 2 != 0 { k = (k * 3 + 1) / 2; s = s + 2; }"
                  " else { k = k / 2; s = s + 1; } }"
                  " print(s); return 0; }")
        completed = self.execute(source)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "7\n")  # 3 -> 5, 8, 4, 2, 1


class PairInlineAckermannTest(EndToEndTest):
    """The two-parameter pair inlining answers Ackermann probes exactly.

    Known values pin the lowering: copies run the same arithmetic as the real calls
    they replace, including the n == 0 mid-chain reintroduction of a literal second
    argument and the depth-capped fall back to real calls.
    """

    ACK = ("fn ack(m: i64, n: i64) -> i64 {"
           " if m == 0 { return n + 1; }"
           " if n == 0 { return ack(m - 1, 1); }"
           " return ack(m - 1, ack(m, n - 1)); }")

    ACK_PROBES = [
        ("0, 1", "2"),
        ("1, 4", "6"),
        ("2, 3", "9"),
        ("2, 5", "13"),
        ("3, 3", "61"),
        ("3, 5", "253"),
    ]

    def test_ackermann_probes_match_the_known_values(self) -> None:
        for arguments, expected in self.ACK_PROBES:
            source = (self.ACK + f" fn main() -> i64 {{ print(ack({arguments}));"
                      " return 0; }")
            completed = self.execute(source)
            self.assertEqual(completed.returncode, 0, arguments)
            self.assertEqual(completed.stdout, expected + "\n", arguments)

    def test_deep_recursion_meets_the_depth_cap_and_still_answers(self) -> None:
        # A(3, 9) nests far beyond the inline depth cap: the copies must hand off to
        # real calls and land the exact value.
        source = self.ACK + " fn main() -> i64 { print(ack(3, 9)); return 0; }"
        completed = self.execute(source)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "4093\n")


class TwinParityTest(unittest.TestCase):
    """Every benchmark quadruple compiles to programs that print the same total.

    A scoreboard number earned against a twin that computes something else would be a
    lie, so the harness's own builders compile the Cairn source and its C, C++ and Rust
    twins and the executables must agree byte for byte on stdout. Baselines without an
    installed compiler are skipped, never assumed.
    """

    @classmethod
    def setUpClass(cls) -> None:
        from benchmarks import run_benchmarks
        cls.harness = run_benchmarks
        cls.c_compiler = run_benchmarks.find_c_compiler()
        cls.rust_compiler = run_benchmarks.find_rust_compiler()
        cls.cpp_compiler = run_benchmarks.find_cpp_compiler()

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="cairn-parity-"))
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for child in sorted(self.folder.rglob("*"), reverse=True):
            child.unlink(missing_ok=True)
        self.folder.rmdir()

    def _assert_par(self, case) -> None:
        cairn_exe = self.folder / f"{case.name}.cairn.exe"
        self.harness.build_cairn(self.harness.ROOT / case.cairn, cairn_exe)
        cairn_out = self.harness.run_command([str(cairn_exe)], 300)
        self.assertEqual(cairn_out.returncode, 0, case.name)
        expected = cairn_out.stdout
        self.assertTrue(expected.strip(), case.name)
        if self.c_compiler:
            c_exe = self.folder / f"{case.name}.c.exe"
            self.harness.build_c(self.c_compiler, self.harness.ROOT / case.c_source, c_exe)
            c_out = self.harness.run_command([str(c_exe)], 300)
            self.assertEqual(c_out.returncode, 0, case.name)
            self.assertEqual(c_out.stdout, expected, f"{case.name}: C twin diverges")
        if self.cpp_compiler:
            cpp_exe = self.folder / f"{case.name}.cpp.exe"
            self.harness.build_cpp(self.cpp_compiler,
                                   self.harness.ROOT / case.cpp_source, cpp_exe)
            cpp_out = self.harness.run_command([str(cpp_exe)], 300)
            self.assertEqual(cpp_out.returncode, 0, case.name)
            self.assertEqual(cpp_out.stdout, expected, f"{case.name}: C++ twin diverges")
        if self.rust_compiler:
            rust_exe = self.folder / f"{case.name}.rust.exe"
            self.harness.build_rust(self.rust_compiler,
                                    self.harness.ROOT / case.rust_source, rust_exe)
            rust_out = self.harness.run_command([str(rust_exe)], 300)
            self.assertEqual(rust_out.returncode, 0, case.name)
            self.assertEqual(rust_out.stdout, expected, f"{case.name}: Rust twin diverges")

    def test_xorshift_twins_print_the_same_total(self) -> None:
        self._assert_par(self.harness.CASES[0])

    def test_fib30_twins_print_the_same_total(self) -> None:
        self._assert_par(self.harness.CASES[1])

    def test_folded_math_twins_print_the_same_total(self) -> None:
        self._assert_par(self.harness.CASES[2])

    def test_collatz_twins_print_the_same_total(self) -> None:
        self._assert_par(self.harness.CASES[3])

    def test_ackermann_twins_print_the_same_total(self) -> None:
        self._assert_par(self.harness.CASES[4])

    def test_nqueens_twins_print_the_same_total(self) -> None:
        self._assert_par(self.harness.CASES[5])

    def test_binary_gcd_twins_print_the_same_total(self) -> None:
        self._assert_par(self.harness.CASES[6])

    def test_call_chain_twins_print_the_same_total(self) -> None:
        self._assert_par(self.harness.CASES[7])

    def test_tak_twins_print_the_same_total(self) -> None:
        self._assert_par(self.harness.CASES[8])

    def test_primes_twins_print_the_same_total(self) -> None:
        self._assert_par(self.harness.CASES[9])

    def test_mandelbrot_twins_print_the_same_total(self) -> None:
        self._assert_par(self.harness.CASES[10])

    def test_splitmix_twins_print_the_same_total(self) -> None:
        self._assert_par(self.harness.CASES[11])


NEGATIVE_BIT_TESTS = """
fn main() -> i64 {
    var x: i64 = -6;
    var y: i64 = 3;
    var total: i64 = 0;
    if (x & 1) == 1 && y > 0 { total = total + 1; }
    if (x & 2) != 0 || y < 0 { total = total + 2; }
    if x > 0 && (y & 1) == 1 { total = total + 4; }
    if x < 0 || (y / 2) > 5 { total = total + 8; }
    if !((x & 4) == 4) { total = total + 16; }
    while (x & 1) == 0 && x != 0 {
        x = x / 2;
        total = total + 16;
    }
    print(total);
    return 0;
}
"""

SHORT_CIRCUIT_CALLS = """
fn tick(n: i64) -> i64 {
    print(n);
    return n;
}

fn main() -> i64 {
    var x: i64 = 4;
    if x > 0 || tick(7) > 0 { print(1); }
    if x < 0 && tick(9) < 0 { print(2); }
    print(3);
    return 0;
}
"""

CONDITION_CHAINS = """
fn main() -> i64 {
    var a: i64 = 5;
    var b: i64 = -3;
    var c: i64 = 8;
    var total: i64 = 0;
    if a > 1 && b < 0 && c > 2 { total = 1; }
    if a > 9 || b > 0 || c > 100 { total = total + 2; }
    if a > 9 && b < 0 && c > 2 { total = total + 4; }
    if !(a > 9) && (b < 0 || c > 100) { total = total + 8; }
    print(total);
    return 0;
}
"""

MASK_COUNTS = """
fn main() -> i64 {
    var total: i64 = 0;
    var i: i64 = -20;
    while i <= 20 {
        if i % 4 == 0 { total = total + 1; }
        if i % 8 != 0 { total = total + 2; }
        i = i + 1;
    }
    print(total);
    return 0;
}
"""


@WINDOWS_ONLY
class FlagFusedConditionRuntimeTest(EndToEndTest):
    """Branches the fuser claims to handle stay semantically exact over negative values."""

    def test_bit_tests_and_masks_over_negative_values(self) -> None:
        completed = self.execute(NEGATIVE_BIT_TESTS)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "42\n")

    def test_short_circuit_branches_skip_call_sides(self) -> None:
        completed = self.execute(SHORT_CIRCUIT_CALLS)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "1\n3\n")

    def test_chained_conjunctions_and_alternations(self) -> None:
        completed = self.execute(CONDITION_CHAINS)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "9\n")

    def test_mask_counts_over_a_negative_span(self) -> None:
        completed = self.execute(MASK_COUNTS)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "83\n")


SPLITMIX_STAGE = """
fn main() -> i64 {
    var x: i64 = 88172645463325252;
    x = x - 7046029254386353131;
    print(x);
    var w: i64 = x;
    w = (w ^ ((w >> 30) & 17179869183)) * 3;
    print(w);
    w = (w ^ ((w >> 27) & 137438953471)) * -7723592293110705685;
    print(w);
    return 0;
}
"""


@WINDOWS_ONLY
class OperandParkTest(EndToEndTest):
    """A merged left value in its destination register must survive right-side evaluation.

    Found by the splitmix benchmark: the xor-and-shift chain parked `w` in rcx and the
    big-mask `&` then parked its own left in the same register, so the xor consumed the
    shifted value instead of `w`.
    """

    def test_parked_survives_a_generic_right_side(self) -> None:
        completed = self.execute(SPLITMIX_STAGE)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "-6957856608923027879\n-2426825779848323636\n-1061881911778666648\n")


LOGICAL_SHIFT_VALUES = """
fn main() -> i64 {
    var a: i64 = -12345678;
    var b: i64 = 987654321;
    var lo: i64 = -9223372036854775807 - 1;
    print((a >> 30) & 17179869183);
    print((b >> 63) & 1);
    print((lo >> 60) & 15);
    print((a >> 1) & 9223372036854775807);
    return 0;
}
"""


@WINDOWS_ONLY
class LogicalShiftValueTest(EndToEndTest):
    """Covering masks over every sign must read exactly the logical shift."""

    def test_masked_shifts_match_the_logical_result_everywhere(self) -> None:
        completed = self.execute(LOGICAL_SHIFT_VALUES)
        self.assertEqual(completed.returncode, 0)
        def sar(v, k):
            return v >> k
        def masked(v, k):
            return sar(v, k) & ((1 << (64 - k)) - 1)
        lo = -9223372036854775808
        expected = "\n".join(str(value) for value in (
            masked(-12345678, 30),
            masked(987654321, 63),
            masked(lo, 60),
            masked(-12345678, 1),
        )) + "\n"
        self.assertEqual(completed.stdout, expected)


SPLITMIX_LOOP = """
fn main() -> i64 {
    var z: i64 = 548732102;
    var i: i64 = 0;
    while i < 64 {
        z = (z ^ ((z >> 30) & 17179869183)) * 6364136223846793005;
        i = i + 1;
    }
    print(z);
    return 0;
}
"""


def _wrap(value: int) -> int:
    value &= (1 << 64) - 1
    return value - (1 << 64) if value >= (1 << 63) else value


READ_AFTER_MERGE_TRAP = """
fn main() -> i64 {
    var z: i64 = 11;
    z = (z + 5) ^ ((z >> 30) & 17179869183);
    print(z);
    return 0;
}
"""


@WINDOWS_ONLY
class MergedMaskLoopValueTest(EndToEndTest):
    """The reclaimed mix lowering must stay exact across a real mixer loop."""

    def test_a_masked_xor_multiplier_loop_wraps_like_the_source(self) -> None:
        completed = self.execute(SPLITMIX_LOOP)
        self.assertEqual(completed.returncode, 0)
        z = 548732102
        for _ in range(64):
            z = _wrap((z ^ ((z >> 30) & 17179869183)) * 6364136223846793005)
        self.assertEqual(completed.stdout, f"{z}\n")

    def test_a_merged_left_update_never_leaks_into_a_right_home_read(self) -> None:
        completed = self.execute(READ_AFTER_MERGE_TRAP)
        self.assertEqual(completed.returncode, 0)
        # The peephole reads z before the xor's left side (z + 5) has replaced it:
        # 16 ^ ((11 >> 30) & mask) = 16, never the post-update value.
        self.assertEqual(completed.stdout, "16\n")


HOISTED_MIXER_LOOP = """
fn main() -> i64 {
    var z: i64 = 548732102;
    var i: i64 = 64;
    while i > 0 {
        z = (z ^ ((z >> 30) & 17179869183)) * 6364136223846793005;
        z = z + 7046029254386353131;
        i = i - 1;
    }
    print(z);
    return 0;
}
"""

TWO_HOISTED_LOOPS = """
fn main() -> i64 {
    var a: i64 = 42;
    var b: i64 = 11;
    var i: i64 = 9;
    while i > 0 {
        a = a * 6364136223846793005;
        i = i - 1;
    }
    i = 7;
    while i > 0 {
        b = b * 6364136223846793005;
        i = i - 1;
    }
    print(a);
    print(b);
    return 0;
}
"""

@WINDOWS_ONLY
class ForLoopValueTest(EndToEndTest):
    """The for loop earns its place by running: ranges, control flow and scoped names."""

    def test_for_ranges_control_flow_and_scoped_variables(self) -> None:
        completed = self.execute(FOR_LOOPS)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "10\n15\n0\n0\n6\n4\n16\n0\n9\n")

    def test_the_end_bound_is_re_evaluated_each_iteration_like_c(self) -> None:
        completed = self.execute(FOR_END_REEVALUATED)
        self.assertEqual(completed.returncode, 0)
        # C order: the condition runs before the body on every check, so edge(3) prints
        # before i is printed each iteration and once more on the failing check.
        self.assertEqual(completed.stdout, "3\n0\n3\n1\n3\n2\n3\n")



@WINDOWS_ONLY
class LoopConstantHoistValueTest(EndToEndTest):
    """Hoisted big constants must read the loaded value on every loop iteration."""

    def test_a_countdown_mixer_with_hoisted_constants_wraps_like_the_source(self) -> None:
        completed = self.execute(HOISTED_MIXER_LOOP)
        self.assertEqual(completed.returncode, 0)
        z = 548732102
        for _ in range(64):
            z = _wrap((z ^ ((z >> 30) & 17179869183)) * 6364136223846793005)
            z = _wrap(z + 7046029254386353131)
        self.assertEqual(completed.stdout, f"{z}\n")

    def test_two_sequential_loops_hoist_their_own_constants(self) -> None:
        completed = self.execute(TWO_HOISTED_LOOPS)
        self.assertEqual(completed.returncode, 0)
        a = 42
        for _ in range(9):
            a = _wrap(a * 6364136223846793005)
        b = 11
        for _ in range(7):
            b = _wrap(b * 6364136223846793005)
        self.assertEqual(completed.stdout, f"{a}\n{b}\n")


if __name__ == "__main__":
    unittest.main()



COMMUTATIVE_PEEPHOLE = """
fn main() -> i64 {
    var a: i64 = 240;
    var b: i64 = 15;
    var c: i64 = 0;
    if (a & a) != 0 { print(7); }
    if (a & b) != 0 { print(1); }
    if (a & b) == 0 { print(2); }
    print(a + b);
    print(b | c);
    print(a ^ c);
    return 0;
}
"""


@WINDOWS_ONLY
class CommutativePeepholeValueTest(EndToEndTest):
    """The commutative-home fast path and its ZF-fed branch print identical results."""

    def test_the_peephole_functions_preserve_output(self) -> None:
        completed = self.execute(COMMUTATIVE_PEEPHOLE)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "7\n2\n255\n15\n240\n")


SAME_NAME_IN_PLACE = """
fn probe(a: i64, b: i64) -> i64 { return a * a; }
fn main() -> i64 {
    print(probe(9, -3));
    print(probe(-11, 100));
    var a: i64 = 5; var b: i64 = 6; var c: i64 = 7; var d: i64 = 8;
    var e: i64 = 9; var f: i64 = 10; var g: i64 = 0;
    g = 13;
    print(g * g);
    print(a * b);
    print(c + c);
    print(d & d);
    return 0;
}
"""


@WINDOWS_ONLY
class SameNameInPlaceValueTest(EndToEndTest):
    """Same-name in-place products and homes-fed multiplies print identical values."""

    def test_same_name_and_multiply_forms_preserve_output(self) -> None:
        completed = self.execute(SAME_NAME_IN_PLACE)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "81\n121\n169\n30\n14\n8\n")


FRAME_PATH_INLINE = (
    "fn twice(v: i64) -> i64 { return v + v; }"
    "fn mix(a: i64, b: i64, c: i64) -> i64 { return a * b + c; }"
    "fn main() -> i64 { "
    + " ".join(f"var a{i}: i64 = {i};" for i in range(6))
    + " var g: i64 = 0; var i: i64 = 0;"
    " while i < 3 { g = g + twice(g + a1); i = i + 1; }"
    " print(g);"
    " print(mix(a0, a1, a2));"
    " print(mix(a5, a4, a3));"
    " print(twice(a3) + twice(a4) + a5);"
    " return 0; }")


@WINDOWS_ONLY
class FramePathInlineValueTest(EndToEndTest):
    """A 7+-slot main that foreign-copies its callees prints the leaf-path answers."""

    def test_frame_path_copies_preserve_output(self) -> None:
        completed = self.execute(FRAME_PATH_INLINE)
        self.assertEqual(completed.returncode, 0)
        # g + 2*(g + a1) = 3g + 2 per iteration (a1 = 1): 0 -> 2 -> 8 -> 26
        # mix(0,1,2)=2; mix(5,4,3)=23; twice(3)+twice(4)+5 = 6+8+5 = 19
        self.assertEqual(completed.stdout, "26\n2\n23\n19\n")
