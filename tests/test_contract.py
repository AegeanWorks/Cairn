"""The doc-law contract: SPEC.md, LLM_SYSTEM.md and the manifest describe one language.

`python -m cairn capabilities --json` is the machine source of truth; SPEC.md is its
complete human rendering and LLM_SYSTEM.md its compact one. These tests bind all three
documents to the manifest so an agent never has to guess which document describes the
actual language. The benchmark case list is part of the same published surface: the
README must name every case the harness runs.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cairn import capabilities
from benchmarks import run_benchmarks as run_benchmarks

MANIFEST = capabilities.capabilities()
SPEC = (ROOT / "SPEC.md").read_text(encoding="utf-8")
SYSTEM = (ROOT / "LLM_SYSTEM.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def spec_section(number: str) -> str:
    """One `## N.` section body, up to the next `## ` heading."""
    head = f"## {number} "
    start = SPEC.index(head)
    follow = SPEC.find("\n## ", start + len(head))
    return SPEC[start:follow if follow >= 0 else len(SPEC)]


def manifest_builtin_names() -> "list[str]":
    return list(MANIFEST["builtins"])


class SpecContractTest(unittest.TestCase):
    """SPEC.md names the whole surface: every builtin, limit and import it really has."""

    def test_the_standard_surface_lists_every_builtin(self) -> None:
        section = spec_section("10.")
        missing = [name for name in manifest_builtin_names()
                   if not re.search(rf"`{re.escape(name)}\(", section)]
        self.assertEqual(missing, [],
                         "SPEC §10 forgets builtin(s) the manifest publishes")

    def test_the_parameter_limit_matches_the_manifest(self) -> None:
        limit = MANIFEST["limits"]["parameters_per_function"]
        surface = spec_section("10.") + spec_section("5.")
        self.assertIn(f"{limit} parameters", SPEC, "SPEC §5 states the real limit")
        self.assertNotIn("four parameters", SPEC.lower(), "the stale limit of 4 is gone")
        self.assertNotIn("4 parameters", surface,
                         "no stale 4-parameter claim survives in §5 or §10")

    def test_the_target_lists_every_import(self) -> None:
        section = spec_section("8.")
        missing = [name for name in MANIFEST["target"]["imports"] if name not in section]
        self.assertEqual(missing,
                         [], "SPEC §8 misses imports the artifact really carries")

    def test_no_section_forbids_the_legal_for_loop(self) -> None:
        # `for` is law (§2, §6.3, §13); the stale style rule banning it must not survive.
        self.assertNotIn("or `for`", spec_section("14."),
                         "the style rule still treats `for` as illegal")
        self.assertIn("for ident in expr", SPEC,
                      "SPEC documents for ranges in its grammar")
        # The manifest and the concrete grammar must agree that for exists.
        self.assertIn("for", MANIFEST["statements"])

    def test_the_trap_exit_and_the_cli_exits_do_not_collide(self) -> None:
        self.assertEqual(MANIFEST["semantics"]["division_by_zero"], "trap, exit code 3")
        self.assertNotIn('"3":', json.dumps(MANIFEST["exit_codes"]),
                         "CLI exit codes stay 0/1/2; the trap code is a program exit")


class SystemCardContractTest(unittest.TestCase):
    """LLM_SYSTEM.md is the compact face of the same manifest, never an older one."""

    def test_the_card_names_every_builtin(self) -> None:
        missing = [name for name in manifest_builtin_names() if name not in SYSTEM]
        self.assertEqual(missing, [],
                         "the system card forgets builtin(s) the manifest publishes")

    def test_the_card_carries_the_real_parameter_limit(self) -> None:
        self.assertTrue(re.search(r"([8]|eight) parameters", SYSTEM) is not None,
                        "the card states the eight-parameter limit")
        self.assertNotIn("4 parameters", SYSTEM)
        self.assertNotIn("four parameters", SYSTEM)

    def test_the_card_teaches_the_legal_for_loop(self) -> None:
        syntax = SYSTEM[SYSTEM.index("## Syntax"):SYSTEM.index("## Types")]
        self.assertIn("for", syntax, "the syntax block shows the counted range")
        absent = SYSTEM[SYSTEM.index("## What does not exist"):
                        SYSTEM.index("## Forbidden emissions")]
        self.assertNotIn("`for`", absent,
                         "the does-not-exist list still bans a legal construct")

    def test_the_card_stays_compact(self) -> None:
        self.assertLessEqual(len(SYSTEM), 5000,
                             "compact is the whole point of the card")


class ReadmeContractTest(unittest.TestCase):
    """The README publishes the benchmark scoreboard surface: all cases, named."""

    def test_the_readme_names_every_benchmark_case(self) -> None:
        missing = [case.name for case in run_benchmarks.CASES if case.name not in README]
        self.assertEqual(missing, [], "README omits benchmark case(s)")


if __name__ == "__main__":
    unittest.main()
