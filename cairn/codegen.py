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

from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from cairn import syntax
from cairn.image import ENTRY_LABEL, Artifact, ReadOnlyData, iat_label
from cairn.machine import ARGUMENT_REGISTERS, Assembler
from cairn.optimize import INT64_MIN, has_call, _contains_break_or_continue
from cairn.runtime import (DIV_ZERO_LABEL, PRINT_CHAR_LABEL, PRINT_HEX_LABEL, PRINT_LABEL,
                           emit_runtime)

COMPARISON_CONDITIONS = {
    "==": "e", "!=": "ne", "<": "l", "<=": "le", ">": "g", ">=": "ge",
}

# The condition that jumps when the compared relation is false.
INVERTED_CONDITIONS = {"e": "ne", "ne": "e", "l": "ge", "ge": "l", "le": "g", "g": "le"}

ARITHMETIC_OPCODES = {
    "+": 0x01, "&": 0x21, "|": 0x09, "^": 0x31,
}

# Fall-through binary ops whose register-register lowering can read the left operand's
# home directly instead of a parked rcx copy: the commutative arithmetic group, plus
# `*`, whose two-operand form `imul rax, r/m64` takes any second register. `x op x`
# additionally loads its slot once; `sub` is excluded because it is not commutative.
IN_PLACE_BINARY_OPS = ("+", "&", "|", "^", "*")

# Group-1 immediate extensions for an arithmetic right operand that is a literal.
IMMEDIATE_BINARY_EXTENSIONS = {"+": 0, "-": 5, "&": 4, "|": 1, "^": 6}
# Shift extensions for a literal count: `shl` and `sar`, matching Cairn's `>>` semantics.
IMMEDIATE_SHIFT_EXTENSIONS = {"<<": 4, ">>": 7}

# Binary operators that can update a destination in place: `slot = slot op value`.
UPDATE_OPCODES = {"+": 0x01, "-": 0x29, "&": 0x21, "|": 0x09, "^": 0x31}
COMMUTATIVE_UPDATES = frozenset({"+", "*", "&", "|", "^"})
IMMEDIATE_UPDATES = frozenset({"+", "-"})
# `name * m` for these factors is one scaled `lea`: m = 2**log2 + 1, and the lea's
# wrapping at 64 bits equals the multiply's, sign included.
LEA_PRODUCT_FACTORS = {3: 1, 5: 2, 9: 3}
IMMEDIATE_MIN = -0x80000000
IMMEDIATE_MAX = 0x7FFFFFFF
MAX_INLINE_DEPTH = 7
# Spare home registers one loop may claim for its invariant big constants, so the
# remaining pool still covers the next outer loop's constants.
HOIST_HOMES_PER_LOOP = 3
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
ZERO_EXTEND_MASKS = {255: 8, 65535: 16}



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


