"""Benchmark harness tests: summarising and rendering, without running a benchmark."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks import paired, run_benchmarks


class PairedVerdictTest(unittest.TestCase):
    """A win is a gap outside the identical-binary band, not a raw best time."""

    def test_an_identical_binary_is_a_tie(self) -> None:
        self.assertEqual(paired.verdict(1.002, (0.98, 1.02)), "tie")

    def test_a_gap_inside_the_band_is_a_tie(self) -> None:
        self.assertEqual(paired.verdict(0.95, (0.94, 1.06)), "tie")

    def test_a_clear_gap_below_the_band_is_a_win(self) -> None:
        self.assertEqual(paired.verdict(0.88, (0.98, 1.02)), "faster")

    def test_a_clear_gap_above_the_band_is_a_loss(self) -> None:
        self.assertEqual(paired.verdict(1.12, (0.98, 1.02)), "slower")

    def test_the_control_band_trims_extremes(self) -> None:
        self.assertEqual(paired.band([float(i) for i in range(1, 21)]), (3.0, 18.0))


class SummaryTest(unittest.TestCase):
    """The arithmetic an agent reads from a report must be reproducible by hand."""

    def test_summary_reports_best_median_worst_and_per_unit_cost(self) -> None:
        summary = run_benchmarks.summarise([300.0, 100.0, 200.0], 1_000_000)
        self.assertEqual(summary["best_ms"], 100.0)
        self.assertEqual(summary["median_ms"], 200.0)
        self.assertEqual(summary["worst_ms"], 300.0)
        self.assertEqual(summary["ns_per_unit"], 100.0)

    def test_zero_iterations_does_not_divide(self) -> None:
        self.assertEqual(run_benchmarks.summarise([5.0], 0)["ns_per_unit"], 0.0)

    def test_summarise_rejects_empty_samples(self) -> None:
        with self.assertRaises(ValueError):
            run_benchmarks.summarise([], 10)

    def test_every_case_declares_an_existing_cairn_and_c_source(self) -> None:
        for case in run_benchmarks.CASES:
            self.assertTrue((run_benchmarks.ROOT / case.cairn).is_file(), case.cairn)
            self.assertTrue((run_benchmarks.ROOT / case.c_source).is_file(), case.c_source)

    def test_every_case_declares_an_existing_rust_twin(self) -> None:
        for case in run_benchmarks.CASES:
            self.assertTrue(case.rust_source.endswith(".rs"), case.rust_source)
            self.assertTrue((run_benchmarks.ROOT / case.rust_source).is_file(),
                            case.rust_source)

    def test_every_case_declares_an_existing_cpp_twin(self) -> None:
        for case in run_benchmarks.CASES:
            self.assertTrue(case.cpp_source.endswith(".cpp"), case.cpp_source)
            self.assertTrue((run_benchmarks.ROOT / case.cpp_source).is_file(),
                            case.cpp_source)

    def test_cpp_report_keys_are_present_on_the_error_path(self) -> None:
        folder = Path(tempfile.mkdtemp(prefix="cairn-bench-test-"))
        self.addCleanup(lambda: [child.unlink(missing_ok=True) for child in
                                 sorted(folder.rglob("*"), reverse=True)] +
                        [folder.rmdir()])
        with mock.patch.object(run_benchmarks, "measure",
                               side_effect=RuntimeError("boom")):
            report = run_benchmarks.benchmark_case(run_benchmarks.CASES[0], None, 1,
                                                  folder)
        self.assertIn("cpp", report)
        self.assertIn("cpp_compiler", report)
        self.assertIn("verdict_cpp", report)
        self.assertEqual(report["cpp_compiler"], None)
        self.assertEqual(report["verdict_cpp"], "unavailable")

    def test_a_measured_cpp_baseline_records_verdict_and_ratio(self) -> None:
        folder = Path(tempfile.mkdtemp(prefix="cairn-bench-test-"))
        self.addCleanup(lambda: [child.unlink(missing_ok=True) for child in
                                 sorted(folder.rglob("*"), reverse=True)] +
                        [folder.rmdir()])

        def fake_run(argv, timeout, cwd=None):
            for index, item in enumerate(argv):
                if item == "-o" and index + 1 < len(argv):
                    Path(argv[index + 1]).write_bytes(b"placeholder")
            return mock.Mock(returncode=0, stderr="")

        case = run_benchmarks.CASES[0]
        with mock.patch.object(run_benchmarks, "run_command", side_effect=fake_run), \
                mock.patch.object(run_benchmarks, "measure",
                                  side_effect=[[1.0, 2.0], [2.0, 3.0], [2.0, 3.0]]):
            report = run_benchmarks.benchmark_case(case, "gcc", 2, folder,
                                                   rust_compiler=None,
                                                   cpp_compiler="g++")
        self.assertEqual(report["cpp_compiler"], "g++")
        self.assertEqual(report["verdict_cpp"], "faster")
        self.assertEqual(report["cpp"]["ratio_to_cpp"], 0.5)
        self.assertIn("cpp", run_benchmarks.render([report], "gcc"))

    def test_render_says_unavailable_when_there_is_no_cpp_compiler(self) -> None:
        report = {"case": "x", "unit": "iterations", "iterations": 10,
                  "cairn": run_benchmarks.summarise([1.0], 10), "c": None}
        text = run_benchmarks.render([report], None)
        self.assertIn("C++ baseline: unavailable", text)
        self.assertIn("no g++, clang++ or cl", text)
        self.assertIn("Goal: faster than C++ -> unavailable", text)

    def test_render_states_the_cpp_goal_for_each_outcome(self) -> None:
        def report(verdict_text: str) -> dict:
            return {"case": "x", "unit": "iterations", "iterations": 10,
                    "cairn": run_benchmarks.summarise([2.0], 10),
                    "c": {**run_benchmarks.summarise([1.0], 10), "ratio_to_c": 2.0},
                    "verdict": "faster",
                    "cpp": None if verdict_text == "unavailable" else
                    {**run_benchmarks.summarise([4.0], 10), "ratio_to_cpp": 0.5},
                    "verdict_cpp": verdict_text}

        self.assertIn("unavailable", run_benchmarks.render([report("unavailable")], None))
        self.assertIn("measured on every case",
                      run_benchmarks.render([report("faster")], "gcc", cpp_compiler="g++"))
        self.assertIn("not met on x",
                      run_benchmarks.render([report("slower")], "gcc", cpp_compiler="g++"))

    def test_rust_report_keys_are_present_on_the_error_path(self) -> None:
        folder = Path(tempfile.mkdtemp(prefix="cairn-bench-test-"))
        self.addCleanup(lambda: [child.unlink(missing_ok=True) for child in
                                 sorted(folder.rglob("*"), reverse=True)] +
                        [folder.rmdir()])
        with mock.patch.object(run_benchmarks, "measure",
                               side_effect=RuntimeError("boom")):
            report = run_benchmarks.benchmark_case(run_benchmarks.CASES[0], None, 1,
                                                  folder)
        self.assertIn("rust", report)
        self.assertIn("rust_compiler", report)
        self.assertIn("verdict_rust", report)
        self.assertEqual(report["rust_compiler"], None)
        self.assertEqual(report["verdict_rust"], "unavailable")

    def test_a_measured_rust_baseline_records_verdict_and_ratio(self) -> None:
        folder = Path(tempfile.mkdtemp(prefix="cairn-bench-test-"))
        self.addCleanup(lambda: [child.unlink(missing_ok=True) for child in
                                 sorted(folder.rglob("*"), reverse=True)] +
                        [folder.rmdir()])

        def fake_run(argv, timeout, cwd=None):
            for index, item in enumerate(argv):
                if item == "-o" and index + 1 < len(argv):
                    Path(argv[index + 1]).write_bytes(b"placeholder")
            return mock.Mock(returncode=0, stderr="")

        case = run_benchmarks.CASES[0]
        with mock.patch.object(run_benchmarks, "run_command", side_effect=fake_run), \
                mock.patch.object(run_benchmarks, "find_c_compiler",
                                  return_value="gcc"), \
                mock.patch.object(run_benchmarks, "measure",
                                  side_effect=[[1.0, 2.0], [2.0, 3.0], [1.5, 2.5]]):
            report = run_benchmarks.benchmark_case(case, "gcc", 2, folder,
                                                   rust_compiler="rustc")
        self.assertEqual(report["verdict"], "faster")
        self.assertEqual(report["rust_compiler"], "rustc")
        self.assertEqual(report["verdict_rust"], "faster")
        self.assertEqual(report["rust"]["ratio_to_rust"], 0.667)
        self.assertIn("rust", run_benchmarks.render([report], "gcc"))

    def test_render_says_unavailable_when_there_is_no_rust_compiler(self) -> None:
        report = {"case": "x", "unit": "iterations", "iterations": 10,
                  "cairn": run_benchmarks.summarise([1.0], 10), "c": None}
        text = run_benchmarks.render([report], None)
        self.assertIn("Rust baseline: unavailable", text)
        self.assertIn("no rustc", text)
        self.assertIn("Goal: faster than Rust -> unavailable", text)

    def test_render_states_the_rust_goal_for_each_outcome(self) -> None:
        def report(verdict_text: str) -> dict:
            return {"case": "x", "unit": "iterations", "iterations": 10,
                    "cairn": run_benchmarks.summarise([2.0], 10),
                    "c": {**run_benchmarks.summarise([1.0], 10), "ratio_to_c": 2.0},
                    "verdict": "faster",
                    "rust": None if verdict_text == "unavailable" else
                    {**run_benchmarks.summarise([3.0], 10), "ratio_to_rust": 0.667},
                    "verdict_rust": verdict_text}

        self.assertIn("Goal: faster than Rust -> measured on every case",
                      run_benchmarks.render([report("faster")], "gcc", rust_compiler="rustc"))
        self.assertIn("Goal: faster than Rust -> not met on x",
                      run_benchmarks.render([report("slower")], "gcc", rust_compiler="rustc"))
        self.assertIn("Goal: faster than Rust -> unavailable",
                      run_benchmarks.render([report("unavailable")], "gcc"))

    def test_strict_exits_2_when_no_baseline_can_be_measured(self) -> None:
        with mock.patch.object(run_benchmarks, "find_c_compiler", return_value=None), \
                mock.patch.object(run_benchmarks, "find_rust_compiler", return_value=None), \
                mock.patch.object(run_benchmarks, "find_cpp_compiler", return_value=None):
            self.assertEqual(run_benchmarks.main(["--strict"]), 2)

    def test_strict_passes_when_the_measured_baseline_is_won(self) -> None:
        report = {"case": "x", "unit": "iterations", "iterations": 10,
                  "cairn": run_benchmarks.summarise([1.0], 10),
                  "c": {**run_benchmarks.summarise([2.0], 10), "ratio_to_c": 0.5},
                  "verdict": "faster",
                  "rust": None, "rust_compiler": None, "verdict_rust": "unavailable",
                  "cpp": None, "cpp_compiler": None, "verdict_cpp": "unavailable",
                  "error": None}
        with mock.patch.object(run_benchmarks, "find_c_compiler", return_value="gcc"), \
                mock.patch.object(run_benchmarks, "find_rust_compiler", return_value=None), \
                mock.patch.object(run_benchmarks, "find_cpp_compiler", return_value=None), \
                mock.patch.object(run_benchmarks, "benchmark_case", return_value=report), \
                mock.patch.object(run_benchmarks, "CASES", (object(),)):
            self.assertEqual(run_benchmarks.main(["--strict"]), 0)

    def test_strict_fails_when_rust_wins_a_case(self) -> None:
        report = {"case": "x", "unit": "iterations", "iterations": 10,
                  "cairn": run_benchmarks.summarise([1.0], 10),
                  "c": {**run_benchmarks.summarise([2.0], 10), "ratio_to_c": 0.5},
                  "verdict": "faster",
                  "rust": {**run_benchmarks.summarise([0.5], 10), "ratio_to_rust": 2.0},
                  "rust_compiler": "rustc", "verdict_rust": "slower",
                  "cpp": None, "cpp_compiler": None, "verdict_cpp": "unavailable",
                  "error": None}
        with mock.patch.object(run_benchmarks, "find_c_compiler", return_value="gcc"), \
                mock.patch.object(run_benchmarks, "find_rust_compiler", return_value="rustc"), \
                mock.patch.object(run_benchmarks, "find_cpp_compiler", return_value=None), \
                mock.patch.object(run_benchmarks, "benchmark_case", return_value=report), \
                mock.patch.object(run_benchmarks, "CASES", (object(),)):
            self.assertEqual(run_benchmarks.main(["--strict"]), 1)

    def test_strict_fails_when_cpp_wins_a_case(self) -> None:
        report = {"case": "x", "unit": "iterations", "iterations": 10,
                  "cairn": run_benchmarks.summarise([1.0], 10),
                  "c": {**run_benchmarks.summarise([2.0], 10), "ratio_to_c": 0.5},
                  "verdict": "faster",
                  "rust": None, "rust_compiler": None, "verdict_rust": "unavailable",
                  "cpp": {**run_benchmarks.summarise([0.5], 10), "ratio_to_cpp": 2.0},
                  "cpp_compiler": "g++", "verdict_cpp": "slower",
                  "error": None}
        with mock.patch.object(run_benchmarks, "find_c_compiler", return_value="gcc"), \
                mock.patch.object(run_benchmarks, "find_rust_compiler", return_value=None), \
                mock.patch.object(run_benchmarks, "find_cpp_compiler", return_value="g++"), \
                mock.patch.object(run_benchmarks, "benchmark_case", return_value=report), \
                mock.patch.object(run_benchmarks, "CASES", (object(),)):
            self.assertEqual(run_benchmarks.main(["--strict"]), 1)

    def test_json_goal_carries_both_baselines(self) -> None:
        report = {"case": "x", "unit": "iterations", "iterations": 10,
                  "cairn": run_benchmarks.summarise([1.0], 10),
                  "c": {**run_benchmarks.summarise([2.0], 10), "ratio_to_c": 0.5},
                  "verdict": "faster",
                  "rust": {**run_benchmarks.summarise([0.5], 10), "ratio_to_rust": 2.0},
                  "rust_compiler": "rustc", "verdict_rust": "slower",
                  "cpp": None, "cpp_compiler": None, "verdict_cpp": "unavailable",
                  "error": None}
        stdout = io.StringIO()
        with mock.patch.object(run_benchmarks, "find_c_compiler", return_value="gcc"), \
                mock.patch.object(run_benchmarks, "find_rust_compiler", return_value="rustc"), \
                mock.patch.object(run_benchmarks, "find_cpp_compiler", return_value=None), \
                mock.patch.object(run_benchmarks, "benchmark_case", return_value=report), \
                mock.patch.object(run_benchmarks, "CASES", (object(),)), \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(run_benchmarks.main(["--json"]), 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["goal"]["faster_than_c"], True)
        self.assertEqual(payload["goal"]["faster_than_rust"], False)
        self.assertIsNone(payload["goal"]["faster_than_cpp"])
        self.assertEqual(payload["compiler"], "gcc")
        self.assertEqual(payload["rust_compiler"], "rustc")
        self.assertEqual(payload["cpp_compiler"], None)

    def test_json_goal_reports_the_cpp_baseline_when_measured(self) -> None:
        report = {"case": "x", "unit": "iterations", "iterations": 10,
                  "cairn": run_benchmarks.summarise([1.0], 10),
                  "c": {**run_benchmarks.summarise([2.0], 10), "ratio_to_c": 0.5},
                  "verdict": "faster",
                  "rust": None, "rust_compiler": None, "verdict_rust": "unavailable",
                  "cpp": {**run_benchmarks.summarise([2.0], 10), "ratio_to_cpp": 0.5},
                  "cpp_compiler": "g++", "verdict_cpp": "faster",
                  "error": None}
        stdout = io.StringIO()
        with mock.patch.object(run_benchmarks, "find_c_compiler", return_value="gcc"), \
                mock.patch.object(run_benchmarks, "find_rust_compiler", return_value=None), \
                mock.patch.object(run_benchmarks, "find_cpp_compiler", return_value="g++"), \
                mock.patch.object(run_benchmarks, "benchmark_case", return_value=report), \
                mock.patch.object(run_benchmarks, "CASES", (object(),)), \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(run_benchmarks.main(["--json"]), 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["goal"]["faster_than_cpp"], True)
        self.assertEqual(payload["goal"]["verdicts_cpp"], ["faster"])
        self.assertEqual(payload["cpp_compiler"], "g++")

    def test_render_says_unavailable_when_there_is_no_c_compiler(self) -> None:
        report = {"case": "x", "unit": "iterations", "iterations": 10,
                  "cairn": run_benchmarks.summarise([1.0], 10), "c": None}
        text = run_benchmarks.render([report], None)
        self.assertIn("C baseline: unavailable", text)
        self.assertIn("cairn", text)

    def test_the_loop_case_is_a_serial_xorshift_workload(self) -> None:
        """The integer-loop case must be optimizer-resistant, so neither side may fold it."""
        entry = run_benchmarks.CASES[0]
        self.assertEqual(entry.name, "xorshift")
        for text in [(run_benchmarks.ROOT / entry.cairn).read_text(),
                     (run_benchmarks.ROOT / entry.c_source).read_text(),
                     (run_benchmarks.ROOT / entry.rust_source).read_text()]:
            self.assertIn("^", text)
            self.assertIn(">>", text)
            self.assertNotIn("gcc", text)

    def test_a_errored_case_names_the_case_dynamically(self) -> None:
        folder = Path(tempfile.mkdtemp(prefix="cairn-bench-test-"))
        self.addCleanup(lambda: [child.unlink(missing_ok=True) for child in
                                 sorted(folder.rglob("*"), reverse=True)] +
                        [folder.rmdir()])
        with mock.patch.object(run_benchmarks, "measure",
                               side_effect=RuntimeError("boom")):
            report = run_benchmarks.benchmark_case(run_benchmarks.CASES[0], None, 1,
                                                  folder)
        self.assertEqual(report["verdict"], "error")
        self.assertIn("boom", str(report["error"]))
        self.assertIn(f"errored on {run_benchmarks.CASES[0].name}",
                      run_benchmarks.render([report], None))

    def test_build_command_skips_folding_only_when_asked(self) -> None:
        source, destination = run_benchmarks.ROOT / "x.crn", run_benchmarks.ROOT / "x.exe"
        self.assertNotIn("--no-optimize",
                         run_benchmarks.cairn_build_command(source, destination))
        self.assertIn("--no-optimize",
                      run_benchmarks.cairn_build_command(source, destination, optimise=False))

    def test_render_names_an_unoptimised_build(self) -> None:
        report = {"case": "x", "unit": "iterations", "iterations": 10,
                  "cairn": run_benchmarks.summarise([1.0], 10), "c": None}
        self.assertIn("--no-optimize", run_benchmarks.render([report], None, optimise=False))


class VerdictTest(unittest.TestCase):
    """The `faster than C` goal is decided by measurements, never assumed."""

    def test_lower_best_time_wins(self) -> None:
        self.assertEqual(run_benchmarks.verdict(90.0, 100.0), "faster")
        self.assertEqual(run_benchmarks.verdict(110.0, 100.0), "slower")
        self.assertEqual(run_benchmarks.verdict(100.0, 100.0), "tie")

    def test_missing_baseline_is_unavailable_not_a_win(self) -> None:
        self.assertEqual(run_benchmarks.verdict(90.0, None), "unavailable")
        self.assertEqual(run_benchmarks.verdict(None, 90.0), "unavailable")
        self.assertEqual(run_benchmarks.verdict(90.0, 0.0), "unavailable")

    def test_render_states_the_goal_for_each_outcome(self) -> None:
        def report(verdict_text: str) -> dict:
            return {"case": "x", "unit": "iterations", "iterations": 10,
                    "cairn": run_benchmarks.summarise([2.0], 10),
                    "c": None if verdict_text == "unavailable" else
                    {**run_benchmarks.summarise([1.0], 10), "ratio_to_c": 2.0},
                    "verdict": verdict_text}

        self.assertIn("unavailable", run_benchmarks.render([report("unavailable")], None))
        self.assertIn("measured on every case",
                      run_benchmarks.render([report("faster")], "gcc"))
        self.assertIn("not met on x", run_benchmarks.render([report("slower")], "gcc"))

    def test_a_report_without_a_verdict_is_treated_as_unmeasured(self) -> None:
        report = {"case": "x", "unit": "iterations", "iterations": 10,
                  "cairn": run_benchmarks.summarise([1.0], 10), "c": None}
        self.assertIn("unavailable", run_benchmarks.render([report], None))

    def test_render_shows_c_image_size_when_present(self) -> None:
        report = {"case": "x", "unit": "iterations", "iterations": 10,
                  "cairn": run_benchmarks.summarise([2.0], 10),
                  "c": {**run_benchmarks.summarise([1.0], 10), "ratio_to_c": 2.0,
                        "image_bytes": 4096},
                  "verdict": "slower"}
        self.assertIn("4096", run_benchmarks.render([report], "gcc"))

    def test_errored_case_is_reported_not_raised(self) -> None:
        folder = Path(tempfile.mkdtemp(prefix="cairn-bench-test-"))
        self.addCleanup(lambda: [child.unlink(missing_ok=True) for child in
                                 sorted(folder.rglob("*"), reverse=True)] +
                        [folder.rmdir()])
        with mock.patch.object(run_benchmarks, "measure",
                               side_effect=RuntimeError("boom")):
            report = run_benchmarks.benchmark_case(run_benchmarks.CASES[0], None, 1,
                                                  folder)
        self.assertEqual(report["verdict"], "error")
        self.assertIn("boom", str(report["error"]))


class CompilerProbeTest(unittest.TestCase):
    """Compiler detection must return a name the harness knows, or nothing at all."""

    def test_detected_compiler_is_one_of_the_known_families(self) -> None:
        compiler = run_benchmarks.find_c_compiler()
        self.assertIn(compiler, [None, *run_benchmarks.C_COMPILERS])

    def test_detected_cpp_compiler_is_one_of_the_known_families(self) -> None:
        compiler = run_benchmarks.find_cpp_compiler()
        self.assertIn(compiler, [None, *run_benchmarks.CPP_COMPILERS])


class RegistryShapeTest(unittest.TestCase):
    """The measured set: which cases the scoreboard commits to, by name and unit."""

    def test_the_registry_names_every_commitment(self) -> None:
        names = [case.name for case in run_benchmarks.CASES]
        self.assertEqual(names, ["xorshift", "fib30", "folded_math", "collatz",
                                 "ackermann", "nqueens", "binary_gcd", "call_chain",
                                 "tak", "primes", "mandelbrot", "splitmix", "hex_io",
                                 "many_params", "argv_walk", "char_stream", "str_scan",
                                 "array_sum", "sieve", "popcount"])

    def test_char_stream_stdin_has_no_windows_eof_byte(self) -> None:
        case = next(case for case in run_benchmarks.CASES if case.name == "char_stream")
        self.assertNotIn(0x1A, case.stdin,
                         "0x1A is text-mode EOF on the Windows CRT, so C and C++ "
                         "twins would read only the bytes before it")

    def test_char_stream_stdin_matches_its_declared_size(self) -> None:
        case = next(case for case in run_benchmarks.CASES if case.name == "char_stream")
        self.assertEqual(len(case.stdin), case.iterations)

    def test_each_case_declares_a_unit_and_a_positive_iteration_count(self) -> None:
        for case in run_benchmarks.CASES:
            self.assertTrue(case.unit, case.name)
            self.assertGreater(case.iterations, 0, case.name)


class ManyParamsContractTest(unittest.TestCase):
    """ADR-071: no twin may let a compiler fold the whole program to one print.

    The bound every twin runs under comes from its runtime's argument count, so it
    is invisible to a compiler, and a bare run still executes exactly the registry's
    iteration count. A later edit that freezes the bound back into a literal fails
    here, before a scoreboard claim can be earned against a constant.
    """

    def sources(self):
        case = next(case for case in run_benchmarks.CASES if case.name == "many_params")
        return [run_benchmarks.ROOT / case.cairn,
                run_benchmarks.ROOT / case.c_source,
                run_benchmarks.ROOT / case.cpp_source,
                run_benchmarks.ROOT / case.rust_source]

    def compact(self, source: Path) -> str:
        """One twin's text with the spellings that differ per language removed."""
        text = source.read_text(encoding="utf-8")
        for noise in ("_", ",", " ", "(", ")", "\n"):
            text = text.replace(noise, "")
        return text

    def test_every_twin_derives_its_bound_from_its_argument_count(self) -> None:
        for source in self.sources():
            text = source.read_text(encoding="utf-8")
            self.assertTrue("argc" in text or "args()" in text, source.name)

    def test_the_bound_is_the_registry_iteration_count_plus_the_argument_count(self) -> None:
        case = next(case for case in run_benchmarks.CASES if case.name == "many_params")
        for source in self.sources():
            self.assertIn(f"{case.iterations}+", self.compact(source), source.name)

    def test_the_mix_body_is_not_a_closed_form_recurrence(self) -> None:
        for source in self.sources():
            text = source.read_text(encoding="utf-8")
            self.assertIn("65535", text, source.name)
            self.assertIn("^", text, source.name)


