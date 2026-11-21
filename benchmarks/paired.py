"""Deterministic paired A/B verdict for the benchmark registry.

A single whole-process run cannot arbitrate small gaps on a loaded host: two
consecutive quiet-window runs of run_benchmarks.py disagreed on byte-identical
builds. This tool measures Cairn and each baseline in the *same* interleaved
session and reports a control band from two copies of the Cairn binary. A case
counts as won only when its gap to a baseline is outside that band.
"""
from __future__ import annotations

import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks import run_benchmarks as harness

ROUNDS = 16


def timed(executable: Path, stdin: Optional[bytes]) -> Tuple[float, bytes]:
    """One wall-clock run; returns milliseconds and stdout for parity checks."""
    started = time.perf_counter()
    completed = subprocess.run([str(executable)], capture_output=True, input=stdin,
                               timeout=120)
    if completed.returncode != 0:
        raise RuntimeError(f"{executable.name} exited {completed.returncode}")
    return (time.perf_counter() - started) * 1000, completed.stdout


def gap(left: Path, right: Path, rounds: int = ROUNDS,
        stdin: Optional[bytes] = None) -> Tuple[float, List[float]]:
    """Median ratio left/right and its samples (lower = left faster)."""
    samples = []
    for index in range(rounds):
        pair = [(left, 0), (right, 1)]
        if index % 2:
            pair.reverse()
        values = {}
        for executable, key in pair:
            values[key] = timed(executable, stdin)[0]
        samples.append(values[0] / values[1])
    return statistics.median(samples), samples


def band(samples: List[float]) -> Tuple[float, float]:
    """The central 80 percent of an identical-binary control run."""
    ordered = sorted(samples)
    trim = max(1, len(ordered) // 10)
    return ordered[trim], ordered[-trim - 1]


def verdict(gap_value: float, control_band: Tuple[float, float]) -> str:
    """Win only when the gap is clearly outside the control band."""
    low, high = control_band
    if gap_value < low:
        return "faster"
    if gap_value > high:
        return "slower"
    return "tie"


def measure_case(case, folder: Path, rounds: int = ROUNDS) -> Dict[str, object]:
    """Build every twin for one case and measure its gaps with a control band."""
    cairn = folder / f"{case.name}.cairn.exe"
    harness.build_cairn(harness.ROOT / case.cairn, cairn)
    copy = folder / f"{case.name}.copy.exe"
    shutil.copyfile(cairn, copy)
    _, control_samples = gap(cairn, copy, rounds, case.stdin)
    control_band = band(control_samples)
    report: Dict[str, object] = {"case": case.name,
                                 "control": (round(control_band[0], 3),
                                             round(control_band[1], 3)),
                                 "gaps": {}}
    builders = {"c": (harness.build_c, harness.find_c_compiler()),
                "cpp": (harness.build_cpp, harness.find_cpp_compiler()),
                "rust": (harness.build_rust, harness.find_rust_compiler())}
    for language, source in (("c", case.c_source), ("cpp", case.cpp_source),
                             ("rust", case.rust_source)):
        builder, compiler = builders[language]
        if not compiler or not source:
            continue
        other = folder / f"{case.name}.{language}.exe"
        if builder(compiler, harness.ROOT / source, other) is None:
            continue
        measured, _ = gap(cairn, other, rounds, case.stdin)
        report["gaps"][language] = (round(measured, 3),
                                    verdict(measured, control_band))
    return report


def main(argv=None) -> int:
    rounds = int(argv[0]) if argv else ROUNDS
    reports: List[Dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="cairn-paired-") as folder:
        for case in harness.CASES:
            report = measure_case(case, Path(folder), rounds)
            reports.append(report)
            gaps = " ".join(f"{name}={value:.3f}:{kind}"
                            for name, (value, kind) in report["gaps"].items())
            print(f"{report['case']:12s} control={report['control']} {gaps}", flush=True)
    for language in ("c", "cpp", "rust"):
        losses = [report["case"] for report in reports
                  if language in report["gaps"]
                  and report["gaps"][language][1] == "slower"]
        print(f"faster than {language}: "
              f"{'ALL WON' if not losses else 'not won on ' + ', '.join(losses)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
