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
class StrLiteral(Expr):
    """A quoted string literal: read-only bytes in .rodata, NUL-terminated.

    Its value is the address of its first byte as an `i64` (a C-programmer's string
    decay), shared with any other literal of the same content; `label` is filled in
    by code generation.
    """

    text: str = ""
    label: str = ""


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
class ArrayLiteral(Expr):
    """An array literal `[e0, e1, ...]`; a fill `[v; N]` is expanded to N copies of `v`.

    Every element expression is evaluated in source order at binding initialisation,
    once per element, with the leftmost element first. The literal is only valid as a
    binding's initializer.
    """

    elements: List[Expr] = field(default_factory=list)


@dataclass
class Index(Expr):
    """`a[i]`; `a` must name an array binding. `slot` is the array's base slot and
    `size` its declared element count, both filled in by semantic analysis."""

    operand: Optional[Expr] = None
    index: Optional[Expr] = None
    slot: int = -1
    size: int = 0


@dataclass
class Stmt(Node):
    """Base class for statements."""


@dataclass
class Block(Stmt):
    """A brace-delimited statement sequence."""

    statements: List[Stmt] = field(default_factory=list)


@dataclass
class Let(Stmt):
    """`let x: i64 = e;` (immutable) or `var x: i64 = e;` (mutable).

    A `[N]i64` binding names zero `array_size` and reserves N frame slots: `slot` is
    the first of them, elements at `slot + 0 .. slot + N - 1`, ascending.
    """

    name: str = ""
    mutable: bool = False
    type_name: str = ""
    value: Optional[Expr] = None
    slot: int = -1
    array_size: Optional[int] = None


@dataclass
class Assign(Stmt):
    """`x = e;`, valid only for a `var` binding; `a[i] = e;` stores into one element."""

    name: str = ""
    value: Optional[Expr] = None
    slot: int = -1
    index: Optional[Expr] = None
    temp_slot: Optional[int] = None


@dataclass
class If(Stmt):
    """`if cond { ... } else { ... }`, where the else block is optional."""

    condition: Optional[Expr] = None
    then_block: Optional[Block] = None
    else_block: Optional[Block] = None


@dataclass
class While(Stmt):
    """`while cond { ... }`; `countdown` marks a counter loop the optimiser converted."""

    condition: Optional[Expr] = None
    body: Optional[Block] = None
    countdown: bool = False


@dataclass
class For(Stmt):
    """`for name in start..end { ... }`, or `start..=end` for the inclusive bound.

    Semantic analysis gives the loop variable its own scoped slot; the optimiser lowers
    the node to a mutable `let` plus a `while`, so code generation never sees it.
    """

    name: str = ""
    start: Optional[Expr] = None
    end: Optional[Expr] = None
    inclusive: bool = False
    body: Optional[Block] = None
    slot: int = -1


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
    "Assign", "Binary", "Block", "Break", "Call", "Continue", "Expr", "ExprStmt", "For",
    "Function", "If", "IntLiteral", "Let", "Name", "Node", "Parameter", "Parens", "Program",
    "Return", "Stmt", "Unary", "While",
]
