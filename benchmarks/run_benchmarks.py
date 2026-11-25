"""Benchmark harness: measure Cairn executables and, when one exists, C, C++ and Rust baselines.

The harness compiles each case with the compiler in this repository and runs the produced
executable several times, reporting the best and median wall clock. A C, C++ or Rust compiler
is used only when it is already on `PATH`; when none is found the report says so instead of
guessing, because an invented comparison would be worse than no comparison.

Usage:
  python benchmarks/run_benchmarks.py              # human table
  python benchmarks/run_benchmarks.py --json       # machine-readable report
  python benchmarks/run_benchmarks.py --rounds 7   # more samples per case
  python benchmarks/run_benchmarks.py --save       # append the run to the history file
  python benchmarks/run_benchmarks.py --save --compare   # diff against the newest saved
                                                   # run; exit 1 on a Cairn regression
                                                   # beyond the noise band
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
# Run-to-run spread on this machine is about 10 percent (README); a comparison only
# flags a change in the Cairn numbers that is bigger than this band.
NOISE_BAND = 0.10
HISTORY = ROOT / ".aikit" / "benchmark_history.jsonl"
# ADR-069's quiet-window probe: a saved run whose `many_params` best is above this
# threshold was measured while the machine was loaded, and a flag against it may be
# window noise rather than a regression. The field rides every record so the
# decision is auditable later.
QUIET_PROBE_CASE = "many_params"
QUIET_PROBE_MS = 20.0
# A mid-run stall inflates every language for a case. A case whose Cairn and C bests
# both more than double against a quiet record was measured through such an
# interruption, and saving it would poison the baseline the next run compares against.
STALL_RATIO = 1.5

C_COMPILERS: Dict[str, List[str]] = {
    "gcc": ["gcc", "-O2", "-o"],
    "clang": ["clang", "-O2", "-o"],
    "cl": ["cl", "/O2", "/nologo"],
}

RUST_COMPILERS: Dict[str, List[str]] = {
    "rustc": ["rustc", "-O", "-o"],
}

CPP_COMPILERS: Dict[str, List[str]] = {
    "g++": ["g++", "-O2", "-o"],
    "clang++": ["clang++", "-O2", "-o"],
    "cl": ["cl", "/O2", "/nologo", "/EHsc"],
}


@dataclass(frozen=True)
class Case:
    """One benchmark: a Cairn source, equivalent C, C++ and Rust sources, and its unit."""

    name: str
    cairn: str
    c_source: str
    rust_source: str
    cpp_source: str = ""
    iterations: int = 0
    unit: str = ""
    # Piped stdin for the run, for the cases whose workload is an input stream.
    stdin: Optional[bytes] = None


CASES: Tuple[Case, ...] = (
    Case("xorshift", "benchmarks/xorshift.crn", "benchmarks/xorshift.c",
         "benchmarks/xorshift.rs", "benchmarks/xorshift.cpp", 200_000_000, "iterations"),
    Case("fib30", "benchmarks/fib30.crn", "benchmarks/fib30.c", "benchmarks/fib30.rs", "benchmarks/fib30.cpp",
         2_692_537, "calls"),
    Case("folded_math", "benchmarks/folded_math.crn", "benchmarks/folded_math.c",
         "benchmarks/folded_math.rs", "benchmarks/folded_math.cpp", 20_000_000, "iterations"),
    Case("collatz", "benchmarks/collatz.crn", "benchmarks/collatz.c",
         "benchmarks/collatz.rs", "benchmarks/collatz.cpp", 1_000_000, "probes"),
    Case("ackermann", "benchmarks/ackermann.crn", "benchmarks/ackermann.c",
         "benchmarks/ackermann.rs", "benchmarks/ackermann.cpp", 1, "calls"),
    Case("nqueens", "benchmarks/nqueens.crn", "benchmarks/nqueens.c",
         "benchmarks/nqueens.rs", "benchmarks/nqueens.cpp", 1, "solutions"),
    Case("binary_gcd", "benchmarks/binary_gcd.crn", "benchmarks/binary_gcd.c",
         "benchmarks/binary_gcd.rs", "benchmarks/binary_gcd.cpp", 1_000_000, "pairs"),
    Case("call_chain", "benchmarks/call_chain.crn", "benchmarks/call_chain.c",
         "benchmarks/call_chain.rs", "benchmarks/call_chain.cpp", 20_000_000, "calls"),
    Case("tak", "benchmarks/tak.crn", "benchmarks/tak.c",
         "benchmarks/tak.rs", "benchmarks/tak.cpp", 63_609, "calls"),
    Case("primes", "benchmarks/primes.crn", "benchmarks/primes.c",
         "benchmarks/primes.rs", "benchmarks/primes.cpp", 300_000, "candidates"),
    Case("mandelbrot", "benchmarks/mandelbrot.crn", "benchmarks/mandelbrot.c",
         "benchmarks/mandelbrot.rs", "benchmarks/mandelbrot.cpp", 2_073_600, "pixels"),
    Case("splitmix", "benchmarks/splitmix.crn", "benchmarks/splitmix.c",
         "benchmarks/splitmix.rs", "benchmarks/splitmix.cpp", 100_000_000, "mixes"),
    Case("hex_io", "benchmarks/hex_io.crn", "benchmarks/hex_io.c",
         "benchmarks/hex_io.rs", "benchmarks/hex_io.cpp", 400_000, "values"),
    Case("many_params", "benchmarks/many_params.crn", "benchmarks/many_params.c",
         "benchmarks/many_params.rs", "benchmarks/many_params.cpp", 10_000_000, "calls"),
    Case("argv_walk", "benchmarks/argv_walk.crn", "benchmarks/argv_walk.c",
         "benchmarks/argv_walk.rs", "benchmarks/argv_walk.cpp", 12_800_000, "reads"),
    Case("char_stream", "benchmarks/char_stream.crn", "benchmarks/char_stream.c",
         "benchmarks/char_stream.rs", "benchmarks/char_stream.cpp", 4_194_304, "bytes",
         # 0x1A is text-mode EOF on the Windows CRT: the C and C++ getchar twins
         # would stop after the 26th byte and the scoreboard would compare a
         # 26-byte read against Cairn's full 4 MiB stream.
         (bytes(i for i in range(256) if i != 0x1A) * 16_500)[:4_194_304]),
    Case("str_scan", "benchmarks/str_scan.crn", "benchmarks/str_scan.c",
         "benchmarks/str_scan.rs", "benchmarks/str_scan.cpp", 12_800_000, "reads"),
    Case("array_sum", "benchmarks/array_sum.crn", "benchmarks/array_sum.c",
         "benchmarks/array_sum.rs", "benchmarks/array_sum.cpp", 6_619_136, "elements"),
    Case("sieve", "benchmarks/sieve.crn", "benchmarks/sieve.c",
         "benchmarks/sieve.rs", "benchmarks/sieve.cpp", 65_536, "candidates"),
    Case("popcount", "benchmarks/popcount.crn", "benchmarks/popcount.c",
         "benchmarks/popcount.rs", "benchmarks/popcount.cpp", 10_000_000, "values"),
    Case("clz", "benchmarks/clz.crn", "benchmarks/clz.c",
         "benchmarks/clz.rs", "benchmarks/clz.cpp", 10_000_000, "values"),
    Case("ctz", "benchmarks/ctz.crn", "benchmarks/ctz.c",
         "benchmarks/ctz.rs", "benchmarks/ctz.cpp", 10_000_000, "values"),
    Case("bswap", "benchmarks/bswap.crn", "benchmarks/bswap.c",
         "benchmarks/bswap.rs", "benchmarks/bswap.cpp", 10_000_000, "values"),
    Case("rotl", "benchmarks/rotl.crn", "benchmarks/rotl.c",
         "benchmarks/rotl.rs", "benchmarks/rotl.cpp", 10_000_000, "values"),
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


def find_cpp_compiler() -> Optional[str]:
    """The first C++ compiler already installed, or None when the machine has none."""
    for name in CPP_COMPILERS:
        if shutil.which(name):
            return name
    return None


def decode_output(raw: bytes) -> str:
    """Decode captured bytes with the universal newlines of a text-mode run.

    The stdin branch must observe the same stdout text as the text-mode branch,
    or a CRLF-writing twin would diverge from a raw-write twin on any platform
    whose C runtime translates newlines (ADR-112).
    """
    text = raw.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def run_command(argv: Sequence[str], timeout: float,
                cwd: Optional[Path] = None,
                stdin: Optional[bytes] = None) -> subprocess.CompletedProcess:
    """Run one command with a hard limit, capturing text."""
    if stdin is None:
        return subprocess.run(list(argv), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              check=False, cwd=cwd)
    raw = subprocess.run(list(argv), capture_output=True, input=stdin,
                         timeout=timeout, check=False, cwd=cwd)
    return subprocess.CompletedProcess(raw.args, raw.returncode,
                                       decode_output(raw.stdout),
                                       decode_output(raw.stderr))


def cairn_build_command(source: Path, destination: Path, optimise: bool = True) -> List[str]:
    """The exact Rust `cairn-rs buildexe` command for one case."""
    binary = ROOT / "rust" / "target" / "debug" / "cairn-rs.exe"
    if not binary.is_file():
        binary = binary.with_suffix("")
    argv = [str(binary), "buildexe", str(source), str(destination)]
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


def build_cpp(compiler: str, source: Path, destination: Path) -> Optional[float]:
    """Compile one C++ case; None when this compiler does not produce an executable."""
    template = CPP_COMPILERS[compiler]
    if compiler == "cl":
        argv = [template[0], template[1], template[2], template[3], f"/Fe:{destination}",
                str(source)]
    else:
        argv = [template[0], template[1], template[2], str(destination), str(source)]
    started = time.perf_counter()
    completed = run_command(argv, BUILD_TIMEOUT, cwd=ROOT)
    if completed.returncode != 0 or not destination.is_file():
        return None
    return (time.perf_counter() - started) * 1000.0


def measure(executable: Path, rounds: int, stdin: Optional[bytes] = None) -> List[float]:
    """Wall-clock milliseconds for each run of one executable."""
    samples: List[float] = []
    for _ in range(rounds):
        started = time.perf_counter()
        completed = run_command([str(executable)], RUN_TIMEOUT, stdin=stdin)
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


FLOOR_SOURCES = {
    "cairn": "fn main() -> i64 { print(0); return 0; }\n",
    "c": "#include <stdio.h>\nint main(void) { printf(\"0\"); return 0; }\n",
    "cpp": "#include <iostream>\nint main() { std::cout << \"0\"; return 0; }\n",
    "rust": "fn main() { print!(\"0\"); }\n",
}


def measure_floors(folder: Path, compiler: Optional[str], cpp_compiler: Optional[str],
                   rust_compiler: Optional[str], rounds: int,
                   optimise: bool = True) -> Dict[str, float]:
    """Each language's startup floor, in ms: an empty program's best time.

    Every case is scored on whole-process time, and a minimal program already
    costs 5-17M cycles depending on its runtime's own startup (ADR-084). Measuring
    the floor lets a codegen change be judged on compute instead of on a total the
    loader dominates, without changing the scoreboard itself.
    """
    floors: Dict[str, float] = {}
    cairn_source = folder / "floor.crn"
    cairn_source.write_text(FLOOR_SOURCES["cairn"], encoding="utf-8")
    cairn_exe = folder / "floor.cairn.exe"
    build_cairn(cairn_source, cairn_exe, optimise)
    floors["cairn"] = min(measure(cairn_exe, rounds))
    builders = (("c", compiler, build_c, "floor.c"),
                ("cpp", cpp_compiler, build_cpp, "floor.cpp"),
                ("rust", rust_compiler, build_rust, "floor.rs"))
    for language, found, builder, filename in builders:
        if not found:
            continue
        source = folder / filename
        source.write_text(FLOOR_SOURCES[language], encoding="utf-8")
        exe = folder / f"floor.{language}.exe"
        if builder(found, source, exe) is not None:
            floors[language] = min(measure(exe, rounds))
    return floors


def compute_adjusted(reports: List[Dict[str, object]],
                     floors: Dict[str, float]) -> List[str]:
    """One line per case: each language's best minus that language's startup floor.

    Diagnostic only - the scoreboard and every verdict stay on whole-process best
    times, because that is what a user waits for. On a short case the loader's
    share is most of the number, so this column is where a codegen change shows.
    """
    lines = []
    for report in reports:
        parts = []
        for language in ("cairn", "c", "cpp", "rust"):
            data = report.get(language)
            floor = floors.get(language)
            if not isinstance(data, dict) or floor is None:
                continue
            parts.append(f"{language} {max(float(data['best_ms']) - floor, 0.0):.2f}")
        if parts:
            lines.append(f"{str(report['case']):12s} " + "  ".join(parts) + " ms")
    return lines


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


def compare_runs(current: Dict[str, Dict[str, float]], previous: Dict[str, Dict[str, float]],
                 noise: float = NOISE_BAND) -> Tuple[List[str], bool]:
    """Per-case best-time deltas against a saved run; (readable lines, any regression).

    Only the Cairn numbers gate: a previous run's C or Rust readings cannot regress
    from a change to this repository, so a `noise`-banded look at the Cairn column is
    what a regression is. A case the previous run never measured is reported as `new`,
    and a case the current run is missing is simply not claimed.
    """
    lines = []
    regressed = False
    for case, now in current.get("cairn", {}).items():
        was = previous.get("cairn", {}).get(case)
        if was is None or was <= 0:
            lines.append(f"{case}: new (no earlier measurement to compare)")
            continue
        delta = (float(now) - float(was)) / float(was)
        band = noise * float(was)
        if float(now) - float(was) > band:
            state = "regressed"
            regressed = True
        elif float(was) - float(now) > band:
            state = "improved"
        else:
            state = "within noise"
        lines.append(f"{case}: {was:.1f} -> {now:.1f} ms  {delta:+.1%}  {state}")
    return lines, regressed


def window_note(record: Optional[Dict[str, object]]) -> Optional[str]:
    """A warning when a saved record was measured in a loaded window, else None."""
    if not isinstance(record, dict) or record.get("quiet") is not False:
        return None
    probe = record.get("probe_ms")
    measured = f" (probe {probe:.1f} ms)" if isinstance(probe, (int, float)) else ""
    return ("warning: the saved baseline was measured in a loaded window"
            f"{measured}, above the {QUIET_PROBE_MS:.0f} ms criterion; a flagged case may be"
            " window noise - verify byte identity before treating it as a regression")


def latest_history_record(path: Path) -> Optional[Dict[str, object]]:
    """The newest valid JSONL record in the history file, or None without one.

    A corrupted trailing line is skipped over, never allowed to hide the last good
    run: partial writes and hand edits must not turn the watch off.
    """
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as history:
        lines = history.readlines()
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            return record
    return None


def record_from_reports(reports: List[Dict[str, object]], compiler: Optional[str],
                        rust_compiler: Optional[str], rounds: int, optimise: bool,
                        cpp_compiler: Optional[str] = None) -> Dict[str, object]:
    """One history record: the commit this run measured and each best time by language."""
    def bests(key: str) -> Dict[str, float]:
        table: Dict[str, float] = {}
        for report in reports:
            data = report.get(key)
            if isinstance(data, dict) and data.get("best_ms") is not None:
                table[str(report["case"])] = float(data["best_ms"])
        return table

    commit = None
    git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                         text=True, timeout=10, cwd=str(ROOT))
    if git.returncode == 0:
        commit = git.stdout.strip() or None
    cairn = bests("cairn")
    probe = cairn.get(QUIET_PROBE_CASE)
    return {"timestamp": round(time.time(), 3), "commit": commit, "rounds": rounds,
            "optimise": optimise, "c_compiler": compiler, "rust_compiler": rust_compiler,
            "cpp_compiler": cpp_compiler,
            "probe_ms": None if probe is None else round(float(probe), 2),
            "quiet": None if probe is None else bool(probe <= QUIET_PROBE_MS),
            "cairn": cairn, "c": bests("c"), "cpp": bests("cpp"),
            "rust": bests("rust")}


def stalled_cases(record: Dict[str, object],
                  previous: Optional[Dict[str, object]]) -> List[str]:
    """Cases where any language's best blew up against a quiet previous record.

    The C, C++ and Rust twins are compiled from unchanged sources, so a best time above
    the ratio for any of them is a disturbed window, never a repository change. Cairn is
    held to the same bound because a run that slow would also poison the baseline; the
    printed report still shows the numbers, and `--compare` still flags the case.
    """
    if not isinstance(previous, dict) or previous.get("quiet") is False:
        return []
    stalled: List[str] = []
    for language in ("cairn", "c", "cpp", "rust"):
        current = record.get(language) or {}
        before = previous.get(language) or {}
        for name, best in current.items():
            old = before.get(name)
            if not (isinstance(best, (int, float)) and isinstance(old, (int, float))
                    and old > 0 and best > old * STALL_RATIO):
                continue
            label = str(name)
            if label not in stalled:
                stalled.append(label)
    return stalled


def save_record(path: Path, record: Dict[str, object]) -> None:
    """Append one record as a single JSONL line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as history:
        history.write(json.dumps(record) + "\n")


