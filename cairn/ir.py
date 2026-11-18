"""A deliberately minimal typed SSA IR between the AST and machine code.

The values are `i64` only, expression temporaries are single-assignment, and
mutable things (locals, parameters, short-circuit results) live in cells that
read as zero before any store, mirroring the zeroed-frame rule. Every block
ends in exactly one terminator (`return`, `exit`, `jump` or `branch`), so the
IR is directly executable: `Interpret` runs it at full machine semantics —
wrapping arithmetic, truncating division, traps — and the differential tests
prove it answers what the compiled executables answer. This is the substrate
the optimizer passes will operate on; the production pipeline is untouched.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from cairn import syntax
from cairn.runtime import STDOUT_BUFFER_SIZE

MAX_U64 = 0xFFFFFFFFFFFFFFFF
MAX_I64 = 0x7FFFFFFFFFFFFFFF
MIN_I64 = -0x8000000000000000

BINOPS = {"+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>",
          "==", "!=", "<", "<=", ">", ">="}
COMPARISONS = {"==", "!=", "<", "<=", ">", ">="}
TERMINATORS = ("jump", "branch", "return", "exit")
STEP_LIMIT = 2_000_000
CALL_LIMIT = 200_000


def to_signed(raw: int) -> int:
    """The wrapped 64-bit pattern `raw` re-read as a signed integer."""
    raw &= MAX_U64
    return raw - (MAX_U64 + 1) if raw > MAX_I64 else raw


class DivZeroTrap(Exception):
    """`/ 0` or `i64.min / -1`: the machine's message plus exit code 3."""


def trunc_divmod(left: int, right: int) -> Tuple[int, int]:
    """C semantics: quotient truncates toward zero, remainder keeps the sign.

    Raises `DivZeroTrap` for a zero divisor or `i64.min / -1`, exactly the
    machine trapping pair.
    """
    if right == 0 or (left == MIN_I64 and right == -1):
        raise DivZeroTrap()
    raw = abs(left) // abs(right)
    if (left < 0) != (right < 0):
        raw = -raw
    return raw, left - raw * right


def apply_binop(op: str, left: int, right: int) -> int:
    """One `i64` binary operation at machine semantics, signed values in and out."""
    if op == "+":
        return to_signed(left + right)
    if op == "-":
        return to_signed(left - right)
    if op == "*":
        return to_signed(left * right)
    if op == "/":
        return trunc_divmod(left, right)[0]
    if op == "%":
        return trunc_divmod(left, right)[1]
    if op == "&":
        return to_signed(left & right)
    if op == "|":
        return to_signed(left | right)
    if op == "^":
        return to_signed(left ^ right)
    if op == "<<":
        return to_signed(left << (right & 63))
    if op == ">>":
        return left >> (right & 63)
    if op == "==":
        return int(left == right)
    if op == "!=":
        return int(left != right)
    if op == "<":
        return int(left < right)
    if op == "<=":
        return int(left <= right)
    if op == ">":
        return int(left > right)
    if op == ">=":
        return int(left >= right)
    raise AssertionError(f"ir: unknown binary operator {op!r}")


@dataclass
class Op:
    """One instruction; the last op of every block is always a terminator.

    `kind` selects the meaning: `const` (value), `const_str` (text), `cell_load`
    (cell), `cell_store` (cell, lhs), `bin` (op, lhs, rhs), `unary` (op, lhs),
    `param` (text = the declared parameter name), `call` (op = callee name,
    args), `jump` (target), `branch` (lhs, target, other), `return` (lhs),
    `exit` (lhs), `array_load` (cell = base cell, lhs = index), `array_store`
    (cell = base cell, lhs = index, rhs = value). `dest` is the SSA result
    slot of a value-producing op.
    """

    kind: str
    dest: int = -1
    lhs: int = -1
    rhs: int = -1
    value: int = 0
    op: str = ""
    cell: int = -1
    target: str = ""
    other: str = ""
    text: str = ""
    args: List[int] = field(default_factory=list)


@dataclass
class Block:
    """One basic block: straight-line ops, then exactly one terminator."""

    label: str
    ops: List[Op] = field(default_factory=list)


@dataclass
class Function:
    """One lowered function: parameter cells in call order, blocks in emit order."""

    name: str
    parameters: Tuple[str, ...] = ()
    parameter_cells: Tuple[int, ...] = ()
    cells: int = 0
    values: int = 0
    blocks: List[Block] = field(default_factory=list)