class QuietWindowProvenanceTest(unittest.TestCase):
    """A saved record carries the ADR-069 probe, so a loaded baseline stays auditable.

    Without the field, a flag against a baseline measured on a busy machine reads
    exactly like a regression and has to be diagnosed from memory (the ADR-076 and
    ADR-078 sessions did that by hand); with it, the harness states the window
    state whenever it saves, and the compare warns when the baseline was loaded.
    """

    def record(self, probe):
        reports = [{"case": run_benchmarks.QUIET_PROBE_CASE,
                    "cairn": {"best_ms": probe}}]
        return run_benchmarks.record_from_reports(reports, "gcc", "rustc", 5, True, "g++")

    def test_a_probe_under_the_criterion_marks_the_run_quiet(self) -> None:
        record = self.record(15.2)
        self.assertTrue(record["quiet"])
        self.assertEqual(record["probe_ms"], 15.2)

    def test_a_probe_over_the_criterion_marks_the_run_loaded(self) -> None:
        record = self.record(74.3)
        self.assertFalse(record["quiet"])
        self.assertEqual(record["probe_ms"], 74.3)

    def test_a_run_without_the_probe_answers_unknown(self) -> None:
        record = run_benchmarks.record_from_reports(
            [{"case": "fib30", "cairn": {"best_ms": 10.0}}], "gcc", None, 5, True)
        self.assertIsNone(record["quiet"])
        self.assertIsNone(record["probe_ms"])

    def test_the_window_note_warns_only_for_a_loaded_baseline(self) -> None:
        self.assertIsNone(run_benchmarks.window_note(None))
        self.assertIsNone(run_benchmarks.window_note({"quiet": True, "probe_ms": 15.0}))
        self.assertIsNone(run_benchmarks.window_note({}))
        note = run_benchmarks.window_note({"quiet": False, "probe_ms": 74.3})
        self.assertIn("loaded window", note)
        self.assertIn("74.3", note)


