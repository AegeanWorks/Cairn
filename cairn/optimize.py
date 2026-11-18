"""Optimiser: constant folding and provably safe algebraic simplification.

The pass runs after semantic analysis and before code generation, and it is deliberately
conservative: a rewrite happens only when the result is identical to what the machine code
would compute, including wrapping arithmetic, arithmetic right shift, and the divide-by-zero
trap. `print` is an effect, so no rewrite may drop or duplicate a call.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from cairn import syntax
from cairn.diagnostics import Span

INT64_MIN = -(2 ** 63)
INT64_MAX = 2 ** 63 - 1
MASK64 = (1 << 64) - 1

COMPARISONS = {
    "==": lambda a, b: int(a == b),
    "!=": lambda a, b: int(a != b),
    "<": lambda a, b: int(a < b),
    "<=": lambda a, b: int(a <= b),
    ">": lambda a, b: int(a > b),
    ">=": lambda a, b: int(a >= b),
}

# Where each operator has a neutral right operand, and (separately) a neutral left operand.
RIGHT_NEUTRAL = {"+": 0, "-": 0, "|": 0, "^": 0, "<<": 0, ">>": 0, "*": 1}
LEFT_NEUTRAL = {"+": 0, "|": 0, "^": 0, "*": 1}


def wrap(value: int) -> int:
    """Reduce an exact integer to the i64 value the machine would hold."""
    value &= MASK64
    return value - (1 << 64) if value > INT64_MAX else value


def truncate_division(left: int, right: int) -> int:
    """Signed division truncating toward zero, the way `idiv` does."""
    quotient = abs(left) // abs(right)
    return quotient if (left < 0) == (right < 0) else -quotient


def fold_constant(op: str, left: int, right: int) -> Optional[int]:
    """The folded value of `left op right`, or None when folding would change behaviour."""
    if op in COMPARISONS:
        return COMPARISONS[op](left, right)
    if op in ("/", "%"):
        if right == 0 or (left == INT64_MIN and right == -1):
            return None
        quotient = truncate_division(left, right)
        return quotient if op == "/" else left - quotient * right
    if op == "+":
        return wrap(left + right)
    if op == "-":
        return wrap(left - right)
    if op == "*":
        return wrap(left * right)
    if op == "&":
        return wrap(left & right)
    if op == "|":
        return wrap(left | right)
    if op == "^":
        return wrap(left ^ right)
    if op == "<<":
        return wrap(left << (right & 63))
    if op == ">>":
        return wrap(left >> (right & 63))
    if op == "&&":
        return int(bool(left) and bool(right))
    if op == "||":
        return int(bool(left) or bool(right))
    return None


def fold_unary(op: str, value: int) -> Optional[int]:
    """The folded value of a unary operator, or None when it must stay dynamic."""
    if op == "-":
        return wrap(-value)
    if op == "~":
        return wrap(~value)
    if op == "!":
        return int(value == 0)
    return None


def has_call(expression: Optional[syntax.Expr]) -> bool:
    """Whether evaluating `expression` can run a call, and therefore an effect."""
    if expression is None:
        return False
    if isinstance(expression, syntax.Call):
        return True
    if isinstance(expression, syntax.Parens):
        return has_call(expression.inner)
    if isinstance(expression, syntax.Unary):
        return has_call(expression.operand)
    if isinstance(expression, syntax.Binary):
        return has_call(expression.left) or has_call(expression.right)
    if isinstance(expression, syntax.Index):
        return has_call(expression.operand) or has_call(expression.index)
    if isinstance(expression, syntax.ArrayLiteral):
        return any(has_call(element) for element in expression.elements)
    return False


def _contains_index(expression) -> bool:
    """Whether an element load sits anywhere under one expression tree.

    The pure inliner refuses such bodies: an array binding lives in its own
    function's frame, so the array name's slot means nothing at a call site.
    """
    if isinstance(expression, syntax.Index):
        return True
    if isinstance(expression, syntax.Parens):
        return _contains_index(expression.inner)
    if isinstance(expression, syntax.Unary):
        return _contains_index(expression.operand)
    if isinstance(expression, syntax.Binary):
        return _contains_index(expression.left) or _contains_index(expression.right)
    if isinstance(expression, syntax.Call):
        return any(_contains_index(argument) for argument in expression.arguments)
    if isinstance(expression, syntax.ArrayLiteral):
        return any(_contains_index(element) for element in expression.elements)
    return False


def _may_trap(expression: Optional[syntax.Expr]) -> bool:
    """Whether evaluating `expression` can trap on `/` or `%`.

    A divisor that is provably a safe non-zero literal (and not the single
    `INT64_MIN / -1` overflow) cannot trap; anything else carrying a `/` or
    `%` must be kept so the backend still emits its trap check.
    """
    if expression is None:
        return False
    if isinstance(expression, syntax.Binary):
        if expression.op in ("/", "%"):
            left = _constant(expression.left)
            right = _constant(expression.right)
            if (right is not None and right != 0
                    and not (right == -1 and (left is None or left == INT64_MIN))):
                # Provably safe divisor; only the operands themselves could trap.
                return _may_trap(expression.left) or _may_trap(expression.right)
            return True
        return _may_trap(expression.left) or _may_trap(expression.right)
    if isinstance(expression, syntax.Parens):
        return _may_trap(expression.inner)
    if isinstance(expression, syntax.Unary):
        return _may_trap(expression.operand)
    if isinstance(expression, syntax.Call):
        return any(_may_trap(argument) for argument in expression.arguments)
    if isinstance(expression, syntax.Index):
        return _may_trap(expression.operand) or _may_trap(expression.index)
    if isinstance(expression, syntax.ArrayLiteral):
        return any(_may_trap(element) for element in expression.elements)
    return False


@dataclass
class Optimizer:
    """One folding pass, rewriting expressions in place and returning the same nodes."""

    folded: int = field(default=0)

    def run(self, program: syntax.Program) -> syntax.Program:
        """Optimise every function body, then convert counter loops and fuse odd pairs."""
        apply_pure_inlining(program)
        for function in program.functions:
            function.body = self._block(function.body)
        apply_algebraic_simplification(program)
        for function in program.functions:
            function.body = self._block(function.body)
        apply_copy_propagation(program)
        apply_countdown_latches(program)
        for _ in range(2):
            if not apply_odd_pair_fusion(program):
                break
            # A fused step re-reads the folded expressions; fold again so constant
            # arithmetic does not survive, then one more fusion round if shapes repeat.
            for function in program.functions:
                function.body = self._block(function.body)
        apply_copy_propagation(program)
        return program

    def _block(self, block: Optional[syntax.Block]) -> Optional[syntax.Block]:
        """Optimise each statement; splice merged branches and truncate what cannot run."""
        if block is None:
            return None
        statements = [self._statement(statement) for statement in block.statements]
        flattened = _flatten(statements)
        kept = _reachable(flattened)
        self.folded += len(flattened) - len(kept)
        block.statements = kept
        return block

    def _statement(self, statement: syntax.Stmt) -> syntax.Stmt:
        """Fold one statement, replacing a constant `if` with the branch it always takes."""
        if isinstance(statement, syntax.Let):
            statement.value = self._expression(statement.value)
        elif isinstance(statement, syntax.Assign):
            statement.value = self._expression(statement.value)
        elif isinstance(statement, syntax.Return):
            statement.value = self._expression(statement.value)
        elif isinstance(statement, syntax.ExprStmt):
            statement.expression = self._expression(statement.expression)
        elif isinstance(statement, syntax.If):
            statement.condition = self._expression(statement.condition)
            statement.then_block = self._block(statement.then_block)
            statement.else_block = self._block(statement.else_block)
            return self._choose_branch(statement)
        elif isinstance(statement, syntax.While):
            statement.condition = self._expression(statement.condition)
            statement.body = self._block(statement.body)
            if _constant(statement.condition) == 0:
                self.folded += 1
                return syntax.Block(span=statement.span, statements=[])
        elif isinstance(statement, syntax.Block):
            self._block(statement)
        return statement

    def _choose_branch(self, statement: syntax.If) -> syntax.Stmt:
        """Replace an `if` whose condition is a constant with the branch it always takes."""
        condition = _constant(statement.condition)
        if condition is None:
            return statement
        self.folded += 1
        if condition != 0:
            return statement.then_block
        return statement.else_block or syntax.Block(span=statement.span, statements=[])

    def _expression(self, expression: Optional[syntax.Expr]) -> Optional[syntax.Expr]:
        """Fold one expression; returns the replacement node."""
        if expression is None:
            return None
        if isinstance(expression, syntax.Parens):
            expression.inner = self._expression(expression.inner)
            if isinstance(expression.inner, syntax.IntLiteral):
                self.folded += 1
                return expression.inner
            return expression
        if isinstance(expression, syntax.Unary):
            expression.operand = self._expression(expression.operand)
            return self._fold_unary(expression)
        if isinstance(expression, syntax.Binary):
            expression.left = self._expression(expression.left)
            expression.right = self._expression(expression.right)
            return self._fold_binary(expression)
        if isinstance(expression, syntax.Call):
            expression.arguments = [self._expression(argument)
                                    for argument in expression.arguments]
            folded_string = self._fold_string_builtin(expression)
            if folded_string is not None:
                self.folded += 1
                return folded_string
        return expression

    def _fold_string_builtin(self, expression: syntax.Expr):
        """Fold `str_len`/`str_byte` of a literal handle into the specified answer.

        The handle's value is the address of the literal's bytes, so a literal
        argument already knows every answer the runtime helper could give:
        `str_len` is the byte count, `str_byte` is `text[j]` for `0 <= j < len`
        and exactly `-1` for a negative index and for `j == len` (the NUL
        terminator). Everything else — a non-literal handle, a dynamic index,
        a read past the terminator — stays a call: undefined stays undefined.
        """
        if not (isinstance(expression, syntax.Call)
                and expression.name in ("str_len", "str_byte") and expression.arguments):
            return None
        handle = expression.arguments[0]
        if not isinstance(handle, syntax.StrLiteral):
            return None
        text = handle.text
        if expression.name == "str_len":
            return syntax.IntLiteral(span=expression.span, value=len(text))
        if len(expression.arguments) != 2:
            return None
        index = expression.arguments[1]
        if not isinstance(index, syntax.IntLiteral):
            return None
        position = index.value
        if position < 0 or position == len(text):
            return syntax.IntLiteral(span=expression.span, value=-1)
        if 0 < position < len(text):
            return syntax.IntLiteral(span=expression.span, value=ord(text[position]))
        if position == 0:
            return syntax.IntLiteral(span=expression.span,
                                     value=ord(text[0]) if text else -1)
        return None

    def _fold_unary(self, expression: syntax.Unary) -> syntax.Expr:
        """Fold a unary operator applied to a literal."""
        value = _constant(expression.operand)
        if value is None:
            return expression
        folded = fold_unary(expression.op, value)
        if folded is None:
            return expression
        self.folded += 1
        return syntax.IntLiteral(span=expression.span, value=folded)

    def _fold_binary(self, expression: syntax.Binary) -> syntax.Expr:
        """Fold two literals, then apply an identity that is safe for every i64."""
        left, right = _constant(expression.left), _constant(expression.right)
        if left is not None and right is not None:
            folded = fold_constant(expression.op, left, right)
            if folded is not None:
                self.folded += 1
                return syntax.IntLiteral(span=expression.span, value=folded)
            return expression
        return self._identity(expression, left, right)

    def _identity(self, expression: syntax.Binary, left: Optional[int],
                  right: Optional[int]) -> syntax.Expr:
        """`x + 0`, `x * 1` and friends; a dropped operand must have no effect."""
        if left is not None and right is not None:
            return expression
        if right is not None and RIGHT_NEUTRAL.get(expression.op) == right:
            self.folded += 1
            return expression.left
        if left is not None and LEFT_NEUTRAL.get(expression.op) == left:
            self.folded += 1
            return expression.right
        if expression.op in ("*", "&") and (right == 0 or left == 0):
            dropped = expression.right if right == 0 else expression.left
            kept = expression.left if right == 0 else expression.right
            if (not has_call(dropped) and not has_call(kept)
                    and not _may_trap(dropped) and not _may_trap(kept)):
                self.folded += 1
                return syntax.IntLiteral(span=expression.span, value=0)
        return expression


def _constant(expression: Optional[syntax.Expr]) -> Optional[int]:
    """The literal value of an expression, or None when it is not a constant."""
    if isinstance(expression, syntax.IntLiteral):
        return expression.value
    return None


def _peel_parens(expression: Optional[syntax.Expr]) -> Optional[syntax.Expr]:
    while isinstance(expression, syntax.Parens):
        expression = expression.inner
    return expression


def _mentions_slot(node, slot: int) -> bool:
    """Whether a statement or expression reads or writes the variable in `slot`."""
    if isinstance(node, (list, tuple)):
        return any(_mentions_slot(item, slot) for item in node)
    if isinstance(node, (syntax.Let, syntax.Assign)):
        if node.slot == slot:
            return True
        return _mentions_slot(node.value, slot)
    if isinstance(node, syntax.Index):
        return (_mentions_slot(node.operand, slot)
                or _mentions_slot(node.index, slot))
    if isinstance(node, syntax.ArrayLiteral):
        return any(_mentions_slot(element, slot) for element in node.elements)
    if isinstance(node, syntax.Name):
        return node.slot == slot
    if isinstance(node, syntax.If):
        return (_mentions_slot(node.condition, slot)
                or _mentions_slot(node.then_block, slot)
                or _mentions_slot(node.else_block, slot))
    if isinstance(node, syntax.While):
        return _mentions_slot(node.condition, slot) or _mentions_slot(node.body, slot)
    if isinstance(node, syntax.Block):
        return any(_mentions_slot(statement, slot) for statement in node.statements)
    if isinstance(node, syntax.Return):
        return _mentions_slot(node.value, slot)
    if isinstance(node, syntax.ExprStmt):
        return _mentions_slot(node.expression, slot)
    if isinstance(node, (syntax.Break, syntax.Continue)):
        return False
    if isinstance(node, (syntax.Unary,)):
        return _mentions_slot(node.operand, slot)
    if isinstance(node, syntax.Binary):
        return _mentions_slot(node.left, slot) or _mentions_slot(node.right, slot)
    if isinstance(node, syntax.Parens):
        return _mentions_slot(node.inner, slot)
    if isinstance(node, syntax.Call):
        return any(_mentions_slot(argument, slot) for argument in node.arguments)
    return False


def _contains_break_or_continue(node) -> bool:
    """Whether a statement tree can leave the loop other than by falling out of its test."""
    if isinstance(node, (syntax.Break, syntax.Continue)):
        return True
    if isinstance(node, (syntax.Let, syntax.Assign)):
        return False
    if isinstance(node, syntax.If):
        return (_contains_break_or_continue(node.then_block)
                or _contains_break_or_continue(node.else_block))
    if isinstance(node, syntax.While):
        return _contains_break_or_continue(node.body)
    if isinstance(node, syntax.Block):
        return any(_contains_break_or_continue(statement) for statement in node.statements)
    return False


def _count_reads(node, slot: int) -> int:
    """How many times a statement tree reads the variable in `slot` (writes are not reads)."""
    reads = 0
    if isinstance(node, (syntax.Let, syntax.Assign)):
        reads = _count_reads(node.value, slot)
        if isinstance(node, syntax.Assign) and node.index is not None:
            reads += _count_reads(node.index, slot)
        return reads
    if isinstance(node, syntax.Name):
        return int(node.slot == slot)
    if isinstance(node, syntax.If):
        return (_count_reads(node.condition, slot)
                + _count_reads(node.then_block, slot)
                + _count_reads(node.else_block, slot))
    if isinstance(node, syntax.While):
        return _count_reads(node.condition, slot) + _count_reads(node.body, slot)
    if isinstance(node, syntax.Block):
        return sum(_count_reads(statement, slot) for statement in node.statements)
    if isinstance(node, syntax.Return):
        return _count_reads(node.value, slot)
    if isinstance(node, syntax.ExprStmt):
        return _count_reads(node.expression, slot)
    if isinstance(node, syntax.Unary):
        return _count_reads(node.operand, slot)
    if isinstance(node, syntax.Binary):
        return _count_reads(node.left, slot) + _count_reads(node.right, slot)
    if isinstance(node, syntax.Parens):
        return _count_reads(node.inner, slot)
    if isinstance(node, syntax.Index):
        reads = _count_reads(node.index, slot)
        if node.operand is not None:
            reads += _count_reads(node.operand, slot)
        return reads
    if isinstance(node, syntax.ArrayLiteral):
        return sum(_count_reads(element, slot) for element in node.elements)
    if isinstance(node, syntax.Call):
        return sum(_count_reads(argument, slot) for argument in node.arguments)
    return reads


def _find_increment(store, slot: int):
    """Locate the (list, index) of `v = v + 1` inside a statement list, or None."""
    for index, statement in enumerate(store):
        if isinstance(statement, syntax.Assign) and statement.slot == slot:
            value = _peel_parens(statement.value)
            if (isinstance(value, syntax.Binary) and value.op == "+"
                    and value.left is not None and value.right is not None
                    and isinstance(_peel_parens(value.left), syntax.Name)
                    and _peel_parens(value.left).slot == slot
                    and isinstance(_peel_parens(value.right), syntax.IntLiteral)
                    and _peel_parens(value.right).value == 1):
                return store, index
        if isinstance(statement, (syntax.If,)):
            for branch in (statement.then_block, statement.else_block):
                if branch is not None:
                    found = _find_increment(branch.statements, slot)
                    if found is not None:
                        return found
        if isinstance(statement, syntax.While):
            if statement.body is not None:
                found = _find_increment(statement.body.statements, slot)
                if found is not None:
                    return found
        if isinstance(statement, syntax.Block):
            found = _find_increment(statement.statements, slot)
            if found is not None:
                return found
    return None


def _try_countdown_latch(statement: syntax.While, init, count: int, trailing) -> bool:
    """Rewrite one `while v < C { ...; v = v + 1; ... }` as `while v > 0`, or refuse."""
    condition = _peel_parens(statement.condition)
    if not (isinstance(condition, syntax.Binary) and condition.op == "<"
            and isinstance(_peel_parens(condition.right), syntax.IntLiteral)):
        return False
    counter = _peel_parens(condition.left)
    if not (isinstance(counter, syntax.Name) and counter.slot >= 0):
        return False
    slot = counter.slot
    body = statement.body
    if body is None or statement.countdown:
        return False
    if _contains_break_or_continue(body):
        return False
    if _count_reads(body, slot) != 1:
        return False
    increment = _find_increment(body.statements, slot)
    if increment is None:
        return False
    if _mentions_slot(trailing, slot):
        return False
    # The counter becomes the remaining iteration count, so its literal
    # initialiser is rewritten and the in-body increment is deleted.
    condition.right = syntax.IntLiteral(span=condition.right.span, value=0)
    condition.op = ">"
    condition.left = counter
    statement.countdown = True
    store, index = increment
    del store[index]
    init.value = syntax.IntLiteral(span=init.value.span, value=count)
    return True


def _countdown_block(block: Optional[syntax.Block], trailing, parameters, changed: list) -> None:
    """Walk one block, converting eligible counter loops; `trailing` guards post-loop reads."""
    if block is None:
        return
    statements = block.statements
    literal_writes: dict = {}
    for index, statement in enumerate(statements):
        rest = statements[index + 1:] + trailing
        if isinstance(statement, (syntax.Let, syntax.Assign)):
            literal = _constant(statement.value)
            if literal is None:
                literal_writes.pop(statement.slot, None)
            else:
                literal_writes[statement.slot] = (statement, literal)
        elif isinstance(statement, (syntax.If, syntax.Block)):
            branches = []
            if isinstance(statement, syntax.If):
                branches = [b for b in (statement.then_block, statement.else_block)
                            if b is not None]
            else:
                branches = [statement]
            for branch in branches:
                _countdown_block(branch, rest, parameters, changed)
                for slot in list(literal_writes):
                    if _mentions_slot(branch, slot):
                        literal_writes.pop(slot)
        if isinstance(statement, syntax.While):
            # Nested loops convert first, so their rewrites are visible here.
            _countdown_block(statement.body, rest, parameters, changed)
            condition = _peel_parens(statement.condition)
            if isinstance(condition, syntax.Binary) and condition.op == "<":
                counter = _peel_parens(condition.left)
                if (isinstance(counter, syntax.Name)
                        and counter.slot not in parameters
                        and counter.slot in literal_writes):
                    bound = _constant(condition.right)
                    # A while over a computed bound (a lowered `for i in a..edge()`)
                    # has no literal count to latch onto; leave it alone.
                    if bound is None:
                        continue
                    init, initial = literal_writes[counter.slot]
                    count = wrap(bound - initial)
                    if _try_countdown_latch(statement, init, count, rest):
                        changed.append(statement)
            for slot in list(literal_writes):
                if _mentions_slot(statement.body, slot):
                    literal_writes.pop(slot)


def lower_for_loops(program: syntax.Program) -> syntax.Program:
    """Lower every `for` into a mutable `let` plus a `while`; runs before any folding.

    Innermost loops lower first, so a `continue` in a nested `for` was already bound to
    the inner increment before the outer rewrite looks at the block, and the outer
    rewrite never descends into a `while`, which is what the inner loop became.
    """
    for function in program.functions:
        _lower_block(function.body)
    return program


def _lower_block(block: Optional[syntax.Block]) -> None:
    """Lower one block in place; the loop body lowers before the loop itself."""
    if block is None:
        return
    lowered: List[syntax.Stmt] = []
    for statement in block.statements:
        if isinstance(statement, syntax.For):
            _lower_block(statement.body)
            _rewrite_continues(statement.body, statement.name, statement.slot)
            statement.body.statements.append(_increment(statement.name, statement.slot,
                                                        statement.span))
            lowered.extend(_for_to_while(statement))
        elif isinstance(statement, (syntax.If, syntax.While, syntax.Block)):
            if isinstance(statement, syntax.If):
                _lower_block(statement.then_block)
                _lower_block(statement.else_block)
            elif isinstance(statement, syntax.While):
                _lower_block(statement.body)
            else:
                _lower_block(statement)
            lowered.append(statement)
        else:
            lowered.append(statement)
    block.statements = lowered


def _for_to_while(statement: syntax.For) -> List[syntax.Stmt]:
    """The two statements a `for` becomes: the mutable initialiser and the counted `while`."""
    comparison = "<=" if statement.inclusive else "<"
    condition = syntax.Binary(
        span=Span.join(statement.start.span, statement.end.span), op=comparison,
        left=syntax.Name(span=statement.start.span, name=statement.name,
                         slot=statement.slot),
        right=statement.end)
    initialiser = syntax.Let(span=statement.span, name=statement.name, mutable=True,
                             type_name="i64", value=statement.start, slot=statement.slot)
    return [initialiser,
            syntax.While(span=statement.span, condition=condition, body=statement.body)]


def _increment(name: str, slot: int, span) -> syntax.Assign:
    """`name = name + 1` on the loop's own slot."""
    return syntax.Assign(
        span=span, name=name, slot=slot,
        value=syntax.Binary(span=span, op="+",
                            left=syntax.Name(span=span, name=name, slot=slot),
                            right=syntax.IntLiteral(span=span, value=1)))


