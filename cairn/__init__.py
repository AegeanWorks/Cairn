"""Cairn: an agent-first systems language with its own x86-64 backend.

The package is a bootstrap compiler written in Python. It lexes and parses a `.crn` source
file, checks it, lowers it to x86-64 machine code and writes a Windows PE executable without
calling any external assembler, linker or C compiler.
"""

from cairn.diagnostics import Diagnostic, DiagnosticError, Span
from cairn.version import VERSION

__all__ = ["VERSION", "Diagnostic", "DiagnosticError", "Span"]
