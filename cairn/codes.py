"""The diagnostic code registry: one record per code the compiler can emit.

Codes are a contract (ADR-003), so they live in one place instead of only inside the raise site.
Tests assert three directions: every code used in the package is registered, every registered code
is documented in README.md, and every registered code appears in `cairn capabilities`.

Families: E1xxx lexer, E2xxx parser, E3xxx semantics, Vxxxx verification of a built artifact.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Set, Tuple

CODE_PATTERN = r'"[EV]\d{4}"'


@dataclass(frozen=True)
class Code:
    """One diagnostic code: its stage family and a one-line statement of what it means."""

    code: str
    stage: str
    summary: str


REGISTRY: Tuple[Code, ...] = (
    Code("E1001", "lexer", "Unexpected character"),
    Code("E1003", "lexer", "Integer literal does not fit in i64"),
    Code("E2001", "parser", "Grammar expected a specific token"),
    Code("E2002", "parser", "Unknown type"),
    Code("E2003", "parser", "Assignment target is not a name"),
    Code("E2004", "parser", "Expression statement with no effect"),
    Code("E3001", "semantics", "Duplicate function"),
    Code("E3003", "semantics", "Unknown function"),
    Code("E3004", "semantics", "Wrong argument count"),
    Code("E3005", "semantics", "Assignment to an immutable binding"),
    Code("E3006", "semantics", "Duplicate binding or parameter"),
    Code("E3007", "semantics", "Undeclared name"),
    Code("E3008", "semantics", "break or continue outside a loop"),
    Code("E3009", "semantics", "Function can finish without returning"),
    Code("E3010", "semantics", "More than four parameters"),
    Code("E3011", "semantics", "Missing or ill-formed main"),
    Code("E3012", "semantics", "Builtin redefined or used as a value"),
    Code("V0001", "verify", "DOS or PE signature is missing"),
    Code("V0002", "verify", "COFF header does not describe an amd64 image"),
    Code("V0003", "verify", "Optional header is not PE32+ console"),
    Code("V0004", "verify", "Section table is not .text, .rdata, .data"),
    Code("V0005", "verify", "Section alignment or raw pointer is wrong"),
    Code("V0006", "verify", "Entry point is outside .text"),
    Code("V0007", "verify", "Import directory is missing or outside .rdata"),
    Code("V0008", "verify", "Image size does not match the section layout"),
    Code("V0009", "verify", "A section has the wrong permissions"),
    Code("V0010", "verify", "The file is shorter than its section table claims"),
)

BY_CODE: Dict[str, Code] = {entry.code: entry for entry in REGISTRY}


def codes_in_source(texts: Iterable[str]) -> Set[str]:
    """Every code literal that appears in source text, which is what the compiler emits."""
    import re

    found: Set[str] = set()
    for text in texts:
        found.update(match.strip('"') for match in re.findall(CODE_PATTERN, text))
    return found


def codes_by_stage() -> Dict[str, List[str]]:
    """Registered codes grouped by stage, in registry order, for the capabilities manifest."""
    grouped: Dict[str, List[str]] = {}
    for entry in REGISTRY:
        grouped.setdefault(entry.stage, []).append(entry.code)
    return grouped


__all__ = ["BY_CODE", "CODE_PATTERN", "Code", "REGISTRY", "codes_by_stage", "codes_in_source"]