def _rewrite_continues(block: Optional[syntax.Block], name: str, slot: int) -> None:
    """Prefix every `continue` bound to this loop with the increment, in place.

    A `continue` in a nested `while` (or in a nested block of one) belongs to that
    inner loop and is left alone; `if` branches and bare blocks are descended into.
    """
    if block is None:
        return
    for index, statement in enumerate(block.statements):
        if isinstance(statement, syntax.Continue):
            block.statements[index] = syntax.Block(
                span=statement.span,
                statements=[_increment(name, slot, statement.span), statement])
        elif isinstance(statement, syntax.If):
            _rewrite_continues(statement.then_block, name, slot)
            _rewrite_continues(statement.else_block, name, slot)
        elif isinstance(statement, syntax.While):
            continue
        elif isinstance(statement, syntax.Block):
            _rewrite_continues(statement, name, slot)


def _read_slots(expression, acc: set) -> None:
    """Add every slot `expression` reads to `acc`."""
    if isinstance(expression, syntax.Name):
        acc.add(expression.slot)
    elif isinstance(expression, syntax.Unary):
        _read_slots(expression.operand, acc)
    elif isinstance(expression, syntax.Binary):
        _read_slots(expression.left, acc)
        _read_slots(expression.right, acc)
    elif isinstance(expression, syntax.Parens):
        _read_slots(expression.inner, acc)
    elif isinstance(expression, syntax.Call):
        for argument in expression.arguments:
            _read_slots(argument, acc)
    elif isinstance(expression, syntax.Index):
        if isinstance(expression.operand, syntax.Name):
            acc.add(expression.operand.slot)
        _read_slots(expression.index, acc)
    elif isinstance(expression, syntax.ArrayLiteral):
        for element in expression.elements:
            _read_slots(element, acc)


