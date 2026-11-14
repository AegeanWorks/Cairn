"""Benchmark harness tests: summarising and rendering, without running a benchmark."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks import run_benchmarks


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
                mock.patch.object(run_benchmarks, "find_rust_compiler", return_value=None):
            self.assertEqual(run_benchmarks.main(["--strict"]), 2)

    def test_strict_passes_when_the_measured_baseline_is_won(self) -> None:
        report = {"case": "x", "unit": "iterations", "iterations": 10,
                  "cairn": run_benchmarks.summarise([1.0], 10),
                  "c": {**run_benchmarks.summarise([2.0], 10), "ratio_to_c": 0.5},
                  "verdict": "faster",
                  "rust": None, "rust_compiler": None, "verdict_rust": "unavailable",
                  "error": None}
        with mock.patch.object(run_benchmarks, "find_c_compiler", return_value="gcc"), \
                mock.patch.object(run_benchmarks, "find_rust_compiler", return_value=None), \
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
                  "error": None}
        with mock.patch.object(run_benchmarks, "find_c_compiler", return_value="gcc"), \
                mock.patch.object(run_benchmarks, "find_rust_compiler", return_value="rustc"), \
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
                  "error": None}
        stdout = io.StringIO()
        with mock.patch.object(run_benchmarks, "find_c_compiler", return_value="gcc"), \
                mock.patch.object(run_benchmarks, "find_rust_compiler", return_value="rustc"), \
                mock.patch.object(run_benchmarks, "benchmark_case", return_value=report), \
                mock.patch.object(run_benchmarks, "CASES", (object(),)), \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(run_benchmarks.main(["--json"]), 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["goal"]["faster_than_c"], True)
        self.assertEqual(payload["goal"]["faster_than_rust"], False)
        self.assertEqual(payload["compiler"], "gcc")
        self.assertEqual(payload["rust_compiler"], "rustc")

    def test_render_says_unavailable_when_there_is_no_c_compiler(self) -> None:
        report = {"case": "x", "unit": "iterations", "iterations": 10,
                  "cairn": run_benchmarks.summarise([1.0], 10), "c": None}
        text = run_benchmarks.render([report], None)
        self.assertIn("C baseline: unavailable", text)
        self.assertIn("cairn", text)

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
        self.assertIn("errored on loop_sum", run_benchmarks.render([report], None))


class CompilerProbeTest(unittest.TestCase):
    """Compiler detection must return a name the harness knows, or nothing at all."""

    def test_detected_compiler_is_one_of_the_known_families(self) -> None:
        compiler = run_benchmarks.find_c_compiler()
        self.assertIn(compiler, [None, *run_benchmarks.C_COMPILERS])


if __name__ == "__main__":
    unittest.main()
