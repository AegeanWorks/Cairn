"""Benchmark harness: measure Cairn executables and, when one exists, a C baseline.

The harness compiles each case with the compiler in this repository and runs the produced
executable several times, reporting the best and median wall clock. A C compiler is used only
when it is already on `PATH`; when none is found the report says so instead of guessing, because
an invented comparison would be worse than no comparison.

Usage:
  python benchmarks/run_benchmarks.py              # human table
  python benchmarks/run_benchmarks.py --json       # machine-readable report
  python benchmarks/run_benchmarks.py --rounds 7   # more samples per case
  python benchmarks/run_benchmarks.py --strict     # exit 1 unless Cairn wins every case
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


@dataclass(frozen=True)
class Case:
    """One benchmark: a Cairn source, an equivalent C source, and what one iteration is."""

    name: str
    cairn: str
    c_source: str
    iterations: int
    unit: str


CASES: Tuple[Case, ...] = (
    Case("loop_sum", "benchmarks/loop_sum.crn", "benchmarks/loop_sum.c", 200_000_000,
         "iterations"),
    Case("fib30", "benchmarks/fib30.crn", "benchmarks/fib30.c", 2_692_537, "calls"),
    Case("folded_math", "benchmarks/folded_math.crn", "benchmarks/folded_math.c", 20_000_000,
         "iterations"),
)


def find_c_compiler() -> Optional[str]:
    """The first C compiler already installed, or None when the machine has none."""
    for name in C_COMPILERS:
        if shutil.which(name):
            return name
    return None


def run_command(argv: Sequence[str], timeout: float,
                cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Run one command with a hard limit, capturing text."""
    return subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False, cwd=cwd)


def build_cairn(source: Path, destination: Path) -> float:
    """Compile one Cairn case and return the compile time in milliseconds."""
    started = time.perf_counter()
    completed = run_command([sys.executable, "-m", "cairn", "build", str(source),
                             "-o", str(destination)], BUILD_TIMEOUT, cwd=ROOT)
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
    best = min(samples)
    return {
        "runs": len(samples),
        "best_ms": round(best, 2),
        "median_ms": round(statistics.median(samples), 2),
        "worst_ms": round(max(samples), 2),
        "ns_per_unit": round(best * 1_000_000.0 / iterations, 3) if iterations else 0.0,
    }


def verdict(best_cairn: Optional[float], best_c: Optional[float]) -> str:
    """The measured standing against C: `faster`, `slower`, `tie` or `unavailable`.

    A verdict is only ever computed from two measurements taken in the same run. Without a C
    baseline the answer is `unavailable`, never an assumption.
    """
    if best_cairn is None or best_c is None or best_c <= 0:
        return "unavailable"
    if best_cairn < best_c:
        return "faster"
    if best_cairn > best_c:
        return "slower"
    return "tie"


def benchmark_case(case: Case, compiler: Optional[str], rounds: int,
                   folder: Path) -> Dict[str, object]:
    """Measure one case in Cairn and, when available, in C."""
    cairn_source = ROOT / case.cairn
    cairn_exe = folder / f"{case.name}.cairn.exe"
    report: Dict[str, object] = {
        "case": case.name, "unit": case.unit, "iterations": case.iterations,
        "cairn": {}, "c": None, "c_compiler": compiler, "verdict": "unavailable",
    }
    compile_ms = build_cairn(cairn_source, cairn_exe)
    cairn = summarise(measure(cairn_exe, rounds), case.iterations)
    cairn["compile_ms"] = round(compile_ms, 2)
    cairn["image_bytes"] = cairn_exe.stat().st_size
    report["cairn"] = cairn
    if compiler is None:
        return report
    c_exe = folder / f"{case.name}.c.exe"
    c_compile = build_c(compiler, ROOT / case.c_source, c_exe)
    if c_compile is None:
        return report
    baseline = summarise(measure(c_exe, rounds), case.iterations)
    best_cairn = float(cairn["best_ms"])
    best_c = float(baseline["best_ms"])
    baseline["compile_ms"] = round(c_compile, 2)
    baseline["ratio_to_c"] = round(best_cairn / best_c, 3) if best_c else 0.0
    report["c"] = baseline
    report["verdict"] = verdict(best_cairn, best_c)
    return report


def render(reports: List[Dict[str, object]], compiler: Optional[str]) -> str:
    """The human table; one line per case and per measured language."""
    lines = ["case        language  best ms  median ms  compile ms  ns/unit  size",
             "----------  --------  -------  ---------  ----------  -------  ----"]
    for report in reports:
        for language, key in (("cairn", "cairn"), ("c", "c")):
            data = report.get(key)
            if not isinstance(data, dict):
                continue
            lines.append(
                f"{str(report['case']):10s}  {language:8s}  {data['best_ms']:7.2f}  "
                f"{data['median_ms']:9.2f}  {data.get('compile_ms', 0.0):10.2f}  "
                f"{data['ns_per_unit']:7.3f}  {data.get('image_bytes', '-')}")
    lines.append("")
    lines.append(f"C baseline: {compiler}" if compiler
                 else "C baseline: unavailable (no gcc, clang or cl on PATH)")
    ratios = [float(data["ratio_to_c"]) for report in reports
              for data in [report.get("c")] if isinstance(data, dict) and data.get("ratio_to_c")]
    if ratios:
        lines.append(f"Cairn/C best-time ratio: {min(ratios):.2f} to {max(ratios):.2f} "
                     f"(1.00 means equal)")
    verdicts = [str(report.get("verdict")) for report in reports]
    if "unavailable" in verdicts:
        lines.append("Goal: faster than C -> unavailable (install gcc, clang or cl to measure)")
    elif all(item in ("faster", "tie") for item in verdicts):
        lines.append("Goal: faster than C -> measured on every case in this run")
    else:
        slower = [str(report["case"]) for report in reports
                  if report.get("verdict") == "slower"]
        lines.append(f"Goal: faster than C -> not met on {', '.join(slower)}")
    return "\n".join(lines)


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
        description="Measure Cairn executables, and a C baseline when a C compiler is present.")
    parser.add_argument("--rounds", type=_number, default=DEFAULT_ROUNDS,
                        help=f"runs per executable (default: {DEFAULT_ROUNDS})")
    parser.add_argument("--json", action="store_true", help="print one JSON report object")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 unless Cairn is at least as fast as C on every case; "
                             "exit 2 when no C compiler can be measured")
    arguments = parser.parse_args(argv)
    compiler = find_c_compiler()
    with tempfile.TemporaryDirectory(prefix="cairn-bench-") as folder:
        reports = [benchmark_case(case, compiler, arguments.rounds, Path(folder))
                   for case in CASES]
    verdicts = [str(report.get("verdict")) for report in reports]
    met = all(item in ("faster", "tie") for item in verdicts)
    if arguments.json:
        print(json.dumps({"compiler": compiler, "rounds": arguments.rounds,
                          "goal": {"faster_than_c": met if "unavailable" not in verdicts else None,
                                   "verdicts": verdicts},
                          "cases": reports}, indent=2))
    else:
        print(render(reports, compiler))
    if not arguments.strict:
        return EXIT_OK
    if "unavailable" in verdicts:
        return EXIT_NO_BASELINE
    return EXIT_OK if met else EXIT_SLOWER


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))