def apply_copy_propagation(program: syntax.Program) -> int:
    """Replace `x = t` with t's definition when the copy is t's only read.

    A `let` whose value is pure (call-free and trap-free) and whose binding is read
    exactly once — by an immediate copy assignment in the same block — is deleted and
    the copy takes the definition. The statements between the two must leave the
    definition's operand slots untouched, since the definition now evaluates at the
    copy, and nothing else may even mention the binding. Everything else refuses
    silently: a branchy span, a second read, a write of the binding in the span,
    an impure definition.
    """
    propagated = 0
    for function in program.functions:
        for _ in range(64):
            if not _propagate_once(function.body, function.body):
                break
            propagated += 1
    return propagated


def _propagate_once(root: Optional[syntax.Block], block: Optional[syntax.Block]) -> bool:
    """Apply at most one copy propagation under `block`; returns whether one applied."""
    if block is None:
        return False
    statements = block.statements
    for index, statement in enumerate(statements):
        if isinstance(statement, (syntax.Break, syntax.Continue, syntax.Return,
                                  syntax.ExprStmt)):
            continue
        if isinstance(statement, syntax.If):
            if (_propagate_once(root, statement.then_block)
                    or _propagate_once(root, statement.else_block)):
                return True
            continue
        if isinstance(statement, syntax.While):
            if _propagate_once(root, statement.body):
                return True
            continue
        if isinstance(statement, syntax.Block):
            if _propagate_once(root, statement):
                return True
            continue
        if not (isinstance(statement, syntax.Let) and statement.value is not None
                and not has_call(statement.value) and not _may_trap(statement.value)):
            continue
        slot = statement.slot
        if _mentions_slot(statement.value, slot) or _count_reads(root, slot) != 1:
            continue
        operands: set = set()
        _read_slots(statement.value, operands)
        target = None
        blocked = False
        for cursor in range(index + 1, len(statements)):
            current = statements[cursor]
            if (isinstance(current, (syntax.Assign, syntax.Let))
                    and isinstance(current.value, syntax.Name)
                    and current.value.slot == slot
                    and current.slot != slot):
                if target is not None:
                    blocked = True
                    break
                target = cursor
                continue
            if _mentions_slot(current, slot):
                blocked = True
                break
            if target is None and any(_mentions_slot(current, operand)
                                      for operand in operands):
                blocked = True
                break
        if blocked or target is None:
            continue
        statements[target].value = statement.value
        block.statements = [item for pos, item in enumerate(statements)
                            if pos != index]
        return True
    return False


