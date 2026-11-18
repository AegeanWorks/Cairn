"""SSA IR foundation tests: differential execution and invariants.

Differential half: every corpus source is compiled to a real executable and
run, and `cairn.ir.translate` + `cairn.ir.Interpret` must answer the identical
exit code and stdout bytes. Invariant half: the translated module itself must
hold the SSA shape (one terminator per block end, single assignment per
destination, definition before use) without running anything.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cairn.builder import compile_source, write_executable
from cairn import ir

WINDOWS_ONLY = unittest.skipUnless(sys.platform == "win32",
                                   "produced executables are Windows PE images")
TIMEOUT_SECONDS = 20


# ---------------------------------------------------------------------------
# Differential corpus. Each entry: source text and optional stdin bytes.
# ---------------------------------------------------------------------------

CORPUS = [
    ("simple", 'fn main() -> i64 { print(42); return 0; }', b""),
    ("loop", """
fn main() -> i64 {
    var total: i64 = 0;
    for i in 0..10 { total = total + i * i; }
    print(total);
    return 0;
}""", b""),
    ("short_circuit", """
fn main() -> i64 {
    var t: i64 = 0;
    if 1 != 0 && 2 > 1 { t = 1; }
    if 0 != 0 && t == 9 { t = 2; }
    if 1 == 1 || t == 7 { t = t + 10; }
    if 0 == 0 || t == 7 { t = t + 100; }
    if 0 != 0 || 5 % 2 == 0 { t = t + 1000; }
    print(t);
    return 0;
}""", b""),
    ("wrap", 'fn main() -> i64 { print(9223372036854775807 + 1); return 0; }', b""),
    ("trap", 'fn main() -> i64 { print(5 % 0); return 0; }', b""),
    ("trap2", 'fn main() -> i64 { print(-9223372036854775808 / -1); return 0; }', b""),
    ("trap_after_prints", """
fn pending_before_trap() -> i64 { print(1); print(2); return 5 % 0; }
fn main() -> i64 { var x: i64 = pending_before_trap(); print(x); return 0; }""", b""),
    ("call", """
fn add(a: i64, b: i64) -> i64 { return a + b; }
fn main() -> i64 { print(add(3, 4)); return 0; }""", b""),
    ("rec", """
fn fib(n: i64) -> i64 { if n < 2 { return n; } return fib(n - 1) + fib(n - 2); }
fn main() -> i64 { print(fib(20)); print(fib(1)); return 0; }""", b""),
    ("shift", """
fn main() -> i64 {
    print(1 << 40);
    print(-16 >> 2);
    print(1 << 64);
    print(3 >> 100);
    return 0;
}""", b""),
    ("while", """
fn main() -> i64 {
    var n: i64 = 6;
    var f: i64 = 1;
    while n > 1 { f = f * n; n = n - 1; }
    print(f);
    return 0;
}""", b""),
    ("branch0", """
fn main() -> i64 {
    var x: i64 = 0 - 7;
    print(x);
    if x < 0 { print(0 - x); } else { print(x); }
    return 0;
}""", b""),
    ("hexneg", """
fn main() -> i64 {
    print_hex(-1); print_char(10);
    print_hex(0); print_char(10);
    print_hex(255); print_char(10);
    return 0;
}""", b""),
    ("notbit", """
fn main() -> i64 {
    print(!0); print(!7); print(6 & 3); print(6 | 3); print(6 ^ 3);
    return 0;
}""", b""),
    ("strs", """
fn main() -> i64 {
    print_str("hello"); print_char(10);
    print(str_len("hello"));
    print(str_byte("hello", 1));
    print(str_byte("hello", 4));
    print(str_byte("hello", 5));
    print(str_byte("hello", -1));
    print(flush());
    return 0;
}""", b""),
    ("spill", """
fn main() -> i64 {
    var i: i64 = 0;
    while i < 30000 { print_char(97); i = i + 1; }
    print_char(33);
    print(i);
    return 0;
}""", b""),
    ("flushmid", """
fn emit3() -> i64 {
    print_char(65);
    print_char(66);
    print_str("x");
    return flush();
}
fn main() -> i64 {
    print(emit3());
    print(1);
    print(2);
    print(flush());
    return 7;
}""", b""),
    ("read1", """
fn main() -> i64 {
    var a: i64 = read_char();
    var b: i64 = read_char();
    var c: i64 = read_char();
    var d: i64 = read_char();
    print(a); print(b); print(c); print(d);
    print(read_char());
    return 0;
}""", b"read-char-stdin"),
    ("read2", """
