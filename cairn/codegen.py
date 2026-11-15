"""Code generation: syntax tree to x86-64, one pass, values in rax.

Every Cairn expression is an i64 in a register, so the lowering never needs a type test. The left
operand of a binary operator lives in a temporary register when the right operand cannot call a
function, and on the stack otherwise; calls pass arguments in the Windows x64 integer registers,
which the same backend also uses internally.

A function's first few slots live in callee-saved registers instead of the frame. A call therefore
cannot clobber a local, and a hot loop stops reloading its counters on every iteration; a function
that uses those registers saves them on entry and restores them on every exit. Functions with more
slots than registers keep the stack path for the rest.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from cairn import syntax
from cairn.image import ENTRY_LABEL, Artifact, ReadOnlyData, iat_label
from cairn.machine import ARGUMENT_REGISTERS, Assembler
from cairn.optimize import INT64_MIN, has_call, _contains_break_or_continue
from cairn.runtime import DIV_ZERO_LABEL, PRINT_LABEL, emit_runtime
from cairn.sema import MAX_PARAMETERS

COMPARISON_CONDITIONS = {
    "==": "e", "!=": "ne", "<": "l", "<=": "le", ">": "g", ">=": "ge",
}

# The condition that jumps when the compared relation is false.
INVERTED_CONDITIONS = {"e": "ne", "ne": "e", "l": "ge", "ge": "l", "le": "g", "g": "le"}

ARITHMETIC_OPCODES = {
    "+": 0x01, "&": 0x21, "|": 0x09, "^": 0x31,
}

# Group-1 immediate extensions for an arithmetic right operand that is a literal.
IMMEDIATE_BINARY_EXTENSIONS = {"+": 0, "-": 5, "&": 4, "|": 1, "^": 6}
# Shift extensions for a literal count: `shl` and `sar`, matching Cairn's `>>` semantics.
IMMEDIATE_SHIFT_EXTENSIONS = {"<<": 4, ">>": 7}

# Binary operators that can update a destination in place: `slot = slot op value`.
UPDATE_OPCODES = {"+": 0x01, "-": 0x29, "&": 0x21, "|": 0x09, "^": 0x31}
COMMUTATIVE_UPDATES = frozenset({"+", "*", "&", "|", "^"})
IMMEDIATE_UPDATES = frozenset({"+", "-"})
IMMEDIATE_MIN = -0x80000000
IMMEDIATE_MAX = 0x7FFFFFFF
MAX_INLINE_DEPTH = 7
# A pair copy branches two ways per level (its own n-argument chain and the m-shifted
# descent), so pure-text copies grow like 2**depth; the cap keeps the emission bounded.
MAX_PAIR_INLINE_DEPTH = 6

# Volatile registers that hold a left operand across a nested expression, innermost last.
TEMPORARY_REGISTERS = ("r11", "r10")
# Scratch for division and shifting: never a temporary register and never live across a call.
SCRATCH = "rsi"
# Callee-saved registers that can hold a function's slots, in allocation order. `rsi` is the
# division scratch and `rbp` is the frame pointer, so neither can be a home.
HOME_REGISTERS = ("rbx", "r12", "r13", "r14", "r15", "rdi")


def function_label(name: str) -> str:
    """The assembler label of a Cairn function."""
    return f"fn.{name}"


def slot_offset(slot: int) -> int:
    """The rbp-relative offset of a frame slot; slot 0 is the first parameter."""
    return -8 * (slot + 1)


def align16(value: int) -> int:
    """Round a frame size up to the 16-byte stack alignment the ABI requires."""
    return (value + 15) & ~15


def slot_of(expression: Optional[syntax.Expr]) -> Optional[int]:
    """The slot a parenthesised name refers to, or None for every other expression."""
    while isinstance(expression, syntax.Parens):
        expression = expression.inner
    return expression.slot if isinstance(expression, syntax.Name) else None


def _is_plus_step(statement: syntax.Stmt, level: int) -> bool:
    """Whether the statement is `amount = amount + level` for its own slot."""
    if not isinstance(statement, syntax.Assign) or statement.slot < 0:
        return False
    value = statement.value
    while isinstance(value, syntax.Parens):
        value = value.inner
    if not (isinstance(value, syntax.Binary) and value.op == "+"):
        return False
    left, right = value.left, value.right
    while isinstance(left, syntax.Parens):
        left = left.inner
    while isinstance(right, syntax.Parens):
        right = right.inner
    if isinstance(right, syntax.IntLiteral) and right.value == level:
        left, right = right, left
    return isinstance(left, syntax.IntLiteral) and left.value == level \
        and isinstance(right, syntax.Name) and right.slot == statement.slot


def is_leaf(expression: Optional[syntax.Expr]) -> bool:
    """Whether evaluating the expression is a load or a literal move and nothing else."""
    while isinstance(expression, syntax.Parens):
        expression = expression.inner
    return isinstance(expression, (syntax.IntLiteral, syntax.Name))


@dataclass
class CodeGenerator:
    """One lowering pass over a checked program."""

    assembler: Assembler = field(default_factory=Assembler)
    rodata: ReadOnlyData = field(default_factory=ReadOnlyData)
    depth: int = 0
    temporaries: int = 0
    break_targets: List[str] = field(default_factory=list)
    continue_targets: List[str] = field(default_factory=list)
    counter: int = 0
    homes: Tuple[str, ...] = ()
    save_base: int = 0
    epilogue: str = ""
    current: Optional[syntax.Function] = None
    inline_depth: int = 0
    pad_slots: Tuple[bool, ...] = ()
    holds: Tuple[Optional[str], ...] = ()
    delta_slot: int = -1
    delta_value: int = 0
    value_slot: int = -1
    hold_units: int = 0
    nonnegative_slots: set = field(default_factory=set)

    def generate(self, program: syntax.Program) -> Artifact:
        """Emit the entry stub, every function, then the runtime helpers."""
        self._emit_entry()
        for function in program.functions:
            self._emit_function(function)
        emit_runtime(self.assembler, self.rodata)
        return Artifact(text=bytes(self.assembler.code),
                        text_labels=dict(self.assembler.labels),
                        fixups=list(self.assembler.fixups),
                        rodata=self.rodata,
                        entry=ENTRY_LABEL)

    def _fresh(self, stem: str) -> str:
        """A label name no other construct uses."""
        self.counter += 1
        return f"{stem}.{self.counter}"

    def _emit_entry(self) -> None:
        """The PE entry point: call main, then exit with main's return value."""
        self.assembler.label(ENTRY_LABEL)
        self.assembler.sub_rsp(40)
        self.assembler.call(function_label("main"))
        self.assembler.mov_reg_reg("rcx", "rax")
        self.assembler.call_indirect(iat_label("ExitProcess"))
        self.assembler.hlt()

    def _emit_function(self, function: syntax.Function) -> None:
        """Prologue, saved homes, cleared frame, parameter spills, body, and one epilogue.

        Every `return` jumps to the single epilogue, so a home is restored on every path out.
        """
        asm = self.assembler
        self.homes = HOME_REGISTERS[:min(function.slots, len(HOME_REGISTERS))]
        self.save_base = function.slots
        self.epilogue = f"{function_label(function.name)}.epilogue"
        frame = align16((function.slots + len(self.homes)) * 8)
        asm.label(function_label(function.name))
        if function.slots <= len(HOME_REGISTERS):
            # Every slot has a home, so no frame (and no rbp) is needed: the homes
            # save and restore with push/pop, which also drops the store-forwarding
            # latency a frame store re-load pays on the hot call path.
            hold_units = self._delta_hold_units(function)
            pair_mode = hold_units == 0 and self._pair_inline_shape(function)
            pad_units = 0 if (len(self.homes) + hold_units) % 2 else 1
            merging = hold_units == 0 and self._body_may_hold(function.body)
            pad = hold_units == 0 and (function.slots % 2 == 0 or merging)
            filler = merging and function.slots % 2 == 1
            self.current = function
            for home in self.homes:
                asm.push(home)
            if pair_mode:
                # The chain counter's save slot: copies rebind r13 freely, and every
                # call inside the copies preserves it (callee-saved ABI register).
                asm.push("r13")
            if hold_units:
                # Delta inlining keeps every nested merge's left value in one of these
                # frame slots, level-indexed, so inline copies carry no stack state of
                # their own. The extra pad unit keeps the unit count odd, i.e. rsp
                # 16-byte aligned for every call the body and its copies will make.
                asm.sub_rsp(8 * (hold_units + pad_units))
                self.pad_slots = ()
            elif filler:
                # This register is pushed and popped untouched: it realigns the unit
                # count while preserving the caller's value; the pad lands on top,
                # which is where body merges read and write it.
                asm.push(HOME_REGISTERS[-1])
                if pad:
                    asm.sub_rsp(8)
                self.pad_slots = (pad,)
            elif pad:
                asm.sub_rsp(8)
                self.pad_slots = (True,)
            else:
                self.pad_slots = (False,)
            self.delta_slot = function.parameters[0].slot if (hold_units or pair_mode) else -1
            self.value_slot = function.parameters[1].slot if pair_mode else -1
            self.delta_value = 0
            self.chain_loop_depth = 0
            self.hold_units = hold_units
            self._clear_frame(function)
            for index, parameter in enumerate(function.parameters[:MAX_PARAMETERS]):
                self._store_slot(parameter.slot, ARGUMENT_REGISTERS[index])
            self.depth = 0
            self.temporaries = 0
            # The implicit-return-zero runs once at entry: explicit returns overwrite it,
            # and a final `return` can fall through into the epilogue label ungated.
            asm.mov_imm64("rax", 0)
            self._emit_block(function.body, tail=True)
            asm.label(self.epilogue)
            if hold_units:
                asm.add_rsp(8 * (hold_units + pad_units))
            elif pad:
                asm.add_rsp(8)  # skip the pad slot before the pops read the save area
            if filler:
                asm.pop(HOME_REGISTERS[-1])
            if pair_mode:
                asm.pop("r13")
            for home in reversed(self.homes):
                asm.pop(home)
            asm.ret()
            self.pad_slots = ()
            self.hold_units = 0
            self.delta_slot = -1
            self.delta_value = 0
            self.value_slot = -1
            self.current = None
            return
        asm.push("rbp")
        asm.mov_reg_reg("rbp", "rsp")
        if frame:
            asm.sub_rsp(frame)
        self.pad_slots = ()
        self._save_homes()
        self._clear_frame(function)
        for index, parameter in enumerate(function.parameters[:MAX_PARAMETERS]):
            self._store_slot(parameter.slot, ARGUMENT_REGISTERS[index])
        self.depth = 0
        self.temporaries = 0
        asm.mov_imm64("rax", 0)
        self._emit_block(function.body, tail=True)
        asm.label(self.epilogue)
        self._restore_homes()
        asm.leave_ret()

    def _save_homes(self) -> None:
        """Keep the caller's values for every callee-saved register this function uses."""
        for index, home in enumerate(self.homes):
            self.assembler.mov_store_rbp(slot_offset(self.save_base + index), home)

    def _restore_homes(self) -> None:
        """Put the caller's values back before returning."""
        for index, home in enumerate(self.homes):
            self.assembler.mov_load_rbp(home, slot_offset(self.save_base + index))

    def _clear_frame(self, function: syntax.Function) -> None:
        """Clear every slot so a binding declared in a branch that never ran reads as zero.

        Cairn has one flat function scope, so a name declared inside an `if` is visible after it.
        A home is cleared in its register; a stack slot is cleared in the frame. A parameter needs
        neither, because the argument register is stored over it before the body runs.
        """
        asm = self.assembler
        if len(self.homes) < function.slots:
            asm.xor_zero("rax")
            for slot in range(len(self.homes), function.slots):
                asm.mov_store_rbp(slot_offset(slot), "rax")
        for index, home in enumerate(self.homes):
            if index >= len(function.parameters):
                asm.xor_zero(home)

    def _home(self, slot: int) -> Optional[str]:
        """The register that holds `slot`, or None when the slot stays in the frame."""
        if 0 <= slot < len(self.homes):
            return self.homes[slot]
        return None

    def _load_slot(self, slot: int, destination: str = "rax") -> None:
        """Move one slot into `destination` from its register or its frame slot.

        The inlined parameter is never moved at all during a delta copy: copies see it
        shifted by the accumulated delta, so reads become one `lea`.
        """
        home = self._home(slot)
        if slot == self.delta_slot and self.delta_value and home is not None:
            self.assembler.lea_imm(destination, home, -self.delta_value)
            return
        if home is None:
            self.assembler.mov_load_rbp(destination, slot_offset(slot))
        elif home != destination:
            self.assembler.mov_reg_reg(destination, home)

    def _store_slot(self, slot: int, source: str = "rax") -> None:
        """Write `source` into one slot's register or frame slot."""
        self.nonnegative_slots.discard(slot)
        home = self._home(slot)
        if home is None:
            self.assembler.mov_store_rbp(slot_offset(slot), source)
        elif home != source:
            self.assembler.mov_reg_reg(home, source)

    def _emit_value(self, slot: int, expression: Optional[syntax.Expr]) -> None:
        """Assign an expression to a slot, folding `slot = slot op value` in place."""
        home = self._home(slot)
        # The fact stays while the right side reads the slot's guarded value; once the
        # assignment lands, the value is whatever the expression produced.
        folded = home is not None and self._fold_update(home, slot, expression)
        if folded:
            if not self._update_keeps_nonneg(slot, expression):
                self.nonnegative_slots.discard(slot)
            return
        self._emit_expression(expression)
        self._store_slot(slot)
        self.nonnegative_slots.discard(slot)

    def _update_keeps_nonneg(self, slot: int, expression: Optional[syntax.Expr]) -> bool:
        """Whether an in-place update provably keeps the slot's value non-negative.

        Only updates whose result derives from a non-negative reading of the same slot
        qualify: `slot / 2**b` is one `sar` (non-negative stays non-negative), the
        remainder mask cannot clear the sign bit, and `slot + slot*f + c` is a scaled
        `lea` that stays non-negative when both parts are.
        """
        if slot not in self.nonnegative_slots or self.delta_value:
            return False
        expr = expression
        while isinstance(expr, syntax.Parens):
            expr = expr.inner
        if not isinstance(expr, syntax.Binary):
            return False
        if expr.op in ("/", "%"):
            divisor = expr.right
            while isinstance(divisor, syntax.Parens):
                divisor = divisor.inner
            power = divisor.value.bit_length() - 1 if isinstance(divisor, syntax.IntLiteral) else -1
            if not (1 <= power <= 63 and (1 << power) == divisor.value):
                return False
            if slot_of(expr.left) == slot:
                return True
            # A fused odd step `slot = (slot*m + c) / 2**b`: the dividend is one scaled lea
            # of the slot's own non-negative value and the halving keeps it non-negative.
            if expr.op != "/":
                return False
            terms = self._scaled_sum_terms(expr.left)
            return (terms is not None and terms[0].slot == slot
                    and self._expression_nonnegative(expr.left))
        if expr.op == "+":
            terms = self._scaled_sum_terms(expr)
            if terms is None:
                return False
            operand, _, addend, _ = terms
            return operand.slot == slot and addend >= 0
        return False

    def _fold_update(self, home: str, slot: int, expression: Optional[syntax.Expr]) -> bool:
        """Emit `home = home op value` when the expression updates its own slot.

        Folding is safe because a Cairn function has no globals or pointers: evaluating the other
        side cannot change the slot, and the operation is the same wrapping instruction the
        general path would use. `+` and `-` with a small literal need no scratch register.
        """
        if not isinstance(expression, syntax.Binary):
            return False
        operator = expression.op
        other = None
        if operator in COMMUTATIVE_UPDATES:
            if slot_of(expression.left) == slot:
                other = expression.right
            elif slot_of(expression.right) == slot:
                other = expression.left
        elif operator == "-" and slot_of(expression.left) == slot:
            other = expression.right
        if (operator in ("/", "%") and self._is_nonnegative(slot)
                and slot_of(expression.left) == slot):
            divisor = expression.right
            while isinstance(divisor, syntax.Parens):
                divisor = divisor.inner
            power = divisor.value.bit_length() - 1 if isinstance(divisor, syntax.IntLiteral) else -1
            if 1 <= power <= 63 and (1 << power) == divisor.value:
                # A non-negative dividend truncates like it floors, so the whole update is
                # one shift (quotient) or one mask (remainder) in the slot's own register.
                if operator == "/":
                    self.assembler.shift_imm(7, home, power)
                else:
                    self.assembler.arith_imm(4, home, divisor.value - 1)
                return True
        if (operator == "/" and isinstance(expression.left, syntax.Binary)
                and self._is_nonnegative(slot)):
            # The fused odd step `slot = (slot * m + c) / 2**b`: one scaled lea of its own
            # register, then the halving, still in the slot's register.
            divisor = expression.right
            while isinstance(divisor, syntax.Parens):
                divisor = divisor.inner
            power = divisor.value.bit_length() - 1 if isinstance(divisor, syntax.IntLiteral) else -1
            terms = None
            left = expression.left
            if 1 <= power <= 63 and (1 << power) == divisor.value:
                terms = self._scaled_sum_terms(left)
            if terms is not None and terms[0].slot == slot:
                _, scale_log2, addend, with_base = terms
                if with_base:
                    self.assembler.lea_scale(home, home, home, scale_log2, addend)
                else:
                    self.assembler.lea_index(home, home, scale_log2, addend)
                self.assembler.shift_imm(7, home, power)
                return True
        if other is None and operator == "+" and not self.delta_value:
            terms = self._scaled_sum_terms(expression)
            if terms is not None:
                operand, scale_log2, addend, with_base = terms
                source = self._home(operand.slot)
                if source is not None and (operand.slot != self.delta_slot or not self.delta_value):
                    if with_base:
                        self.assembler.lea_scale(home, source, source, scale_log2, addend)
                    else:
                        self.assembler.lea_index(home, source, scale_log2, addend)
                    return True
        if other is None:
            return False
        if (operator in IMMEDIATE_UPDATES and isinstance(other, syntax.IntLiteral)
                and IMMEDIATE_MIN <= other.value <= IMMEDIATE_MAX):
            if operator == "+":
                self.assembler.add_imm(home, other.value)
            else:
                self.assembler.sub_imm(home, other.value)
            return True
        self._emit_into(other, "rax")
        if operator == "*":
            self.assembler.imul(home, "rax")
        else:
            self.assembler.binary(UPDATE_OPCODES[operator], home, "rax")
        return True

    def _emit_home_offset(self, destination: str,
                          expression: Optional[syntax.Expr]) -> bool:
        """Lower `homed name ± literal` as one `lea`, or return False when the shape differs.

        A load, one wrapping arithmetic step, and the move into place fold into a single
        instruction with no flag or register side effects; `lea` wraps at 64 bits exactly
        like the general path would.
        """
        if not isinstance(expression, syntax.Binary) or expression.op not in ("+", "-"):
            return False
        if isinstance(expression.right, syntax.IntLiteral):
            target = expression.left
            value = expression.right.value
        elif expression.op == "+" and isinstance(expression.left, syntax.IntLiteral):
            target = expression.right
            value = expression.left.value
        else:
            return False
        if not isinstance(target, syntax.Name):
            return False
        if not -2 ** 31 <= value < 2 ** 31:
            return False
        home = self._home(target.slot)
        if home is None:
            return False
        value = value if expression.op == "+" else -value
        if target.slot == self.delta_slot and self.delta_value:
            value -= self.delta_value
        self.assembler.lea_imm(destination, home, value)
        return True

    def _emit_into(self, expression: Optional[syntax.Expr], destination: str) -> None:
        """Lower an expression, using `destination` directly when it is a leaf.

        A call is the only thing that can clobber a register, and a leaf never calls, so a name or
        literal can be loaded straight into the register that needs it instead of going via rax.
        """
        if destination == "rax":
            self._emit_expression(expression)
        elif isinstance(expression, syntax.Parens):
            self._emit_into(expression.inner, destination)
        elif self._emit_home_offset(destination, expression):
            pass
        elif isinstance(expression, syntax.IntLiteral):
            self.assembler.mov_imm64(destination, expression.value)
        elif isinstance(expression, syntax.Name):
            self._load_slot(expression.slot, destination)
        else:
            self._emit_expression(expression)
            self.assembler.mov_reg_reg(destination, "rax")

    def _emit_block(self, block: Optional[syntax.Block], tail: bool = False) -> None:
        """Emit every statement of a block in order.

        `tail` marks a block whose end runs straight into the function's epilogue label,
        which lets the last `return` fall through instead of jumping to it.
        """
        if block is None:
            return
        for index, statement in enumerate(block.statements):
            # Only the block's last statement falls through into its parent's tail
            # position; everything before it must still jump out of the block.
            last = tail and index == len(block.statements) - 1
            self._emit_statement(statement, last)

    def _emit_statement(self, statement: syntax.Stmt, tail: bool = False) -> None:
        """Lower one statement; `tail` only concerns a final `return`."""
        asm = self.assembler
        if isinstance(statement, syntax.Let):
            self._emit_value(statement.slot, statement.value)
        elif isinstance(statement, syntax.Assign):
            self._emit_value(statement.slot, statement.value)
        elif isinstance(statement, syntax.If):
            self._emit_if(statement, tail)
        elif isinstance(statement, syntax.While):
            self._emit_while(statement)
        elif isinstance(statement, syntax.Return):
            self._emit_expression(statement.value)
            if not tail:
                asm.jmp(self.epilogue)
        elif isinstance(statement, syntax.Break):
            if self.break_targets:
                asm.jmp(self.break_targets[-1])
        elif isinstance(statement, syntax.Continue):
            if self.continue_targets:
                asm.jmp(self.continue_targets[-1])
        elif isinstance(statement, syntax.Block):
            self._emit_block(statement, tail)
        elif isinstance(statement, syntax.ExprStmt):
            self._emit_expression(statement.expression)

    def _emit_if(self, statement: syntax.If, tail: bool = False) -> None:
        """`if` as a conditional branch and two labelled bodies.

        With an else block, the else body sits inline and the condition branches forward
        over it to the then body: the unconditional `jmp` is then paid only by the else
        path, and the frequent path falls straight through to the code after the `if`.
        """
        asm = self.assembler
        if statement.else_block is not None:
            then_label = self._fresh("if.then")
            end = self._fresh("if.end")
            self._emit_condition(statement.condition, then_label, when_true=True)
            self._emit_block(statement.else_block, tail)
            asm.jmp(end)
            asm.label(then_label)
            self._emit_block(statement.then_block, tail)
            asm.label(end)
        else:
            otherwise = self._fresh("if.else")
            self._emit_condition(statement.condition, otherwise, when_true=False)
            self._emit_block(statement.then_block, tail)
            asm.label(otherwise)

    def _emit_while(self, statement: syntax.While) -> None:
        """`while` with its own break and continue targets."""
        asm = self.assembler
        start = self._fresh("while.start")
        end = self._fresh("while.end")
        if statement.countdown:
            # A countdown loop guards once against zero or negative counts, then latches at
            # the bottom with one decrement and one taken-backward conditional branch. The
            # optimiser already deleted the in-body increment, so the latch must be emitted.
            self._emit_condition(statement.condition, end, when_true=False)
            asm.label(start)
            self.break_targets.append(end)
            self._emit_block(statement.body)
            self.break_targets.pop()
            home = self._countdown_home(statement)
            if home is not None:
                asm.sub_imm(home, 1)
            else:
                counter = self._countdown_slot(statement)
                self._load_slot(counter)
                asm.sub_imm("rax", 1)
                self._store_slot(counter)
            self.nonnegative_slots.discard(self._countdown_slot(statement))
            asm.jcc("nz", start)
            asm.label(end)
            return
        if self._try_emit_halving_loop(statement):
            return
        if has_call(statement.condition):
            # A condition whose evaluation calls would run twice if the loop re-emitted it
            # as the bottom latch, so the head-tested shape stays.
            asm.label(start)
            self._emit_condition(statement.condition, end, when_true=False)
            self.break_targets.append(end)
            self.continue_targets.append(start)
            self._emit_block(statement.body)
            self.break_targets.pop()
            self.continue_targets.pop()
            asm.jmp(start)
            asm.label(end)
            return
        # A pure guard rotates to do-while shape: tested once before entry, then latched at
        # the bottom of the body with one taken-backward conditional branch, so no iteration
        # ends in a `jmp` and the head check leaves the loop behind a single branch.
        self._emit_condition(statement.condition, end, when_true=False)
        asm.label(start)
        latch = self._fresh("while.latch")
        self.break_targets.append(end)
        self.continue_targets.append(latch)
        facts = self._guard_nonnegative(statement.condition)
        restored = self.nonnegative_slots
        self.nonnegative_slots = restored | set(facts)
        self._emit_block(statement.body)
        self.nonnegative_slots = restored
        self.break_targets.pop()
        self.continue_targets.pop()
        asm.label(latch)
        self._emit_condition(statement.condition, start, when_true=True)
        asm.label(end)

    def _try_emit_halving_loop(self, statement: syntax.While) -> bool:
        """Emit a fused odd-pair body as one unconditional half-step, or refuse.

        The optimiser's odd-pair pass leaves the shape `while k > c { if k % 2 == 0
        { k = k/2; s = s + 1 } else { k = (k*m + c)/2; s = s + 2 } }`, where m and c
        are odd. Halving FIRST is the same computation with the parity test folded
        into the halving: `shr` leaves the old low bit in CF, so the even path is one
        `add` and the odd path one scaled `lea` of the halved slot, both re-tested by
        the bottom latch. One by-64 identity makes the odd path a single lea, wrap
        included: with k odd and h = k >> 1, `m*k + c` is `2*m*h + (m + c)`, and m + c
        is even, so `(m*k + c) / 2` is exactly `m*h + (m + c) / 2` modulo 2**64. The
        guard `k > c` (c >= 0) proves k >= 1, which is what the odd identity needs and
        what makes `shr` equal `(k*m + c) / 2`'s signed halving. Loops with break or
        continue are refused: both would skip the latch-driven re-test.
        """
        if statement.countdown or statement.body is None:
            return False
        if has_call(statement.condition) or _contains_break_or_continue(statement.body):
            return False
        statements = list(statement.body.statements)
        if len(statements) != 1 or not isinstance(statements[0], syntax.If):
            return False
        branch = statements[0]
        if branch.then_block is None or branch.else_block is None:
            return False
        # The guard proves the dividend non-negative: `k > c` with c >= 0, `k >= c` with c >= 1.
        condition = statement.condition
        while isinstance(condition, syntax.Parens):
            condition = condition.inner
        if not (isinstance(condition, syntax.Binary)
                and condition.op in (">", ">=")
                and isinstance(condition.right, syntax.IntLiteral)
                and (condition.right.value >= 0 if condition.op == ">"
                     else condition.right.value >= 1)):
            return False
        k_slot = slot_of(condition.left)
        # The fact is derived here, before the generic rotation seeds it: the guard runs
        # before every body execution, so `k > c` (c >= 0) proves `k` non-negative in the body.
        if k_slot is None or k_slot < 0 or k_slot not in self._guard_nonnegative(condition):
            return False
        if k_slot == self.delta_slot and self.delta_value:
            return False
        # The parity dispatch: `k % 2 == 0` (then = even) or `k % 2 != 0` (then = odd).
        test = condition_branch = branch.condition
        while isinstance(test, syntax.Parens):
            test = test.inner
        if not (isinstance(test, syntax.Binary) and test.op in ("==", "!=")):
            return False
        zero = test.right
        while isinstance(zero, syntax.Parens):
            zero = zero.inner
        parity = test.left
        while isinstance(parity, syntax.Parens):
            parity = parity.inner
        if not (isinstance(zero, syntax.IntLiteral) and zero.value == 0
                and isinstance(parity, syntax.Binary) and parity.op == "%"):
            return False
        divisor = parity.right
        while isinstance(divisor, syntax.Parens):
            divisor = divisor.inner
        if not (isinstance(divisor, syntax.IntLiteral) and divisor.value == 2):
            return False
        if slot_of(parity.left) != k_slot:
            return False
        then_stmts = list(branch.then_block.statements)
        else_stmts = list(branch.else_block.statements)
        even_stmts, odd_stmts = ((then_stmts, else_stmts) if test.op == "=="
                                 else (else_stmts, then_stmts))
        if len(even_stmts) != 2 or len(odd_stmts) != 2:
            return False
        even, bump_even = even_stmts
        odd, bump_odd = odd_stmts
        if not (isinstance(even, syntax.Assign) and even.slot == k_slot
                and isinstance(odd, syntax.Assign) and odd.slot == k_slot):
            return False
        # Even path: exactly `k = k / 2`.
        division = even.value
        while isinstance(division, syntax.Parens):
            division = division.inner
        if not (isinstance(division, syntax.Binary) and division.op == "/"):
            return False
        divisor_two = division.right
        while isinstance(divisor_two, syntax.Parens):
            divisor_two = divisor_two.inner
        if not (isinstance(divisor_two, syntax.IntLiteral) and divisor_two.value == 2
                and slot_of(division.left) == k_slot):
            return False
        # Odd path: exactly `k = <k*m + c> / 2` with m, c odd.
        fused = odd.value
        while isinstance(fused, syntax.Parens):
            fused = fused.inner
        if not (isinstance(fused, syntax.Binary) and fused.op == "/"):
            return False
        fused_two = fused.right
        while isinstance(fused_two, syntax.Parens):
            fused_two = fused_two.inner
        fused_sum = fused.left
        while isinstance(fused_sum, syntax.Parens):
            fused_sum = fused_sum.inner
        if not (isinstance(fused_two, syntax.IntLiteral) and fused_two.value == 2
                and isinstance(fused_sum, syntax.Binary) and fused_sum.op == "+"):
            return False
        terms = self._scaled_sum_terms(fused_sum)
        if terms is None:
            return False
        operand, scale_log2, addend, with_base = terms
        if operand.slot != k_slot or not with_base or scale_log2 not in (1, 2, 3):
            return False
        multiplier = (1 << scale_log2) + 1
        # A fused odd step leaves k' = m*h + (m + c)/2 with h = k >> 1 >= 0; the guard-free
        # odd loop-back needs k' >= 2, i.e. m + c >= 4 (m = 3, c = 1 is the minimal firing shape).
        if addend % 2 == 0 or multiplier + addend < 4 \
                or not -2 ** 31 <= (multiplier + addend) // 2 < 2 ** 31:
            return False
        # The steps counter: `+ 1` on the even path, `+ 2` on the odd path, no aliasing.
        if not (isinstance(bump_even, syntax.Assign) and bump_even.slot >= 0
                and _is_plus_step(bump_even, 1)
                and isinstance(bump_odd, syntax.Assign)
                and bump_odd.slot == bump_even.slot and _is_plus_step(bump_odd, 2)):
            return False
        s_slot = bump_even.slot
        if s_slot == k_slot or s_slot == self.delta_slot or self.delta_value:
            return False
        k_home, s_home = self._home(k_slot), self._home(s_slot)
        if k_home is None or s_home is None:
            return False
        asm = self.assembler
        end = self._fresh("while.end")
        top = self._fresh("while.half")
        latch = self._fresh("while.latch")
        # Branchless pair: `shr` puts the pair's parity in CF, the odd value rides one
        # scaled lea of the halved slot, `cmov` selects it with no branch, and `adc` pays
        # the pass's steps (1 + the odd path's second step) in one instruction.
        scratch = SCRATCH
        self._emit_condition(statement.condition, end, when_true=False)
        asm.label(top)
        asm.shift_imm(5, k_home, 1)   # shr: k >>= 1, CF = old low bit (the pair's parity)
        asm.lea_scale(scratch, k_home, k_home, scale_log2, (multiplier + addend) // 2)
        asm.cmov("c", k_home, scratch)
        asm.adc_imm(s_home, 1)        # s += 1 + CF
        asm.label(latch)
        self._emit_condition(statement.condition, top, when_true=True)
        asm.jmp(end)
        asm.label(end)
        return True

    def _is_nonnegative(self, slot: int) -> bool:
        """Whether the slot provably holds a non-negative value at this emit point.

        The fact drops through a store of the slot, and a copy-shifted parameter reads a
        moved value, so that case counts as unknown.
        """
        return slot in self.nonnegative_slots and (slot != self.delta_slot or not self.delta_value)

    def _expression_nonnegative(self, expression: Optional[syntax.Expr]) -> bool:
        """Whether the expression provably evaluates non-negative at this emit point.

        A name reads its slot's guard fact; `k*f + c` in one lea form is non-negative when
        k is and the addend is, which is what a fused collatz odd step divides.
        """
        expr = expression
        while isinstance(expr, syntax.Parens):
            expr = expr.inner
        if isinstance(expr, syntax.Name):
            return self._is_nonnegative(expr.slot)
        if isinstance(expr, syntax.Binary) and expr.op == "+":
            terms = self._scaled_sum_terms(expr)
            if terms is not None:
                operand, _, addend, _ = terms
                return addend >= 0 and self._is_nonnegative(operand.slot)
        return False

    def _guard_nonnegative(self, condition: Optional[syntax.Expr]) -> List[int]:
        """Slots a `while slot > c` (or `slot >= c + 1`) guard proves non-negative in its body.

        The guard runs before every body execution, so during the body the slot exceeds a
        non-negative constant; only a store of the slot can break that, and stores drop the
        fact. Division then lowers as one bare arithmetic shift instead of the sign-fixup
        chain, which is the same proof strength gcc's value-range pass provides.
        """
        expression = condition
        while isinstance(expression, syntax.Parens):
            expression = expression.inner
        if not isinstance(expression, syntax.Binary) or expression.op not in (">", ">="):
            return []
        bound = expression.right
        while isinstance(bound, syntax.Parens):
            bound = bound.inner
        if not isinstance(bound, syntax.IntLiteral):
            return []
        slot = slot_of(expression.left)
        if slot is None or slot < 0:
            return []
        if (expression.op == ">" and bound.value >= 0) or (expression.op == ">=" and bound.value >= 1):
            return [slot]
        return []

    def _countdown_slot(self, statement: syntax.While) -> int:
        """The slot a countdown loop's counter occupies."""
        condition = statement.condition
        while isinstance(condition, syntax.Parens):
            condition = condition.inner
        left = condition.left
        while isinstance(left, syntax.Parens):
            left = left.inner
        return left.slot

    def _countdown_home(self, statement: syntax.While) -> Optional[str]:
        """The callee-saved register a countdown loop's counter lives in, or None.

        The latch register must survive every call the body makes, so caller-saved
        homes and frame slots take the generic scratch-based latch instead.
        """
        home = self._home(self._countdown_slot(statement))
        if home in ("rbx", "r12", "r13", "r14", "r15"):
            return home
        return None

    def _emit_condition(self, expression: Optional[syntax.Expr], target: str,
                        when_true: bool) -> None:
        """Jump to `target` when the condition has the requested truth value.

        A comparison branches on its own flags instead of materialising 0 or 1 and testing it
        again, which is most of the overhead in a tight loop. `!` flips the requested value; every
        other expression falls back to evaluating it and testing the result.
        """
        while isinstance(expression, syntax.Parens):
            expression = expression.inner
        if isinstance(expression, syntax.Unary) and expression.op == "!":
            self._emit_condition(expression.operand, target, not when_true)
            return
        if isinstance(expression, syntax.Binary) and expression.op in COMPARISON_CONDITIONS:
            self._emit_comparison_branch(expression, target, when_true)
            return
        self._emit_expression(expression)
        self.assembler.test("rax")
        self.assembler.jcc("nz" if when_true else "z", target)

    def _emit_comparison_branch(self, expression: syntax.Binary, target: str,
                                when_true: bool) -> None:
        """Compare two operands and branch, using an immediate form when the right side is small."""
        asm = self.assembler
        condition = COMPARISON_CONDITIONS[expression.op]
        if not when_true:
            condition = INVERTED_CONDITIONS[condition]
        right = expression.right
        while isinstance(right, syntax.Parens):
            right = right.inner
        left = expression.left
        while isinstance(left, syntax.Parens):
            left = left.inner
        if (isinstance(right, syntax.IntLiteral) and right.value == 0
                and expression.op in ("==", "!=")
                and isinstance(left, syntax.Binary) and left.op == "%"):
            dividend, divisor = left.left, left.right
            while isinstance(divisor, syntax.Parens):
                divisor = divisor.inner
            power = divisor.value.bit_length() - 1 \
                if isinstance(divisor, syntax.IntLiteral) else -1
            if power >= 1 and (1 << power) == divisor.value:
                # `x % 2**b == 0` holds exactly when the low b bits are zero, for every
                # sign, so the mask alone decides the branch and no fixup is needed.
                mask = (1 << power) - 1
                parity = "e" if COMPARISON_CONDITIONS[expression.op] == "e" else "ne"
                dividend_slot = slot_of(dividend)
                home = (self._home(dividend_slot)
                        if dividend_slot is not None
                        and (dividend_slot != self.delta_slot or not self.delta_value)
                        else None)
                if mask <= 127 and home is not None:
                    # The home's low bits decide the branch: no load, no destroyed rax.
                    asm.test_imm(home, mask)
                else:
                    self._emit_expression(dividend)
                    if mask <= 127:
                        asm.test_imm("rax", mask)
                    elif mask <= IMMEDIATE_MAX:
                        asm.arith_imm(4, "rax", mask)   # and rax, 2**b - 1
                    else:
                        asm.mov_imm64(SCRATCH, mask)
                        asm.binary(0x21, "rax", SCRATCH)  # and rax, scratch
                asm.jcc(parity if when_true else INVERTED_CONDITIONS[parity], target)
                return
        if isinstance(right, syntax.IntLiteral) and isinstance(left, syntax.Name) and self._home(left.slot) is not None:
            if left.slot == self.delta_slot and self.delta_value:
                # Inside a copy the parameter is shifted, so the compared value moves too.
                value = right.value + self.delta_value
                if IMMEDIATE_MIN <= value <= IMMEDIATE_MAX:
                    asm.cmp_imm(self._home(left.slot), value)
                    asm.jcc(condition, target)
                    return
            elif IMMEDIATE_MIN <= right.value <= IMMEDIATE_MAX:
                # A homed name compares against the literal in place: no rax load needed.
                asm.cmp_imm(self._home(left.slot), right.value)
                asm.jcc(condition, target)
                return
        if (isinstance(right, syntax.IntLiteral)
                and IMMEDIATE_MIN <= right.value <= IMMEDIATE_MAX):
            self._emit_into(expression.left, "rax")
            asm.cmp_imm("rax", right.value)
        else:
            self._emit_left_operand(expression.left, expression.right)
            asm.cmp_reg_reg("rcx", "rax")
        asm.jcc(condition, target)

    def _emit_expression(self, expression: Optional[syntax.Expr]) -> None:
        """Lower one expression, leaving its value in rax."""
        if expression is None:
            return
        asm = self.assembler
        if isinstance(expression, syntax.IntLiteral):
            asm.mov_imm64("rax", expression.value)
        elif isinstance(expression, syntax.Parens):
            self._emit_expression(expression.inner)
        elif isinstance(expression, syntax.Name):
            self._load_slot(expression.slot)
        elif isinstance(expression, syntax.Unary):
            self._emit_unary(expression)
        elif isinstance(expression, syntax.Binary):
            self._emit_binary(expression)
        elif isinstance(expression, syntax.Call):
            self._emit_call(expression)

    def _emit_unary(self, expression: syntax.Unary) -> None:
        """Negate, complement, or compare with zero."""
        asm = self.assembler
        self._emit_expression(expression.operand)
        if expression.op == "-":
            asm.unary(3, "rax")
        elif expression.op == "~":
            asm.unary(2, "rax")
        elif expression.op == "!":
            asm.test("rax")
            asm.setcc("e")

    def _emit_binary(self, expression: syntax.Binary) -> None:
        """Short-circuit `&&`/`||`, otherwise place the left operand, then combine."""
        if expression.op == "&&":
            self._emit_and(expression)
            return
        if expression.op == "||":
            self._emit_or(expression)
            return
        asm = self.assembler
        right = expression.right
        if expression.op == "+" and self._emit_scaled_sum(expression):
            return
        if (expression.op in ("+", "-") and isinstance(right, syntax.IntLiteral)
                and -IMMEDIATE_MAX - 1 <= right.value <= IMMEDIATE_MAX
                and self._emit_home_offset("rax", expression)):
            return
        if isinstance(right, syntax.IntLiteral) and -IMMEDIATE_MAX - 1 <= right.value <= IMMEDIATE_MAX \
                and expression.op in IMMEDIATE_BINARY_EXTENSIONS:
            self._emit_expression(expression.left)
            asm.arith_imm(IMMEDIATE_BINARY_EXTENSIONS[expression.op], "rax", right.value)
            return
        if isinstance(right, syntax.IntLiteral) and 0 <= right.value <= 63 \
                and expression.op in IMMEDIATE_SHIFT_EXTENSIONS:
            self._emit_expression(expression.left)
            asm.shift_imm(IMMEDIATE_SHIFT_EXTENSIONS[expression.op], "rax", right.value)
            return
        if expression.op in ("/", "%") and self._emit_power_of_two_division(expression):
            return
        self._emit_left_operand(expression.left, expression.right)
        if expression.op in ("/", "%"):
            self._emit_division(expression.op)
        elif expression.op in COMPARISON_CONDITIONS:
            asm.cmp_reg_reg("rcx", "rax")
            asm.setcc(COMPARISON_CONDITIONS[expression.op])
        elif expression.op in ("<<", ">>"):
            asm.mov_reg_reg(SCRATCH, "rax")
            asm.mov_reg_reg("rax", "rcx")
            asm.mov_reg_reg("rcx", SCRATCH)
            asm.shift_cl(4 if expression.op == "<<" else 7)
        elif expression.op == "*":
            asm.imul("rax", "rcx")
        elif expression.op == "-":
            asm.binary(0x29, "rcx", "rax")
            asm.mov_reg_reg("rax", "rcx")
        elif expression.op in ARITHMETIC_OPCODES:
            asm.binary(ARITHMETIC_OPCODES[expression.op], "rax", "rcx")

    def _emit_left_operand(self, left: Optional[syntax.Expr], right: Optional[syntax.Expr]) -> None:
        """Evaluate `left`, keep it across `right`, and leave it in rcx with right in rax.

        A temporary register is used when one is free and the right operand cannot call; a
        call would clobber it. When the right operand does call and the enclosing frame has a
        pad slot, the slot holds the left value instead of a `push`: no stack shift means no
        depth change and so no call-site pads inside `right`. The pad slot is private to this
        frame, and every nested frame's traffic lives below `rsp`, so the span is exclusive.
        """
        asm = self.assembler
        if self.temporaries < len(TEMPORARY_REGISTERS) and not has_call(right):
            if is_leaf(right):
                self._emit_into(left, "rcx")
                self._emit_expression(right)
            else:
                temporary = TEMPORARY_REGISTERS[self.temporaries]
                self._emit_into(left, temporary)
                self.temporaries += 1
                self._emit_expression(right)
                self.temporaries -= 1
                asm.mov_reg_reg("rcx", temporary)
            return
        if self.holds and self.holds[-1] is not None:
            # Inside a copy whose entry pushed the hold register, the left value rides in
            # that register across `right`: deeper copies push and pop it before writing,
            # and real callees keep their merge values in pad slots, so nothing below this
            # frame writes the register without saving it first.
            hold = self.holds[-1]
            self._emit_expression(left)
            asm.mov_reg_reg(hold, "rax")
            self._emit_expression(right)
            asm.mov_reg_reg("rcx", hold)
            return
        if self.hold_units > 0:
            # Every merge of this frame, in its real body or its inline copies, keeps
            # its left value in a level-indexed slot of the real frame: copies carry no
            # stack state of their own, so `right` runs with `depth` and alignment
            # unchanged and its calls need no on-the-spot pads.
            offset = 8 * self.inline_depth + self.depth
            self._emit_expression(left)
            asm.mov_store_rsp_disp(offset, "rax")
            self._emit_expression(right)
            asm.mov_load_rsp_disp(offset, "rcx")
            return
        if self.pad_slots and self.pad_slots[-1]:
            self._emit_expression(left)
            asm.mov_store_rsp("rax")
            self._emit_expression(right)
            asm.mov_load_rsp("rcx")
            return
        self._emit_expression(left)
        asm.push("rax")
        self.depth += 8
        self._emit_expression(right)
        asm.pop("rcx")
        self.depth -= 8

    def _emit_power_of_two_division(self, expression: syntax.Binary) -> bool:
        """Lower `x / 2**b` or `x % 2**b` as a shift fixup chain; True when emitted.

        Division by a positive power of two never faults and needs no `idiv`: the
        sign word `x >>sar 63` (0 or all-ones) becomes `0` or `2**b - 1` by a logical
        shift right, the addition forces negative dividends up to the next multiple
        (toward-zero truncation), and the arithmetic shift gives the quotient. For a
        remainder, masking the shifted-up value and subtracting the fixup recovers
        exactly `x - (x / 2**b) * 2**b`, which keeps the dividend's sign.
        """
        right = expression.right
        while isinstance(right, syntax.Parens):
            right = right.inner
        if not isinstance(right, syntax.IntLiteral) or right.value <= 0:
            return False
        power = right.value.bit_length() - 1
        if power < 1 or power > 63 or (1 << power) != right.value:
            return False
        asm = self.assembler
        if self._expression_nonnegative(expression.left):
            # A non-negative dividend truncates like it floors, so the sign fixup drops:
            # the quotient is one shift and the remainder one mask.
            self._emit_expression(expression.left)
            if expression.op == "/":
                asm.shift_imm(7, "rax", power)
            else:
                asm.arith_imm(4, "rax", (1 << power) - 1)
            return True
        self._emit_expression(expression.left)
        asm.mov_reg_reg("rcx", "rax")
        asm.shift_imm(7, "rcx", 63)       # sar rcx, 63: the sign word
        asm.shift_imm(5, "rcx", 64 - power)  # shr rcx, 64-b: 0 or 2**b - 1 (shift group /5)
        asm.binary(0x01, "rax", "rcx")    # add rax, rcx
        if expression.op == "/":
            asm.shift_imm(7, "rax", power)
        else:
            target = (1 << power) - 1
            if target <= IMMEDIATE_MAX:
                asm.arith_imm(4, "rax", target)   # and rax, 2**b - 1: the masked-up value
            else:
                asm.mov_imm64(SCRATCH, target)
                asm.binary(0x21, "rax", SCRATCH)  # and rax, scratch
            asm.binary(0x29, "rax", "rcx")        # sub rax, rcx: drop the fixup
        return True

    def _scaled_sum_terms(self, expression: syntax.Binary):
        """The scaled-item pattern of a `+` expression: `(Name, scale_log2, addend, with_base)`.

        The factor must be one more than a power of two (`base + index*2**log2`) or a pure
        power of two (`index*2**log2`, no base); the addend must fit the x86 displacement.
        `None` when the expression does not match.
        """
        left, right = expression.left, expression.right
        while isinstance(left, syntax.Parens):
            left = left.inner
        while isinstance(right, syntax.Parens):
            right = right.inner
        addend, scaled, operand = None, None, None
        for part in (left, right):
            if isinstance(part, syntax.IntLiteral) and addend is None:
                addend = part.value
            elif isinstance(part, syntax.Binary) and part.op == "*" and scaled is None:
                multiplier, term = part.left, part.right
                while isinstance(multiplier, syntax.Parens):
                    multiplier = multiplier.inner
                while isinstance(term, syntax.Parens):
                    term = term.inner
                if isinstance(multiplier, syntax.IntLiteral) and isinstance(term, syntax.Name):
                    scaled, operand = multiplier.value, term
                elif isinstance(term, syntax.IntLiteral) and isinstance(multiplier, syntax.Name):
                    scaled, operand = term.value, multiplier
        if addend is None or scaled is None or operand.slot < 0 or not -2 ** 31 <= addend < 2 ** 31:
            return None
        # A power of two that is also one more than a power of two (only 2) prefers the
        # base form; both are one instruction with a displacement.
        forms = {2: (0, True), 3: (1, True), 4: (2, False), 5: (2, True), 9: (3, True)}
        if scaled not in forms:
            return None
        scale_log2, with_base = forms[scaled]
        return operand, scale_log2, addend, with_base

    def _emit_scaled_sum(self, expression: syntax.Binary) -> bool:
        """Lower `c1 + k*c2` (either operand order) as one scaled `lea`; True when emitted.

        This is the `idiv` companion: a collatz-style step `3*k + 1` becomes one instruction.
        """
        terms = self._scaled_sum_terms(expression)
        if terms is None:
            return False
        operand, scale_log2, addend, with_base = terms
        home = self._home(operand.slot)
        if home is None or home == "rbp":
            self._emit_expression(operand)
            home = "rax"
        asm = self.assembler
        if with_base:
            asm.lea_scale("rax", home, home, scale_log2, addend)
        else:
            asm.lea_index("rax", home, scale_log2, addend)
        return True

    def _emit_division(self, operator: str) -> None:
        """Signed division with an explicit zero-divisor trap, quotient or remainder.

        `idiv` faults on `INT64_MIN / -1` as well as on a zero divisor, so both
        route to the `DIV_ZERO` helper the folding pass already preserves.
        """
        asm = self.assembler
        asm.test("rax")
        asm.jcc("z", DIV_ZERO_LABEL)
        no_overflow = self._fresh("div.no_overflow")
        asm.cmp_imm("rax", -1)
        asm.jcc("nz", no_overflow)
        asm.mov_imm64(SCRATCH, INT64_MIN)
        asm.cmp_reg_reg("rcx", SCRATCH)
        asm.jcc("e", DIV_ZERO_LABEL)
        asm.label(no_overflow)
        asm.mov_reg_reg(SCRATCH, "rax")
        asm.mov_reg_reg("rax", "rcx")
        asm.cqo()
        asm.divide(SCRATCH, signed=True)
        if operator == "%":
            asm.mov_reg_reg("rax", "rdx")

    def _emit_and(self, expression: syntax.Binary) -> None:
        """`&&`: the right side is evaluated only when the left side is non-zero."""
        asm = self.assembler
        false_label = self._fresh("and.false")
        end = self._fresh("and.end")
        self._emit_expression(expression.left)
        asm.test("rax")
        asm.jcc("z", false_label)
        self._emit_expression(expression.right)
        asm.test("rax")
        asm.setcc("nz")
        asm.jmp(end)
        asm.label(false_label)
        asm.xor_zero("rax")
        asm.label(end)

    def _emit_or(self, expression: syntax.Binary) -> None:
        """`||`: the right side is evaluated only when the left side is zero."""
        asm = self.assembler
        true_label = self._fresh("or.true")
        end = self._fresh("or.end")
        self._emit_expression(expression.left)
        asm.test("rax")
        asm.jcc("nz", true_label)
        self._emit_expression(expression.right)
        asm.test("rax")
        asm.setcc("nz")
        asm.jmp(end)
        asm.label(true_label)
        asm.mov_imm64("rax", 1)
        asm.label(end)

    def _emit_call(self, expression: syntax.Call) -> None:
        """Evaluate arguments left to right, place them in the ABI registers, then call.

        `print` keeps a stack round-trip on purpose: the runtime helper clobbers both `rax` and
        `rcx`, so the `pop` is what restores the value `print(x)` must return.
        """
        asm = self.assembler
        if expression.name == "print":
            self._emit_expression(expression.arguments[0])
            asm.push("rax")
            self.depth += 8
            self._emit_call_site(PRINT_LABEL)
            asm.pop("rax")
            self.depth -= 8
            return
        if self.delta_slot >= 0 and expression.name == self.current.name:
            if (len(expression.arguments) == 1
                    and self.inline_depth < MAX_INLINE_DEPTH):
                delta = self._delta_argument(expression)
                if delta is not None:
                    self._emit_delta_self_call(expression, delta)
                    return
            second = expression.arguments[1] if len(expression.arguments) == 2 else None
            while isinstance(second, syntax.Parens):
                second = second.inner
            if (len(expression.arguments) == 2
                    and self.value_slot >= 0
                    and self.inline_depth < MAX_PAIR_INLINE_DEPTH
                    and not isinstance(second, (syntax.IntLiteral, syntax.Call))):
                # A literal-valued self-call stays a real call at every depth: its copy
                # is the one shape every enclosing copy's n == 0 branch repeats, and
                # text-copying it 2**depth times is what bloated the instrumented
                # builds. The real call pays two direct binds and no stack state.
                delta = self._delta_argument(expression)
                if delta is not None:
                    self._emit_pair_self_call(expression, delta)
                    return
            # A delta-eligible function's odd-shaped self-calls stay real calls: the
            # shadow-copy machinery would retarget the parameter's home register and
            # break the accumulated-delta reading every pure-text copy relies on.
        elif self._is_inline_candidate(expression):
            self._emit_inlined_self_call(expression)
            return
        if len(expression.arguments) == 1:
            self._emit_into(expression.arguments[0], ARGUMENT_REGISTERS[0])
            self._emit_call_site(function_label(expression.name))
            return
        if self._emit_pure_arguments(expression.arguments):
            self._emit_call_site(function_label(expression.name))
            return
        for argument in expression.arguments:
            self._emit_expression(argument)
            asm.push("rax")
            self.depth += 8
        for index in reversed(range(len(expression.arguments))):
            asm.pop(ARGUMENT_REGISTERS[index])
            self.depth -= 8
        self._emit_call_site(function_label(expression.name))

    def _is_pure_argument(self, expression: Optional[syntax.Expr]) -> bool:
        """Whether an argument can bind straight into its ABI register.

        Names, literals, `name +/- literal` off a home, and their parenthesized forms
        cannot call or trap, so binding them out of source order is unobservable and
        none of their lowerings clobbers another argument register.
        """
        argument = expression
        while isinstance(argument, syntax.Parens):
            argument = argument.inner
        if isinstance(argument, (syntax.IntLiteral, syntax.Name)):
            return True
        if isinstance(argument, syntax.Binary) and argument.op in ("+", "-"):
            target = None
            value = None
            if isinstance(argument.right, syntax.IntLiteral):
                target, value = argument.left, argument.right.value
            elif argument.op == "+" and isinstance(argument.left, syntax.IntLiteral):
                target, value = argument.right, argument.left.value
            return (
                isinstance(target, syntax.Name)
                and -2 ** 31 <= value < 2 ** 31
                and self._home(target.slot) is not None
            )
        return False

    def _emit_pure_arguments(self, arguments: list) -> bool:
        """Bind call arguments straight into their registers, or return False.

        The generic path pays a push and a pop per argument plus the round-trip
        through rax; every multi-argument call with pure arguments drops both. When
        only the first argument is pure (the Ackermann shape `f(m - 1, f(m, n - 1))`),
        it still binds directly after the second argument evaluates, saving one pair.
        """
        registers = ARGUMENT_REGISTERS[:len(arguments)]
        if all(self._is_pure_argument(argument) for argument in arguments):
            for register, argument in zip(reversed(registers), reversed(arguments)):
                self._emit_into(argument, register)
            return True
        if len(arguments) == 2 and self._is_pure_argument(arguments[0]):
            self._emit_expression(arguments[1])
            self.assembler.mov_reg_reg(registers[1], "rax")
            self._emit_into(arguments[0], registers[0])
            return True
        return False

    def _delta_hold_units(self, function: syntax.Function) -> int:
        """The per-level merge slots delta inlining needs, in 8-byte units, or 0 when ineligible.

        A function qualifies when its whole state is one parameter it never rebinds: its
        self-calls with constant-delta arguments lower as pure text copies, and their merges
        keep values in level-indexed slots of this frame instead of any per-copy stack.
        """
        if function.slots != 1 or len(function.parameters) != 1:
            return 0
        if self._assign_writes_parameter(function.body, function.parameters[0].slot):
            return 0
        if not self._has_delta_self_call(function.body, function.name, function.parameters[0].slot):
            return 0
        return MAX_INLINE_DEPTH + 1

    def _assign_writes_parameter(self, node, slot: int) -> bool:
        """Whether any statement in `node` may rebind or shadow the parameter in `slot`."""
        if isinstance(node, (syntax.Let, syntax.Assign)):
            return True
        if isinstance(node, syntax.If):
            return any(self._assign_writes_parameter(b, slot)
                       for b in (node.then_block, node.else_block) if b is not None)
        if isinstance(node, syntax.While):
            return node.body is not None and self._assign_writes_parameter(node.body, slot)
        if isinstance(node, syntax.Block):
            return any(self._assign_writes_parameter(s, slot) for s in node.statements)
        return False

    def _has_delta_self_call(self, node, name: str, slot: int) -> bool:
        """Whether `node` contains at least one self-call whose argument carries a constant delta.

        Pure-text copies only pay off when the call sites they replace are the hot ones: a
        function whose self-recursive arguments never match the shape saves nothing and keeps
        the legacy form.
        """
        if node is None:
            return False
        if isinstance(node, syntax.Call):
            if node.name == name and len(node.arguments) == 1 and self._delta_argument(node, slot) is not None:
                return True
            return any(self._has_delta_self_call(a, name, slot) for a in node.arguments)
        if isinstance(node, (syntax.Let, syntax.Assign)):
            return self._has_delta_self_call(node.value, name, slot)
        if isinstance(node, syntax.ExprStmt):
            return self._has_delta_self_call(node.expression, name, slot)
        if isinstance(node, syntax.Return):
            return self._has_delta_self_call(node.value, name, slot)
        if isinstance(node, syntax.Unary):
            return self._has_delta_self_call(node.operand, name, slot)
        if isinstance(node, syntax.Binary):
            return (self._has_delta_self_call(node.left, name, slot)
                    or self._has_delta_self_call(node.right, name, slot))
        if isinstance(node, syntax.Parens):
            return self._has_delta_self_call(node.inner, name, slot)
        if isinstance(node, syntax.If):
            if self._has_delta_self_call(node.condition, name, slot):
                return True
            return any(self._has_delta_self_call(b, name, slot)
                       for b in (node.then_block, node.else_block) if b is not None)
        if isinstance(node, syntax.While):
            return (self._has_delta_self_call(node.condition, name, slot)
                    or self._has_delta_self_call(node.body, name, slot))
        if isinstance(node, syntax.Block):
            return any(self._has_delta_self_call(s, name, slot) for s in node.statements)
        return False

    def _delta_argument(self, expression: syntax.Call, slot: Optional[int] = None) -> Optional[int]:
        """The constant shift this call's argument adds to the inlined parameter, or None.

        `n`, `n - 1` and `n + 2` qualify; anything the walk cannot tie to the parameter
        alone does not, and falls back to the register-copy form.
        """
        if slot is None:
            slot = self.delta_slot
        if slot < 0:
            return None
        argument = expression.arguments[0]
        while isinstance(argument, syntax.Parens):
            argument = argument.inner
        if isinstance(argument, syntax.Name) and argument.slot == slot:
            return 0
        if (
            isinstance(argument, syntax.Binary)
            and argument.op in ("+", "-")
            and isinstance(argument.left, syntax.Name)
            and argument.left.slot == slot
            and isinstance(argument.right, syntax.IntLiteral)
            and IMMEDIATE_MIN <= argument.right.value <= IMMEDIATE_MAX
        ):
            return argument.right.value if argument.op == "+" else -argument.right.value
        return None

    def _emit_delta_self_call(self, expression: syntax.Call, delta: int) -> None:
        """Lower a constant-delta self-call as pure text: the copy's parameter is the

        caller's parameter plus `delta`, so no shadow register, frame, or argument move
        is emitted at all.
        """
        asm, saved_epilogue = self.assembler, self.epilogue
        saved_value, saved_depth = self.delta_value, self.inline_depth
        self.epilogue = self._fresh(f"{function_label(self.current.name)}.delta")
        self.inline_depth += 1
        # The copy's parameter is `local_parent + delta`, and the copy reads its own
        # parameter as `real - delta_value`, so descending by `delta` grows `delta_value`
        # by the same amount on the opposite sign.
        self.delta_value -= delta
        self._emit_block(self.current.body, tail=True)
        asm.label(self.epilogue)
        self.epilogue = saved_epilogue
        self.delta_value = saved_value
        self.inline_depth = saved_depth

    def _pair_inline_shape(self, function: syntax.Function) -> bool:
        """Whether the function fits the two-parameter pure-text inlining shape.

        The Ackermann lives here: exactly the two parameters (no other slot), a body
        whose every path ends in an explicit `return` (no binding statement, no loop,
        no call inside any condition), every self-call's first argument shifted by a
        constant off the first parameter, and a second argument that is a literal, the
        second parameter (optionally shifted), or another same-shaped self-call. Copies
        then read the first parameter through the delta machinery and carry the second
        in its home register, rebinding it at entry; `MAX_PAIR_INLINE_DEPTH` bounds the
        2**depth growth of the pure-text copies.
        """
        if function.slots != 2 or len(function.parameters) != 2:
            return False
        if not self._has_value_zero_guard(function.body, function.parameters[1].slot):
            return False
        return self._is_pair_body(function.body, function, 0)

    def _has_value_zero_guard(self, node, slot: int) -> bool:
        """Whether the body tests the second parameter against zero ahead of the tail.

        The chain loop's counter starts at the second parameter's live value, which is
        exact only when a `n == 0` guard governs the tail: it is what makes the loop
        count and the recursion's unwinding agree, rung by rung.
        """
        if not isinstance(node, syntax.Block):
            return False
        return any(
            isinstance(statement, syntax.If) and self._is_zero_test(statement.condition, slot)
            for statement in node.statements[:-1])

    def _is_zero_test(self, condition, slot: int) -> bool:
        """Whether the condition compares the slot-holding name against literal zero."""
        while isinstance(condition, syntax.Parens):
            condition = condition.inner
        if isinstance(condition, syntax.Binary) and condition.op == "==":
            names = [side for side in (condition.left, condition.right)
                     if isinstance(side, syntax.Name)]
            zeros = [side for side in (condition.left, condition.right)
                     if isinstance(side, syntax.IntLiteral) and side.value == 0]
            return (len(names) == 1 and len(zeros) == 1
                    and names[0].slot == slot)
        return False

    def _is_chain_step(self, argument) -> bool:
        """Whether the argument is `param1 - 1`: the exact one-step chain the loop replaces."""
        return (isinstance(argument, syntax.Binary) and argument.op == "-"
                and isinstance(argument.right, syntax.IntLiteral)
                and argument.right.value == 1
                and isinstance(argument.left, syntax.Name)
                and argument.left.slot == self.value_slot)

    def _emit_chain_loop(self, expression: syntax.Call) -> None:
        """Lower the one-step delta-0 chain as an iterated loop: `R(j) = f**(j+1)(1)`.

        The enclosing conditions proved m != 0, and the shape's `n == 0` guard governs
        the tail, so the site's own copy body collapses to its tail unfolding: the seed
        value of 1 IS the chain's base case (the n == 0 exit returns ack(m - 1, 1),
        which the first iteration applies), and f(v) = ack(m - 1, v) is exactly one
        m-shifted pair copy whose second parameter needs no rebinding. The counter
        rides r13 - callee-saved, unused by the two-home frame - so the loop body and
        its real calls cannot disturb it. A negative starting value never terminates
        the reference recursion either; the loop wraps after 2**64 counts instead of
        overflowing the stack, which the ADR records as the one divergence.
        """
        asm = self.assembler
        value_home = self._home(self.value_slot)
        top = self._fresh("pair.loop")
        exit_label = self._fresh("pair.exit")
        # A loop whose body copy runs another chain site rebinds r13, so any
        # enclosing loop's counter parks on the stack for the body's duration.
        nested = self.chain_loop_depth > 0
        if nested:
            asm.push("r13")
        self.chain_loop_depth += 1
        asm.mov_reg_reg("r13", value_home)       # k := n: the count the chain runs
        asm.mov_imm64(value_home, 1)             # v := 1: the chain's own base case
        asm.label(top)
        asm.cmp_imm("r13", 0)
        asm.jcc("e", exit_label)
        # One application f(v): the m-shifted copy reads the value register as it is.
        # The synthetic call borrows the site's spans; nothing re-parses source text.
        span = expression.span
        self._emit_pair_self_call(syntax.Call(
            span=span,
            name=self.current.name,
            arguments=[syntax.Binary(span=span, op="-",
                                     left=syntax.Name(span=span, slot=self.delta_slot),
                                     right=syntax.IntLiteral(span=span, value=1)),
                       syntax.Name(span=span, slot=self.value_slot)]), -1)
        asm.mov_reg_reg(value_home, "rax")       # v := f(v)
        asm.sub_imm("r13", 1)
        asm.jcc("nz", top)
        asm.label(exit_label)
        asm.mov_reg_reg("rax", value_home)       # the site's result
        self.chain_loop_depth -= 1
        if nested:
            asm.pop("r13")

    def _is_pair_body(self, node, function: syntax.Function, depth: int) -> bool:
        """Whether every path through `node` ends in an explicit pair-shaped `return`."""
        if node is None:
            return False
        if isinstance(node, syntax.Return):
            return self._is_pair_return(node.value, function, depth)
        if isinstance(node, (syntax.Let, syntax.Assign, syntax.While, syntax.Break,
                             syntax.Continue, syntax.ExprStmt)):
            return False
        if isinstance(node, syntax.If):
            if node.then_block is None or node.else_block is None:
                return False
            if has_call(node.condition):
                return False
            return (self._is_pair_body(node.then_block, function, depth)
                    and self._is_pair_body(node.else_block, function, depth))
        if isinstance(node, syntax.Block):
            statements = node.statements
            if not statements or not isinstance(statements[-1], syntax.Return):
                return False
            for statement in statements[:-1]:
                # A fall-through path runs no copy yet, so the value register it reads
                # is still the caller's; a taken path must leave through an epilogue.
                if not isinstance(statement, syntax.If) or has_call(statement.condition):
                    return False
                if statement.then_block is not None and not self._is_pair_body(
                        statement.then_block, function, depth):
                    return False
                if statement.else_block is not None and not self._is_pair_body(
                        statement.else_block, function, depth):
                    return False
            return self._is_pair_return(statements[-1].value, function, depth)
        return False

    def _is_pair_return(self, expression, function: syntax.Function, depth: int) -> bool:
        """Whether a `return` value fits the pair copies: no self-call, or one that
        matches the two-parameter shape (possibly nesting in its second argument)."""
        while isinstance(expression, syntax.Parens):
            expression = expression.inner
        if not has_call(expression):
            return True
        if isinstance(expression, syntax.Call) and expression.name == function.name:
            return self._is_pair_call(expression, function, depth)
        return False

    def _is_pair_call(self, call: syntax.Call, function: syntax.Function,
                      depth: int) -> bool:
        """Whether one self-call matches the pair shape: first argument a constant shift
        of the first parameter, second a bindable value or a nested same-shaped call."""
        if len(call.arguments) != 2:
            return False
        first, second = call.arguments[0], call.arguments[1]
        if self._delta_argument(call, function.parameters[0].slot) is None:
            return False
        while isinstance(second, syntax.Parens):
            second = second.inner
        if isinstance(second, syntax.IntLiteral):
            return True
        if isinstance(second, syntax.Name) and second.slot == function.parameters[1].slot:
            return True
        if (isinstance(second, syntax.Binary) and second.op in ("+", "-")):
            target, value = None, None
            if isinstance(second.right, syntax.IntLiteral):
                target, value = second.left, second.right.value
            elif second.op == "+" and isinstance(second.left, syntax.IntLiteral):
                target, value = second.right, second.left.value
            if (isinstance(target, syntax.Name)
                    and target.slot == function.parameters[1].slot
                    and -2 ** 31 <= value < 2 ** 31):
                return True
        if (depth < MAX_PAIR_INLINE_DEPTH
                and isinstance(second, syntax.Call)
                and second.name == function.name
                and self._is_pair_call(second, function, depth + 1)):
            return True
        return False

    def _emit_pair_self_call(self, expression: syntax.Call, delta: int) -> None:
        """Lower a two-parameter shape self-call as pure text, binding the value first.

        The copy's first parameter reads through the accumulated delta exactly like a
        delta copy; its second parameter lives in one home register that the copy
        rebinds at entry - a literal, a shift of the enclosing value, or the second
        argument's result (a nested copy, or below the depth cap a real call). Reads of
        the second parameter never sit on a path that survives a rebind: the shape
        checks in `_pair_inline_shape` keep every desced copy's enclosing text past its
        binding free of further reads of the register.
        """
        asm = self.assembler
        saved_epilogue = self.epilogue
        saved_delta, saved_depth = self.delta_value, self.inline_depth
        saved_facts = set(self.nonnegative_slots)
        value_home = self._home(self.value_slot)
        argument = expression.arguments[1]
        while isinstance(argument, syntax.Parens):
            argument = argument.inner
        if self._is_chain_step(argument):
            # The delta-0 chain is a counted iteration, not another text copy: with
            # delta 0 the copy body its loop re-runs is the same text at the same
            # shift, so copying it again would only multiply code (and time).
            self._emit_chain_loop(expression)
            return
        if isinstance(argument, syntax.Call):
            # The nested copy - or, past the depth cap, the real call - lands its
            # result in rax; the copy's second parameter binds it.
            self._emit_expression(argument)
            asm.mov_reg_reg(value_home, "rax")
        elif isinstance(argument, syntax.IntLiteral):
            asm.mov_imm64(value_home, argument.value)
        elif isinstance(argument, syntax.Binary):
            value = argument.right.value if argument.op == "+" else -argument.right.value
            if isinstance(argument.left, syntax.IntLiteral):
                value = argument.left.value
            asm.lea_imm(value_home, value_home, value)
        # A bare second parameter binds nothing: the register already holds the value.
        self.nonnegative_slots.discard(self.value_slot)
        self.delta_value -= delta
        self.inline_depth += 1
        self.epilogue = self._fresh(f"{function_label(self.current.name)}.pair")
        self._emit_block(self.current.body, tail=True)
        asm.label(self.epilogue)
        self.epilogue = saved_epilogue
        self.delta_value = saved_delta
        self.inline_depth = saved_depth
        self.nonnegative_slots = saved_facts

    def _is_inline_candidate(self, expression: syntax.Call) -> bool:
        """Whether this call may be lowered as an inline copy of the current function.

        A copy needs one shadow home register per slot of the current function, disjoint
        from the homes still in use, so only functions that fit twice in the home set
        qualify. Frame functions fall back to a real call, and copies nest only up to
        `MAX_INLINE_DEPTH`, because every extra copy doubles the size of its branch.
        """
        if self.current is None or self.inline_depth >= MAX_INLINE_DEPTH:
            return False
        if expression.name != self.current.name:
            return False
        if len(expression.arguments) != 1 or len(self.current.parameters) != 1:
            return False
        return 2 * len(self.homes) <= len(HOME_REGISTERS)

    def _emit_inlined_self_call(self, expression: syntax.Call) -> None:
        """Lower a self-call as one inline copy: the argument to a shadow home, the body in place.

        The shadow homes are pushed before the copy and popped after, so enclosing frames never
        observe them; an odd count takes one pad slot (below the save area) to keep rsp 16-byte
        aligned at every call the copy makes. The copy's own self-calls stay real calls, bounding
        expansion at the site.
        """
        asm = self.assembler
        saved_homes = self.homes
        saved_epilogue = self.epilogue
        saved_pads = self.pad_slots
        saved_holds = self.holds
        shadow = list(HOME_REGISTERS[len(saved_homes):2 * len(saved_homes)])
        hold = (HOME_REGISTERS[2 * len(saved_homes)]
                if 2 * len(saved_homes) < len(HOME_REGISTERS) else None)
        slots = shadow + ([hold] if hold is not None else [])
        own_pad = len(slots) % 2 == 1
        self._emit_expression(expression.arguments[0])  # the caller's homes are still live
        for register in slots:
            asm.push(register)
        if own_pad:
            # Pushes come first so the pad lands at [rsp]: with no hold register available
            # (three slots), this copy's merges keep their left values in the pad slot, and
            # the pad keeps every call inside the copy free of on-the-spot alignment shifts.
            asm.sub_rsp(8)
        self.homes = tuple(shadow)
        self.pad_slots = saved_pads + (own_pad,)
        self.holds = saved_holds + (hold,)
        self.epilogue = self._fresh(f"{function_label(self.current.name)}.inline")
        self.inline_depth += 1
        self._clear_frame(self.current)
        asm.mov_reg_reg(shadow[0], "rax")
        self._emit_block(self.current.body, tail=True)
        asm.label(self.epilogue)
        if own_pad:
            asm.add_rsp(8)
        for register in reversed(slots):
            asm.pop(register)
        self.homes = saved_homes
        self.pad_slots = saved_pads
        self.holds = saved_holds
        self.epilogue = saved_epilogue
        self.inline_depth -= 1

    def _body_may_hold(self, body: Optional[syntax.Block]) -> bool:
        """Whether any merge in this body keeps its left value across a right operand that calls."""
        def visit(expression: Optional[syntax.Expr]) -> bool:
            if expression is None:
                return False
            if isinstance(expression, syntax.Binary):
                if expression.op not in ("&&", "||") and has_call(expression.right):
                    return True
                return visit(expression.left) or visit(expression.right)
            if isinstance(expression, syntax.Unary):
                return visit(expression.operand)
            if isinstance(expression, syntax.Parens):
                return visit(expression.inner)
            if isinstance(expression, syntax.Call):
                return any(visit(argument) for argument in expression.arguments)
            return False

        def walk(statement: syntax.Stmt) -> bool:
            if isinstance(statement, syntax.Block):
                return any(walk(item) for item in statement.statements)
            if isinstance(statement, (syntax.Let, syntax.Assign)):
                return visit(statement.value)
            if isinstance(statement, syntax.If):
                return (visit(statement.condition) or walk(statement.then_block)
                        or walk(statement.else_block))
            if isinstance(statement, syntax.While):
                return visit(statement.condition) or walk(statement.body)
            if isinstance(statement, syntax.Return):
                return visit(statement.value)
            if isinstance(statement, syntax.ExprStmt):
                return visit(statement.expression)
            return False

        return walk(body)

    def _emit_call_site(self, target: str) -> None:
        """A call, padded so rsp is 16-byte aligned as the ABI requires."""
        padding = 8 if self.depth % 16 else 0
        if padding:
            self.assembler.sub_rsp(padding)
        self.assembler.call(target)
        if padding:
            self.assembler.add_rsp(padding)


def generate(program: syntax.Program) -> Artifact:
    """Lower a checked program to a linked artifact."""
    return CodeGenerator().generate(program)


__all__ = ["CodeGenerator", "HOME_REGISTERS", "align16", "function_label", "generate",
           "slot_offset"]
