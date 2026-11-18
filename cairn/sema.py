"""Semantic analysis: names, arity, mutability and return-path checks.

Every check that can be made before machine code is made here, so the backend never has to
report a user error. All findings are collected and reported together: an agent repairing a file
sees every problem in one pass instead of one per build.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from cairn import syntax
from cairn.diagnostics import Diagnostic, DiagnosticError
from cairn.optimize import INT64_MAX

BUILTINS = {"print": 1, "print_char": 1, "print_hex": 1,
            "argc": 0, "arg_byte": 2, "read_char": 0, "exit": 1,
            "print_str": 1, "str_len": 1, "str_byte": 2, "flush": 0}
# `exit(code)` stops the program, so it has no value: it may sit only as its own
# statement, like a `return`, and any other position is refused (E3013).
STOPS_PROGRAM = frozenset({"exit"})
MAX_PARAMETERS = 8


@dataclass
class Binding:
    """One name in a function: its frame slot, whether it can be assigned, and where it began."""

    slot: int
    mutable: bool
    span: object
    array_size: Optional[int] = None


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
                             f"parameters; Cairn carries four in registers",
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
            if parameter.name in BUILTINS:
                self._report("E3012", f"'{parameter.name}' is a builtin and cannot be redefined",
                             "Rename the parameter; the builtin keeps its meaning.",
                             parameter.span)
                continue
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
        elif isinstance(statement, syntax.For):
            self._check_for(statement)
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
            expression = statement.expression
            while isinstance(expression, syntax.Parens):
                expression = expression.inner
            if (isinstance(expression, syntax.Call)
                    and expression.name in STOPS_PROGRAM):
                self._check_call(expression, allow_stop=True)
            else:
                self._check_expression(statement.expression)

    def _check_for(self, statement: syntax.For) -> None:
        """A `for` declares one scoped, mutable loop variable for its body, then restores.

        The start bound is evaluated in the enclosing scope; the end bound sees the loop
        variable, matching the lowered `while shape the loop actually runs as.
        """
        if statement.name in BUILTINS:
            self._report("E3012", f"'{statement.name}' is a builtin and cannot be redefined",
                         "Rename the loop variable.", statement.span)
            return
        self._check_expression(statement.start)
        outer = self.bindings.get(statement.name)
        statement.slot = self._new_slot()
        self.bindings[statement.name] = Binding(statement.slot, True, statement.span)
        self._check_expression(statement.end)
        self.loop_depth += 1
        self._check_block(statement.body)
        self.loop_depth -= 1
        if outer is None:
            del self.bindings[statement.name]
        else:
            self.bindings[statement.name] = outer

    def _check_binding(self, statement: syntax.Let) -> None:
        """A `let`/`var` declares one new name: a scalar initialises from one i64
        expression, an array needs a literal whose length is the declared size and
        reserves that many consecutive frame slots."""
        if statement.name in BUILTINS:
            self._report("E3012", f"'{statement.name}' is a builtin and cannot be redefined",
                         "Rename the variable.", statement.span)
            self._check_binding_initializer(statement, None)
            return
        if statement.name in self.bindings:
            self._report("E3006", f"'{statement.name}' is already declared in this function",
                         "Use a new name; Cairn has no shadowing.", statement.span)
            self._check_binding_initializer(statement, None)
            return
        size = statement.array_size
        if size is not None and not 1 <= size <= 65536:
            self._report("E3014",
                         f"array '{statement.name}' declares {size} element(s)",
                         "Declare between 1 and 65536 elements.",
                         statement.span)
            size = None
        self._check_binding_initializer(statement, size)
        if size is not None:
            statement.slot = self._new_array_slots(size)
        else:
            statement.slot = self._new_slot()
        self.bindings.setdefault(statement.name, Binding(statement.slot, statement.mutable,
                                                         statement.span, size))

    def _check_binding_initializer(self, statement: syntax.Let,
                                   size: Optional[int]) -> None:
        """Check one binding's initializer against the declared shape.

        `size is None` means a scalar initializer or a refused array declaration:
        the elements are still walked so that their own errors surface, but no
        shape diagnostic is added on top of the size refusal.
        """
        value = statement.value
        while isinstance(value, syntax.Parens):
            value = value.inner
        if isinstance(value, syntax.ArrayLiteral):
            for element in value.elements:
                self._check_expression(element)
        elif size is not None:
            self._report("E3014",
                         f"array '{statement.name}' of {size} element(s) needs an array "
                         "literal",
                         f"Write '[e0, e1, ...]' with exactly {size} element(s).",
                         statement.span)
            return
        else:
            self._check_expression(statement.value)
            return
        if size is not None and len(value.elements) != size:
            self._report("E3014",
                         f"the literal for '{statement.name}' has {len(value.elements)} "
                         f"element(s), declared [{size}]",
                         f"Write exactly {size} element(s) in the literal.",
                         value.span)

    def _new_array_slots(self, count: int) -> int:
        """Allocate `count` consecutive 8-byte frame slots; the first is the base."""
        slot = self.slots
        self.slots += count
        return slot

    def _check_assign(self, statement: syntax.Assign) -> None:
        """Assignment requires an existing `var` binding; `a[i] = e;` a `var` array."""
        if statement.name in BUILTINS:
            self._report("E3012", f"'{statement.name}' is a builtin and cannot be redefined",
                         "Rename the variable; the builtin keeps its meaning.", statement.span)
            binding = None
        else:
            binding = self.bindings.get(statement.name)
        if statement.index is not None:
            self._check_element_store(statement, binding)
            return
        if binding is None:
            if statement.name not in BUILTINS:
                self._report("E3007", f"'{statement.name}' is not declared",
                             "Declare it first with 'var name: i64 = value;'.", statement.span)
        elif not binding.mutable:
            self._report("E3005", f"'{statement.name}' is immutable",
                         "Declare it with 'var' if it must change, otherwise remove the "
                         "assignment.", statement.span)
        else:
            statement.slot = binding.slot
        self._check_expression(statement.value)

    def _check_element_store(self, statement: syntax.Assign,
                             binding: Optional[Binding]) -> None:
        """`a[index] = value;` needs a declared `var` array; scalars refuse indexing.

        A dynamic index reserves one scratch slot: the computed address must survive
        the value expression's evaluation, which may call and clobber every register.
        """
        if binding is None:
            if statement.name not in BUILTINS:
                self._report("E3007", f"'{statement.name}' is not declared",
                             "Declare it first with 'var a: [N]i64 = [...];'.",
                             statement.span)
        elif binding.array_size is None:
            self._report("E3015", f"'{statement.name}' is not an array",
                         "Index only arrays declared as '[N]i64'.", statement.span)
        elif not binding.mutable:
            self._report("E3005", f"'{statement.name}' is immutable",
                         "Declare the array with 'var' to change its elements.",
                         statement.span)
        else:
            statement.slot = binding.slot
        size = binding.array_size if binding is not None else None
        self._check_element_index(statement.index, size)
        self._check_expression(statement.value)
        if (statement.slot >= 0 and size is not None
                and _literal_index(statement.index) is None):
            statement.temp_slot = self._new_slot()

    def _check_element_index(self, index: syntax.Expr, size: Optional[int]) -> None:
        """Check one index expression; a literal index must land inside the array.

        A dynamic index stays unchecked at compile time: out-of-range access is
        undefined, matching Cairn's zero-overhead memory contract.
        """
        self._check_expression(index)
        if size is None:
            return
        position = _literal_index(index)
        if position is not None and not 0 <= position < size:
            self._report("E3017",
                         f"index {position} is outside an array of {size} element(s)",
                         f"Use an index between 0 and {size - 1}.", index.span)

    def _check_expression(self, expression: Optional[syntax.Expr]) -> None:
        """Dispatch one expression to its checker."""
        if expression is None:
            return
        if isinstance(expression, syntax.IntLiteral):
            if expression.value > INT64_MAX:
                self._report("E1003",
                             f"integer literal {expression.value} does not fit in i64",
                             "Write a value between -9223372036854775808 and 9223372036854775807.",
                             expression.span)
            return
        if isinstance(expression, syntax.StrLiteral):
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
        elif isinstance(expression, syntax.Index):
            self._check_index_load(expression)
        elif isinstance(expression, syntax.Call):
            self._check_call(expression)

    def _check_index_load(self, expression: syntax.Index) -> None:
        """`a[i]` loads one element; it needs a declared array, never a scalar."""
        operand = expression.operand
        binding = None
        if not isinstance(operand, syntax.Name):
            self._report("E3015", "only an array name can be indexed",
                         "Write 'a[index]' with `a` an array variable.", operand.span)
        elif operand.name in BUILTINS:
            self._report("E3015", f"'{operand.name}' is not an array",
                         "Index only arrays declared as '[N]i64'.", operand.span)
        else:
            binding = self.bindings.get(operand.name)
            if binding is None:
                self._report("E3007", f"'{operand.name}' is not declared",
                             "Declare it with 'let a: [N]i64 = [...];' before this line.",
                             operand.span)
            elif binding.array_size is None:
                self._report("E3015", f"'{operand.name}' is not an array",
                             "Index only arrays declared as '[N]i64'.", operand.span)
                binding = None
        size = binding.array_size if binding is not None else None
        self._check_element_index(expression.index, size)
        if binding is not None:
            expression.slot = binding.slot
            expression.size = size

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
        if binding.array_size is not None:
            self._report("E3016", f"array '{expression.name}' is not a value",
                         "Index it ('a[i]'); a whole array cannot be read or passed.",
                         expression.span)

    def _check_call(self, expression: syntax.Call,
                    allow_stop: bool = False) -> None:
        """A call names a function (or the builtin) and passes exactly its arity of arguments.

        A stop-program builtin carries no value, so it passes only where a statement walks
        it in directly (`allow_stop`); every value position, including other calls'
        arguments, refuses it.
        """
        if expression.name in STOPS_PROGRAM and not allow_stop:
            self._report("E3013",
                         f"'{expression.name}(...)' stops the program and has no value",
                         "Call it as its own statement, not inside another expression.",
                         expression.span)
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


def _literal_index(expression: syntax.Expr) -> Optional[int]:
    """A statically known index, or None for a dynamic one; parens are unwrapped."""
    inner = expression
    while isinstance(inner, syntax.Parens):
        inner = inner.inner
    if isinstance(inner, syntax.IntLiteral):
        return inner.value
    return None


def emitted_return(block: Optional[syntax.Block]) -> bool:
    """Whether every path through `block` ends in a `return` or an `exit` statement."""
    if block is None or not block.statements:
        return False
    last = block.statements[-1]
    if isinstance(last, syntax.Return):
        return True
    if isinstance(last, syntax.ExprStmt):
        expression = last.expression
        while isinstance(expression, syntax.Parens):
            expression = expression.inner
        if isinstance(expression, syntax.Call) and expression.name in STOPS_PROGRAM:
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