class ComputeAdjustedTest(unittest.TestCase):
    """The floors view subtracts each language's own startup, and only that.

    Whole-process time is the scoreboard because it is what a user waits for, but a
    minimal program already costs millions of cycles (ADR-084), so a codegen delta
    on a short case is invisible in the total. This view is diagnostic: it must
    subtract each language's own floor, never another language's, and never turn a
    missing measurement into a number.
    """

    REPORTS = [
        {"case": "short", "cairn": {"best_ms": 11.0}, "c": {"best_ms": 12.0},
         "cpp": {"best_ms": 20.0}, "rust": {"best_ms": 14.0}},
        {"case": "no_rust", "cairn": {"best_ms": 9.0}, "c": {"best_ms": 10.0}},
    ]

    def test_each_language_subtracts_its_own_floor(self) -> None:
        floors = {"cairn": 5.0, "c": 8.0, "cpp": 17.0, "rust": 11.0}
        lines = run_benchmarks.compute_adjusted(self.REPORTS, floors)
        self.assertIn("short        cairn 6.00  c 4.00  cpp 3.00  rust 3.00 ms", lines)
        self.assertIn("no_rust      cairn 4.00  c 2.00 ms", lines)

    def test_a_missing_floor_omits_the_language_instead_of_guessing(self) -> None:
        lines = run_benchmarks.compute_adjusted(self.REPORTS, {"cairn": 5.0})
        self.assertIn("short        cairn 6.00 ms", lines)

    def test_a_best_below_the_floor_clamps_at_zero(self) -> None:
        lines = run_benchmarks.compute_adjusted(
            [{"case": "odd", "cairn": {"best_ms": 3.0}}], {"cairn": 5.0})
        self.assertIn("cairn 0.00", lines[0])

    def test_the_floor_sources_are_the_four_empty_programs(self) -> None:
        self.assertEqual(set(run_benchmarks.FLOOR_SOURCES), {"cairn", "c", "cpp", "rust"})
        self.assertIn("print(0)", run_benchmarks.FLOOR_SOURCES["cairn"])


