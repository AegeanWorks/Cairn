"""Optimiser: constant folding and provably safe algebraic simplification.

The pass runs after semantic analysis and before code generation, and it is deliberately
conservative: a rewrite happens only when the result is identical to what the machine code
would compute, including wrapping arithmetic, arithmetic right shift, and the divide-by-zero
trap. `print` is an effect, so no rewrite may drop or duplicate a call.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from cairn import syntax

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
    return False


@dataclass
class Optimizer:
    """One folding pass, rewriting expressions in place and returning the same nodes."""

    folded: int = field(default=0)

    def run(self, program: syntax.Program) -> syntax.Program:
        """Optimise every function body, then convert counter loops and fuse odd pairs."""
        for function in program.functions:
            function.body = self._block(function.body)
        apply_countdown_latches(program)
        for _ in range(2):
            if not apply_odd_pair_fusion(program):
                break
            # A fused step re-reads the folded expressions; fold again so constant
            # arithmetic does not survive, then one more fusion round if shapes repeat.
            for function in program.functions:
                function.body = self._block(function.body)
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
        return expression

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
        return _count_reads(node.value, slot)
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
                    init, initial = literal_writes[counter.slot]
                    count = wrap(_constant(condition.right) - initial)
                    if _try_countdown_latch(statement, init, count, rest):
                        changed.append(statement)
            for slot in list(literal_writes):
                if _mentions_slot(statement.body, slot):
                    literal_writes.pop(slot)


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


__all__ = ["INT64_MAX", "INT64_MIN", "MASK64", "Optimizer", "apply_countdown_latches",
           "apply_odd_pair_fusion",
           "fold_constant", "fold_unary", "has_call", "optimize", "truncate_division", "wrap"]