def apply_countdown_latches(program: syntax.Program) -> int:
    """Convert `while v < C` loops with a dead counter into countdown latches.

    The counter becomes a remaining-iteration count: the test flips to `v > 0`, the
    literal initialiser becomes `C - initial`, and the in-body increment is deleted.
    Safe only when the counter is never read in the loop body, never read after the
    loop, not a parameter, and the body has no `break`/`continue`.
    """
    changed: list = []
    for function in program.functions:
        parameters = {parameter.slot for parameter in function.parameters}
        _countdown_block(function.body, [], parameters, changed)
    return len(changed)


def _is_plus_one(statement: syntax.Assign) -> bool:
    """Whether the statement is `s = s + 1` (either operand order)."""
    if statement.slot < 0:
        return False
    value = _peel_parens(statement.value)
    if not (isinstance(value, syntax.Binary) and value.op == "+"):
        return False
    left, right = _peel_parens(value.left), _peel_parens(value.right)
    if isinstance(right, syntax.IntLiteral) and right.value == 1:
        left, right = right, left
    return isinstance(left, syntax.IntLiteral) and left.value == 1 and \
        isinstance(right, syntax.Name) and right.slot == statement.slot


def _scaled_odd_term(value: Optional[syntax.Expr], slot: int) -> Optional[int]:
    """The odd multiplier of `slot * m` (either order), or None."""
    term = _peel_parens(value)
    if not (isinstance(term, syntax.Binary) and term.op == "*"):
        return None
    left, right = _peel_parens(term.left), _peel_parens(term.right)
    if isinstance(left, syntax.Name) and left.slot == slot \
            and isinstance(right, syntax.IntLiteral) and right.value % 2 == 1:
        return right.value
    if isinstance(right, syntax.Name) and right.slot == slot \
            and isinstance(left, syntax.IntLiteral) and left.value % 2 == 1:
        return left.value
    return None


