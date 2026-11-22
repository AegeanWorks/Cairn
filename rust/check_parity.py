"""Prove that the Rust front end accepts exactly the same Cairn language as the Python compiler.

Both implementations print a canonical front-end dump for each source; this driver builds the Rust
binary once and compares the two dumps over every example and benchmark, plus a set of rejected
sources so diagnostic codes, messages and spans are covered too.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST = ROOT / "rust"
BINARY = RUST / "target" / "debug" / "cairn-rs"
ORACLE = RUST / "dump.py"
TIMEOUT_S = 120

REJECTED = (
    "let x: i64 = 9223372036854775809;",
    "fn main() -> f64 { return 0; }",
    "fn main() -> i64 { 1; return 0; }",
    "fn main() -> i64 { return 0 }",
    "fn main() -> i64 { for i in 0 10 { } return 0; }",
    "fn main() -> i64 { var a: [4]i64 = [0; n]; return 0; }",
    "fn main() -> i64 { return -9223372036854775808; }",
    "let caf\u00e9: i64 = 1;",
    'print_str("bad\\q");',
)


def run(command, timeout=TIMEOUT_S):
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, cwd=str(ROOT))


def main():
    built = run(["cargo", "build", "--quiet", "--manifest-path", str(RUST / "Cargo.toml")], 300)
    if built.returncode != 0:
        print("cargo build failed:\n" + built.stdout + built.stderr, file=sys.stderr)
        return 1

    sources = sorted((ROOT / "examples").glob("*.crn"))
    sources += sorted((ROOT / "benchmarks").glob("*.crn"))
    with tempfile.TemporaryDirectory(prefix="cairn-parity-") as folder:
        for index, source in enumerate(REJECTED):
            path = Path(folder) / f"rejected-{index}.crn"
            path.write_text(source, encoding="utf-8")
            sources.append(path)

        failures = []
        checks = [
            ("front end", ORACLE, "dump"),
            ("inlining", RUST / "inline_dump.py", "inline"),
            ("lowering", RUST / "lower_dump.py", "lower"),
            ("copy propagation", RUST / "copy_dump.py", "copyprop"),
            ("algebraic", RUST / "alg_dump.py", "alg"),
            ("unrolling", RUST / "unroll_dump.py", "unroll"),
            ("countdown", RUST / "count_dump.py", "count"),
            ("odd-pair fusion", RUST / "fuse_dump.py", "fuse"),
            ("final passes", RUST / "final_dump.py", "final"),
        ]
        for source in sources:
            for label, oracle, command in checks:
                expected = run([sys.executable, str(oracle), str(source)])
                actual = run([str(BINARY), command, str(source)])
                if expected.returncode != 0 or actual.returncode != 0:
                    failures.append((source, f"{label} dumper exited non-zero"))
                elif expected.stdout != actual.stdout:
                    failures.append((source, f"{label} mismatch"))

        specs = sorted((RUST / "specs").glob("*.txt"))
        for spec in specs:
            if spec.name == "fold_calls.txt":
                oracle, command = RUST / "fold_dump.py", "fold"
            else:
                oracle, command = RUST / "pe_dump.py", "pe"
            expected = run([sys.executable, str(oracle), str(spec)])
            actual = run([str(BINARY), command, str(spec)])
            if expected.returncode != 0 or actual.returncode != 0:
                failures.append((spec, "a backend dumper exited non-zero"))
            elif expected.stdout != actual.stdout:
                failures.append((spec, "backend dump mismatch"))

        runtime_expected = run([sys.executable, str(RUST / "runtime_dump.py")])
        runtime_actual = run([str(BINARY), "runtime"])
        if runtime_expected.returncode != 0 or runtime_actual.returncode != 0:
            failures.append((Path("runtime"), "a runtime dumper exited non-zero"))
        elif runtime_expected.stdout != runtime_actual.stdout:
            failures.append((Path("runtime"), "runtime dump mismatch"))

        total = len(sources) + len(specs) + 1
        print(f"parity: {total - len(failures)}/{total} sources identical")
        for source, reason in failures:
            print(f"  FAIL {source.name}: {reason}")
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