def magic_division_parameters(divisor: int) -> Optional[tuple]:
    """A `(multiplier, shift)` so the magic-multiply chain divides by `divisor` exactly.

    For a positive literal `divisor` that is not a power of two, the returned signed
    multiplier and shift make the gcc-style chain -- signed mulhigh of the folded
    (negative) multiplier, the dividend added back, `sar` by `shift`, and the sign
    word subtracted -- compute truncated division for every int64 dividend.

    Exactness argument. The chain computes `g(n) = floor(n*m / 2**(64+s)) - (n < 0)`
    where `m = ceil(2**(64+s) / d)`. Writing `e = m*d - 2**(64+s)` (so `0 < e < d`)
    turns both floors into the two integer inequalities below, and each is linear in
    `n` within one residue class; the maximum over each residue class sits at the
    largest valid `n`, so only the residue-wrap point (`n = 2**63 - 1`) and the
    `d - 1` residue need checking, plus `INT64_MIN` for the negative side.
    """
    if divisor < 2 or (divisor & (divisor - 1)) == 0:
        return None
    top = (1 << 63) - 1
    for shift in range(1, 64):
        exponents = 64 + shift
        multiplier = -(-2**exponents // divisor)
        if multiplier >= 1 << 64:
            continue
        error = multiplier * divisor - 2**exponents   # 1 .. divisor - 1
        n_wrap = top                                  # residue `(top - j) % divisor == 0`
        n_last = top - ((top - (divisor - 1)) % divisor)  # residue `divisor - 1`
        positive = all(
            n * error < 2**exponents * (divisor - (n % divisor))
            for n in (n_wrap, n_last))
        if not positive:
            continue
        negative = all(
            t * error <= 2**exponents * (divisor - (t % divisor))
            for t in (n_wrap, n_last))
        if not negative:
            continue
        # INT64_MIN: `ceil(2**63 * m / 2**(64+s))` must be `floor(2**63 / d) + 1`.
        if -(-2**63 * multiplier // 2**exponents) != (2**63 // divisor) + 1:
            continue
        return multiplier - 2**64, shift
    return None


@dataclass
class CodeGenerator:
    """One lowering pass over a checked program."""

    assembler: Assembler = field(default_factory=Assembler)
    rodata: ReadOnlyData = field(default_factory=ReadOnlyData)
    program: Optional[syntax.Program] = None
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
    # Loop-invariant hoisting: hoist_plans maps one While statement's identity to its
    # (value, home) pairs, hoist_imm is the active value-to-home map while a hoisted
    # loop's body lowers, and hoist_homes lists the registers the prologue must save.
    hoist_plans: dict = field(default_factory=dict)
    hoist_imm: dict = field(default_factory=dict)
    hoist_homes: Tuple[str, ...] = ()
    # A foreign-inline copy whose bare-name arguments skip their binds maps the copy's
    # parameter slots to the caller's home registers through copy_alias. copy_return,
    # when set, is the register the copy's single return stores into, replacing the
    # result's rax round trip.
    copy_alias: dict = field(default_factory=dict)
    copy_return: Optional[str] = None

    def generate(self, program: syntax.Program) -> Artifact:
        """Emit the entry stub, every function, then the runtime helpers."""
        self.program = program
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
        if (function.slots <= len(HOME_REGISTERS)
                and len(function.parameters) <= len(ARGUMENT_REGISTERS)):
            # Every slot has a home, so no frame (and no rbp) is needed: the homes
            # save and restore with push/pop, which also drops the store-forwarding
            # latency a frame store re-load pays on the hot call path. Functions with
            # stack parameters need the frame: only an rbp chain addresses the
            # caller's outgoing area at a fixed offset.
            hold_units = self._delta_hold_units(function)
            pair_mode = hold_units == 0 and self._pair_inline_shape(function)
            self.hoist_plans.update(self._plan_constant_hoists(function))
            self.hoist_homes = tuple(home for pairs in
                                     (self.hoist_plans.get(id(loop), [])
                                      for loop in self._loops_within(function.body))
                                     for home in (home for _, home in pairs))
            # The hoisted homes ride the save area like slot homes: the parity count
            # that keeps every call 16-aligned must see them too.
            saved_units = len(self.homes) + len(self.hoist_homes)
            pad_units = 0 if (saved_units + hold_units) % 2 else 1
            merging = hold_units == 0 and self._body_may_hold(function.body)
            pad = hold_units == 0 and (saved_units % 2 == 0 or merging)
            filler = merging and saved_units % 2 == 1
            self.current = function
            for home in self.homes:
                asm.push(home)
            for register in self.hoist_homes:
                asm.push(register)
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
            for index, parameter in enumerate(function.parameters[:len(ARGUMENT_REGISTERS)]):
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
            for register in reversed(self.hoist_homes):
                asm.pop(register)
            self.hoist_homes = ()
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
        self.hoist_homes = ()      # every home is a slot home here; nothing to hoist into
        if frame:
            asm.sub_rsp(frame)
        self.pad_slots = ()
        self._save_homes()
        self._clear_frame(function)
        for index, parameter in enumerate(function.parameters[:len(ARGUMENT_REGISTERS)]):
            self._store_slot(parameter.slot, ARGUMENT_REGISTERS[index])
        for index in range(len(ARGUMENT_REGISTERS), len(function.parameters)):
            # A fifth-or-later parameter sits in the caller's outgoing area, directly
            # above the saved return address; pull it into its own slot before the
            # body (and rax) is touched.
            asm.mov_load_rbp("rax", 16 + 8 * (index - len(ARGUMENT_REGISTERS)))
            self._store_slot(function.parameters[index].slot, "rax")
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
        """The register that holds `slot`, or None when the slot stays in the frame.

        An alias slot of an inlined copy's substituted argument names the caller's own
        home register, so every home-aware lowering (scaled `lea`s, in-place folds,
        immediate compares) treats it exactly like a normal homed slot.
        """
        if self.copy_alias and slot in self.copy_alias:
            return self.copy_alias[slot]
        if 0 <= slot < len(self.homes):
            return self.homes[slot]
        return None

    def _emit_loop_head(self, label: str) -> None:
        """Pad onto a 16-byte boundary, then bind the loop's backedge target.

        The rotated loop's head is the branch destination every iteration; when it
        sits in the middle of a fetch line, the fetch window per cycle undersupplies
        the retirement width, which is several percent on a register-bound loop.
        The pad is nops before the label, so every fixup targeting it stays valid.
        """
        pad = (16 - self.assembler.here() % 16) % 16
        if pad:
            self.assembler.code.extend(b"\x90" * pad)
        self.assembler.label(label)

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

    def _emit_zero_extend_mask(self, expression: syntax.Binary,
                               destination: str) -> bool:
        """Lower `name & 255` / `name & 65535` as one movzx of the name's home, or refuse.

        `movzx r32, r/m8|16` reads exactly the masked low bits and zero-extends them,
        so the generic move-plus-and pair computes the same value in one instruction,
        which is what gcc emits for call_chain's `i & 65535`. A name in the frame keeps
        the generic mask (movzx reads only registers), the destination must not be the
        source's own home (movzx would destroy the value it reads), and a delta copy's
        shifted reads refuse because the home no longer holds the slot's own value.
        """
        if self.delta_value:
            return False
        left = self._stripped(expression.left)
        right = self._stripped(expression.right)
        mask = None
        named = None
        if isinstance(right, syntax.IntLiteral) and right.value in ZERO_EXTEND_MASKS:
            mask, named = right.value, left
        elif isinstance(left, syntax.IntLiteral) and left.value in ZERO_EXTEND_MASKS:
            mask, named = left.value, right
        if not isinstance(named, syntax.Name) or mask is None:
            return False
        if named.slot == self.delta_slot or named.slot == self.value_slot:
            return False
        home = self._home(named.slot)
        if home is None or home == destination:
            return False
        if mask == 255:
            self.assembler.movzx_from8(destination, home)
        else:
            self.assembler.movzx_from16(destination, home)
        return True

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
        if (home is not None and isinstance(expression, syntax.Call)
                and self._foreign_inline_target(expression) is not None):
            # A copyable call stores its result directly into the slot's home register;
            # nothing returns through rax, and the caller skips its own rax store.
            written = self._emit_foreign_call(expression, copy_home=home)
            if written is None:
                self._store_slot(slot)
            return
        if home is not None and self._emit_binary_into(expression, home):
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
        if (isinstance(other, syntax.Name) and self._home(other.slot) is not None
                and not (other.slot == self.delta_slot and self.delta_value)
                and operator not in ("/", "%")):
            # The other side lives in a register already: fold against that register
            # instead of paying a move through rax first.
            if operator == "*":
                self.assembler.imul(home, self._home(other.slot))
            else:
                self.assembler.binary(UPDATE_OPCODES[operator], home, self._home(other.slot))
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
            hoisted = self.hoist_imm.get(expression.value)
            if hoisted is None:
                self.assembler.mov_imm64(destination, expression.value)
            elif hoisted != destination:
                self.assembler.mov_reg_reg(destination, hoisted)
        elif isinstance(expression, syntax.Name):
            self._load_slot(expression.slot, destination)
        elif self._emit_binary_into(expression, destination):
            pass
        else:
            self._emit_expression(expression)
            self.assembler.mov_reg_reg(destination, "rax")

    def _emit_binary_into(self, expression: Optional[syntax.Expr],
                          destination: str) -> bool:
        """Lower a call-free, trap-free binary expression with its result in `destination`.

        The general lowering computes in rax and moves; when the destination is known —
        an argument register or a homed slot — the left operand computes in place and
        the right stays in rax, so the closing move disappears. Refusals keep the
        generic path: anything with calls, division, comparisons, `&&`/`||`, unary
        shapes, or a right side that leaves rax (a multiply, a variable shift, a setcc)
        cannot promise which registers survive.
        """
        if isinstance(expression, syntax.Parens):
            return self._emit_binary_into(expression.inner, destination)
        if not isinstance(expression, syntax.Binary):
            return False
        if self._contains_division(expression) or has_call(expression):
            return False
        if expression.op == "&" and self._emit_logical_shift(expression, destination):
            return True
        if expression.op == "&" and self._emit_zero_extend_mask(expression, destination):
            return True
        operator = expression.op
        left, right = expression.left, expression.right
        if operator in ("&&", "||", "/") or operator in COMPARISON_CONDITIONS:
            return False
        if operator == "%" or operator == "~" or operator == "!":
            return False
        stripped_left = self._stripped(left)
        parked_noop = (isinstance(stripped_left, syntax.Name)
                       and self._home(stripped_left.slot) == destination)
        if operator in UPDATE_OPCODES:
            if not self._computes_in_rax_only(right, destination, parked_noop):
                return False
            if not self._emit_left_into(left, destination):
                return False
            operand = self._stripped(right)
            if (isinstance(operand, syntax.IntLiteral)
                    and IMMEDIATE_MIN <= operand.value <= IMMEDIATE_MAX
                    and operator in IMMEDIATE_BINARY_EXTENSIONS):
                self.assembler.arith_imm(
                    IMMEDIATE_BINARY_EXTENSIONS[operator], destination, operand.value)
                return True
            operand_home = (self._home(operand.slot)
                            if isinstance(operand, syntax.Name) else None)
            if operand_home is not None and operand_home != destination:
                self.assembler.binary(UPDATE_OPCODES[operator],
                                      destination, operand_home)
                return True
            self._emit_expression(right)
            self.assembler.binary(UPDATE_OPCODES[operator], destination, "rax")
            return True
        if operator == "*":
            if self._emit_scaled_product(expression, destination):
                return True
            if not self._computes_in_rax_only(right, destination, parked_noop):
                return False
            if not self._emit_left_into(left, destination):
                return False
            operand = self._stripped(right)
            operand_home = (self._home(operand.slot)
                            if isinstance(operand, syntax.Name) else None)
            if operand_home is not None:
                self.assembler.imul(destination, operand_home)
                return True
            self._emit_expression(right)
            self.assembler.imul(destination, "rax")
            return True
        if operator in IMMEDIATE_SHIFT_EXTENSIONS:
            operand = self._stripped(right)
            if (not isinstance(operand, syntax.IntLiteral)
                    or not 0 <= operand.value <= 63):
                return False
            if not self._emit_left_into(left, destination):
                return False
            self.assembler.shift_imm(IMMEDIATE_SHIFT_EXTENSIONS[operator],
                                     destination, operand.value)
            return True
        return False

    def _emit_left_into(self, expression: Optional[syntax.Expr], destination: str) -> bool:
        """Compute the left operand with its value in `destination`, or refuse.

        A homed name whose register IS the destination is already there; other leaves
        load directly; nested accepted binaries recurse. A name whose register or frame
        slot must survive later register use is a caller's concern — the destination is
        owned by this expression alone.
        """
        expression = self._stripped(expression)
        if isinstance(expression, syntax.IntLiteral):
            hoisted = self.hoist_imm.get(expression.value)
            if hoisted is None:
                self.assembler.mov_imm64(destination, expression.value)
            elif hoisted != destination:
                self.assembler.mov_reg_reg(destination, hoisted)
            return True
        if isinstance(expression, syntax.Name):
            home = self._home(expression.slot)
            if home is None:
                if destination == "rax":
                    self._load_slot(expression.slot, "rax")
                else:
                    self.assembler.mov_load_rbp(destination, slot_offset(expression.slot))
                return True
            if home == destination:
                return True
            if destination == "rax":
                self._emit_expression(expression)
                return True
            self.assembler.mov_reg_reg(destination, home)
            return True
        if self._emit_binary_into(expression, destination):
            return True
        if destination == "rax":
            self._emit_expression(expression)
            return True
        return False

    def _computes_in_rax_only(self, expression: Optional[syntax.Expr],
                              destination: str,
                              parked_noop: bool = False) -> bool:
        """Whether a right operand's lowering leaves `destination` untouched.

        The caller has already lowered the left operand into `destination`, so the
        right side's evaluation must not write that register — and must not read a
        value that the left side has replaced. Leaves and their parenthesized forms
        load rax alone; a literal-count shift of such an operand does (`mov rax, home`,
        one shift); a name-with-literal offset lowers as one lea into rax; the
        logical-shift peephole lowers the whole `&` tree into rax. Operators whose
        lowerings park operands in rcx (multiplies of arbitrary operands, divisions,
        comparisons, short circuits, masked sparing of a non-immediate literal) refuse,
        as does any homed name that reads the merged left — unless the caller dropped
        that name in place (`parked_noop`), when the register still holds its value.
        """
        expression = self._stripped(expression)
        if expression is None or isinstance(expression, syntax.IntLiteral):
            return True
        if isinstance(expression, syntax.Name):
            home = self._home(expression.slot)
            return home is None or home != destination or parked_noop
        if isinstance(expression, syntax.Binary):
            if (expression.op in ("&&", "||", "/", "%", "*")
                    or expression.op in COMPARISON_CONDITIONS):
                return False
            right_literal = self._stripped(expression.right)
            if not isinstance(right_literal, syntax.IntLiteral):
                # Nested arithmetic lowers through rcx; only `name op literal` and a
                # literal-count shift of the same kind stay inside rax.
                return False
            if expression.op == "&":
                shape = self._logical_shift_shape(expression)
                if shape is not None:
                    return self._computes_in_rax_only(shape[0], destination,
                                                      parked_noop)
            if (expression.op in IMMEDIATE_BINARY_EXTENSIONS
                    and IMMEDIATE_MIN <= right_literal.value <= IMMEDIATE_MAX):
                return self._computes_in_rax_only(expression.left, destination,
                                                  parked_noop)
            if (expression.op in IMMEDIATE_SHIFT_EXTENSIONS
                    and 0 <= right_literal.value <= 63):
                return self._computes_in_rax_only(expression.left, destination,
                                                  parked_noop)
            # A literal outside the immediate range lowers generically, parking its
            # left operand in rcx — the original merged-park miscompile shape.
            return False
        return False

    def _stripped(self, expression: Optional[syntax.Expr]) -> Optional[syntax.Expr]:
        """The expression without its wrapping parentheses."""
        while isinstance(expression, syntax.Parens):
            expression = expression.inner
        return expression

    def _emit_scaled_product(self, expression: syntax.Binary,
                             destination: str = "rax") -> bool:
        """Lower `name * 3|5|9` as one scaled `lea` into `destination`, or refuse.

        The factor is one more than a power of two, so the lea computes exactly the
        multiply's wrapped value for every input including INT64_MIN; the sign is the
        multiplication's sign. The general imul keeps every other factor.
        """
        left, right = expression.left, expression.right
        stripped_left = self._stripped(left)
        stripped_right = self._stripped(right)
        factor = None
        named = None
        if isinstance(stripped_right, syntax.IntLiteral) \
                and stripped_right.value in LEA_PRODUCT_FACTORS:
            factor, named = stripped_right.value, stripped_left
        elif isinstance(stripped_left, syntax.IntLiteral) \
                and stripped_left.value in LEA_PRODUCT_FACTORS:
            factor, named = stripped_left.value, stripped_right
        if factor is None:
            return False
        if not isinstance(named, syntax.Name) or self.delta_value:
            return False
        home = self._home(named.slot)
        if home is None:
            return False
        self.assembler.lea_scale(destination, home, home,
                                 LEA_PRODUCT_FACTORS[factor], 0)
        return True

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
            if self.copy_return is not None and tail:
                self._emit_into(statement.value, self.copy_return)
            else:
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
        elif isinstance(statement, syntax.For):
            raise RuntimeError(
                "internal: a for statement reached code generation unlowered; "
                "lower_for_loops must run before generate")

    def _emit_if(self, statement: syntax.If, tail: bool = False) -> None:
        """`if` as a conditional branch and two labelled bodies.

        With an else block, the else body sits inline and the condition branches forward
        over it to the then body: the unconditional `jmp` is then paid only by the else
        path, and the frequent path falls straight through to the code after the `if`.
        A two-way select of the same slot folds into branchless arithmetic instead.
        """
        if self._emit_select_if(statement):
            return
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

    def _emit_select_if(self, statement: syntax.If) -> bool:
        """Lower `if t > c { t = t - c; } else { t = t + c; }` as sub, lea, cmov — or refuse.

        The subtract sets exactly the flags the comparison would test (`sub` and `cmp`
        of the same operands agree on every input, overflow included), and `t + c` is
        `(t - c) + 2*c`: one lea. The select then costs two instructions and no branch,
        where the branchy form pays a data-dependent misprediction every iteration. The
        exact shape required: one assignment to the same slot in each branch, minus and
        plus of that slot and one other homed name, and a comparison of exactly those
        two operands; the comparison's inverted flag selects the cmov.
        """
        asm = self.assembler
        if statement.else_block is None:
            return False
        then_stmts = statement.then_block.statements
        else_stmts = statement.else_block.statements
        if len(then_stmts) != 1 or len(else_stmts) != 1:
            return False
        then_assign, else_assign = then_stmts[0], else_stmts[0]
        if (not isinstance(then_assign, syntax.Assign)
                or not isinstance(else_assign, syntax.Assign)):
            return False
        slot = then_assign.slot
        if slot != else_assign.slot or self.delta_value:
            return False
        home = self._home(slot)
        condition = statement.condition
        if home is None or not isinstance(condition, syntax.Binary):
            return False
        condition_left, condition_right = condition.left, condition.right
        while isinstance(condition_left, syntax.Parens):
            condition_left = condition_left.inner
        while isinstance(condition_right, syntax.Parens):
            condition_right = condition_right.inner
        if (condition.op not in COMPARISON_CONDITIONS
                or not isinstance(condition_left, syntax.Name)
                or condition_left.slot != slot
                or not isinstance(condition_right, syntax.Name)
                or condition_right.slot == slot):
            return False
        other_home = self._home(condition_right.slot)
        if other_home is None:
            return False

        def select_side(assignment: syntax.Assign, operator: str) -> bool:
            value = assignment.value
            while isinstance(value, syntax.Parens):
                value = value.inner
            return (isinstance(value, syntax.Binary) and value.op == operator
                    and isinstance(value.left, syntax.Name) and value.left.slot == slot
                    and isinstance(value.right, syntax.Name)
                    and value.right.slot == condition_right.slot)

        if select_side(then_assign, "-") and select_side(else_assign, "+"):
            move = INVERTED_CONDITIONS[COMPARISON_CONDITIONS[condition.op]]
        elif select_side(then_assign, "+") and select_side(else_assign, "-"):
            move = COMPARISON_CONDITIONS[condition.op]
        else:
            return False
        self.nonnegative_slots.discard(slot)
        asm.binary(UPDATE_OPCODES["-"], home, other_home)
        asm.lea_scale("rax", home, other_home, 1, 0)
        asm.cmov(move, home, "rax")
        return True

    def _plan_constant_hoists(self, function: syntax.Function) -> dict:
        """Reserve spare homes for each loop's invariant big literals, or return none.

        Only the leaf frame shape (every slot homed, none left over) leaves registers
        that no push saves; each While statement takes up to `HOIST_HOMES_PER_LOOP` of
        them for its most-used big literals, outer loops first so nested loops never
        share a register. Frame-shaped functions have no spare homes and no plan.
        """
        spare = list(HOME_REGISTERS[len(self.homes):])
        if not spare:
            return {}
        plan: dict = {}
        cursor = 0
        for loop in self._loops_within(function.body):
            counts = self._big_literal_counts(loop)
            if not counts or cursor >= len(spare):
                continue
            homes = spare[cursor:cursor + min(HOIST_HOMES_PER_LOOP, len(spare) - cursor)]
            cursor += len(homes)
            plan[id(loop)] = [(value, home) for (value, _), home
                              in zip(counts.most_common(), homes)]
            if cursor >= len(spare):
                break
        return plan

    def _loops_within(self, block: Optional[syntax.Block]):
        """Every While statement at or below `block`, outer loops before nested ones."""
        if block is None:
            return
        for statement in block.statements:
            if isinstance(statement, syntax.While):
                yield statement
                yield from self._loops_within(statement.body)
            elif isinstance(statement, syntax.If):
                yield from self._loops_within(statement.then_block)
                yield from self._loops_within(statement.else_block)

    def _big_literal_counts(self, loop: syntax.While) -> Counter:
        """Distinct literals outside the immediate range (the movabs-only forms), counted."""
        counter: Counter = Counter()
        self._count_in(loop.condition, counter)
        self._count_in_block(loop.body, counter)
        return counter

    def _count_in_block(self, block: Optional[syntax.Block], counter: Counter) -> None:
        if block is None:
            return
        for statement in block.statements:
            if isinstance(statement, (syntax.Let, syntax.Assign)):
                self._count_in(statement.value, counter)
            elif isinstance(statement, syntax.If):
                self._count_in(statement.condition, counter)
                self._count_in_block(statement.then_block, counter)
                self._count_in_block(statement.else_block, counter)
            elif isinstance(statement, syntax.While):
                self._count_in(statement.condition, counter)
                self._count_in_block(statement.body, counter)
            elif isinstance(statement, (syntax.Return, syntax.ExprStmt)):
                self._count_in(statement.value if isinstance(statement, syntax.Return)
                               else statement.expression, counter)

    def _count_in(self, expression: Optional[syntax.Expr], counter: Counter) -> None:
        if expression is None:
            return
        if isinstance(expression, syntax.IntLiteral):
            if not IMMEDIATE_MIN <= expression.value <= IMMEDIATE_MAX:
                counter[expression.value] += 1
        elif isinstance(expression, syntax.Binary):
            if expression.op == "&":
                shape = self._logical_shift_shape(expression)
                if shape is not None:
                    # The peephole emits nothing for the mask or the count, so only
                    # the shift operand's own literals can reach a movabs.
                    self._count_in(shape[0], counter)
                    return
            self._count_in(expression.left, counter)
            self._count_in(expression.right, counter)
        elif isinstance(expression, syntax.Unary):
            self._count_in(expression.operand, counter)
        elif isinstance(expression, syntax.Parens):
            self._count_in(expression.inner, counter)
        elif isinstance(expression, syntax.Call):
            for argument in expression.arguments:
                self._count_in(argument, counter)

    def _emit_while(self, statement: syntax.While) -> None:
        """Load the loop's plan-hoisted constants into their spare homes, then lower."""
        plan = self.hoist_plans.get(id(statement))
        if plan and not self._lowers_as_real_body():
            plan = None      # a copy's frame never saved the spare homes
        if not plan:
            self._emit_while_body(statement)
            return
        saved_hoist = self.hoist_imm
        for value, register in plan:
            self.assembler.mov_imm64(register, value)
        self.hoist_imm = {**saved_hoist, **dict(plan)}
        try:
            self._emit_while_body(statement)
        finally:
            self.hoist_imm = saved_hoist

    def _lowers_as_real_body(self) -> bool:
        """Whether emission runs in the real body of a fully-homed, pair-free function.

        Copies re-lower this statement inside another frame whose prologue never saved
        the planned spare homes, so only the real body may read them.
        """
        return (self.inline_depth == 0 and self.delta_value == 0
                and self.value_slot == -1 and self.hold_units == 0)

    def _emit_while_body(self, statement: syntax.While) -> None:
        """`while` with its own break and continue targets."""
        asm = self.assembler
        start = self._fresh("while.start")
        end = self._fresh("while.end")
        if statement.countdown:
            # A countdown loop guards once against zero or negative counts, then latches at
            # the bottom with one decrement and one taken-backward conditional branch. The
            # optimiser already deleted the in-body increment, so the latch must be emitted.
            self._emit_condition(statement.condition, end, when_true=False)
            self._emit_loop_head(start)
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
            self._emit_loop_head(start)
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
        self._emit_loop_head(start)
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
        self._emit_loop_head(top)
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
        if isinstance(expression, syntax.Binary) and expression.op in ("&&", "||"):
            self._emit_logical_branch(expression, target, when_true)
            return
        self._emit_expression(expression)
        self.assembler.test("rax")
        self.assembler.jcc("nz" if when_true else "z", target)

    def _emit_logical_branch(self, expression: syntax.Binary, target: str,
                             when_true: bool) -> None:
        """Branch on `a && b` / `a || b` from each side's flags in turn.

        The short-circuit side decides the chain: its false (for `&&`) or true (for `||`)
        outcome jumps past the other side. Because every side branches directly, no
        comparison inside a condition ever materialises as 0 or 1.
        """
        left, right = expression.left, expression.right
        if expression.op == "&&":
            if when_true:
                skipped = self._fresh("and.short")
                self._emit_condition(left, skipped, False)
                self._emit_condition(right, target, True)
                self.assembler.label(skipped)
            else:
                # Jumping on the first false side already skips the rest of the chain.
                self._emit_condition(left, target, False)
                self._emit_condition(right, target, False)
        else:
            skipped = self._fresh("or.short")
            self._emit_condition(left, skipped, True)
            self._emit_condition(right, target, when_true)
            self.assembler.label(skipped)

    @staticmethod
    def _bit_test_operand(expression: syntax.Binary) -> Optional[tuple]:
        """`(value, mask)` when the compare is `x % 2**b` or `x & m`, else None.

        For every sign, `% 2**b == 0` holds exactly when the low b bits are zero, and
        `(x & m) == 0` / `!= 0` reads the same bits directly, so the mask alone decides.
        """
        value_expr = expression.left
        while isinstance(value_expr, syntax.Parens):
            value_expr = value_expr.inner
        if not (isinstance(value_expr, syntax.Binary) and value_expr.op in ("%", "&")):
            return None
        operand_a = value_expr.left
        operand_b = value_expr.right
        while isinstance(operand_a, syntax.Parens):
            operand_a = operand_a.inner
        while isinstance(operand_b, syntax.Parens):
            operand_b = operand_b.inner
        if value_expr.op == "%":
            divisor = operand_b.value if isinstance(operand_b, syntax.IntLiteral) else -1
            power = divisor.bit_length() - 1 if divisor >= 1 else -1
            if power >= 1 and (1 << power) == divisor:
                return operand_a, (1 << power) - 1
            return None
        for literal, other in ((operand_a, operand_b), (operand_b, operand_a)):
            if isinstance(literal, syntax.IntLiteral) and literal.value > 0:
                while isinstance(other, syntax.Parens):
                    other = other.inner
                return other, literal.value
        return None

    def _logical_shift_shape(self, expression: syntax.Expr) -> Optional[tuple]:
        """The `(operand, shift)` of a `(a >> k) & (2**(64-k) - 1)` tree, or None.

        Cairn's `>>` is arithmetic and fills the vacated high bits with the sign, but a
        mask that clears every filled bit makes the fill unobservable: the logical shift
        computes the masked value alone. One instruction replaces the shift, the mask
        load and the `and`, which is what a hash mixer's unmasking step pays for.
        """
        left = self._stripped(expression.left)
        right = self._stripped(expression.right)
        if not (isinstance(left, syntax.Binary) and left.op == ">>"
                and isinstance(right, syntax.IntLiteral)):
            return None
        count = self._stripped(left.right)
        if not isinstance(count, syntax.IntLiteral):
            return None
        shift, mask = count.value, right.value
        if not 1 <= shift <= 63 or mask != (1 << (64 - shift)) - 1:
            return None
        return left.left, shift

    def _emit_logical_shift(self, expression: syntax.Binary, destination: str) -> bool:
        """Lower `(a >> k) & (2**(64-k) - 1)` as one logical `shr`, or refuse."""
        shape = self._logical_shift_shape(expression)
        if shape is None:
            return False
        operand, shift = shape
        stripped_operand = self._stripped(operand)
        if (destination == "rax" and not isinstance(stripped_operand, (syntax.Name,
                syntax.IntLiteral))):
            # The generic rax path may hold a pending left value in rcx; only a leaf
            # operand's load is guaranteed to touch rax alone.
            return False
        if not self._emit_left_into(operand, destination):
            return False
        self.assembler.shift_imm(5, destination, shift)   # shr: the logical form
        return True

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
                and expression.op in ("==", "!=")):
            bit_test = self._bit_test_operand(expression)
            if bit_test is not None:
                dividend, mask = bit_test
                # `x % 2**b == 0` holds exactly when the low b bits are zero, for every
                # sign, and `x & m` reads those bits directly, so the mask decides.
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
        if (isinstance(left, syntax.Name) and isinstance(right, syntax.Name)
                and self._home(left.slot) is not None and self._home(right.slot) is not None
                and not (left.slot == self.delta_slot and self.delta_value)
                and not (right.slot == self.delta_slot and self.delta_value)):
            # Two homed names compare in place: the round-trips through rax and rcx are
            # pure waste when both values already sit in a register.
            asm.cmp_reg_reg(self._home(left.slot), self._home(right.slot))
            asm.jcc(condition, target)
            return
        if (isinstance(right, syntax.IntLiteral)
                and IMMEDIATE_MIN <= right.value <= IMMEDIATE_MAX):
            self._emit_into(expression.left, "rax")
            # The flags already compare rax against zero when the last emitted
            # instruction was an add/or/and/sub/xor whose destination is rax.
            if right.value != 0 or self.assembler.zf_status != "rax":
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
            hoisted = self.hoist_imm.get(expression.value)
            if hoisted is None:
                asm.mov_imm64("rax", expression.value)
            else:
                asm.mov_reg_reg("rax", hoisted)
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
        if expression.op == "&" and self._emit_logical_shift(expression, "rax"):
            return
        if expression.op == "&" and self._emit_zero_extend_mask(expression, "rax"):
            return
        if expression.op == "+" and self._emit_scaled_sum(expression):
            return
        if expression.op == "*" and self._emit_scaled_product(expression):
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
        if expression.op in ("/", "%") and self._emit_magic_division(expression):
            return
        left = expression.left
        right = expression.right
        while isinstance(left, syntax.Parens):
            left = left.inner
        while isinstance(right, syntax.Parens):
            right = right.inner
        if (expression.op in IN_PLACE_BINARY_OPS
                and isinstance(left, syntax.Name)
                and not (left.slot == self.delta_slot and self.delta_value)):
            if (isinstance(right, syntax.Name) and right.slot == left.slot):
                # One load feeds both operands: `x * x` pays half the frame traffic.
                self._load_slot(left.slot, "rax")
                if expression.op == "*":
                    asm.imul("rax", "rax")
                else:
                    asm.binary(ARITHMETIC_OPCODES[expression.op], "rax", "rax")
                return
            left_home = self._home(left.slot)
            if left_home is not None and not has_call(expression.right):
                # The left operand already lives in a register: compute in place
                # instead of parking it in rcx and reading it back from there.
                self._emit_expression(expression.right)
                if expression.op == "*":
                    asm.imul("rax", left_home)
                else:
                    asm.binary(ARITHMETIC_OPCODES[expression.op], "rax", left_home)
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

    def _emit_magic_division(self, expression: syntax.Binary) -> bool:
        """Lower `x / d` or `x % d` for a positive literal `d` as a magic multiply.

        With `m = ceil(2**(64+s) / d)` the gcc-style chain -- signed mulhigh of the
        negative folded multiplier, the dividend added back, one arithmetic shift, and
        the sign word subtracted afterwards -- is exactly
        `floor(n*m / 2**(64+s)) - (n < 0)`, and the parameter search certifies that
        function against truncated division over all of int64. The literal is neither
        zero nor minus one, so the lowering cannot fault and the `DIV_ZERO` trap
        branches disappear along with the `idiv`.
        """
        right = expression.right
        while isinstance(right, syntax.Parens):
            right = right.inner
        if not isinstance(right, syntax.IntLiteral) or right.value < 2:
            return False
        parameters = magic_division_parameters(right.value)
        if parameters is None:
            return False
        multiplier, shift = parameters
        asm = self.assembler
        self._emit_expression(expression.left)
        asm.mov_reg_reg("rcx", "rax")       # the dividend, for the add-back and remainder
        asm.mov_imm64(SCRATCH, multiplier)
        asm.imul_high(SCRATCH)              # rdx:rax = rax * multiplier, signed 128-bit
        asm.lea_scale("rax", "rdx", "rcx", 0, 0)  # rax = mulhigh + n (the add-back)
        asm.shift_imm(7, "rax", shift)
        asm.mov_reg_reg("rdx", "rcx")
        asm.shift_imm(7, "rdx", 63)         # the sign word: 0 or -1
        asm.binary(0x29, "rax", "rdx")      # truncated quotient
        if expression.op == "/":
            return True
        asm.mov_reg_reg("rdx", "rax")
        asm.mov_imm64(SCRATCH, right.value)
        asm.imul("rdx", SCRATCH)            # quotient * divisor
        asm.mov_reg_reg("rax", "rcx")
        asm.binary(0x29, "rax", "rdx")      # n - (n / d) * d keeps the dividend's sign
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

    def _emit_call(self, expression: syntax.Call,
                  copy_home: Optional[str] = None) -> None:
        """Evaluate arguments left to right, place them in the ABI registers, then call.

        `print` keeps a stack round-trip on purpose: the runtime helper clobbers both `rax` and
        `rcx`, so the `pop` is what restores the value `print(x)` must return. The byte and hex
        printers share that shape, which is what makes them return their argument as a value.
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
        if expression.name == "print_char":
            self._emit_expression(expression.arguments[0])
            asm.push("rax")
            self.depth += 8
            self._emit_call_site(PRINT_CHAR_LABEL)
            asm.pop("rax")
            self.depth -= 8
            return
        if expression.name == "print_hex":
            self._emit_expression(expression.arguments[0])
            asm.push("rax")
            self.depth += 8
            self._emit_call_site(PRINT_HEX_LABEL)
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
        if len(expression.arguments) > len(ARGUMENT_REGISTERS):
            self._emit_stack_call(expression)
            return
        self._emit_foreign_call(expression, copy_home)

    def _emit_foreign_call(self, expression: syntax.Call,
                           copy_home: Optional[str] = None) -> Optional[str]:
        """Bind or substitute the arguments, then copy the callee or call it for real.

        The copy's return lands in `copy_home` when the caller asked for one and the
        copy can honor it: its body ends in one return, and the destination is not a
        register the copy borrows (a borrowed home is restored to the caller's own
        value on the way out, which would erase the return). The function returns the
        register that received the value, or None when rax kept it and a caller that
        asked for a home must still store.
        """
        asm = self.assembler
        target = self._foreign_inline_target(expression)
        if target is None:
            self._emit_foreign_arguments(expression, frozenset())
            self._emit_call_site(function_label(expression.name))
            return None
        rebinds, _, spill = self._plan_foreign_copy(target)
        if (copy_home is not None
                and (rebinds or copy_home in spill
                     or not self._ends_in_return(target.body))):
            copy_home = None
        skip = frozenset()
        alias: dict = {}
        if not rebinds:
            substitution = self._substitution_plan(expression, target)
            alias, skip = self._alias_substitution(substitution, spill)
        self._emit_foreign_arguments(expression, skip)
        self._emit_foreign_inline(target, alias, copy_home)
        return copy_home

    def _emit_foreign_arguments(self, expression: syntax.Call, skip: frozenset) -> None:
        """Bind every unsubstituted argument left to right into its ABI register."""
        asm = self.assembler
        arguments = expression.arguments
        if len(arguments) == 1:
            if 0 not in skip:
                self._emit_into(arguments[0], ARGUMENT_REGISTERS[0])
            return
        if self._emit_pure_arguments(arguments, skip):
            return
        for index, argument in enumerate(arguments):
            if index in skip:
                continue
            self._emit_expression(argument)
            asm.push("rax")
            self.depth += 8
        for index in reversed(range(len(arguments))):
            if index in skip:
                continue
            asm.pop(ARGUMENT_REGISTERS[index])
            self.depth -= 8

    def _plan_foreign_copy(self, callee: syntax.Function):
        """The copy's guest, spill and rebind decisions, without emitting anything."""
        parameters = len(callee.parameters)
        if self._rebinds_parameters(callee.body, parameters):
            free = [home for home in HOME_REGISTERS if home not in self.homes]
            guest = tuple((free + list(self.homes))[:callee.slots])
            spill = [home for home in self.homes if home in guest]
            return True, guest, spill
        # The ABI registers already hold the parameters, and a call-free body cannot
        # observe a rebinding order: give the parameter slots their own registers and
        # spend the home pool on the callee's locals.
        entry = ARGUMENT_REGISTERS[:parameters]
        spare = [home for home in HOME_REGISTERS
                 if home not in self.homes and home not in entry]
        shortfall = callee.slots - parameters - len(spare)
        reused = list(self.homes[:shortfall]) if shortfall > 0 else []
        return False, tuple(entry) + tuple(spare + reused), reused

    def _return_slot_override(self, callee: syntax.Function, guest: tuple,
                              alias: dict, copy_home: str) -> int:
        """Whether the copy's final result slot may simply use the return destination.

        A copy stores the body's last `return` value into the caller's destination
        after computing it in a guest register - one serial move per call, which is
        call_chain's `mov r14, rbx`. When that value's slot is a local the body
        defines before it reads, the slot can home in the destination register
        itself, and the move disappears - but only if no substituted alias whose
        register is the destination survives the slot's first definition (a later
        read would observe the copy's result, not the caller's argument), and the
        slot needs no entry-time clear (the xor would erase an aliased caller home).
        """
        body = callee.body
        statements = body.statements if body else []
        if not (statements and isinstance(statements[-1], syntax.Return)):
            return -1
        result = self._stripped(statements[-1].value)
        if not isinstance(result, syntax.Name):
            return -1
        slot = result.slot
        if (slot in alias or slot >= len(guest)
                or guest[slot] == copy_home
                or not self._cleared_before_use(body, slot)):
            return -1
        aliased = {name_slot for name_slot, home in alias.items() if home == copy_home}
        first = -1
        for index in range(len(statements) - 1):
            statement = statements[index]
            if (isinstance(statement, (syntax.Let, syntax.Assign))
                    and statement.slot == slot):
                first = index
                break
        if first < 0:
            return -1
        for statement in statements[first + 1:]:
            for name_slot in aliased:
                if self._statement_touches(statement, name_slot):
                    return -1
        return slot

    def _ends_in_return(self, body: Optional[syntax.Block]) -> bool:
        """Whether a body's last statement returns (the copy honors a return home)."""
        return bool(body and body.statements
                    and isinstance(body.statements[-1], syntax.Return))

    def _emit_stack_call(self, expression: syntax.Call) -> None:
        """Lower a five-or-more argument call: four registers, the rest via the stack.

        Stack parameter j is read by the callee at [rbp+16+8j], so the caller stores
        argument 4+j at [rsp+8j] at the call. One `sub rsp` reserves the pad and every
        stack slot together; the call is issued directly because the reservation
        already carries the alignment parity `_emit_call_site` would double-count.
        Every argument still evaluates left to right: register candidates park on the
        stack while later arguments evaluate, and each stack destination stores in
        place, its displacement folding the parked pushes still outstanding.
        """
        asm = self.assembler
        arguments = expression.arguments
        stack_count = len(arguments) - len(ARGUMENT_REGISTERS)
        reserve = 8 * stack_count + (8 if (self.depth + 8 * stack_count) % 16 else 0)
        asm.sub_rsp(reserve)
        self.depth += reserve
        parked = 0
        for index, argument in enumerate(arguments):
            self._emit_expression(argument)
            if index < len(ARGUMENT_REGISTERS):
                asm.push("rax")
                self.depth += 8
                parked += 1
            else:
                asm.mov_store_rsp_disp(8 * (parked + index - len(ARGUMENT_REGISTERS)), "rax")
        for register_index in reversed(range(len(ARGUMENT_REGISTERS))):
            asm.pop(ARGUMENT_REGISTERS[register_index])
            self.depth -= 8
        asm.call(function_label(expression.name))
        asm.add_rsp(reserve)
        self.depth -= reserve

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

    def _emit_pure_arguments(self, arguments: list, skip: frozenset = frozenset()) -> bool:
        """Bind call arguments straight into their registers, or return False.

        The generic path pays a push and a pop per argument plus the round-trip
        through rax; every multi-argument call with pure arguments drops both. When
        only the first argument is pure (the Ackermann shape `f(m - 1, f(m, n - 1))`),
        it still binds directly after the second argument evaluates, saving one pair.
        `skip` names argument indices a foreign-inline copy substitutes instead of
        binding, so their evaluation happens at the copy's use sites, not here.
        """
        registers = ARGUMENT_REGISTERS[:len(arguments)]
        if all(self._is_pure_argument(argument) for argument in arguments):
            for index in range(len(arguments) - 1, -1, -1):
                if index not in skip:
                    self._emit_into(arguments[index], registers[index])
            return True
        if (any(not self._is_pure_argument(argument) for argument in arguments)
                and all(not has_call(argument) for argument in arguments)
                and all(not self._contains_division(argument) for argument in arguments)):
            # Every argument is call-free and trap-free, so binding in reverse costs
            # nothing observable: source order only matters when an argument can call
            # (nested calls clobber the caller-saved argument registers already bound)
            # or trap (a trap would fire in the source's left-to-right order). Each
            # binding writes only its own ABI register, and a later evaluation's lower
            # -ings clobber none of them; the last-bound first argument lands in rcx,
            # which variable shifts reuse as a scratch only after it is already bound.
            for index in range(len(arguments) - 1, -1, -1):
                if index not in skip:
                    self._emit_into(arguments[index], registers[index])
            return True
        if len(arguments) == 2 and self._is_pure_argument(arguments[0]):
            self._emit_expression(arguments[1])
            self.assembler.mov_reg_reg(registers[1], "rax")
            if 0 not in skip:
                self._emit_into(arguments[0], registers[0])
            return True
        return False

    def _contains_division(self, expression: Optional[syntax.Expr]) -> bool:
        """Whether any subtree of `expression` divides — the one trapping operand kind."""
        if isinstance(expression, syntax.Parens):
            return self._contains_division(expression.inner)
        if isinstance(expression, syntax.Binary):
            if expression.op in ("/", "%"):
                return True
            return (self._contains_division(expression.left)
                    or self._contains_division(expression.right))
        if isinstance(expression, syntax.Unary):
            return self._contains_division(expression.operand)
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
        self._emit_loop_head(top)
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

    def _foreign_inline_target(self, expression: syntax.Call) -> Optional[syntax.Function]:
        """The function to copy into this call site, or None when the shape stays a call.

        A copy needs all of the callee's state in home registers (its slots cannot exceed
        the home set, or a slot access would read the caller's frame), and the callee must
        be completely call-free, trap-free, and loop-free: no nested call can then observe
        the parameters' register bindings, no division can fire inside a foreign frame,
        and no loop label or chain counter can outlive the copy. The caller must hold no
        in-flight temporary across the copy, because the copy's lowerings would clobber
        the register that keeps it.
        """
        if (self.program is None or self.current is None
                or self.depth or self.temporaries
                or self.inline_depth >= MAX_INLINE_DEPTH
                or expression.name == self.current.name):
            return None
        for function in self.program.functions:
            if function.name == expression.name:
                break
        else:
            return None
        if (len(expression.arguments) != len(function.parameters)
                or not 0 < len(expression.arguments) <= len(ARGUMENT_REGISTERS)
                or function.slots > len(HOME_REGISTERS)):
            return None
        if not self._body_is_copy_free(function.body):
            return None
        return function

    def _body_is_copy_free(self, body: syntax.Block) -> bool:
        """Whether a whole statement tree calls, divides, loops, or breaks — any refuses a copy."""

        def visit(expression: Optional[syntax.Expr]) -> bool:
            if expression is None:
                return False
            if isinstance(expression, syntax.Call):
                return True
            if isinstance(expression, syntax.Binary):
                return (visit(expression.left) or visit(expression.right)
                        or self._contains_division(expression))
            if isinstance(expression, syntax.Unary):
                return visit(expression.operand) or self._contains_division(expression)
            if isinstance(expression, syntax.Parens):
                return visit(expression.inner) or self._contains_division(expression)
            return self._contains_division(expression)

        def walk(statement: syntax.Stmt) -> bool:
            if isinstance(statement, syntax.Block):
                return any(walk(item) for item in statement.statements)
            if isinstance(statement, (syntax.Let, syntax.Assign)):
                return visit(statement.value)
            if isinstance(statement, syntax.If):
                return (visit(statement.condition) or walk(statement.then_block)
                        or walk(statement.else_block))
            if isinstance(statement, (syntax.While, syntax.Break, syntax.Continue)):
                return True
            if isinstance(statement, syntax.Return):
                return visit(statement.value)
            if isinstance(statement, syntax.ExprStmt):
                return visit(statement.expression)
            return False

        return not walk(body)

    def _substitution_plan(self, expression: syntax.Call,
                           target: Optional[syntax.Function]) -> dict:
        """Which arguments a foreign-inline copy never binds: the bare names.

        An argument that is a bare name with a register home aliases into the copy:
        the copy's parameter slot resolves, through `_home`, to the caller's own home
        register, so every use of the parameter loads the value where it already sits
        and the bind move never happens. Arguments that compute, trap, or call keep
        their bound form, and callees that rebind a parameter refuse the whole plan:
        a rebind must write the bind-once shadow home, not the caller's register.
        """
        if target is None or self.delta_value or self.value_slot >= 0:
            return {}
        if self._rebinds_parameters(target.body, len(target.parameters)):
            return {}
        plan: dict = {}
        for index, argument in enumerate(expression.arguments[:len(target.parameters)]):
            argument = self._stripped(argument)
            if (isinstance(argument, syntax.Name)
                    and argument.slot != self.delta_slot
                    and argument.slot != self.value_slot
                    and self._home(argument.slot) is not None):
                plan[index] = self._home(argument.slot)
        return plan

    def _alias_substitution(self, substitution: dict, borrowed: list):
        """Keep every alias whose home register the copy leaves intact.

        The copy's guest borrows caller homes only when its locals outgrow the free
        ones; a borrowed register is restored to the caller's own value on the way
        out, so an alias onto it would let the copy overwrite the value it reads.
        Returns the alias map (parameter slot -> caller home) and the substituted
        indices.
        """
        alias = {slot: home for slot, home in substitution.items()
                 if home not in borrowed}
        return alias, frozenset(alias)

    def _emit_foreign_inline(self, callee: syntax.Function,
                             alias: Optional[dict] = None,
                             copy_home: Optional[str] = None) -> None:
        """Lower a call to another function as one copy of its body, result in rax.

        When no statement rebinds a parameter, the argument registers themselves are the
        parameter homes: the copy pays no binding moves and no spill for them, exactly
        what a register-allocated compiler keeps after inlining. A rebind forces the
        bind-once form, where each argument moves from its ABI register into a shadow
        home and any reused caller home crosses the copy on the stack. The implicit
        return zero runs at entry like a prologue's, minus when the body ends in a
        return, and a slot whose top level assigns it before anything reads it skips
        its clear.
        """
        asm = self.assembler
        saved = (self.homes, self.current, self.epilogue, self.pad_slots, self.holds,
                 self.delta_slot, self.delta_value, self.value_slot, self.hold_units,
                 self.chain_loop_depth, self.temporaries, self.nonnegative_slots,
                 self.copy_alias, self.copy_return)
        alias = dict(alias or {})
        parameters = len(callee.parameters)
        rebinds, guest, spill = self._plan_foreign_copy(callee)
        if copy_home is not None and not rebinds:
            ret_slot = self._return_slot_override(callee, guest, alias, copy_home)
            if ret_slot >= 0:
                guest = tuple(copy_home if index == ret_slot else home
                              for index, home in enumerate(guest))
        for home in spill:
            asm.push(home)
        self.homes = guest
        self.current = callee
        self.epilogue = self._fresh(f"{function_label(callee.name)}.copy")
        self.pad_slots = ()
        self.holds = ()
        self.delta_slot = -1
        self.delta_value = 0
        self.value_slot = -1
        self.hold_units = 0
        self.chain_loop_depth = 0
        self.temporaries = 0
        self.inline_depth += 1
        # Substituted arguments read the caller's homes through alias slots; a return
        # destination, when honored, receives the copy's result without the rax move.
        self.copy_alias = alias
        self.copy_return = copy_home
        for index, parameter in enumerate(callee.parameters[:len(ARGUMENT_REGISTERS)]):
            if rebinds:
                asm.mov_reg_reg(self.homes[index], ARGUMENT_REGISTERS[index])
        for index in range(parameters, callee.slots):
            if not self._cleared_before_use(callee.body, index):
                asm.xor_zero(self.homes[index])
        self.nonnegative_slots = set()
        statements = callee.body.statements
        if not (statements and isinstance(statements[-1], syntax.Return)):
            # The implicit return zero only guards the fall-off-the-end path; a body
            # whose last statement returns cannot reach the copy's epilogue without rax,
            # and a copy runs once per loop iteration, so the move is loop-carried waste.
            asm.mov_imm64("rax", 0)
        self._emit_block(callee.body, tail=True)
        asm.label(self.epilogue)
        self.inline_depth -= 1
        for home in reversed(spill):
            asm.pop(home)
        (self.homes, self.current, self.epilogue, self.pad_slots, self.holds,
         self.delta_slot, self.delta_value, self.value_slot, self.hold_units,
         self.chain_loop_depth, self.temporaries, self.nonnegative_slots,
         self.copy_alias, self.copy_return) = saved

    def _rebinds_parameters(self, body: Optional[syntax.Block], count: int) -> bool:
        """Whether any statement assigns a parameter slot."""
        if body is None:
            return False
        for statement in body.statements:
            if isinstance(statement, syntax.Assign) and statement.slot < count:
                return True
            if isinstance(statement, syntax.Block) and self._rebinds_parameters(statement, count):
                return True
            if isinstance(statement, syntax.If):
                if (self._rebinds_parameters(statement.then_block, count)
                        or self._rebinds_parameters(statement.else_block, count)):
                    return True
            if isinstance(statement, syntax.While) and self._rebinds_parameters(statement.body, count):
                return True
        return False

    def _expression_reads(self, expression: Optional[syntax.Expr], slot: int) -> bool:
        """Whether an expression reads `slot`."""
        if expression is None:
            return False
        if isinstance(expression, syntax.Name):
            return expression.slot == slot
        if isinstance(expression, syntax.Binary):
            return (self._expression_reads(expression.left, slot)
                    or self._expression_reads(expression.right, slot))
        if isinstance(expression, syntax.Unary):
            return self._expression_reads(expression.operand, slot)
        if isinstance(expression, syntax.Parens):
            return self._expression_reads(expression.inner, slot)
        if isinstance(expression, syntax.Call):
            return any(self._expression_reads(argument, slot)
                       for argument in expression.arguments)
        return False

    def _statement_touches(self, statement: syntax.Stmt, slot: int) -> bool:
        """Whether a statement reads or writes `slot` in any position."""
        if isinstance(statement, (syntax.Let, syntax.Assign)):
            return (statement.slot == slot
                    or self._expression_reads(statement.value, slot))
        if isinstance(statement, syntax.If):
            return (self._expression_reads(statement.condition, slot)
                    or (self._statement_touches_branch(statement.then_block, slot))
                    or (self._statement_touches_branch(statement.else_block, slot)))
        if isinstance(statement, syntax.While):
            return (self._expression_reads(statement.condition, slot)
                    or self._statement_touches_branch(statement.body, slot))
        if isinstance(statement, syntax.Return):
            return self._expression_reads(statement.value, slot)
        if isinstance(statement, syntax.ExprStmt):
            return self._expression_reads(statement.expression, slot)
        return False

    def _statement_touches_branch(self, block: Optional[syntax.Block], slot: int) -> bool:
        """Whether any statement of a nested block, at any depth, touches `slot`."""
        if block is None:
            return False
        if any(self._statement_touches(item, slot) for item in block.statements):
            return True
        for item in block.statements:
            if isinstance(item, syntax.Block) and self._statement_touches_branch(item, slot):
                return True
            if isinstance(item, syntax.If):
                if (self._statement_touches_branch(item.then_block, slot)
                        or self._statement_touches_branch(item.else_block, slot)):
                    return True
            if isinstance(item, syntax.While) and self._statement_touches_branch(item.body, slot):
                return True
        return False

    def _cleared_before_use(self, body: syntax.Block, slot: int) -> bool:
        """Whether a top-level assignment to `slot` comes before anything can touch it.

        A prologue's clear only defends a slot read before its first assignment; when
        the first top-level statement is that assignment and its value cannot read the
        slot, ... skip the clear. A slot first touched by a branch or by an assignment
        that reads the slot still needs it.
        """
        for statement in body.statements:
            if isinstance(statement, syntax.Let) and statement.slot == slot:
                return not self._expression_reads(statement.value, slot)
            if self._statement_touches(statement, slot):
                return False
        return False

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