def _odd_step_value(value: Optional[syntax.Expr], slot: int) -> Optional[Tuple[int, int]]:
    """The `(m, c)` of `k = k*m + c` with both odd, or None.

    Both odd is what makes the fusion sound: on the odd path `k` is odd, so `m*k + c`
    (odd times odd plus odd) is even and the halving that follows is guaranteed.
    """
    expression = _peel_parens(value)
    if not (isinstance(expression, syntax.Binary) and expression.op == "+"):
        return None
    for term, addend in ((_peel_parens(expression.left), _peel_parens(expression.right)),
                         (_peel_parens(expression.right), _peel_parens(expression.left))):
        multiplier = _scaled_odd_term(term, slot)
        if multiplier is not None and isinstance(addend, syntax.IntLiteral) \
                and addend.value % 2 == 1:
            return multiplier, addend.value
    return None


def _halving_value(value: Optional[syntax.Expr], slot: int) -> bool:
    """Whether `value` is exactly `k / 2` for the slot."""
    expression = _peel_parens(value)
    if not (isinstance(expression, syntax.Binary) and expression.op == "/"):
        return False
    right = _peel_parens(expression.right)
    return slot_of_name(_peel_parens(expression.left)) == slot \
        and isinstance(right, syntax.IntLiteral) and right.value == 2


def slot_of_name(expression: Optional[syntax.Expr]) -> Optional[int]:
    """The slot of a (parenthesised) name, or None."""
    return expression.slot if isinstance(expression, syntax.Name) else None


def _fuse_odd_pair(statement: syntax.While) -> bool:
    """Rewrite one collatz-shaped loop body so the odd step rides with the halving after it.

    On the odd path `k = k*m + c` (m and c odd) leaves an even `k`, so the next iteration
    is a guaranteed halving before anything else reads `k`. Fusing them into
    `k = (k*m + c) / 2` and raising that path's step count to two performs the identical
    sequence of values and step totals with one pass through the parity test instead of two.
    Loops with `break`/`continue` are refused: both would skip the shared increment this
    rewrite moves into the branches.
    """
    if statement.countdown:
        return False
    body = statement.body
    if body is None or _contains_break_or_continue(body):
        return False
    statements = body.statements
    for index in range(len(statements) - 1):
        branch, increment = statements[index], statements[index + 1]
        if not isinstance(branch, syntax.If) or branch.else_block is None:
            continue
        if not isinstance(increment, syntax.Assign) or not _is_plus_one(increment):
            continue
        condition = _peel_parens(branch.condition)
        if not (isinstance(condition, syntax.Binary) and condition.op in ("==", "!=")):
            continue
        zero = _peel_parens(condition.right)
        if not (isinstance(zero, syntax.IntLiteral) and zero.value == 0):
            continue
        parity = _peel_parens(condition.left)
        if not (isinstance(parity, syntax.Binary) and parity.op == "%"):
            continue
        dividend, modulus = _peel_parens(parity.left), _peel_parens(parity.right)
        if not (isinstance(modulus, syntax.IntLiteral) and modulus.value == 2):
            continue
        slot = slot_of_name(dividend)
        if slot is None or slot < 0:
            continue
        then_stmts = list(branch.then_block.statements) if branch.then_block else []
        else_stmts = list(branch.else_block.statements) if branch.else_block else []
        if len(then_stmts) != 1 or len(else_stmts) != 1:
            continue
        even_side, odd_side = then_stmts[0], else_stmts[0]
        if condition.op == "!=":
            even_side, odd_side = odd_side, even_side
        if not (isinstance(even_side, syntax.Assign) and even_side.slot == slot
                and _halving_value(even_side.value, slot)):
            continue
        if not (isinstance(odd_side, syntax.Assign) and odd_side.slot == slot):
            continue
        scaled = _odd_step_value(odd_side.value, slot)
        if scaled is None:
            continue
        multiplier, addend = scaled
        # The value the odd step leaves: `(k * m + c) / 2`.
        fused = syntax.Assign(span=odd_side.span, name=odd_side.name, slot=slot,
                              value=syntax.Binary(
                                  span=odd_side.value.span, op="/",
                                  left=odd_side.value,
                                  right=syntax.IntLiteral(span=zero.span, value=2)))
        counted = [syntax.Assign(span=increment.span, name=increment.name, slot=increment.slot,
                                 value=syntax.Binary(
                                     span=increment.value.span, op="+",
                                     left=syntax.Name(span=increment.span,
                                                      name=increment.name, slot=increment.slot),
                                     right=syntax.IntLiteral(span=increment.span, value=level)))
                   for level in (1, 2)]
        if condition.op == "==":
            branch.then_block.statements = [even_side, counted[0]]
            branch.else_block.statements = [fused, counted[1]]
        else:
            branch.then_block.statements = [fused, counted[1]]
            branch.else_block.statements = [even_side, counted[0]]
        del statements[index + 1]
        return True
    return False


