"""Syntax tree: one dataclass per construct, each carrying its source span.

The tree is deliberately shallow. There are no expressions statements that are both a value and
a declaration, no operator overloading and no implicit conversions, so an agent can predict the
meaning of any node from its class alone.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from cairn.diagnostics import Span


@dataclass
class Node:
    """Every node carries the span it was parsed from."""

    span: Span


@dataclass
class Expr(Node):
    """Base class for value-producing nodes."""


@dataclass
class IntLiteral(Expr):
    """A decimal integer literal, already range-checked by the lexer."""

    value: int = 0


@dataclass
class Name(Expr):
    """A reference to a parameter or a local; `slot` is filled in by semantic analysis."""

    name: str = ""
    slot: int = -1


@dataclass
class Unary(Expr):
    """`-x`, `!x` or `~x`."""

    op: str = ""
    operand: Optional[Expr] = None


@dataclass
class Binary(Expr):
    """A binary operator; `op` is the source spelling."""

    op: str = ""
    left: Optional[Expr] = None
    right: Optional[Expr] = None


@dataclass
class Parens(Expr):
    """A parenthesised expression; kept so spans and grouping survive into code generation."""

    inner: Optional[Expr] = None


@dataclass
class Call(Expr):
    """`name(arg, ...)`; the callee is a function name or the builtin `print`."""

    name: str = ""
    arguments: List[Expr] = field(default_factory=list)


@dataclass
class Stmt(Node):
    """Base class for statements."""


@dataclass
class Block(Stmt):
    """A brace-delimited statement sequence."""

    statements: List[Stmt] = field(default_factory=list)


@dataclass
class Let(Stmt):
    """`let x: i64 = e;` (immutable) or `var x: i64 = e;` (mutable)."""

    name: str = ""
    mutable: bool = False
    type_name: str = ""
    value: Optional[Expr] = None
    slot: int = -1


@dataclass
class Assign(Stmt):
    """`x = e;`, valid only for a `var` binding."""

    name: str = ""
    value: Optional[Expr] = None
    slot: int = -1


@dataclass
class If(Stmt):
    """`if cond { ... } else { ... }`, where the else block is optional."""

    condition: Optional[Expr] = None
    then_block: Optional[Block] = None
    else_block: Optional[Block] = None


@dataclass
class While(Stmt):
    """`while cond { ... }`."""

    condition: Optional[Expr] = None
    body: Optional[Block] = None


@dataclass
class Return(Stmt):
    """`return e;`; every function returns an i64."""

    value: Optional[Expr] = None


@dataclass
class Break(Stmt):
    """Leave the innermost loop."""


@dataclass
class Continue(Stmt):
    """Start the next iteration of the innermost loop."""


@dataclass
class ExprStmt(Stmt):
    """An expression evaluated for its effect, which in Cairn means a call."""

    expression: Optional[Expr] = None


@dataclass
class Parameter(Node):
    """One parameter: a name and its declared type."""

    name: str = ""
    type_name: str = ""
    slot: int = -1


@dataclass
class Function(Node):
    """A top-level function; `slots` is the number of 8-byte frame slots it needs."""

    name: str = ""
    parameters: List[Parameter] = field(default_factory=list)
    return_type: str = "i64"
    body: Optional[Block] = None
    slots: int = 0


@dataclass
class Program(Node):
    """A whole source file."""

    functions: List[Function] = field(default_factory=list)


__all__ = [
    "Assign", "Binary", "Block", "Break", "Call", "Continue", "Expr", "ExprStmt", "Function",
    "If", "IntLiteral", "Let", "Name", "Node", "Parameter", "Parens", "Program", "Return",
    "Stmt", "Unary", "While",
]
