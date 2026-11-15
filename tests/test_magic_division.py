"""Magic-multiply division: the parameter search is exact, and the bytes back it up.

Literal divisors own a gcc-style lowering (signed mulhigh, dividend add-back, one
arithmetic shift, sign word subtracted). These tests pin the three layers:
the parameter search against the exact machine chain over the whole int64 domain it
claims, the emitted bytes, and the executed results.
"""

import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cairn import parser, sema
from cairn.builder import compile_source, write_executable
from cairn.codegen import CodeGenerator, magic_division_parameters
from cairn.machine import Assembler

WINDOWS_ONLY = unittest.skipUnless(sys.platform == "win32",
                                   "produced executables are Windows PE images")
TIMEOUT_SECONDS = 20

# The movabs/imul/lea chain gcc -O2 emits for n % 2147483647, transcribed from
# the captured C build; the search must reproduce its parameters exactly.
GCC_MULTIPLIER = -9223372032559808509
GCC_SHIFT = 30

LO, HI = -2 ** 63, 2 ** 63 - 1


def truncdiv(n, d):
    """C-truncated division, the language's own / and % semantics."""
    q = abs(n) // abs(d)
    return -q if (n < 0) != (d < 0) else q


def machine_chain(n, m_signed, s):
    """What the emitted instructions compute, modelled exactly.

    One-operand imul gives the signed 128-bit product's high half; the lea adds
    the dividend back through a 64-bit wrap; sar and the sign-word sub finish.
    """
    hi = (n * m_signed) >> 64
    hi &= (2 ** 64 - 1)
    if hi >= 2 ** 63:
        hi -= 2 ** 64
    value = (hi + n) & (2 ** 64 - 1)
    if value >= 2 ** 63:
        value -= 2 ** 64
    quotient = value >> s
    return quotient - (-1 if n < 0 else 0)


