"""The core values are a contract: six values, each with a live guard and a proving command."""

import ast
import unittest
from pathlib import Path

import cairn
from cairn import capabilities
from cairn.values import BY_KEY, VALUES

PROJECT = Path(cairn.__file__).parent.parent
README = PROJECT / "README.md"

EXPECTED = (
    ("no-dependency", "No Dependency"),
    ("ai-compatible", "AI Compatible"),
    ("easily-understandable", "Easily Understandable"),
    ("machine-language-like", "Almost Machine Language Like"),
    ("verifiable", "Verifiable"),
    ("error-codes", "Easily Problem Understood via Error Codes"),
)


def guard_source(guard: str) -> ast.Module:
    """Parse the test file a guard names, so the guard is checked without being run again."""
    relative = guard.split("::", 1)[0]
    return ast.parse((PROJECT / relative).read_text(encoding="utf-8"))


def guard_names(guard: str) -> list:
    """The dotted names after the file, for example `DependencyTest` then its test method."""
    return guard.split("::")[1:]


def evidence_command(evidence: str) -> str:
    """The `cairn` subcommand, or the test module, that an evidence line runs."""
    tokens = evidence.split()
    if "cairn" in tokens:
        return tokens[tokens.index("cairn") + 1]
    return tokens[-1]


class CoreValuesTest(unittest.TestCase):
    """The six values and their order are frozen; evidence and guards must stay real."""

    def test_the_six_values_are_frozen_in_order(self) -> None:
        self.assertEqual(tuple((entry.key, entry.name) for entry in VALUES), EXPECTED)

    def test_every_value_has_a_claim_an_evidence_command_and_a_distinct_guard(self) -> None:
        for entry in VALUES:
            with self.subTest(value=entry.key):
                self.assertTrue(entry.claim)
                self.assertTrue(entry.evidence.startswith("python "))
                self.assertIn("::", entry.guard)
        self.assertEqual(len({entry.guard for entry in VALUES}), len(VALUES))

    def test_every_hygiene_guard_names_a_real_test_method(self) -> None:
        for entry in VALUES:
            with self.subTest(value=entry.key), self.subTest(guard=entry.guard):
                tree = guard_source(entry.guard)
                names = guard_names(entry.guard)
                classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
                self.assertIn(names[0], classes)
                target = next(node for node in tree.body
                              if isinstance(node, ast.ClassDef) and node.name == names[0])
                methods = {child.name for child in target.body
                           if isinstance(child, ast.FunctionDef)}
                self.assertIn(names[1], methods)

    def test_every_evidence_command_names_a_published_subcommand(self) -> None:
        published = set(capabilities.COMMANDS)
        for entry in VALUES:
            with self.subTest(value=entry.key):
                command = evidence_command(entry.evidence)
                if command.startswith("tests."):
                    self.assertTrue((PROJECT / (command.replace(".", "/") + ".py")).is_file())
                else:
                    self.assertIn(command, published)

    def test_keys_are_unique_and_indexed(self) -> None:
        self.assertEqual(set(BY_KEY), {key for key, _ in EXPECTED})

    def test_capabilities_publishes_every_value_with_its_evidence(self) -> None:
        published = capabilities.capabilities()["core_values"]
        self.assertEqual([(item["key"], item["name"]) for item in published], list(EXPECTED))
        self.assertEqual([item["evidence"] for item in published],
                         [entry.evidence for entry in VALUES])

    def test_readme_states_every_value_in_its_frozen_order(self) -> None:
        readme = README.read_text(encoding="utf-8")
        positions = [readme.find(f"**{name}**") for _, name in EXPECTED]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
