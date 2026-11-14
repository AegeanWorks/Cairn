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
from cairn.optimize import INT64_MIN, has_call
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
    hold_units: int = 0

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
            pad_units = 0 if (len(self.homes) + hold_units) % 2 else 1
            merging = hold_units == 0 and self._body_may_hold(function.body)
            pad = hold_units == 0 and (function.slots % 2 == 0 or merging)
            filler = merging and function.slots % 2 == 1
            self.current = function
            for home in self.homes:
                asm.push(home)
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
            self.delta_slot = function.parameters[0].slot if hold_units else -1
            self.delta_value = 0
            self.hold_units = hold_units
            self._clear_frame(function)
            for index, parameter in enumerate(function.parameters[:MAX_PARAMETERS]):
                self._store_slot(parameter.slot, ARGUMENT_REGISTERS[index])
            self.depth = 0
            self.temporaries = 0
            self._emit_block(function.body)
            asm.mov_imm64("rax", 0)
            asm.label(self.epilogue)
            if hold_units:
                asm.add_rsp(8 * (hold_units + pad_units))
            elif pad:
                asm.add_rsp(8)  # skip the pad slot before the pops read the save area
            if filler:
                asm.pop(HOME_REGISTERS[-1])
            for home in reversed(self.homes):
                asm.pop(home)
            asm.ret()
            self.pad_slots = ()
            self.hold_units = 0
            self.delta_slot = -1
            self.delta_value = 0
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
        self._emit_block(function.body)
        asm.mov_imm64("rax", 0)
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
        home = self._home(slot)
        if home is None:
            self.assembler.mov_store_rbp(slot_offset(slot), source)
        elif home != source:
            self.assembler.mov_reg_reg(home, source)

    def _emit_value(self, slot: int, expression: Optional[syntax.Expr]) -> None:
        """Assign an expression to a slot, folding `slot = slot op value` in place."""
        home = self._home(slot)
        if home is not None and self._fold_update(home, slot, expression):
            return
        self._emit_expression(expression)
        self._store_slot(slot)

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
        elif isinstance(expression, syntax.IntLiteral):
            self.assembler.mov_imm64(destination, expression.value)
        elif isinstance(expression, syntax.Name):
            self._load_slot(expression.slot, destination)
        elif isinstance(expression, syntax.Parens):
            self._emit_into(expression.inner, destination)
        else:
            self._emit_expression(expression)
            self.assembler.mov_reg_reg(destination, "rax")

    def _emit_block(self, block: Optional[syntax.Block]) -> None:
        """Emit every statement of a block in order."""
        if block is None:
            return
        for statement in block.statements:
            self._emit_statement(statement)

    def _emit_statement(self, statement: syntax.Stmt) -> None:
        """Lower one statement."""
        asm = self.assembler
        if isinstance(statement, syntax.Let):
            self._emit_value(statement.slot, statement.value)
        elif isinstance(statement, syntax.Assign):
            self._emit_value(statement.slot, statement.value)
        elif isinstance(statement, syntax.If):
            self._emit_if(statement)
        elif isinstance(statement, syntax.While):
            self._emit_while(statement)
        elif isinstance(statement, syntax.Return):
            self._emit_expression(statement.value)
            asm.jmp(self.epilogue)
        elif isinstance(statement, syntax.Break):
            if self.break_targets:
                asm.jmp(self.break_targets[-1])
        elif isinstance(statement, syntax.Continue):
            if self.continue_targets:
                asm.jmp(self.continue_targets[-1])
        elif isinstance(statement, syntax.Block):
            self._emit_block(statement)
        elif isinstance(statement, syntax.ExprStmt):
            self._emit_expression(statement.expression)

    def _emit_if(self, statement: syntax.If) -> None:
        """`if` as a conditional branch and two labelled bodies."""
        asm = self.assembler
        otherwise = self._fresh("if.else")
        end = self._fresh("if.end")
        self._emit_condition(statement.condition, otherwise, when_true=False)
        self._emit_block(statement.then_block)
        if statement.else_block is not None:
            asm.jmp(end)
            asm.label(otherwise)
            self._emit_block(statement.else_block)
            asm.label(end)
        else:
            asm.label(otherwise)

    def _emit_while(self, statement: syntax.While) -> None:
        """`while` with its own break and continue targets."""
        asm = self.assembler
        start = self._fresh("while.start")
        end = self._fresh("while.end")
        asm.label(start)
        self._emit_condition(statement.condition, end, when_true=False)
        self.break_targets.append(end)
        self.continue_targets.append(start)
        self._emit_block(statement.body)
        self.break_targets.pop()
        self.continue_targets.pop()
        asm.jmp(start)
        asm.label(end)

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
            if self.inline_depth < MAX_INLINE_DEPTH:
                delta = self._delta_argument(expression)
                if delta is not None:
                    self._emit_delta_self_call(expression, delta)
                    return
            # A delta-eligible function's odd-shaped self-calls stay real calls: the
            # shadow-copy machinery would retarget the parameter's home register and
            # break the accumulated-delta reading every pure-text copy relies on.
        elif self._is_inline_candidate(expression):
            self._emit_inlined_self_call(expression)
            return
        if len(expression.arguments) == 1:
            if self._emit_home_offset(ARGUMENT_REGISTERS[0], expression.arguments[0]):
                self._emit_call_site(function_label(expression.name))
                return
            self._emit_expression(expression.arguments[0])
            asm.mov_reg_reg(ARGUMENT_REGISTERS[0], "rax")
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
        self._emit_block(self.current.body)
        asm.label(self.epilogue)
        self.epilogue = saved_epilogue
        self.delta_value = saved_value
        self.inline_depth = saved_depth

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
        self._emit_block(self.current.body)
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