def dividend_battery(d):
    """Every structured int64 edge a magic chain can go wrong on, both signs."""
    edges = [0, 1, 2, d - 1, d, d + 1, 2 * d, 2 * d + 1, -(d - 1), -d, -(d + 1), -2 * d,
             2 ** 31 - d, 2 ** 31, 2 ** 31 + d, 2 ** 40, 2 ** 62,
             HI - 1, HI, LO + 1, LO, -1, -2]
    edges += [n for n in (k * d + j
                          for k in (0, 1, 2, HI // d - 1, HI // d, HI // d + 1)
                          for j in (-2, -1, 0, 1, 2))
              if LO <= n <= HI]
    return edges


class MagicParameterTest(unittest.TestCase):
    """The parameter search before any code generation."""

    def test_the_search_reproduces_gccs_parameters_for_the_benchmark_divisor(self) -> None:
        self.assertEqual(magic_division_parameters(2147483647),
                         (GCC_MULTIPLIER, GCC_SHIFT))

    def test_powers_of_two_and_degenerate_divisors_refuse(self) -> None:
        for divisor in (0, 1, -3, 2, 4, 8, 16, 1024, 1 << 62):
            self.assertIsNone(magic_division_parameters(divisor), divisor)

    def test_the_emitted_chain_matches_truncated_division_for_small_divisors(self) -> None:
        random.seed(41)
        for divisor in range(3, 65):
            if divisor & (divisor - 1) == 0:
                continue  # powers of two take the shift lowering and refuse here
            parameters = magic_division_parameters(divisor)
            self.assertIsNotNone(parameters, divisor)
            multiplier, shift = parameters
            for n in dividend_battery(divisor):
                self.assertEqual(machine_chain(n, multiplier, shift), truncdiv(n, divisor),
                                 f"{n} / {divisor}")

    def test_the_emitted_chain_matches_truncated_division_for_large_divisors(self) -> None:
        random.seed(43)
        for divisor in (999, 1009, 6553, 65537, 65539, 2 ** 31 - 7,
                        2 ** 31 + 7, 2147483647, 2 ** 40 + 3, 2 ** 61 + 1):
            parameters = magic_division_parameters(divisor)
            self.assertIsNotNone(parameters, divisor)
            multiplier, shift = parameters
            sample = dividend_battery(divisor)
            sample += [random.randint(LO, HI) for _ in range(400)]
            for n in sample:
                self.assertEqual(machine_chain(n, multiplier, shift), truncdiv(n, divisor),
                                 f"{n} / {divisor}")


class MagicDivisionBytesTest(unittest.TestCase):
    """The lowering replaces the idiv and its trap branches."""

    def main_region(self, body: str) -> bytes:
        source = "fn main() -> i64 { " + body + " }"
        program = sema.analyze(parser.parse(source))
        assembler = Assembler()
        artifact = CodeGenerator(assembler=assembler).generate(program)
        start = artifact.text_labels["fn.main"]
        later = sorted(offset for label, offset in artifact.text_labels.items()
                       if offset > start and (label.startswith("cairn.")
                                              or (label.startswith("fn.")
                                                  and label.count(".") == 1)))
        return artifact.text[start:later[0] if later else len(artifact.text)]

    def test_a_literal_remainder_carries_idiv_or_trap_branches_nowhere(self) -> None:
        region = self.main_region(
            "var x: i64 = 89466097; print(x % 2147483647); return 0;")
        self.assertNotIn(b"\xf7\xfe", region)      # no idiv over the scratch register
        self.assertNotIn(b"\x74", region)          # no short-form trap branch
        self.assertNotIn(b"\x0f\x84", region)      # no long-form zero check either

    def test_the_benchmark_chain_matches_the_captured_gcc_bytes(self) -> None:
        region = self.main_region("var x: i64 = 89466097; return x % 2147483647;")
        multiplier_bytes = (GCC_MULTIPLIER & (2 ** 64 - 1)).to_bytes(8, "little")
        divisor_bytes = (2147483647).to_bytes(8, "little")
        self.assertIn(b"\x48\xbe" + multiplier_bytes, region)   # mov rsi, gcc's magic
        self.assertIn(b"\x48\xf7\xee", region)                  # imul rsi: mulhigh
        self.assertIn(b"\x48\x8d\x44\x0a\x00", region)          # lea rax, [rdx+rcx]
        self.assertIn(b"\x48\xc1\xf8\x1e", region)              # sar rax, 30
        self.assertIn(b"\x48\xc1\xfa\x3f", region)              # sar rdx, 63: sign word
        self.assertIn(b"\x48\xbe" + divisor_bytes, region)      # mov rsi, 2147483647
        self.assertIn(b"\x48\x0f\xaf\xd6", region)              # imul rdx, rsi: q*d
        self.assertIn(b"\x48\x29\xd0", region)                  # sub rax, rdx: remainder

    def test_a_quotient_keeps_the_sign_word_and_no_remainder_tail(self) -> None:
        region = self.main_region("var x: i64 = 89466097; return x / 2147483647;")
        multiplier_bytes = (GCC_MULTIPLIER & (2 ** 64 - 1)).to_bytes(8, "little")
        self.assertIn(b"\x48\xbe" + multiplier_bytes, region)
        self.assertIn(b"\x48\xc1\xf8\x1e", region)              # sar rax, 30
        self.assertIn(b"\x48\xc1\xfa\x3f", region)              # sar rdx, 63
        self.assertIn(b"\x48\x29\xd0", region)                  # sub rax, rdx
        self.assertNotIn(b"\x4c\x0f\xaf\xd6", region)           # no q*d remainder tail


MAGIC_PROGRAM = """
fn main() -> i64 {
    print(2147483646 % 2147483647);
    print(2147483647 % 2147483647);
    print(2147483648 % 2147483647);
    print(1024 % 2147483647);
    print(-1024 % 2147483647);
    print(1099511627776 / 2147483647);
    print(1099511627776 % 2147483647);
    print(-1099511627776 / 2147483647);
    print(-1099511627776 % 2147483647);
    print(9223372036854775807 / 2147483647);
    print(9223372036854775807 % 2147483647);
    print(-9223372036854775808 / 2147483647);
    print(-9223372036854775808 % 2147483647);
    print(-9223372036854775808 % 2147483646);
    print(-9223372036854775808 / 3);
    print(-9223372036854775808 % 3);
    print(-9223372036854775808 % 10);
    print(1000000007 / 2147483647);
    return 0;
}
"""


@WINDOWS_ONLY
class MagicDivisionExecutionTest(unittest.TestCase):
    """Real executables agree with truncated division, extremes included."""

    def execute(self, source: str, name: str) -> subprocess.CompletedProcess:
        folder = Path(tempfile.mkdtemp(prefix="cairn_magic_"))
        self.addCleanup(self._cleanup, folder)
        executable = folder / name
        write_executable(executable, compile_source(source).image)
        return subprocess.run([str(executable)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)

    def _cleanup(self, folder: Path) -> None:
        for child in sorted(folder.rglob("*"), reverse=True):
            child.unlink(missing_ok=True)
        folder.rmdir()

    def expected_battery(self) -> list:
        """The 18 printed lines of MAGIC_PROGRAM under truncated division semantics."""
        dividend = 2147483647
        program_lines = [
            ("%", 2147483646, dividend), ("%", 2147483647, dividend),
            ("%", 2147483648, dividend), ("%", 1024, dividend), ("%", -1024, dividend),
            ("/", 1099511627776, dividend), ("%", 1099511627776, dividend),
            ("/", -1099511627776, dividend), ("%", -1099511627776, dividend),
            ("/", 2 ** 63 - 1, dividend), ("%", 2 ** 63 - 1, dividend),
            ("/", LO, dividend), ("%", LO, dividend), ("%", LO, 2147483646),
            ("/", LO, 3), ("%", LO, 3), ("%", LO, 10),
            ("/", 1000000007, dividend),
        ]
        lines = []
        for operator, value, divisor in program_lines:
            quotient = truncdiv(value, divisor)
            lines.append(value - quotient * divisor if operator == "%" else quotient)
        return lines

    def test_a_battery_of_dividends_matches_truncated_semantics(self) -> None:
        completed = self.execute(MAGIC_PROGRAM, "magic.exe")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout,
                         "".join(f"{value}\n" for value in self.expected_battery()))

    def test_plain_builds_agree_on_the_battery(self) -> None:
        folded = self.execute(MAGIC_PROGRAM, "folded.exe")
        plain = self.execute(MAGIC_PROGRAM, "plain.exe")
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertEqual(folded.returncode, 0, folded.stderr)
        self.assertEqual(plain.stdout, folded.stdout)


if __name__ == "__main__":
    unittest.main()
