"""The six core values, each bound to the command and the test that prove it.

A value that lives only in prose cannot fail, so an agent cannot tell whether it still holds.
Every record here names the command that demonstrates the value and the guard that fails when the
compiler stops honouring it. `cairn capabilities --json` publishes this list and README.md renders
it for humans, so a value that loses its evidence fails the suite instead of quietly decaying.
"""

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class Value:
    """One core value: the claim, the command that shows it, and the test that defends it."""

    key: str
    name: str
    claim: str
    evidence: str
    guard: str


VALUES = (
    Value(
        key="no-dependency",
        name="No Dependency",
        claim="The compiler imports only the Python standard library, and a produced .exe "
              "needs 64-bit Windows and nothing else: no C toolchain, assembler, linker or "
              "third-party package.",
        evidence="python -m unittest tests.test_capabilities",
        guard="tests/test_capabilities.py::DependencyTest"
              "::test_the_package_imports_only_stdlib_and_itself",
    ),
    Value(
        key="ai-compatible",
        name="AI Compatible",
        claim="An agent discovers the whole surface, every limit and every command from one "
              "JSON object, and every failure is one machine-readable object with a stable code.",
        evidence="python -m cairn capabilities --json",
        guard="tests/test_capabilities.py::CapabilitiesTest"
              "::test_manifest_is_json_serialisable_and_names_every_command",
    ),
    Value(
        key="easily-understandable",
        name="Easily Understandable",
        claim="One numeric type, one binding form per intent, one flat scope, no implicit "
              "conversion and no case an agent must look up in a long reference.",
        evidence="python -m cairn capabilities",
        guard="tests/test_capabilities.py::CapabilitiesTest"
              "::test_manifest_publishes_the_frozen_surface",
    ),
    Value(
        key="machine-language-like",
        name="Almost Machine Language Like",
        claim="Source maps to emitted x86-64 bytes with no interpreter, no garbage collector and "
              "no hidden runtime, and those bytes can be read back label by label.",
        evidence="python -m cairn listing examples/hello.crn",
        guard="tests/test_capabilities.py::ListingTest"
              "::test_listing_names_entry_main_and_runtime_helpers",
    ),
    Value(
        key="verifiable",
        name="Verifiable",
        claim="A built artifact is proved well formed from its bytes alone, and two builds of the "
              "same source are byte-identical.",
        evidence="python -m cairn verify examples/hello.crn",
        guard="tests/test_verify.py::VerifyTest::test_a_fresh_build_has_no_problems",
    ),
    Value(
        key="error-codes",
        name="Easily Problem Understood via Error Codes",
        claim="Every diagnostic carries a stable code from a registry that is documented and "
              "published, and a corrupted image names the invariant it broke.",
        evidence="python -m cairn check broken.crn --json",
        guard="tests/test_capabilities.py::RegistryTest"
              "::test_every_registered_code_is_documented_in_the_readme",
    ),
)

BY_KEY: Dict[str, Value] = {entry.key: entry for entry in VALUES}


def values_manifest() -> List[Dict[str, Any]]:
    """The values as JSON-ready records, in their frozen order."""
    return [{"key": entry.key, "name": entry.name, "claim": entry.claim,
             "evidence": entry.evidence} for entry in VALUES]


__all__ = ["BY_KEY", "VALUES", "Value", "values_manifest"]
