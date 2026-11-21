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
from typing import Callable, Dict, List, Optional, Tuple

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


def measure_interleaved(timers: Dict[str, Callable[[], float]],
                        rounds: int) -> Dict[str, List[float]]:
    """Sample every timer in alternating order across the same rounds.

    Returning one sample per key per round keeps the control and all baselines in
    a single wall-clock window: a load spike hits a whole round's samples, so it
    cancels in every ratio instead of separating the control band from a baseline
    measured in a later phase. A key whose run raises TimeoutExpired is dropped
    from further sampling and from the result, so one stalled baseline reads as
    unavailable instead of discarding the case's other verdicts (ADR-113).
    """
    names = list(timers)
    samples: Dict[str, List[float]] = {name: [] for name in names}
    for index in range(rounds):
        order = names if index % 2 == 0 else list(reversed(names))
        for name in order:
            if name not in samples:
                continue
            try:
                samples[name].append(timers[name]())
            except subprocess.TimeoutExpired:
                del samples[name]
    return samples


def verdicts_from_samples(samples: Dict[str, List[float]],
                          baselines: Tuple[str, ...] = ("c", "cpp", "rust")
                          ) -> Tuple[Optional[Tuple[float, float]],
                                     Dict[str, Tuple[float, str]]]:
    """Derive the control band and baseline verdicts from interleaved samples.

    Without a complete Cairn/copy control pair no band exists, so the case reads
    as unavailable (``None`` and no gaps) instead of producing a verdict the
    instrument cannot support. A baseline missing from the samples, or holding a
    different sample count because it was dropped, is skipped the same way.
    """
    cairn = samples.get("cairn")
    copy = samples.get("copy")
    if not cairn or not copy or len(cairn) != len(copy):
        return None, {}
    control = [c / co for c, co in zip(cairn, copy)]
    control_band = band(control)
    gaps: Dict[str, Tuple[float, str]] = {}
    for name in baselines:
        values = samples.get(name)
        if not values or len(values) != len(cairn):
            continue
        ratios = [c / b for c, b in zip(cairn, values)]
        measured = statistics.median(ratios)
        gaps[name] = (round(measured, 3), verdict(measured, control_band))
    return control_band, gaps


def measure_case(case, folder: Path, rounds: int = ROUNDS) -> Dict[str, object]:
    """Build every twin for one case and measure all gaps in one interleaved window."""
    cairn = folder / f"{case.name}.cairn.exe"
    harness.build_cairn(harness.ROOT / case.cairn, cairn)
    copy = folder / f"{case.name}.copy.exe"
    shutil.copyfile(cairn, copy)
    timers: Dict[str, Callable[[], float]] = {
        "cairn": lambda: timed(cairn, case.stdin)[0],
        "copy": lambda: timed(copy, case.stdin)[0],
    }
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
        timers[language] = lambda exe=other: timed(exe, case.stdin)[0]
    control_band, gaps = verdicts_from_samples(measure_interleaved(timers, rounds))
    if control_band is None:
        return {"case": case.name, "control": None, "gaps": {}}
    return {"case": case.name,
            "control": (round(control_band[0], 3), round(control_band[1], 3)),
            "gaps": gaps}


def main(argv=None) -> int:
    rounds = int(argv[0]) if argv else ROUNDS
    reports: List[Dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="cairn-paired-") as folder:
        for case in harness.CASES:
            report = measure_case(case, Path(folder), rounds)
            reports.append(report)
            gaps = " ".join(f"{name}={value:.3f}:{kind}"
                            for name, (value, kind) in report["gaps"].items())
            control = ("unavailable (a Cairn run timed out)"
                       if report["control"] is None else str(report["control"]))
            print(f"{report['case']:12s} control={control} {gaps}".rstrip(),
                  flush=True)
    for language in ("c", "cpp", "rust"):
        losses = [report["case"] for report in reports
                  if language in report["gaps"]
                  and report["gaps"][language][1] == "slower"]
        print(f"faster than {language}: "
              f"{'ALL WON' if not losses else 'not won on ' + ', '.join(losses)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
