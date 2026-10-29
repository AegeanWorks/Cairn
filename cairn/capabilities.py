"""The machine-readable description of what this compiler accepts and emits.

`cairn capabilities --json` prints this object. It is the first thing an agent should read: it
lists the surface, the limits, the exit codes and every diagnostic code with its stage, so no
answer has to be inferred from prose or from reading the parser.
"""

from typing import Any, Dict, List

from cairn.codes import REGISTRY
from cairn.image import IMPORTS
from cairn.optimize import INT64_MAX, INT64_MIN
from cairn.parser import PRECEDENCE, TYPE_NAMES
from cairn.sema import BUILTINS, MAX_PARAMETERS
from cairn.values import values_manifest
from cairn.version import LANGUAGE_VERSION, VERSION

EXIT_CODES = {
    "0": "success",
    "1": "the source did not compile, or the program could not start",
    "2": "bad command line, or the input file could not be read",
}

COMMANDS = {
    "build": "compile a .crn file to a Windows .exe",
    "check": "parse and analyse without writing an executable",
    "run": "build to a temporary file, run it, and forward its exit code",
    "verify": "check a built .exe, or a .crn compiled in memory, against the artifact invariants",
    "listing": "print the emitted machine code by label, without writing a file",
    "capabilities": "print this object",
}

SECTIONS = {".text": "executable code", ".rdata": "import names and string constants",
            ".data": "the import address table"}

GUARANTEES = (
    "no undefined behaviour: integer overflow wraps, division by zero traps",
    "deterministic output: the same source produces byte-identical executables",
    "no external toolchain: no assembler, linker, C compiler or runtime library",
    "no third-party dependency: the compiler imports only the Python standard library",
    "defined reads: every frame slot is zeroed on entry",
    "one repair pass: semantic analysis reports every finding, not just the first",
)


def _operators() -> List[Dict[str, Any]]:
    """Operators grouped by precedence tier, loosest first."""
    tiers: Dict[int, List[str]] = {}
    for symbol, level in PRECEDENCE.items():
        tiers.setdefault(level, []).append(symbol)
    return [{"tier": level, "operators": sorted(tiers[level])} for level in sorted(tiers)]


def capabilities() -> Dict[str, Any]:
    """The whole surface as one JSON-serialisable object."""
    return {
        "name": "cairn",
        "version": VERSION,
        "language_version": LANGUAGE_VERSION,
        "core_values": values_manifest(),
        "target": {"os": "windows", "architecture": "x86-64", "format": "PE32+ console",
                   "sections": SECTIONS, "imports": {name: "kernel32.dll" for name in IMPORTS}},
        "types": sorted(TYPE_NAMES),
        "literals": {"integer": "decimal, signed 64-bit", "boolean": "true is 1, false is 0"},
        "statements": ["let", "var", "assignment", "if/else", "while", "return", "break",
                       "continue", "block"],
        "operators_by_precedence": _operators(),
        "unary_operators": ["-", "!", "~"],
        "builtins": {name: {"parameters": arity, "returns": "its argument"}
                     for name, arity in sorted(BUILTINS.items())},
        "comments": "// to the end of the line",
        "limits": {"parameters_per_function": MAX_PARAMETERS,
                   "integer_min": INT64_MIN, "integer_max": INT64_MAX,
                   "identifiers": "ASCII letters, digits and underscore; no shadowing"},
        "semantics": {"overflow": "wraps", "division": "truncates toward zero",
                      "division_by_zero": "trap, exit code 3", "right_shift": "arithmetic",
                      "comparison_result": "0 or 1", "evaluation": "left to right",
                      "short_circuit": ["&&", "||"]},
        "commands": COMMANDS,
        "flags": {"--json": "machine-readable output", "--no-optimize": "skip constant folding",
                  "--timeout": "run: seconds before the program is stopped"},
        "exit_codes": EXIT_CODES,
        "diagnostic_codes": [{"code": entry.code, "stage": entry.stage,
                              "summary": entry.summary} for entry in REGISTRY],
        "guarantees": list(GUARANTEES),
    }


def render_text() -> str:
    """A short human summary: the surface on one screen."""
    surface = capabilities()
    lines = [f"Cairn {surface['version']} ({surface['language_version']})",
             f"target: {surface['target']['format']} on {surface['target']['architecture']}",
             f"types: {', '.join(surface['types'])}",
             f"builtins: {', '.join(surface['builtins'])}",
             f"limits: at most {MAX_PARAMETERS} parameters; i64 range "
             f"{INT64_MIN}..{INT64_MAX}",
             f"diagnostic codes: {len(REGISTRY)} registered",
             f"core values: {len(surface['core_values'])}, each with a proving command",
             "commands: " + ", ".join(sorted(COMMANDS))]
    return "\n".join(lines)


__all__ = ["COMMANDS", "EXIT_CODES", "GUARANTEES", "SECTIONS", "capabilities", "render_text"]
