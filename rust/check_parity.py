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
        for source in sources:
            expected = run([sys.executable, str(ORACLE), str(source)])
            actual = run([str(BINARY), "dump", str(source)])
            if expected.returncode != 0 or actual.returncode != 0:
                failures.append((source, "a dumper exited non-zero"))
            elif expected.stdout != actual.stdout:
                failures.append((source, "dump mismatch"))
            else:
                lowered_expected = run([sys.executable, str(RUST / "lower_dump.py"), str(source)])
                lowered_actual = run([str(BINARY), "lower", str(source)])
                if lowered_expected.returncode != 0 or lowered_actual.returncode != 0:
                    failures.append((source, "a lowering dumper exited non-zero"))
                elif lowered_expected.stdout != lowered_actual.stdout:
                    failures.append((source, "lowering mismatch"))
                else:
                    copy_expected = run([sys.executable, str(RUST / "copy_dump.py"), str(source)])
                    copy_actual = run([str(BINARY), "copyprop", str(source)])
                    if copy_expected.returncode != 0 or copy_actual.returncode != 0:
                        failures.append((source, "a copy dumper exited non-zero"))
                    elif copy_expected.stdout != copy_actual.stdout:
                        failures.append((source, "copy propagation mismatch"))
                    else:
                        alg_expected = run([sys.executable, str(RUST / "alg_dump.py"), str(source)])
                        alg_actual = run([str(BINARY), "alg", str(source)])
                        if alg_expected.returncode != 0 or alg_actual.returncode != 0:
                            failures.append((source, "an algebraic dumper exited non-zero"))
                        elif alg_expected.stdout != alg_actual.stdout:
                            failures.append((source, "algebraic mismatch"))

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