@dataclass
class Module:
    """Every function of one program, callable by name."""

    functions: Dict[str, Function] = field(default_factory=dict)


class _Translator:
    """AST to IR: expressions become SSA values, control flow becomes blocks."""

    def __init__(self) -> None:
        self.module = Module()
        self.blocks: List[Block] = []
        self.current = Block(label="entry")
        self.values = 0
        self.cells = 0
        self.slot_cells: Dict[int, int] = {}
        self.loop_cond: List[str] = []
        self.loop_exit: List[str] = []
        self.labels = 0

    def _fresh_value(self) -> int:
        self.values += 1
        return self.values - 1

    def _fresh_cell(self) -> int:
        self.cells += 1
        return self.cells - 1

    def _fresh_label(self, prefix: str) -> str:
        self.labels += 1
        return f"{prefix}.l{self.labels}"

    @staticmethod
    def terminates(block: Block) -> bool:
        return bool(block.ops) and block.ops[-1].kind in TERMINATORS

    def open_block(self, label: str) -> None:
        self.blocks.append(self.current)
        self.current = Block(label=label)

    def close_with(self, op: Op) -> None:
        self.current.ops.append(op)

    def emit_jump_to(self, target: str) -> None:
        if not self.terminates(self.current):
            self.close_with(Op(kind="jump", target=target))

    def emit(self, op: Op) -> int:
        self.current.ops.append(op)
        return op.dest

    def const(self, value: int) -> int:
        zero = self.emit(Op(kind="const", dest=self._fresh_value(), value=value))
        return zero

    def value_of(self, expression: syntax.Expr) -> int:
        """Lower one expression, returning the SSA value id holding its result."""
        while isinstance(expression, syntax.Parens):
            expression = expression.inner
        if isinstance(expression, syntax.IntLiteral):
            return self.const(expression.value)
        if isinstance(expression, syntax.StrLiteral):
            return self.emit(Op(kind="const_str", dest=self._fresh_value(),
                                text=expression.text))
        if isinstance(expression, syntax.Name):
            return self.emit(Op(kind="cell_load", dest=self._fresh_value(),
                                cell=self.slot_cells[expression.slot]))
        if isinstance(expression, syntax.Unary):
            operand = self.value_of(expression.operand)
            if expression.op == "!":
                return self.emit(Op(kind="bin", dest=self._fresh_value(), op="==",
                                    lhs=operand, rhs=self.const(0)))
            return self.emit(Op(kind="unary", dest=self._fresh_value(), op=expression.op,
                                lhs=operand))
        if isinstance(expression, syntax.Binary):
            if expression.op in ("&&", "||"):
                return self.short_circuit(expression)
            left = self.value_of(expression.left)
            right = self.value_of(expression.right)
            return self.emit(Op(kind="bin", dest=self._fresh_value(), op=expression.op,
                                lhs=left, rhs=right))
        if isinstance(expression, syntax.Index):
            base = self.slot_cells[expression.slot]
            static = _static_index(expression.index)
            if static is not None:
                return self.emit(Op(kind="cell_load", dest=self._fresh_value(),
                                    cell=base + static))
            index = self.value_of(expression.index)
            return self.emit(Op(kind="array_load", dest=self._fresh_value(),
                                cell=base, lhs=index))
        if isinstance(expression, syntax.Call):
            return self.emit(Op(kind="call", dest=self._fresh_value(),
                                op=expression.name,
                                args=[self.value_of(arg) for arg in expression.arguments]))
        raise AssertionError(f"ir: unsupported expression node {type(expression).__name__}")

    def short_circuit(self, expression: syntax.Binary) -> int:
        """`&&`/`||` lower to branches across one result cell; unstored is false.

        The zero-initialised cell carries the false short path for `&&`; `||`'s
        short path is true, so it stores an explicit 1.
        """
        gate = self._fresh_label("sc")
        end = self._fresh_label("sc")
        result_cell = self._fresh_cell()
        left = self.value_of(expression.left)
        probe = self.emit(Op(kind="bin", dest=self._fresh_value(),
                             op="!=", lhs=left, rhs=self.const(0)))
        if expression.op == "&&":
            self.close_with(Op(kind="branch", lhs=probe, target=gate, other=end))
            self.open_block(gate)
            right = self.value_of(expression.right)
            bit = self.emit(Op(kind="bin", dest=self._fresh_value(),
                               op="!=", lhs=right, rhs=self.const(0)))
            self.emit(Op(kind="cell_store", cell=result_cell, lhs=bit))
        else:
            short = self._fresh_label("sc")
            self.close_with(Op(kind="branch", lhs=probe, target=short, other=gate))
            self.open_block(short)
            self.emit(Op(kind="cell_store", cell=result_cell,
                         lhs=self.const(1)))
            self.emit_jump_to(end)
            self.open_block(gate)
            right = self.value_of(expression.right)
            bit = self.emit(Op(kind="bin", dest=self._fresh_value(),
                               op="!=", lhs=right, rhs=self.const(0)))
            self.emit(Op(kind="cell_store", cell=result_cell, lhs=bit))
        self.emit_jump_to(end)
        self.open_block(end)
        return self.emit(Op(kind="cell_load", dest=self._fresh_value(),
                            cell=result_cell))

    def lower_function(self, function: syntax.Function) -> None:
        if any(isinstance(stmt, syntax.For)
               for stmt in function.body.statements):
            raise AssertionError("ir: lower for-loops first (builder.lower_for_loops)")
        self.blocks = []
        self.current = Block(label="entry")
        self.values = 0
        self.cells = 0
        self.slot_cells = {}
        self.labels = 0
        parameter_cells = []
        for parameter in function.parameters:
            cell = self._fresh_cell()
            self.slot_cells[parameter.slot] = cell
            parameter_cells.append(cell)
            self.emit(Op(kind="param", text=parameter.name))
        lowered = Function(name=function.name,
                           parameters=tuple(p.name for p in function.parameters),
                           parameter_cells=tuple(parameter_cells),
                           cells=0,
                           values=0,
                           blocks=[])
        for statement in function.body.statements:
            self.emit_stmt(statement)
        if not self.terminates(self.current):
            self.close_with(Op(kind="return", lhs=self.const(0)))
        self.blocks.append(self.current)
        lowered.cells = self.cells
        lowered.values = self.values
        lowered.blocks = self.blocks
        self.module.functions[function.name] = lowered

    def emit_stmt(self, statement: syntax.Stmt) -> None:
        if self.terminates(self.current):
            return  # statements after a terminator are unreachable; drop them
        if isinstance(statement, syntax.Let):
            if statement.array_size is not None:
                first = self._fresh_cell()
                self.slot_cells[statement.slot] = first
                self._fresh_cell_many(statement.array_size - 1)
                for offset, element in enumerate(
                        self._initializer_elements(statement)):
                    value = self.value_of(element)
                    self.emit(Op(kind="cell_store", cell=first + offset, lhs=value))
                return
            cell = self._fresh_cell()
            self.slot_cells[statement.slot] = cell
            value = self.value_of(statement.value)
            self.emit(Op(kind="cell_store", cell=cell, lhs=value))
            return
        if isinstance(statement, syntax.Assign):
            if statement.index is not None:
                base = self.slot_cells[statement.slot]
                static = _static_index(statement.index)
                if static is not None:
                    value = self.value_of(statement.value)
                    self.emit(Op(kind="cell_store", cell=base + static, lhs=value))
                else:
                    index = self.value_of(statement.index)
                    value = self.value_of(statement.value)
                    self.emit(Op(kind="array_store", cell=base, lhs=index, rhs=value))
                return
            value = self.value_of(statement.value)
            self.emit(Op(kind="cell_store", cell=self.slot_cells[statement.slot],
                         lhs=value))
            return
        if isinstance(statement, syntax.Return):
            value = (self.value_of(statement.value) if statement.value is not None
                     else self.const(0))
            self.close_with(Op(kind="return", lhs=value))
            return
        if isinstance(statement, syntax.ExprStmt):
            self.value_of(statement.expression)
            return
        if isinstance(statement, syntax.If):
            condition = self.value_of(statement.condition)
            self.close_with(Op(kind="branch", lhs=condition,
                               target=self._fresh_label("if"),
                               other=self._fresh_label("else")))
            then_label = self.current.ops[-1].target
            else_label = self.current.ops[-1].other
            merge_label = self._fresh_label("end")
            self.open_block(then_label)
            for stmt in statement.then_block.statements:
                self.emit_stmt(stmt)
            self.emit_jump_to(merge_label)
            self.open_block(else_label)
            for stmt in (statement.else_block.statements
                         if statement.else_block is not None else []):
                self.emit_stmt(stmt)
            self.emit_jump_to(merge_label)
            self.open_block(merge_label)
            return
        if isinstance(statement, syntax.While):
            cond_label = self._fresh_label("loop")
            body_label = self._fresh_label("body")
            merge_label = self._fresh_label("done")
            self.emit_jump_to(cond_label)
            self.open_block(cond_label)
            condition = self.value_of(statement.condition)
            self.close_with(Op(kind="branch", lhs=condition, target=body_label,
                               other=merge_label))
            self.open_block(body_label)
            self.loop_cond.append(cond_label)
            self.loop_exit.append(merge_label)
            for stmt in statement.body.statements:
                self.emit_stmt(stmt)
            self.loop_cond.pop()
            self.loop_exit.pop()
            self.emit_jump_to(cond_label)
            self.open_block(merge_label)
            return
        if isinstance(statement, syntax.Break):
            self.close_with(Op(kind="jump", target=self.loop_exit[-1]))
            return
        if isinstance(statement, syntax.Continue):
            self.close_with(Op(kind="jump", target=self.loop_cond[-1]))
            return
        raise AssertionError(f"ir: unsupported statement node {type(statement).__name__}")

    def _fresh_cell_many(self, count: int) -> None:
        """Reserve `count` cell ids beyond the last one already issued."""
        self.cells += count

    def _initializer_elements(self, statement: syntax.Let) -> List[syntax.Expr]:
        """The element expressions of an array binding, parens unwrapped.

        Sematic analysis guarantees an ArrayLiteral of the declared length.
        """
        value = statement.value
        while isinstance(value, syntax.Parens):
            value = value.inner
        return value.elements


