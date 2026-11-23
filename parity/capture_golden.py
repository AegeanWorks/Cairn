"""One-time capture of the canonical reference-output hash manifest.

Run once after cross-validating the Rust implementation against the Python compiler.
The manifest is frozen reference data; day-to-day verification uses the Rust
`golden_parity` integration test and does not invoke Python.
"""

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARITY = ROOT / "parity"
MANIFEST = PARITY / "golden.sha256"

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

CHECKS = (
    ("dump", "dump.py"),
    ("inline", "inline_dump.py"),
    ("lower", "lower_dump.py"),
    ("copyprop", "copy_dump.py"),
    ("alg", "alg_dump.py"),
    ("unroll", "unroll_dump.py"),
    ("count", "count_dump.py"),
    ("fuse", "fuse_dump.py"),
    ("final", "final_dump.py"),
)

CODEGEN_CHECKS = (
    ("codegen", "codegen_dump.py"),
    ("build", "builder_dump.py"),
    ("listjson", "listing_dump.py"),
    ("verifyjson", "verify_dump.py"),
    ("runjson", "run_dump.py"),
    ("check", "cli_dump.py"),
)

BACKEND = (
    ("fold", "fold_dump.py", "fold_calls.txt"),
    ("magic", "magic_dump.py", "magic.txt"),
    ("pe", "pe_dump.py", "pe_basic.txt"),
)

ENTRIES = []


def capture(name, source, oracle, filename=None):
    command = [sys.executable, str(PARITY / oracle)]
    if source is not None:
        command.append(str(source))
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=180, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit(f"oracle failed for {name}: {source}")
    digest = hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()
    key = filename or source.name
    ENTRIES.append((digest, f"{name}/{key}"))


def main():
    sources = sorted((ROOT / "examples").glob("*.crn"))
    sources += sorted((ROOT / "benchmarks").glob("*.crn"))
    with tempfile.TemporaryDirectory(prefix="cairn-golden-") as folder:
        for index, text in enumerate(REJECTED):
            path = Path(folder) / f"rejected-{index}.crn"
            path.write_text(text, encoding="utf-8")
            sources.append(path)
        for command, oracle in CHECKS:
            for source in sources:
                capture(command, source, oracle)

    codegen_sources = sorted((PARITY / "specs" / "codegen").glob("*.crn"))
    codegen_sources += sorted((ROOT / "examples").glob("*.crn"))
    codegen_sources += sorted((ROOT / "benchmarks").glob("*.crn"))
    # argv_walk and hex_io fold program-path bytes or wall-clock work into their output,
    # so their `run --json` envelope is environment-dependent and cannot be frozen.
    volatile_run = {"argv_walk.crn", "hex_io.crn"}
    for source in codegen_sources:
        for command, oracle in CODEGEN_CHECKS:
            if command == "runjson" and source.name in volatile_run:
                continue
            capture(command, source, oracle)

    for command, oracle, spec in BACKEND:
        capture(command, PARITY / "specs" / spec, oracle)

    capture("runtime", None, "runtime_dump.py", "runtime.txt")
    capture("capjson", None, "cap_dump.py", "capabilities.txt")

    MANIFEST.write_text("\n".join(f"{d}  {k}" for d, k in ENTRIES) + "\n", encoding="utf-8")
    print(f"captured {len(ENTRIES)} golden hashes")


if __name__ == "__main__":
    sys.exit(main())