def benchmark_case(case: Case, compiler: Optional[str], rounds: int, folder: Path,
                   optimise: bool = True, rust_compiler: Optional[str] = None,
                   cpp_compiler: Optional[str] = None) -> Dict[str, object]:
    """Measure one case in Cairn and, when available, in C, C++ and Rust."""
    cairn_source = ROOT / case.cairn
    cairn_exe = folder / f"{case.name}.cairn.exe"
    report: Dict[str, object] = {
        "case": case.name, "unit": case.unit, "iterations": case.iterations,
        "cairn": None, "c": None, "cpp": None, "rust": None,
        "c_compiler": compiler, "rust_compiler": rust_compiler,
        "cpp_compiler": cpp_compiler,
        "verdict": "unavailable", "verdict_rust": "unavailable",
        "verdict_cpp": "unavailable", "error": None,
    }
    try:
        compile_ms = build_cairn(cairn_source, cairn_exe, optimise)
        cairn = summarise(measure(cairn_exe, rounds, case.stdin), case.iterations)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as failure:
        report["verdict"] = "error"
        report["error"] = str(failure)
        return report
    cairn["compile_ms"] = round(compile_ms, 2)
    cairn["image_bytes"] = cairn_exe.stat().st_size
    report["cairn"] = cairn
    best_cairn = float(cairn["best_ms"])
    for language, baseline_compiler, build, source_rel, exe_name, verdict_key, ratio_key in (
            ("c", compiler, build_c, case.c_source, f"{case.name}.c.exe",
             "verdict", "ratio_to_c"),
            ("cpp", cpp_compiler, build_cpp, case.cpp_source,
             f"{case.name}.cpp.exe", "verdict_cpp", "ratio_to_cpp"),
            ("rust", rust_compiler, build_rust, case.rust_source,
             f"{case.name}.rust.exe", "verdict_rust", "ratio_to_rust")):
        if baseline_compiler is None or not source_rel:
            continue
        source_path = ROOT / source_rel
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
            baseline = summarise(measure(exe, rounds, case.stdin), case.iterations)
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
           optimise: bool = True, rust_compiler: Optional[str] = None,
           cpp_compiler: Optional[str] = None) -> str:
    """The human table; one line per case and per measured language."""
    languages = [("cairn", "cairn"), ("c", "c"), ("cpp", "cpp"), ("rust", "rust")]
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
    lines.append(f"C++ baseline: {cpp_compiler}" if cpp_compiler
                 else "C++ baseline: unavailable (no g++, clang++ or cl on PATH)")
    ratios = [float(data["ratio_to_c"]) for report in reports
              for data in [report.get("c")] if isinstance(data, dict) and data.get("ratio_to_c")]
    if ratios:
        lines.append(f"Cairn/C best-time ratio: {min(ratios):.2f} to {max(ratios):.2f} "
                     f"(1.00 means equal)")
    cpp_ratios = [float(data["ratio_to_cpp"]) for report in reports
                  for data in [report.get("cpp")]
                  if isinstance(data, dict) and data.get("ratio_to_cpp")]
    if cpp_ratios:
        lines.append(f"Cairn/C++ best-time ratio: {min(cpp_ratios):.2f} to "
                     f"{max(cpp_ratios):.2f} (1.00 means equal)")
    rust_ratios = [float(data["ratio_to_rust"]) for report in reports
                   for data in [report.get("rust")]
                   if isinstance(data, dict) and data.get("ratio_to_rust")]
    if rust_ratios:
        lines.append(f"Cairn/Rust best-time ratio: {min(rust_ratios):.2f} to "
                     f"{max(rust_ratios):.2f} (1.00 means equal)")
    lines.extend(_goal_lines(reports, "C", compiler, "verdict", "gcc, clang or cl"))
    lines.extend(_goal_lines(reports, "C++", cpp_compiler, "verdict_cpp",
                             "g++, clang++ or cl"))
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
        description="Measure Cairn executables, and C, C++ or Rust baselines when a "
                    "compiler for them is present.")
    parser.add_argument("--rounds", type=_number, default=DEFAULT_ROUNDS,
                        help=f"runs per executable (default: {DEFAULT_ROUNDS})")
    parser.add_argument("--json", action="store_true", help="print one JSON report object")
    parser.add_argument("--no-optimize", action="store_true",
                        help="build Cairn without the folding pass, to measure what it buys")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 unless Cairn is at least as fast as every measured "
                             "baseline on every case; exit 2 when no baseline can be measured")
    parser.add_argument("--save", action="store_true",
                        help="append this run's best times and commit to "
                             f"{HISTORY.relative_to(ROOT)}")
    parser.add_argument("--floors", action="store_true",
                        help="also measure each language's empty-program startup floor and "
                             "print a compute-adjusted (best minus floor) table; diagnostic "
                             "only, the scoreboard keeps whole-process times")
    parser.add_argument("--compare", action="store_true",
                        help="diff this run's Cairn best times against the newest saved "
                             "record and exit 1 on a regression beyond the noise band")
    arguments = parser.parse_args(argv)
    compiler = find_c_compiler()
    rust_compiler = find_rust_compiler()
    cpp_compiler = find_cpp_compiler()
    optimise = not arguments.no_optimize
    with tempfile.TemporaryDirectory(prefix="cairn-bench-") as folder:
        reports = [benchmark_case(case, compiler, arguments.rounds, Path(folder), optimise,
                                  rust_compiler=rust_compiler, cpp_compiler=cpp_compiler)
                   for case in CASES]
        floors = measure_floors(Path(folder), compiler, cpp_compiler, rust_compiler,
                                arguments.rounds, optimise) if arguments.floors else None
    verdicts = [str(report.get("verdict")) for report in reports]
    rust_verdicts = [str(report.get("verdict_rust")) for report in reports]
    cpp_verdicts = [str(report.get("verdict_cpp")) for report in reports]
    met_c = all(item in ("faster", "tie") for item in verdicts)
    met_rust = all(item in ("faster", "tie") for item in rust_verdicts)
    met_cpp = all(item in ("faster", "tie") for item in cpp_verdicts)
    met = (met_c if compiler is not None else True) and \
          (met_rust if rust_compiler is not None else True) and \
          (met_cpp if cpp_compiler is not None else True)
    if arguments.json:
        print(json.dumps({"compiler": compiler, "rust_compiler": rust_compiler,
                          "cpp_compiler": cpp_compiler,
                          "rounds": arguments.rounds, "optimise": optimise,
                          "goal": {"faster_than_c": met_c
                                   if "unavailable" not in verdicts else None,
                                   "verdicts": verdicts,
                                   "faster_than_rust": met_rust
                                   if "unavailable" not in rust_verdicts else None,
                                   "verdicts_rust": rust_verdicts,
                                   "faster_than_cpp": met_cpp
                                   if "unavailable" not in cpp_verdicts else None,
                                   "verdicts_cpp": cpp_verdicts},
                          "cases": reports}, indent=2))
    else:
        print(render(reports, compiler, optimise, rust_compiler, cpp_compiler))
        if floors:
            print()
            print("Startup floor (empty program best): "
                  + "  ".join(f"{language} {value:.2f} ms"
                              for language, value in floors.items()))
            print("Compute-adjusted (best minus that language's floor; diagnostic only, "
                  "the scoreboard is whole-process time):")
            for line in compute_adjusted(reports, floors):
                print(f"  {line}")
    previous = latest_history_record(HISTORY) if (arguments.compare or arguments.save) else None
    if arguments.save:
        record = record_from_reports(reports, compiler, rust_compiler,
                                     arguments.rounds, optimise, cpp_compiler)
        stalled = stalled_cases(record, previous)
        if stalled:
            print("[REFUSED] the window stalled mid-run on " + ", ".join(stalled)
                  + f" (a best above {STALL_RATIO:g}x the previous"
                  " quiet record); rerun with --save in a quiet window")
        else:
            save_record(HISTORY, record)
            print(f"[SAVED] {HISTORY.relative_to(ROOT)}")
        if record.get("quiet") is None:
            print(f"[WINDOW] no {QUIET_PROBE_CASE} reading; window state unknown")
        else:
            state = "quiet" if record["quiet"] else "loaded"
            print(f"[WINDOW] probe {record['probe_ms']:.1f} ms vs "
                  f"{QUIET_PROBE_MS:.0f} ms criterion: {state}")
    if arguments.compare:
        if previous is None:
            print("Compare: no earlier saved run; this --save run becomes the baseline.")
        else:
            lines, regressed = compare_runs(
                record_from_reports(reports, compiler, rust_compiler,
                                    arguments.rounds, optimise, cpp_compiler), previous,
                noise=NOISE_BAND)
            print("Compare against the newest saved run"
                  + (f" (commit {previous.get('commit')})"
                     if previous.get("commit") else "") + ":")
            note = window_note(previous)
            if note:
                print(f"  {note}")
            for line in lines:
                print(f"  {line}")
            if regressed:
                return EXIT_SLOWER
    if not arguments.strict:
        return EXIT_OK
    if compiler is None and rust_compiler is None and cpp_compiler is None:
        return EXIT_NO_BASELINE
    return EXIT_OK if met and "error" not in verdicts + rust_verdicts + cpp_verdicts \
        else EXIT_SLOWER


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))