def apply_odd_pair_fusion(program: syntax.Program) -> int:
    """Fuse collatz-shaped odd steps with the halving that follows them, loop-wide."""
    changed: list = []

    def visit(block: Optional[syntax.Block]) -> None:
        if block is None:
            return
        for statement in block.statements:
            if isinstance(statement, syntax.While):
                visit(statement.body)
                if _fuse_odd_pair(statement):
                    changed.append(statement)
            elif isinstance(statement, syntax.If):
                visit(statement.then_block)
                visit(statement.else_block)
            elif isinstance(statement, syntax.Block):
                visit(statement)

    for function in program.functions:
        visit(function.body)
    return len(changed)


def _flatten(statements: List[syntax.Stmt]) -> List[syntax.Stmt]:
    """Splice a branch selected by folding into its parent, since Cairn has one flat scope."""
    flattened: List[syntax.Stmt] = []
    for statement in statements:
        if isinstance(statement, syntax.Block):
            flattened.extend(_flatten(statement.statements))
        else:
            flattened.append(statement)
    return flattened


def _reachable(statements: List[syntax.Stmt]) -> List[syntax.Stmt]:
    """Drop statements after one that cannot fall through: `return`, `break`, `continue`."""
    for index, statement in enumerate(statements):
        if isinstance(statement, (syntax.Return, syntax.Break, syntax.Continue)):
            return statements[:index + 1]
    return statements


def optimize(program: syntax.Program) -> syntax.Program:
    """Run one folding pass over a checked program, then convert counter loops."""
    return Optimizer().run(program)


INLINE_EXPANSION_NODES = 512
INLINE_ROUNDS = 8


def _node_count(expression) -> int:
    """How many expression nodes one tree carries."""
    if expression is None:
        return 0
    if isinstance(expression, (syntax.IntLiteral, syntax.Name, syntax.StrLiteral)):
        return 1
    if isinstance(expression, syntax.Parens):
        return 1 + _node_count(expression.inner)
    if isinstance(expression, syntax.Unary):
        return 1 + _node_count(expression.operand)
    if isinstance(expression, syntax.Binary):
        return 1 + _node_count(expression.left) + _node_count(expression.right)
    if isinstance(expression, syntax.Call):
        return 1 + sum(_node_count(argument) for argument in expression.arguments)
    if isinstance(expression, syntax.Index):
        return 1 + _node_count(expression.operand) + _node_count(expression.index)
    if isinstance(expression, syntax.ArrayLiteral):
        return 1 + sum(_node_count(element) for element in expression.elements)
    return 1


def _clone_expression(expression):
    """A deep copy of one expression node tree."""
    if isinstance(expression, syntax.IntLiteral):
        return syntax.IntLiteral(span=expression.span, value=expression.value)
    if isinstance(expression, syntax.Name):
        return syntax.Name(span=expression.span, name=expression.name,
                           slot=expression.slot)
    if isinstance(expression, syntax.StrLiteral):
        return syntax.StrLiteral(span=expression.span, text=expression.text)
    if isinstance(expression, syntax.Unary):
        return syntax.Unary(span=expression.span, op=expression.op,
                            operand=_clone_expression(expression.operand))
    if isinstance(expression, syntax.Binary):
        return syntax.Binary(span=expression.span, op=expression.op,
                             left=_clone_expression(expression.left),
                             right=_clone_expression(expression.right))
    if isinstance(expression, syntax.Parens):
        return syntax.Parens(span=expression.span,
                             inner=_clone_expression(expression.inner))
    if isinstance(expression, syntax.Call):
        return syntax.Call(span=expression.span, name=expression.name,
                           arguments=[_clone_expression(argument)
                                      for argument in expression.arguments])
    if isinstance(expression, syntax.Index):
        return syntax.Index(span=expression.span, slot=expression.slot,
                            size=expression.size,
                            operand=_clone_expression(expression.operand),
                            index=_clone_expression(expression.index))
    if isinstance(expression, syntax.ArrayLiteral):
        return syntax.ArrayLiteral(span=expression.span,
                                   elements=[_clone_expression(element)
                                             for element in expression.elements])
    raise AssertionError(f"optimize: cannot clone {type(expression).__name__}")


def _single_return_bodies(program: syntax.Program) -> dict:
    """Callees whose body is exactly one `return <expr>;`, within the inline budget."""
    bodies = {}
    for function in program.functions:
        statements = function.body.statements if function.body is not None else []
        if (len(statements) == 1 and isinstance(statements[0], syntax.Return)
                and statements[0].value is not None
                and _node_count(statements[0].value) <= INLINE_EXPANSION_NODES
                and not _contains_index(statements[0].value)):
            bodies[function.name] = function
    return bodies


def apply_pure_inlining(program: syntax.Program) -> int:
    """Inline every call to a single-`return` function, substituting its arguments.

    A call to a function whose body is exactly one `return <expr>;` is rewritten
    at the call site into that expression with every parameter replaced by the
    argument, so the call-per-iteration cost of pure scalar helpers disappears.
    Guards: a parameter read twice or more may only take a call-free argument,
    because a call is an effect and must not be duplicated; a parameter never read
    must not carry a call or trap argument either, because dropping the argument
    would drop its effect. Anything else keeps the call. Substitution past twice
    the node budget keeps the call, and a callee reached again inside its own
    inlined body refuses, so `f(4)` stays one call.
    """
    bodies = _single_return_bodies(program)
    if not bodies:
        return 0
    inlined = 0
    for _ in range(INLINE_ROUNDS):
        if not _inline_program(program, bodies):
            break
        inlined += 1
    return inlined


def _inline_program(program: syntax.Program, bodies: dict) -> int:
    """One inlining round over every statement; answers how many statements changed."""
    inlined = 0
    for function in program.functions:
        inlined += _inline_statements(function, function.body, bodies, frozenset())
    return inlined


def _inline_statements(function: syntax.Function, block: Optional[syntax.Block],
                       bodies: dict, active: frozenset) -> int:
    """Rewrite calls inside one block, descending into every statement kind."""
    if block is None:
        return 0
    inlined = 0
    for statement in block.statements:
        if isinstance(statement, syntax.If):
            inlined += _inline_attribute(function, statement, "condition", bodies,
                                         active)
            inlined += _inline_statements(function, statement.then_block, bodies,
                                          active)
            inlined += _inline_statements(function, statement.else_block, bodies,
                                          active)
        elif isinstance(statement, syntax.While):
            inlined += _inline_attribute(function, statement, "condition", bodies,
                                         active)
            inlined += _inline_statements(function, statement.body, bodies, active)
        elif isinstance(statement, syntax.Block):
            inlined += _inline_statements(function, statement, bodies, active)
        elif isinstance(statement, syntax.For):
            for attribute in ("start", "end"):
                inlined += _inline_attribute(function, statement, attribute, bodies,
                                             active)
            inlined += _inline_statements(function, statement.body, bodies, active)
        elif isinstance(statement, (syntax.Let, syntax.Assign, syntax.Return)):
            inlined += _inline_attribute(function, statement, "value", bodies, active)
        elif isinstance(statement, syntax.ExprStmt):
            inlined += _inline_attribute(function, statement, "expression", bodies,
                                         active)
    return inlined


