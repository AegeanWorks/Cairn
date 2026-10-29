"""Benchmark harness tests: summarising and rendering, without running a benchmark."""

import unittest

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

    def test_every_case_declares_an_existing_cairn_and_c_source(self) -> None:
        for case in run_benchmarks.CASES:
            self.assertTrue((run_benchmarks.ROOT / case.cairn).is_file(), case.cairn)
            self.assertTrue((run_benchmarks.ROOT / case.c_source).is_file(), case.c_source)

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


class CompilerProbeTest(unittest.TestCase):
    """Compiler detection must return a name the harness knows, or nothing at all."""

    def test_detected_compiler_is_one_of_the_known_families(self) -> None:
        compiler = run_benchmarks.find_c_compiler()
        self.assertIn(compiler, [None, *run_benchmarks.C_COMPILERS])


if __name__ == "__main__":
    unittest.main()