fn main() -> i64 {
    print(read_char());
    return 0;
}""", b"quiet"),
    ("array_fill", 'fn main() -> i64 {'
     ' var a: [8]i64 = [0; 8]; var i: i64 = 0;'
     ' while i < 8 { a[i] = i * i; i = i + 1; }'
     ' print(a[7]); return 0; }', b""),
    ("array_nested", 'fn main() -> i64 {'
     ' var a: [4]i64 = [3, 1, 2, 0]; var b: [4]i64 = [40, 50, 60, 70];'
     ' print(b[a[0]]); a[0] = 2; print(b[a[0]]); print(a[a[1]]); return 0; }', b""),
    ("array_callee", 'fn squares() -> i64 {'
     ' var s: [6]i64 = [0; 6]; var i: i64 = 0;'
     ' while i < 6 { s[i] = i * i; i = i + 1; } return s[3] + s[5]; }'
     ' fn main() -> i64 { print(squares()); return 0; }', b""),
    ("exit_midstream", """
fn main() -> i64 {
    print(1);
    exit(2);
    print(9);
    return 0;
}""", b""),
    ("exit_pending", """
fn main() -> i64 {
    print(1); print(2); print(3);
    exit(4);
}""", b""),
    ("exit_from_helper", """
fn bail() -> i64 { print_str("bail"); exit(5); return 0; }
fn main() -> i64 { var x: i64 = bail(); print(x); return 0; }""", b""),
    ("unreachable_dropped", """
fn main() -> i64 {
    if 1 == 1 { return 5; print(1); }
    return 0;
}""", b""),
]


class DifferentialTest(unittest.TestCase):
    """Compiled machine behaviour and interpreted IR behaviour must agree."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="cairn-ir-diff-"))
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for child in sorted(self.folder.rglob("*"), reverse=True):
            child.unlink(missing_ok=True)
        self.folder.rmdir()

    def _machine_run(self, source: str, stdin: bytes) -> subprocess.CompletedProcess:
        executable = self.folder / "program.exe"
        write_executable(executable, compile_source(source).image)
        return subprocess.run([str(executable)], capture_output=True, timeout=TIMEOUT_SECONDS,
                              input=stdin)

    def test_corpus_machine_and_interpreter_agree(self) -> None:
        for name, source, stdin in CORPUS:
            with self.subTest(case=name):
                completed = self._machine_run(source, stdin)
                module = ir.translate(compile_source(source).program)
                interp_exit, interp_stdout = ir.Interpret(stdin).run(module)
                self.assertEqual(interp_exit, completed.returncode)
                self.assertEqual(interp_stdout, completed.stdout)

    def test_interpreted_str_byte_answers_minus_one_past_the_nul(self) -> None:
        module = ir.translate(compile_source(
            'fn main() -> i64 { print(str_byte("hello", 5)); '
            'print(str_byte("hello", 9)); return 0; }').program)
        _, stdout = ir.Interpret().run(module)
        self.assertEqual(stdout, b"-1\n-1\n")


class InvariantTest(unittest.TestCase):
    """Shape the translator emits before anything runs."""

    def _translate(self, source: str) -> ir.Module:
        return ir.translate(compile_source(source).program)

    def test_every_block_ends_with_one_terminator(self) -> None:
        module = self._translate(CORPUS[1][1])
        for function in module.functions.values():
            for block in function.blocks:
                if block.ops:
                    self.assertIn(block.ops[-1].kind, ("jump", "branch", "return", "exit"))
                elif block is not function.blocks[-1]:
                    self.fail(f"block {block.label} is empty mid-function")
                for op in block.ops[:-1]:
                    self.assertNotIn(op.kind, ("jump", "branch", "return", "exit"))

    def test_single_assignment_per_destination(self) -> None:
        module = self._translate(CORPUS[2][1])
        for function in module.functions.values():
            seen = set()
            for block in function.blocks:
                for op in block.ops:
                    if op.dest != -1:
                        self.assertNotIn(op.dest, seen,
                                         f"{function.name}: value %{op.dest} assigned twice")
                        seen.add(op.dest)

    def test_uses_follow_definitions(self) -> None:
        module = self._translate(CORPUS[8][1])
        for function in module.functions.values():
            definitions = set()
            for block in function.blocks:
                for op in block.ops:
                    if op.kind in ("bin", "unary", "branch", "return", "exit",
                                   "cell_store"):
                        for value in (op.lhs, op.rhs):
                            if value != -1:
                                self.assertIn(value, definitions,
                                              f"{function.name}: %{value} used before defined")
                    if op.args:
                        self.assertTrue(all(v in definitions for v in op.args),
                                        f"{function.name}: call uses undefined values")
                    if op.dest != -1:
                        definitions.add(op.dest)

    def test_terminator_ops_carry_no_destination(self) -> None:
        module = self._translate(CORPUS[1][1])
        for function in module.functions.values():
            for block in function.blocks:
                for op in block.ops:
                    if op.kind in ("jump", "branch", "return", "exit"):
                        self.assertEqual(op.dest, -1)
                    elif op.kind in ("param", "cell_load"):
                        self.assertNotEqual(op.dest, -1)

    def test_values_do_not_exceed_the_function_budget(self) -> None:
        module = self._translate(CORPUS[8][1])
        for function in module.functions.values():
            for block in function.blocks:
                for op in block.ops:
                    if op.dest != -1:
                        self.assertLess(op.dest, function.values)


if __name__ == "__main__":
    unittest.main()