def _inline_attribute(function: syntax.Function, statement: syntax.Stmt,
                      attribute: str, bodies: dict, active: frozenset) -> int:
    """Inline calls inside one statement's expression; answers whether it changed."""
    value = getattr(statement, attribute, None)
    if value is None:
        return 0
    rewritten = _inline_expression(function, value, bodies, active)
    if rewritten is None:
        return 0
    setattr(statement, attribute, rewritten)
    return 1


def _inline_expression(function: syntax.Function, expression, bodies: dict,
                       active: frozenset):
    """Replace inlineable calls throughout one expression; None when unchanged.

    Substitution keeps the effect count exact: a parameter read once takes any
    argument, a parameter read twice takes only a call-free argument (a fresh
    clone is inserted per read), and a parameter never read must not carry a
    call or trap argument, since dropping the argument would drop its effect.
    """
    if expression is None:
        return None
    if isinstance(expression, syntax.Call):
        changed = False
        arguments = []
        for argument in expression.arguments:
            replacement = _inline_expression(function, argument, bodies, active)
            if replacement is not None:
                arguments.append(replacement)
                changed = True
            else:
                arguments.append(argument)
        if changed:
            expression.arguments = arguments
        if expression.name in bodies and expression.name not in active:
            substituted = _inlined_body(expression, bodies,
                                        active | {expression.name})
            if substituted is not None:
                return substituted
        return expression if changed else None
    if isinstance(expression, syntax.Parens):
        replacement = _inline_expression(function, expression.inner, bodies, active)
        if replacement is not None:
            expression.inner = replacement
            return expression
        return None
    if isinstance(expression, syntax.Unary):
        replacement = _inline_expression(function, expression.operand, bodies, active)
        if replacement is not None:
            expression.operand = replacement
            return expression
        return None
    if isinstance(expression, syntax.Binary):
        left = _inline_expression(function, expression.left, bodies, active)
        right = _inline_expression(function, expression.right, bodies, active)
        if left is not None:
            expression.left = left
        if right is not None:
            expression.right = right
        return expression if left is not None or right is not None else None
    return None


def _inlined_body(call: syntax.Call, bodies: dict, active: frozenset):
    """The substituted body of one inlineable call, or None when a guard refuses."""
    callee = bodies[call.name]
    value = callee.body.statements[0].value
    counts = [(parameter, _count_reads(value, parameter.slot), argument)
              for parameter, argument in zip(callee.parameters, call.arguments)]
    for _, count, argument in counts:
        if count == 0 and (has_call(argument) or _may_trap(argument)):
            return None  # dropping the argument would drop its effect
        if count > 1 and has_call(argument):
            return None  # duplicating the argument would duplicate its call
    substitutions = {parameter.slot: argument for parameter, count, argument in counts
                     if count > 0}
    if not substitutions:
        return None
    substituted = _substitute_parameters(_clone_expression(value), substitutions)
    if _node_count(substituted) > INLINE_EXPANSION_NODES * 2:
        return None
    if any(node.name in bodies and node.name in active
           for node in _calls_in(substituted)):
        return None  # recursion back into the inline set
    return substituted


def _substitute_parameters(expression, substitutions: dict):
    """Replace every read of a substituted parameter with a fresh argument clone."""
    if isinstance(expression, syntax.Name):
        if expression.slot in substitutions:
            return _clone_expression(substitutions[expression.slot])
        return expression
    if isinstance(expression, syntax.Parens):
        expression.inner = _substitute_parameters(expression.inner, substitutions)
        return expression
    if isinstance(expression, syntax.Unary):
        expression.operand = _substitute_parameters(expression.operand, substitutions)
        return expression
    if isinstance(expression, syntax.Binary):
        expression.left = _substitute_parameters(expression.left, substitutions)
        expression.right = _substitute_parameters(expression.right, substitutions)
        return expression
    if isinstance(expression, syntax.Call):
        expression.arguments = [_substitute_parameters(argument, substitutions)
                                for argument in expression.arguments]
        return expression
    if isinstance(expression, syntax.Index):
        expression.operand = _substitute_parameters(expression.operand, substitutions)
        expression.index = _substitute_parameters(expression.index, substitutions)
        return expression
    return expression


def _calls_in(expression) -> list:
    """Every Call node under one expression tree."""
    if expression is None:
        return []
    if isinstance(expression, syntax.Call):
        found = [expression]
    else:
        found = []
    if isinstance(expression, syntax.Parens):
        found += _calls_in(expression.inner)
    if isinstance(expression, syntax.Unary):
        found += _calls_in(expression.operand)
    if isinstance(expression, syntax.Binary):
        found += _calls_in(expression.left) + _calls_in(expression.right)
    if isinstance(expression, syntax.Call):
        for argument in expression.arguments:
            found += _calls_in(argument)
    return found


def apply_algebraic_simplification(program: syntax.Program) -> int:
    """Expand constant coefficients over add-chains and collect like terms.

    `31 * (i + 2)` rewrites as `i * 31 + 62`, and `i * 31 + i * 37` collects as
    `i * 68`; both are ring-exact under wrapping i64 arithmetic (multiplication
    distributes over addition and subtraction modulo 2^64, and `/`, `%` and the
    shifts never take part in a collection). Terms whose subtree carries a call
    neither group nor drop, because a call is an effect: `read_char() * 3 +
    read_char() * 4` stays two reads. A chain rebuilds only when its term count
    actually shrinks.
    """
    changed = 0
    for function in program.functions:
        changed += _algebraic_statements(function.body)
    return changed


def _algebraic_statements(block: Optional[syntax.Block]) -> int:
    """Collect like terms in every statement of one block, recursing through shapes."""
    if block is None:
        return 0
    changed = 0
    for statement in block.statements:
        if isinstance(statement, (syntax.Let, syntax.Assign)):
            changed += _algebraic_attribute(statement, "value")
        elif isinstance(statement, syntax.Return):
            changed += _algebraic_attribute(statement, "value")
        elif isinstance(statement, syntax.ExprStmt):
            changed += _algebraic_attribute(statement, "expression")
        elif isinstance(statement, syntax.If):
            changed += _algebraic_attribute(statement, "condition")
            changed += _algebraic_statements(statement.then_block)
            changed += _algebraic_statements(statement.else_block)
        elif isinstance(statement, syntax.While):
            changed += _algebraic_attribute(statement, "condition")
            changed += _algebraic_statements(statement.body)
        elif isinstance(statement, syntax.Block):
            changed += _algebraic_statements(statement)
        elif isinstance(statement, syntax.For):
            for attribute in ("start", "end"):
                changed += _algebraic_attribute(statement, attribute)
            changed += _algebraic_statements(statement.body)
    return changed


