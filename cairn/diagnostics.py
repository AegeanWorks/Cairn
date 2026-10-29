"""Diagnostics: every failure carries a stable code, a span and a fix.

An agent reads the code and the span, not the prose, so the machine-readable form is part of
the contract: `{"code", "severity", "line", "column", "end_line", "end_column", "message",
"fix", "path"}`.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class Span:
    """A half-open source range, in offsets and in 1-based line/column pairs."""

    start: int
    end: int
    line: int
    column: int
    end_line: int
    end_column: int

    @staticmethod
    def point(offset: int, line: int, column: int) -> "Span":
        """A span covering one position, used for a missing token."""
        return Span(offset, offset, line, column, line, column)

    @staticmethod
    def join(first: "Span", last: "Span") -> "Span":
        """The smallest span covering both inputs."""
        return Span(first.start, last.end, first.line, first.column, last.end_line, last.end_column)


@dataclass
class Diagnostic:
    """One error or warning: what is wrong, where, and how to fix it."""

    code: str
    message: str
    fix: str
    span: Span
    path: str = "<source>"
    severity: str = "error"

    def to_dict(self) -> Dict[str, Any]:
        """The JSON form an agent reads; keys and order are the contract."""
        return {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "line": self.span.line,
            "column": self.span.column,
            "end_line": self.span.end_line,
            "end_column": self.span.end_column,
            "message": self.message,
            "fix": self.fix,
        }

    def render(self, source: Optional[str] = None) -> str:
        """The human form: a positioned line, the offending text, and a fix line."""
        head = (f"{self.path}:{self.span.line}:{self.span.column}: "
                f"{self.severity}[{self.code}]: {self.message}")
        lines = [head]
        excerpt = _excerpt(source, self.span) if source is not None else ""
        if excerpt:
            lines.append(f"  {excerpt}")
            lines.append(f"  {' ' * (self.span.column - 1)}"
                         f"{'^' * max(1, self.span.end_column - self.span.column)}")
        lines.append(f"  fix: {self.fix}")
        return "\n".join(lines)


def _excerpt(source: str, span: Span) -> str:
    """The source line the span starts on, without its line ending."""
    lines = source.splitlines()
    if not 1 <= span.line <= len(lines):
        return ""
    return lines[span.line - 1]


class DiagnosticError(Exception):
    """Raised by a stage that cannot continue; carries every diagnostic found so far."""

    def __init__(self, diagnostics: Iterable[Diagnostic]) -> None:
        self.diagnostics: List[Diagnostic] = list(diagnostics)
        first = self.diagnostics[0] if self.diagnostics else None
        super().__init__(first.message if first is not None else "compilation failed")

    def render(self, source: Optional[str] = None) -> str:
        """All diagnostics, one after another."""
        return "\n".join(diagnostic.render(source) for diagnostic in self.diagnostics)


def fail(code: str, message: str, fix: str, span: Span, path: str) -> DiagnosticError:
    """One diagnostic as an exception, for the stage that cannot continue past it."""
    return DiagnosticError([Diagnostic(code, message, fix, span, path)])


__all__ = ["Diagnostic", "DiagnosticError", "Span", "fail"]
