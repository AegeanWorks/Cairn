"""Semantic analysis: names, arity, mutability and return-path checks.

Every check that can be made before machine code is made here, so the backend never has to
report a user error. All findings are collected and reported together: an agent repairing a file
sees every problem in one pass instead of one per build.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from cairn import syntax
from cairn.diagnostics import Diagnostic, DiagnosticError

BUILTINS = {"print": 1}
MAX_PARAMETERS = 4


@dataclass
class Binding:
    """One name in a function: its frame slot, whether it can be assigned, and where it began."""

    slot: int
    mutable: bool
    span: object


@dataclass
class Analyzer:
    """One pass over a parsed program, mutating slots and collecting diagnostics."""

    path: str = "<source>"
    diagnostics: List[Diagnostic] = field(default_factory=list)
    functions: Dict[str, syntax.Function] = field(default_factory=dict)
    bindings: Dict[str, Binding] = field(default_factory=dict)
    slots: int = 0
    loop_depth: int = 0
    current: Optional[syntax.Function] = None

    def analyze(self, program: syntax.Program) -> syntax.Program:
        """Check the whole program; raise every diagnostic at once if any was found."""
        self._collect_functions(program)
        self._check_entry_point(program)
        for function in program.functions:
            self._check_function(function)
        if self.diagnostics:
            raise DiagnosticError(self.diagnostics)
        return program

    def _report(self, code: str, message: str, fix: str, span) -> None:
        """Record one diagnostic and keep going."""
        self.diagnostics.append(Diagnostic(code, message, fix, span, self.path))

    def _collect_functions(self, program: syntax.Program) -> None:
        """Register top-level names and reject duplicates, redefinitions and arity overflow."""
        for function in program.functions:
            if function.name in BUILTINS:
                self._report("E3012", f"'{function.name}' is a builtin and cannot be redefined",
                             "Rename the function; the builtin keeps its meaning.",
                             function.span)
                continue
            if function.name in self.functions:
                self._report("E3001", f"function '{function.name}' is defined twice",
                             "Keep one definition, or rename one of them.", function.span)
                continue
            if len(function.parameters) > MAX_PARAMETERS:
                self._report("E3010",
                             f"function '{function.name}' takes {len(function.parameters)} "
                             f"parameters; Cairn passes at most {MAX_PARAMETERS} in registers",
                             f"Reduce the parameters to {MAX_PARAMETERS} or fewer, packing "
                             "the extras into one i64.",
                             function.span)
            self.functions[function.name] = function

    def _check_entry_point(self, program: syntax.Program) -> None:
        """`fn main() -> i64` is the executable's entry point and must take no parameters."""
        main = self.functions.get("main")
        if main is None:
            span = program.span
            self._report("E3011", "the program has no 'fn main() -> i64'",
                         "Add 'fn main() -> i64 { return 0; }' as the entry point.", span)
            return
        if main.parameters:
            self._report("E3011", "main must take no parameters",
                         "Declare 'fn main() -> i64' and read inputs from helpers instead.",
                         main.span)

    def _check_function(self, function: syntax.Function) -> None:
        """Check one function body with a fresh flat scope."""
        self.current = function
        self.bindings = {}
        self.slots = 0
        self.loop_depth = 0
        for parameter in function.parameters:
            if parameter.name in self.bindings:
                self._report("E3006", f"parameter '{parameter.name}' is declared twice",
                             "Give each parameter a distinct name.", parameter.span)
                continue
            parameter.slot = self._new_slot()
            self.bindings[parameter.name] = Binding(parameter.slot, True, parameter.span)
        self._check_block(function.body)
        function.slots = self.slots
        if not emitted_return(function.body):
            self._report("E3009", f"function '{function.name}' can finish without returning",
                         "End every path with 'return expression;'.",
                         function.span)

    def _new_slot(self) -> int:
        """Allocate the next 8-byte frame slot."""
        slot = self.slots
        self.slots += 1
        return slot

    def _check_block(self, block: Optional[syntax.Block]) -> None:
        """Check statements in order; the scope is flat and function-wide."""
        if block is None:
            return
        for statement in block.statements:
            self._check_statement(statement)

    def _check_statement(self, statement: syntax.Stmt) -> None:
        """Dispatch one statement to its checker."""
        if isinstance(statement, syntax.Let):
            self._check_binding(statement)
        elif isinstance(statement, syntax.Assign):
            self._check_assign(statement)
        elif isinstance(statement, syntax.If):
            self._check_expression(statement.condition)
            self._check_block(statement.then_block)
            self._check_block(statement.else_block)
        elif isinstance(statement, syntax.While):
            self._check_expression(statement.condition)
            self.loop_depth += 1
            self._check_block(statement.body)
            self.loop_depth -= 1
        elif isinstance(statement, syntax.Return):
            self._check_expression(statement.value)
        elif isinstance(statement, (syntax.Break, syntax.Continue)):
            if self.loop_depth == 0:
                kind = "break" if isinstance(statement, syntax.Break) else "continue"
                self._report("E3008", f"'{kind}' is only valid inside a 'while' loop",
                             "Move it inside a loop, or remove it.", statement.span)
        elif isinstance(statement, syntax.Block):
            self._check_block(statement)
        elif isinstance(statement, syntax.ExprStmt):
            self._check_expression(statement.expression)

    def _check_binding(self, statement: syntax.Let) -> None:
        """A `let`/`var` declares one new name and initialises it from an i64 expression."""
        if statement.name in BUILTINS:
            self._report("E3012", f"'{statement.name}' is a builtin and cannot be redefined",
                         "Rename the variable.", statement.span)
        elif statement.name in self.bindings:
            self._report("E3006", f"'{statement.name}' is already declared in this function",
                         "Use a new name; Cairn has no shadowing.", statement.span)
        self._check_expression(statement.value)
        statement.slot = self._new_slot()
        self.bindings.setdefault(statement.name, Binding(statement.slot, statement.mutable,
                                                         statement.span))

    def _check_assign(self, statement: syntax.Assign) -> None:
        """Assignment requires an existing `var` binding."""
        binding = self.bindings.get(statement.name)
        if binding is None:
            self._report("E3007", f"'{statement.name}' is not declared",
                         "Declare it first with 'var name: i64 = value;'.", statement.span)
        elif not binding.mutable:
            self._report("E3005", f"'{statement.name}' is immutable",
                         "Declare it with 'var' if it must change, otherwise remove the "
                         "assignment.", statement.span)
        else:
            statement.slot = binding.slot
        self._check_expression(statement.value)

    def _check_expression(self, expression: Optional[syntax.Expr]) -> None:
        """Dispatch one expression to its checker."""
        if expression is None:
            return
        if isinstance(expression, syntax.IntLiteral):
            return
        if isinstance(expression, syntax.Parens):
            self._check_expression(expression.inner)
        elif isinstance(expression, syntax.Name):
            self._check_name(expression)
        elif isinstance(expression, syntax.Unary):
            self._check_expression(expression.operand)
        elif isinstance(expression, syntax.Binary):
            self._check_expression(expression.left)
            self._check_expression(expression.right)
        elif isinstance(expression, syntax.Call):
            self._check_call(expression)

    def _check_name(self, expression: syntax.Name) -> None:
        """A name must be a declared local; `print` is callable but not a value."""
        if expression.name in BUILTINS:
            self._report("E3012", f"'{expression.name}' is a builtin function, not a value",
                         f"Call it as '{expression.name}(value)'.", expression.span)
            return
        binding = self.bindings.get(expression.name)
        if binding is None:
            self._report("E3007", f"'{expression.name}' is not declared",
                         "Declare it with 'let name: i64 = value;' before this line.",
                         expression.span)
            return
        expression.slot = binding.slot

    def _check_call(self, expression: syntax.Call) -> None:
        """A call names a function (or the builtin) and passes exactly its arity of arguments."""
        for argument in expression.arguments:
            self._check_expression(argument)
        expected = BUILTINS.get(expression.name)
        if expected is None:
            target = self.functions.get(expression.name)
            if target is None:
                self._report("E3003", f"function '{expression.name}' is not defined",
                             "Define it with 'fn', or call a name that exists.",
                             expression.span)
                return
            expected = len(target.parameters)
        if len(expression.arguments) != expected:
            self._report("E3004",
                         f"'{expression.name}' takes {expected} argument(s), "
                         f"not {len(expression.arguments)}",
                         f"Pass exactly {expected} i64 value(s).", expression.span)


def emitted_return(block: Optional[syntax.Block]) -> bool:
    """Whether every path through `block` ends in a `return` statement."""
    if block is None or not block.statements:
        return False
    last = block.statements[-1]
    if isinstance(last, syntax.Return):
        return True
    if isinstance(last, syntax.Block):
        return emitted_return(last)
    if isinstance(last, syntax.If) and last.else_block is not None:
        return emitted_return(last.then_block) and emitted_return(last.else_block)
    return False


def analyze(program: syntax.Program, path: str = "<source>") -> syntax.Program:
    """Run semantic analysis over one parsed program."""
    return Analyzer(path).analyze(program)


__all__ = ["BUILTINS", "MAX_PARAMETERS", "Analyzer", "analyze", "emitted_return"]