def _algebraic_attribute(statement: syntax.Stmt, attribute: str) -> int:
    """Collect one statement root; answers whether the tree changed."""
    value = getattr(statement, attribute, None)
    if value is None:
        return 0
    replacement = _algebraic_expression(value)
    if replacement is None:
        return 0
    setattr(statement, attribute, replacement)
    return 1


def _algebraic_expression(expression):
    """Collect beneath non-chain operators; a `+`/`-` chain collects as a whole."""
    if expression is None or isinstance(expression, (syntax.IntLiteral, syntax.Name,
                                                     syntax.StrLiteral)):
        return None
    if isinstance(expression, syntax.Parens):
        replacement = _algebraic_expression(expression.inner)
        if replacement is not None:
            expression.inner = replacement
            return expression
        return None
    if isinstance(expression, syntax.Unary):
        replacement = _algebraic_expression(expression.operand)
        if replacement is not None:
            expression.operand = replacement
            return expression
        return None
    if isinstance(expression, syntax.Call):
        changed = False
        arguments = []
        for argument in expression.arguments:
            replacement = _algebraic_expression(argument)
            if replacement is not None:
                arguments.append(replacement)
                changed = True
            else:
                arguments.append(argument)
        if changed:
            expression.arguments = arguments
            return expression
        return None
    if isinstance(expression, syntax.Binary) and expression.op in ("+", "-"):
        return _collect_chain(expression)
    if isinstance(expression, syntax.Binary):
        left = _algebraic_expression(expression.left)
        right = _algebraic_expression(expression.right)
        if left is not None:
            expression.left = left
        if right is not None:
            expression.right = right
        return expression if left is not None or right is not None else None
    return None


def _collect_chain(expression):
    """The collected `+`/`-` chain rooted at `expression`, or None when nothing improved.

    The chain flattens into signed terms; product spines with a literal
    coefficient distribute over nested chains; same-signature terms sum. A
    rebuild happens only when grouping, cancelling or folding shrinks the term
    count, so `x * 0` (effect-free) vanishes but `0 * g(x)` stays.
    """
    if not (isinstance(expression, syntax.Binary) and expression.op in ("+", "-")):
        return None
    terms = _chain_terms(expression, 1)
    order = []
    table = {}
    for sign, kind, content, span in terms:
        if kind == "const":
            if content == 0:
                continue
            key = ("const",)
            base = None
        elif has_call(content):
            key = ("effect", id(content))
            base = content
        else:
            key = _signature(content)
            base = content
        if key not in table:
            table[key] = [0, base, span, 0]
            order.append(key)
        entry = table[key]
        entry[0] = wrap(entry[0] + sign)
        entry[3] += 1
    # Collect only when two or more non-constant terms really merge; a lone
    # expression term keeps its original shape (e.g. `0 - a` stays a negation).
    if not any(key != ("const",) and table[key][3] >= 2 for key in order):
        return None
    rebuilt = None
    span = expression.span
    for key in order:
        sign_total, base, term_span, _ = table[key]
        if key == ("const",):
            if sign_total == 0:
                continue
            node = syntax.IntLiteral(span=term_span, value=sign_total)
        elif sign_total == 0:
            if has_call(base) or _may_trap(base):
                node = syntax.Binary(span=term_span, op="*",
                                     left=syntax.IntLiteral(span=term_span, value=0),
                                     right=base)
            else:
                continue
        elif sign_total == 1:
            node = base
        else:
            node = syntax.Binary(span=term_span, op="*", left=base,
                                 right=syntax.IntLiteral(span=term_span,
                                                         value=sign_total))
        if rebuilt is None:
            rebuilt = node
        else:
            rebuilt = syntax.Binary(span=span, op="+", left=rebuilt, right=node)
    return rebuilt if rebuilt is not None else syntax.IntLiteral(span=span, value=0)


def _signature(expression) -> tuple:
    """A structural key for one expression subtree; sames group together."""
    expression = _peel_parens(expression)
    if isinstance(expression, syntax.IntLiteral):
        return ("int", expression.value)
    if isinstance(expression, syntax.Name):
        return ("slot", expression.slot)
    if isinstance(expression, syntax.StrLiteral):
        return ("str", expression.text)
    if isinstance(expression, syntax.Unary):
        return ("unary", expression.op, _signature(expression.operand))
    if isinstance(expression, syntax.Binary):
        return ("bin", expression.op, _signature(expression.left),
                _signature(expression.right))
    if isinstance(expression, syntax.Call):
        return ("call", expression.name,
                tuple(_signature(argument) for argument in expression.arguments))
    return ("other", id(expression))


def _chain_terms(expression, sign):
    """Flatten a `+`/`-` chain into signed terms.

    Each term is `(sign, kind, content, span)`, where `kind` is `"const"`
    (content the value) or `"expr"` (content the node). Product spines carrying a
    literal coefficient distribute over nested chains; signs stay wrapped.
    """
    expression = _peel_parens(expression)
    if isinstance(expression, syntax.Binary) and expression.op in ("+", "-"):
        right_sign = sign if expression.op == "+" else -sign
        return _chain_terms(expression.left, sign) + \
            _chain_terms(expression.right, right_sign)
    return _product_term(expression, sign)


def _product_term(expression, sign) -> list:
    """One non-chain term as a one-element list: literal-coefficient products distribute."""
    span = expression.span
    expression = _peel_parens(expression)
    if isinstance(expression, syntax.Unary) and expression.op == "-":
        return _product_term(expression.operand, -sign)
    if isinstance(expression, syntax.IntLiteral):
        return [(wrap(sign * expression.value), "const", expression.value, span)]
    if isinstance(expression, syntax.Binary) and expression.op == "*":
        left = _peel_parens(expression.left)
        right = _peel_parens(expression.right)
        literal = None
        if isinstance(left, syntax.IntLiteral) and not isinstance(right, syntax.IntLiteral):
            literal, base = left, right
        elif isinstance(right, syntax.IntLiteral) and not isinstance(left, syntax.IntLiteral):
            literal, base = right, left
        if literal is not None:
            pieces = _chain_terms(base, wrap(sign * literal.value))
            return [(piece[0], piece[1], piece[2], span) for piece in pieces]
    return [(sign, "expr", expression, span)]


__all__ = ["INT64_MAX", "INT64_MIN", "MASK64", "Optimizer", "apply_algebraic_simplification",
           "apply_copy_propagation", "apply_countdown_latches", "apply_odd_pair_fusion",
           "apply_pure_inlining", "fold_constant", "fold_unary", "has_call",
           "lower_for_loops", "optimize", "truncate_division", "wrap"]