"""Benchmark harness: measure Cairn executables and, when one exists, C and Rust baselines.

The harness compiles each case with the compiler in this repository and runs the produced
executable several times, reporting the best and median wall clock. A C or Rust compiler is
used only when it is already on `PATH`; when none is found the report says so instead of
guessing, because an invented comparison would be worse than no comparison.

Usage:
  python benchmarks/run_benchmarks.py              # human table
  python benchmarks/run_benchmarks.py --json       # machine-readable report
  python benchmarks/run_benchmarks.py --rounds 7   # more samples per case
  python benchmarks/run_benchmarks.py --strict     # exit 1 unless Cairn wins every
                                                   # measured baseline; exit 2 when no
                                                   # baseline can be measured
"""

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS = ROOT / "benchmarks"
BUILD_TIMEOUT = 120.0
RUN_TIMEOUT = 300.0
DEFAULT_ROUNDS = 5
EXIT_OK = 0
EXIT_SLOWER = 1
EXIT_NO_BASELINE = 2

C_COMPILERS: Dict[str, List[str]] = {
    "gcc": ["gcc", "-O2", "-o"],
    "clang": ["clang", "-O2", "-o"],
    "cl": ["cl", "/O2", "/nologo"],
}

RUST_COMPILERS: Dict[str, List[str]] = {
    "rustc": ["rustc", "-O", "-o"],
}


@dataclass(frozen=True)
class Case:
    """One benchmark: a Cairn source, equivalent C and Rust sources, and what one iteration is."""

    name: str
    cairn: str
    c_source: str
    rust_source: str
    iterations: int
    unit: str


CASES: Tuple[Case, ...] = (
    Case("xorshift", "benchmarks/xorshift.crn", "benchmarks/xorshift.c",
         "benchmarks/xorshift.rs", 200_000_000, "iterations"),
    Case("fib30", "benchmarks/fib30.crn", "benchmarks/fib30.c", "benchmarks/fib30.rs",
         2_692_537, "calls"),
    Case("folded_math", "benchmarks/folded_math.crn", "benchmarks/folded_math.c",
         "benchmarks/folded_math.rs", 20_000_000, "iterations"),
    Case("collatz", "benchmarks/collatz.crn", "benchmarks/collatz.c",
         "benchmarks/collatz.rs", 1_000_000, "probes"),
    Case("ackermann", "benchmarks/ackermann.crn", "benchmarks/ackermann.c",
         "benchmarks/ackermann.rs", 1, "calls"),
    Case("nqueens", "benchmarks/nqueens.crn", "benchmarks/nqueens.c",
         "benchmarks/nqueens.rs", 1, "solutions"),
    Case("binary_gcd", "benchmarks/binary_gcd.crn", "benchmarks/binary_gcd.c",
         "benchmarks/binary_gcd.rs", 1_000_000, "pairs"),
    Case("call_chain", "benchmarks/call_chain.crn", "benchmarks/call_chain.c",
         "benchmarks/call_chain.rs", 20_000_000, "calls"),
)


def find_c_compiler() -> Optional[str]:
    """The first C compiler already installed, or None when the machine has none."""
    for name in C_COMPILERS:
        if shutil.which(name):
            return name
    return None


def find_rust_compiler() -> Optional[str]:
    """The first Rust compiler already installed, or None when the machine has none."""
    for name in RUST_COMPILERS:
        if shutil.which(name):
            return name
    return None


