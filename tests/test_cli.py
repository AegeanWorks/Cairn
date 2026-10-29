"""CLI tests: exit codes, JSON diagnostics, build output and atomic overwrite."""

import contextlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from cairn.cli import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, main

GOOD = "fn main() -> i64 { print(7); return 0; }\n"
BAD = "fn main() -> i64 { return missing; }\n"


class CliTest(unittest.TestCase):
    """An agent drives the compiler through this surface, so it is tested as a contract."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="cairn-cli-"))
        self.addCleanup(_remove_tree, self.folder)
        self.good = self.folder / "good.crn"
        self.good.write_text(GOOD, encoding="utf-8")
        self.bad = self.folder / "bad.crn"
        self.bad.write_text(BAD, encoding="utf-8")

    def test_check_json_reports_success(self) -> None:
        output, status = _capture(lambda: main(["check", str(self.good), "--json"]))
        self.assertEqual(status, EXIT_OK)
        payload = json.loads(output)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["functions"], 1)
        self.assertEqual(payload["diagnostics"], [])

    def test_check_json_lists_every_diagnostic(self) -> None:
        output, status = _capture(lambda: main(["check", str(self.bad), "--json"]))
        self.assertEqual(status, EXIT_FAILURE)
        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["diagnostics"][0]["code"], "E3007")
        self.assertIn("fix", payload["diagnostics"][0])
        self.assertEqual(payload["diagnostics"][0]["line"], 1)

    def test_check_human_form_states_the_fix(self) -> None:
        output, status = _capture(lambda: main(["check", str(self.bad)]))
        self.assertEqual(status, EXIT_FAILURE)
        self.assertIn("E3007", output)
        self.assertIn("fix:", output)

    def test_build_writes_the_requested_path(self) -> None:
        executable = self.folder / "program.exe"
        output, status = _capture(
            lambda: main(["build", str(self.good), "-o", str(executable)]))
        self.assertEqual(status, EXIT_OK)
        self.assertTrue(executable.is_file())
        self.assertGreater(executable.stat().st_size, 512)
        self.assertIn("ok:", output)

    def test_build_twice_produces_identical_bytes(self) -> None:
        first = self.folder / "first.exe"
        second = self.folder / "second.exe"
        _capture(lambda: main(["build", str(self.good), "-o", str(first)]))
        _capture(lambda: main(["build", str(self.good), "-o", str(second)]))
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_missing_input_is_a_usage_error(self) -> None:
        _, status = _capture(lambda: main(["check", str(self.folder / "absent.crn")]))
        self.assertEqual(status, EXIT_USAGE)

    def test_version_is_printed(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(SystemExit) as caught:
                main(["--version"])
        self.assertEqual(caught.exception.code, EXIT_OK)
        self.assertIn("cairn", buffer.getvalue())

    def test_capabilities_json_publishes_codes_and_commands(self) -> None:
        output, status = _capture(lambda: main(["capabilities", "--json"]))
        self.assertEqual(status, EXIT_OK)
        surface = json.loads(output)
        self.assertEqual(surface["name"], "cairn")
        self.assertIn("verify", surface["commands"])
        self.assertIn("E3007", {entry["code"] for entry in surface["diagnostic_codes"]})

    def test_capabilities_text_names_the_surface(self) -> None:
        output, status = _capture(lambda: main(["capabilities"]))
        self.assertEqual(status, EXIT_OK)
        self.assertIn("types: i64", output)
        self.assertIn("diagnostic codes:", output)

    def test_listing_json_carries_the_text_by_label(self) -> None:
        output, status = _capture(lambda: main(["listing", str(self.good), "--json"]))
        self.assertEqual(status, EXIT_OK)
        payload = json.loads(output)
        self.assertIn("fn.main", payload["labels"])
        self.assertTrue(payload["listing"].startswith("# .text, "))
        self.assertGreater(payload["text_bytes"], 0)

    def test_verify_accepts_a_source_and_a_built_executable(self) -> None:
        source_output, source_status = _capture(lambda: main(["verify", str(self.good)]))
        self.assertEqual(source_status, EXIT_OK)
        self.assertIn("verified", source_output)
        executable = self.folder / "verify.exe"
        _capture(lambda: main(["build", str(self.good), "-o", str(executable)]))
        _, exe_status = _capture(lambda: main(["verify", str(executable)]))
        self.assertEqual(exe_status, EXIT_OK)

    def test_verify_reports_a_broken_image_with_its_code(self) -> None:
        executable = self.folder / "broken.exe"
        _capture(lambda: main(["build", str(self.good), "-o", str(executable)]))
        image = bytearray(executable.read_bytes())
        image[0x84:0x86] = struct.pack("<H", 0x014C)
        executable.write_bytes(image)
        output, status = _capture(lambda: main(["verify", str(executable)]))
        self.assertEqual(status, EXIT_FAILURE)
        self.assertIn("V0002", output)

    def test_verify_forwards_a_compile_failure(self) -> None:
        output, status = _capture(lambda: main(["verify", str(self.bad), "--json"]))
        self.assertEqual(status, EXIT_FAILURE)
        self.assertEqual(json.loads(output)["diagnostics"][0]["code"], "E3007")

    def test_verify_missing_target_is_a_usage_error(self) -> None:
        _, status = _capture(lambda: main(["verify", str(self.folder / "absent.exe")]))
        self.assertEqual(status, EXIT_USAGE)


def _capture(call) -> tuple:
    """Run a CLI call with both streams redirected; returns their text and the exit code."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        status = call()
    return buffer.getvalue(), status


def _remove_tree(folder: Path) -> None:
    """Delete the test's temporary folder, ignoring files Windows may still hold."""
    for child in sorted(folder.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink(missing_ok=True)
        else:
            child.rmdir()
    folder.rmdir()


if __name__ == "__main__":
    unittest.main()