class CompareRunsTest(unittest.TestCase):
    """The regression watch: a saved run's numbers diffed against the new run."""

    def test_a_cairn_regression_beyond_the_noise_band_is_flagged(self) -> None:
        previous = {"cairn": {"binary_gcd": 180.0, "nqueens": 25.0}}
        current = {"cairn": {"binary_gcd": 200.0, "nqueens": 25.5}}
        lines, regressed = run_benchmarks.compare_runs(current, previous, noise=0.10)
        self.assertTrue(regressed)
        self.assertIn("binary_gcd", "".join(lines))
        self.assertIn("regressed", "".join(lines))

    def test_drift_inside_the_noise_band_is_reported_but_not_flagged(self) -> None:
        previous = {"cairn": {"binary_gcd": 180.0}}
        current = {"cairn": {"binary_gcd": 190.0}}
        lines, regressed = run_benchmarks.compare_runs(current, previous, noise=0.10)
        self.assertFalse(regressed)
        self.assertIn("within noise", "".join(lines))

    def test_an_improvement_is_named_and_never_flags(self) -> None:
        previous = {"cairn": {"call_chain": 48.0}}
        current = {"cairn": {"call_chain": 43.0}}
        lines, regressed = run_benchmarks.compare_runs(current, previous, noise=0.10)
        self.assertFalse(regressed)
        self.assertIn("improved", "".join(lines))

    def test_a_case_absent_from_the_previous_run_is_reported_as_new(self) -> None:
        previous = {"cairn": {}}
        current = {"cairn": {"xorshift": 300.0}}
        lines, regressed = run_benchmarks.compare_runs(current, previous, noise=0.10)
        self.assertFalse(regressed)
        self.assertIn("new", "".join(lines))

    def test_a_case_missing_from_the_current_run_is_not_imagined(self) -> None:
        previous = {"cairn": {"binary_gcd": 180.0}}
        current = {"cairn": {}}
        lines, regressed = run_benchmarks.compare_runs(current, previous, noise=0.10)
        self.assertFalse(regressed)
        self.assertEqual(lines, [])

    def test_compare_is_symmetric_about_the_noise_band_edges(self) -> None:
        # changes clearly inside the band stay unflagged, clearly outside it flag;
        # the exact boundary itself is float territory no assertion should live on
        previous = {"cairn": {"x": 100.0}}
        for factor, flagged in ((1.09, False), (1.12, True), (0.91, False)):
            lines, regressed = run_benchmarks.compare_runs(
                {"cairn": {"x": 100.0 * factor}}, previous, noise=0.10)
            self.assertEqual(regressed, flagged, factor)

    def test_the_newest_valid_history_record_wins_over_corrupt_lines(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.jsonl"
            good = json.dumps({"timestamp": 1, "cairn": {"x": 100.0}})
            path.write_text(good + "\nnot json\n{bad\n", encoding="utf-8")
            record = run_benchmarks.latest_history_record(path)
            self.assertIsNotNone(record)
            self.assertEqual(record["cairn"], {"x": 100.0})

    def test_missing_history_reads_as_no_record(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(run_benchmarks.latest_history_record(Path(folder) / "none.jsonl"))


if __name__ == "__main__":
    unittest.main()
