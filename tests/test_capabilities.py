"""The machine-checkable core: registry, manifest, listing and the stdlib-only proof."""

import ast
import json
import sys
import unittest
from pathlib import Path

import cairn
from cairn import capabilities, codes
from cairn.builder import compile_source
from cairn.listing import render_listing

PACKAGE = Path(cairn.__file__).parent
README = PACKAGE.parent / "README.md"
HELLO = "fn main() -> i64 { print(40 + 2); return 0; }"


def package_sources() -> list:
    """Every Python module of the compiler, not its tests or benchmarks."""
    return sorted(PACKAGE.glob("*.py"))


class RegistryTest(unittest.TestCase):
    """ADR-003 makes codes a contract, so all three directions are asserted."""

    def test_every_code_the_source_emits_is_registered(self) -> None:
        texts = [path.read_text(encoding="utf-8") for path in package_sources()]
        self.assertEqual(codes.codes_in_source(texts) - set(codes.BY_CODE), set())

    def test_every_registered_code_is_documented_in_the_readme(self) -> None:
        readme = README.read_text(encoding="utf-8")
        missing = [code for code in codes.BY_CODE if code not in readme]
        self.assertEqual(missing, [])

    def test_every_registered_code_is_in_the_capabilities_manifest(self) -> None:
        published = {entry["code"] for entry in capabilities.capabilities()["diagnostic_codes"]}
        self.assertEqual(published, set(codes.BY_CODE))

    def test_the_registry_has_no_duplicate_codes(self) -> None:
        listed = [entry.code for entry in codes.REGISTRY]
        self.assertEqual(len(listed), len(set(listed)))

    def test_codes_group_by_stage_in_registry_order(self) -> None:
        grouped = codes.codes_by_stage()
        self.assertEqual(list(grouped["lexer"]), ["E1001", "E1003"])
        self.assertEqual(grouped["verify"][0], "V0001")


class CapabilitiesTest(unittest.TestCase):
    """The manifest is the first thing an agent reads, so it must be complete and valid."""

    def test_manifest_is_json_serialisable_and_names_every_command(self) -> None:
        surface = json.loads(json.dumps(capabilities.capabilities()))
        self.assertEqual(set(surface["commands"]), set(capabilities.COMMANDS))
        self.assertEqual(set(capabilities.COMMANDS),
                         {"build", "check", "run", "verify", "listing", "capabilities"})

    def test_manifest_publishes_the_frozen_surface(self) -> None:
        surface = capabilities.capabilities()
        self.assertEqual(surface["types"], ["i64"])
        self.assertEqual(surface["limits"]["parameters_per_function"], 4)
        self.assertEqual(surface["target"]["imports"],
                         {"GetStdHandle": "kernel32.dll", "WriteFile": "kernel32.dll",
                          "ExitProcess": "kernel32.dll"})
        self.assertIn("wraps", surface["semantics"]["overflow"])

    def test_text_form_fits_one_screen(self) -> None:
        rendered = capabilities.render_text()
        self.assertLessEqual(len(rendered.splitlines()), 10)
        self.assertIn("capabilities", rendered)


class ListingTest(unittest.TestCase):
    """The listing is read-only and shows the labels the linker resolves."""

    def test_listing_names_entry_main_and_runtime_helpers(self) -> None:
        rendered = render_listing(compile_source(HELLO).artifact)
        self.assertTrue(rendered.startswith("# .text, "))
        for label in ("cairn.entry", "fn.main", "cairn.runtime.print_i64"):
            self.assertIn(f"<{label}>", rendered)

    def test_listing_rows_are_hex_at_six_digit_offsets(self) -> None:
        rendered = render_listing(compile_source(HELLO).artifact)
        rows = [line for line in rendered.splitlines() if line.startswith("  ")]
        self.assertTrue(rows)
        self.assertRegex(rows[0], r"^  [0-9a-f]{6}  ([0-9a-f]{2} ?)+$")


class DependencyTest(unittest.TestCase):
    """The compiler may import the Python standard library and nothing else."""

    def test_the_package_imports_only_stdlib_and_itself(self) -> None:
        external = set()
        allowed = set(sys.stdlib_module_names) | {"cairn"}
        for path in package_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    roots = [(node.module or "").split(".")[0]]
                else:
                    continue
                external.update(root for root in roots if root and root not in allowed)
        self.assertEqual(external, set())


if __name__ == "__main__":
    unittest.main()
