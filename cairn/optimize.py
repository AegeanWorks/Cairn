"""Optimiser: constant folding and provably safe algebraic simplification.

The pass runs after semantic analysis and before code generation, and it is deliberately
conservative: a rewrite happens only when the result is identical to what the machine code
would compute, including wrapping arithmetic, arithmetic right shift, and the divide-by-zero
trap. `print` is an effect, so no rewrite may drop or duplicate a call.
"""

from dataclasses import dataclass, field
from typing import List, Optional

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


@dataclass
class Optimizer:
    """One folding pass, rewriting expressions in place and returning the same nodes."""

    folded: int = field(default=0)

    def run(self, program: syntax.Program) -> syntax.Program:
        """Optimise every function body in the program."""
        for function in program.functions:
            function.body = self._block(function.body)
        return program

    def _block(self, block: Optional[syntax.Block]) -> Optional[syntax.Block]:
        """Optimise each statement; splice merged branches and truncate what cannot run."""
        if block is None:
            return None
        statements = [self._statement(statement) for statement in block.statements]
        block.statements = _reachable(_flatten(statements))
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
            if not has_call(dropped) and not has_call(kept):
                self.folded += 1
                return syntax.IntLiteral(span=expression.span, value=0)
        return expression


def _constant(expression: Optional[syntax.Expr]) -> Optional[int]:
    """The literal value of an expression, or None when it is not a constant."""
    if isinstance(expression, syntax.IntLiteral):
        return expression.value
    return None


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
    """Run one folding pass over a checked program."""
    return Optimizer().run(program)


__all__ = ["INT64_MAX", "INT64_MIN", "MASK64", "Optimizer", "fold_constant", "fold_unary",
           "has_call", "optimize", "truncate_division", "wrap"]