def run_command(argv: Sequence[str], timeout: float,
                cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Run one command with a hard limit, capturing text."""
    return subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False, cwd=cwd)


def cairn_build_command(source: Path, destination: Path, optimise: bool = True) -> List[str]:
    """The exact `cairn build` command for one case, with the folding pass optional."""
    argv = [sys.executable, "-m", "cairn", "build", str(source), "-o", str(destination)]
    if not optimise:
        argv.append("--no-optimize")
    return argv


def build_cairn(source: Path, destination: Path, optimise: bool = True) -> float:
    """Compile one Cairn case and return the compile time in milliseconds."""
    started = time.perf_counter()
    completed = run_command(cairn_build_command(source, destination, optimise),
                            BUILD_TIMEOUT, cwd=ROOT)
    elapsed = (time.perf_counter() - started) * 1000.0
    if completed.returncode != 0:
        raise RuntimeError(f"cairn build failed for {source.name}:\n{completed.stderr}")
    return elapsed


def build_c(compiler: str, source: Path, destination: Path) -> Optional[float]:
    """Compile one C case; None when this compiler does not produce an executable."""
    template = C_COMPILERS[compiler]
    if compiler == "cl":
        argv = [template[0], template[1], template[2], f"/Fe:{destination}", str(source)]
    else:
        argv = [template[0], template[1], template[2], str(destination), str(source)]
    started = time.perf_counter()
    completed = run_command(argv, BUILD_TIMEOUT, cwd=ROOT)
    if completed.returncode != 0 or not destination.is_file():
        return None
    return (time.perf_counter() - started) * 1000.0


def build_rust(compiler: str, source: Path, destination: Path) -> Optional[float]:
    """Compile one Rust case; None when this compiler does not produce an executable."""
    template = RUST_COMPILERS[compiler]
    argv = [template[0], template[1], template[2], str(destination), str(source)]
    started = time.perf_counter()
    completed = run_command(argv, BUILD_TIMEOUT, cwd=ROOT)
    if completed.returncode != 0 or not destination.is_file():
        return None
    return (time.perf_counter() - started) * 1000.0


def measure(executable: Path, rounds: int) -> List[float]:
    """Wall-clock milliseconds for each run of one executable."""
    samples: List[float] = []
    for _ in range(rounds):
        started = time.perf_counter()
        completed = run_command([str(executable)], RUN_TIMEOUT)
        samples.append((time.perf_counter() - started) * 1000.0)
        if completed.returncode != 0:
            raise RuntimeError(f"{executable.name} exited with {completed.returncode}: "
                               f"{completed.stdout}{completed.stderr}")
    return samples


def summarise(samples: List[float], iterations: int) -> Dict[str, float]:
    """Best, median and per-unit cost from one set of wall-clock samples."""
    if not samples:
        raise ValueError("summarise needs at least one sample")
    best = min(samples)
    return {
        "runs": len(samples),
        "best_ms": round(best, 2),
        "median_ms": round(statistics.median(samples), 2),
        "worst_ms": round(max(samples), 2),
        "ns_per_unit": round(best * 1_000_000.0 / iterations, 3) if iterations else 0.0,
    }


def verdict(best_cairn: Optional[float], best_c: Optional[float]) -> str:
    """The measured standing against one baseline: `faster`, `slower`, `tie` or `unavailable`.

    A verdict is only ever computed from two measurements taken in the same run. Without a
    baseline the answer is `unavailable`, never an assumption. Used unchanged for the C and
    the Rust baseline.
    """
    if best_cairn is None or best_c is None or best_c <= 0:
        return "unavailable"
    if best_cairn < best_c:
        return "faster"
    if best_cairn > best_c:
        return "slower"
    return "tie"


def benchmark_case(case: Case, compiler: Optional[str], rounds: int, folder: Path,
                   optimise: bool = True, rust_compiler: Optional[str] = None) -> Dict[str, object]:
    """Measure one case in Cairn and, when available, in C and in Rust."""
    cairn_source = ROOT / case.cairn
    cairn_exe = folder / f"{case.name}.cairn.exe"
    report: Dict[str, object] = {
        "case": case.name, "unit": case.unit, "iterations": case.iterations,
        "cairn": None, "c": None, "rust": None,
        "c_compiler": compiler, "rust_compiler": rust_compiler,
        "verdict": "unavailable", "verdict_rust": "unavailable",
        "error": None,
    }
    try:
        compile_ms = build_cairn(cairn_source, cairn_exe, optimise)
        cairn = summarise(measure(cairn_exe, rounds), case.iterations)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as failure:
        report["verdict"] = "error"
        report["error"] = str(failure)
        return report
    cairn["compile_ms"] = round(compile_ms, 2)
    cairn["image_bytes"] = cairn_exe.stat().st_size
    report["cairn"] = cairn
    best_cairn = float(cairn["best_ms"])
    for language, baseline_compiler, build, source_path, exe_name, verdict_key, ratio_key in (
            ("c", compiler, build_c, ROOT / case.c_source, f"{case.name}.c.exe",
             "verdict", "ratio_to_c"),
            ("rust", rust_compiler, build_rust, ROOT / case.rust_source,
             f"{case.name}.rust.exe", "verdict_rust", "ratio_to_rust")):
        if baseline_compiler is None:
            continue
        exe = folder / exe_name
        try:
            baseline_compile = build(baseline_compiler, source_path, exe)
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as failure:
            report[verdict_key] = "error"
            report["error"] = f"{language} build failed: {failure}"
            return report
        if baseline_compile is None:
            report[verdict_key] = "error"
            report["error"] = f"{language}-build-failed"
            return report
        try:
            baseline = summarise(measure(exe, rounds), case.iterations)
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as failure:
            report[verdict_key] = "error"
            report["error"] = str(failure)
            return report
        best_baseline = float(baseline["best_ms"])
        baseline["compile_ms"] = round(baseline_compile, 2)
        baseline["image_bytes"] = exe.stat().st_size
        baseline[ratio_key] = round(best_cairn / best_baseline, 3) if best_baseline else 0.0
        report[language] = baseline
        report[verdict_key] = verdict(best_cairn, best_baseline)
    return report


def render(reports: List[Dict[str, object]], compiler: Optional[str],
           optimise: bool = True, rust_compiler: Optional[str] = None) -> str:
    """The human table; one line per case and per measured language."""
    languages = [("cairn", "cairn"), ("c", "c"), ("rust", "rust")]
    lines = ["case        language  best ms  median ms  compile ms  ns/unit  size",
             "----------  --------  -------  ---------  ----------  -------  ----"]
    for report in reports:
        for language, key in languages:
            data = report.get(key)
            if not isinstance(data, dict):
                continue
            lines.append(
                f"{str(report['case']):10s}  {language:8s}  {data['best_ms']:7.2f}  "
                f"{data['median_ms']:9.2f}  {data.get('compile_ms', 0.0):10.2f}  "
                f"{data['ns_per_unit']:7.3f}  {data.get('image_bytes', '-')}")
    lines.append("")
    if not optimise:
        lines.append("Cairn build: --no-optimize (the folding pass is skipped)")
    lines.append(f"C baseline: {compiler}" if compiler
                 else "C baseline: unavailable (no gcc, clang or cl on PATH)")
    lines.append(f"Rust baseline: {rust_compiler}" if rust_compiler
                 else "Rust baseline: unavailable (no rustc on PATH)")
    ratios = [float(data["ratio_to_c"]) for report in reports
              for data in [report.get("c")] if isinstance(data, dict) and data.get("ratio_to_c")]
    if ratios:
        lines.append(f"Cairn/C best-time ratio: {min(ratios):.2f} to {max(ratios):.2f} "
                     f"(1.00 means equal)")
    rust_ratios = [float(data["ratio_to_rust"]) for report in reports
                   for data in [report.get("rust")]
                   if isinstance(data, dict) and data.get("ratio_to_rust")]
    if rust_ratios:
        lines.append(f"Cairn/Rust best-time ratio: {min(rust_ratios):.2f} to "
                     f"{max(rust_ratios):.2f} (1.00 means equal)")
    lines.extend(_goal_lines(reports, "C", compiler, "verdict", "gcc, clang or cl"))
    lines.extend(_goal_lines(reports, "Rust", rust_compiler, "verdict_rust", "rustc"))
    return "\n".join(lines)


def _goal_lines(reports: List[Dict[str, object]], label: str, compiler: Optional[str],
                verdict_key: str, install_hint: str) -> List[str]:
    """The scoreboard line for one baseline, decided only by measurements from this run."""
    verdicts = [str(report[verdict_key]) if isinstance(report.get(verdict_key), str)
                else "unavailable" for report in reports]
    if "error" in verdicts:
        errored = [str(report["case"]) for report in reports
                   if report.get(verdict_key) == "error"]
        return [f"Goal: faster than {label} -> errored on {', '.join(errored)} "
                f"(see the report 'error' fields)"]
    if "unavailable" in verdicts:
        return [f"Goal: faster than {label} -> unavailable "
                f"(install {install_hint} to measure)"]
    if all(item in ("faster", "tie") for item in verdicts):
        return [f"Goal: faster than {label} -> measured on every case in this run"]
    slower = [str(report["case"]) for report in reports
              if report.get(verdict_key) == "slower"]
    return [f"Goal: faster than {label} -> not met on {', '.join(slower)}"]


def _number(text: str) -> int:
    """A positive sample count from the command line."""
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("rounds must be at least 1")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse options, run every case and print the report."""
    parser = argparse.ArgumentParser(
        prog="run_benchmarks.py",
        description="Measure Cairn executables, and a C or Rust baseline when a "
                    "compiler for it is present.")
    parser.add_argument("--rounds", type=_number, default=DEFAULT_ROUNDS,
                        help=f"runs per executable (default: {DEFAULT_ROUNDS})")
    parser.add_argument("--json", action="store_true", help="print one JSON report object")
    parser.add_argument("--no-optimize", action="store_true",
                        help="build Cairn without the folding pass, to measure what it buys")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 unless Cairn is at least as fast as every measured "
                             "baseline on every case; exit 2 when no baseline can be measured")
    arguments = parser.parse_args(argv)
    compiler = find_c_compiler()
    rust_compiler = find_rust_compiler()
    optimise = not arguments.no_optimize
    with tempfile.TemporaryDirectory(prefix="cairn-bench-") as folder:
        reports = [benchmark_case(case, compiler, arguments.rounds, Path(folder), optimise,
                                  rust_compiler=rust_compiler)
                   for case in CASES]
    verdicts = [str(report.get("verdict")) for report in reports]
    rust_verdicts = [str(report.get("verdict_rust")) for report in reports]
    met_c = all(item in ("faster", "tie") for item in verdicts)
    met_rust = all(item in ("faster", "tie") for item in rust_verdicts)
    met = (met_c if compiler is not None else True) and \
          (met_rust if rust_compiler is not None else True)
    if arguments.json:
        print(json.dumps({"compiler": compiler, "rust_compiler": rust_compiler,
                          "rounds": arguments.rounds, "optimise": optimise,
                          "goal": {"faster_than_c": met_c
                                   if "unavailable" not in verdicts else None,
                                   "verdicts": verdicts,
                                   "faster_than_rust": met_rust
                                   if "unavailable" not in rust_verdicts else None,
                                   "verdicts_rust": rust_verdicts},
                          "cases": reports}, indent=2))
    else:
        print(render(reports, compiler, optimise, rust_compiler))
    if not arguments.strict:
        return EXIT_OK
    if compiler is None and rust_compiler is None:
        return EXIT_NO_BASELINE
    return EXIT_OK if met and "error" not in verdicts + rust_verdicts else EXIT_SLOWER


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))