def _static_index(expression: syntax.Expr):
    """A compile-time element position, or None; sema already range-checks this."""
    inner = expression
    while isinstance(inner, syntax.Parens):
        inner = inner.inner
    return inner.value if isinstance(inner, syntax.IntLiteral) else None


def translate(program: syntax.Program) -> Module:
    """Lower one optimized program (for-loops already lowered) into a module."""
    builder = _Translator()
    for function in program.functions:
        builder.lower_function(function)
    return builder.module


class Interpret:
    """Run a translated module at full machine semantics.

    `run` answers `(exit code, stdout bytes)`: a `return`/`exit` value is the
    exit code, a `DivZeroTrap` maps to the machine's exit 3 (its message is a
    stderr-only artifact), `read_char` consumes `stdin`, and the print builtins
    append to `stdout` exactly as the runtime's buffered stream delivers them —
    pending bytes board a 64 KiB shadow of the shared buffer in emit order,
    `flush()` answers and drains it, the run's end drains it, and a trap
    abandons it, all matching the machine.
    """

    def __init__(self, stdin: bytes = b"") -> None:
        self.stdin = stdin
        self.cursor = 0
        self.stdout = bytearray()
        self.pending = bytearray()
        self.steps = 0
        self.depth = 0

    def run(self, module: Module, start: str = "main") -> Tuple[int, bytes]:
        try:
            _, code = self._call(module, start, [])
        except _ProcessExit as stop:
            code = stop.code
        except DivZeroTrap:
            code = 3
            return code, bytes(self.stdout)  # the machine loses pending bytes too
        self._drain()
        return code, bytes(self.stdout)

    def _emit(self, chunk: bytes) -> None:
        """Append output bytes the way the runtime's buffer boards them."""
        self.pending += chunk
        while len(self.pending) >= STDOUT_BUFFER_SIZE:
            self.stdout += self.pending[:STDOUT_BUFFER_SIZE]
            del self.pending[:STDOUT_BUFFER_SIZE]

    def _drain(self) -> None:
        self.stdout += self.pending
        self.pending.clear()

    def _call(self, module: Module, name: str,
              arguments: List[int]) -> Tuple[str, int]:
        self.depth += 1
        if self.depth > CALL_LIMIT:
            raise RecursionError(f"ir: call depth beyond {CALL_LIMIT}")
        function = module.functions.get(name)
        if function is None:
            raise AssertionError(f"ir: call to undefined function {name!r}")
        if len(arguments) != len(function.parameter_cells):
            raise AssertionError(
                f"ir: {name} wants {len(function.parameter_cells)} arguments, "
                f"got {len(arguments)}")
        try:
            cells = [0] * function.cells
            for cell, value in zip(function.parameter_cells, arguments):
                cells[cell] = value
            vals: List = [0] * function.values
            block = function.blocks[0]
            index = 0
            wanted_params = len(arguments)
            filled_params = 0
            while True:
                self.steps += 1
                if self.steps > STEP_LIMIT:
                    raise RecursionError(f"ir: step budget beyond {STEP_LIMIT}")
                op = block.ops[index]
                if op.kind == "jump":
                    block = self._by_label(function, op.target)
                    index = 0
                    continue
                if op.kind == "branch":
                    block = self._by_label(
                        function, op.target if vals[op.lhs] != 0 else op.other)
                    index = 0
                    continue
                if op.kind == "return":
                    return "return", vals[op.lhs]
                if op.kind == "exit":
                    return "exit", vals[op.lhs]
                if op.kind == "const":
                    vals[op.dest] = op.value
                elif op.kind == "const_str":
                    vals[op.dest] = op.text
                elif op.kind == "param":
                    cells[op.cell] = arguments[filled_params]
                    filled_params += 1
                elif op.kind == "cell_load":
                    vals[op.dest] = cells[op.cell]
                elif op.kind == "cell_store":
                    cells[op.cell] = vals[op.lhs]
                elif op.kind == "array_load":
                    vals[op.dest] = cells[op.cell + vals[op.lhs]]
                elif op.kind == "array_store":
                    cells[op.cell + vals[op.lhs]] = vals[op.rhs]
                elif op.kind == "unary":
                    operand = vals[op.lhs]
                    if op.op == "-":
                        vals[op.dest] = to_signed(-operand)
                    else:
                        vals[op.dest] = to_signed(~operand)
                elif op.kind == "bin":
                    vals[op.dest] = apply_binop(op.op, vals[op.lhs], vals[op.rhs])
                elif op.kind == "call":
                    kind, answer = self._builtin(module, op, vals)
                    if kind == "exit":
                        raise _ProcessExit(answer)
                    vals[op.dest] = answer
                else:
                    raise AssertionError(f"ir: unknown op kind {op.kind!r}")
                index += 1
        finally:
            self.depth -= 1

    def _builtin(self, module: Module, op: Op, vals: List) -> Tuple[str, int]:
        name = op.op
        if name == "print":
            self._emit(encode_number(vals[op.args[0]]) + b"\n")
            return "return", vals[op.args[0]]
        if name == "print_char":
            self._emit(bytes([vals[op.args[0]] & 0xFF]))
            return "return", vals[op.args[0]]
        if name == "print_hex":
            self._emit(format(vals[op.args[0]] & MAX_U64, "x").encode("ascii"))
            return "return", vals[op.args[0]]
        if name == "print_str":
            self._emit(vals[op.args[0]].encode("utf-8"))
            return "return", vals[op.args[0]]
        if name == "str_len":
            return "return", len(vals[op.args[0]].encode("utf-8"))
        if name == "str_byte":
            # Machine contract: negative j or the NUL at j answer -1; reads
            # past the terminator are machine-undefined (adjacent rodata) and
            # modelled here as -1, so differential cases stay within the
            # defined domain (j <= len).
            encoded = vals[op.args[0]].encode("utf-8")
            j = vals[op.args[1]]
            if 0 <= j < len(encoded):
                return "return", encoded[j]
            return "return", -1
        if name == "read_char":
            if self.cursor < len(self.stdin):
                byte = self.stdin[self.cursor]
                self.cursor += 1
                return "return", byte
            return "return", -1
        if name == "flush":
            # The machine answers how many pending bytes the call drained.
            answer = len(self.pending)
            self._drain()
            return "return", answer
        if name == "exit":
            return "exit", vals[op.args[0]]
        # A user function: its arguments evaluated left to right in the caller.
        kind, answer = self._call(module, name, [vals[arg] for arg in op.args])
        if kind == "exit":
            raise _ProcessExit(answer)
        return "return", answer

    def _by_label(self, function: Function, label: str) -> Block:
        for block in function.blocks:
            if block.label == label:
                return block
        raise AssertionError(f"ir: no block labelled {label!r} in {function.name}")


class _ProcessExit(Exception):
    """`exit(code)` called from inside a user function: unwinds the whole walk."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def encode_number(value: int) -> bytes:
    """The runtime's decimal form of one signed `i64` (no newline)."""
    return str(value).encode("ascii")
