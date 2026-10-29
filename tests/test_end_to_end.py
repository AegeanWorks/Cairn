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

DIVIDE_BY_ZERO = """
fn main() -> i64 {
    var zero: i64 = 0;
    print(10 / zero);
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

    def test_division_by_zero_is_a_trap_with_a_message_and_exit_code_3(self) -> None:
        completed = self.execute(DIVIDE_BY_ZERO)
        self.assertEqual(completed.returncode, 3)
        self.assertIn("divide by zero", completed.stderr)

    def test_min_int64_prints_with_its_sign(self) -> None:
        completed = self.execute("fn main() -> i64 { print(-9223372036854775807 - 1); return 0; }")
        self.assertEqual(completed.stdout, "-9223372036854775808\n")

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


if __name__ == "__main__":
    unittest.main()
