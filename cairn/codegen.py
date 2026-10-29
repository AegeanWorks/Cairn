"""Code generation: syntax tree to x86-64, one pass, values in rax.

Every Cairn expression is an i64 in a register, so the lowering never needs a type test. The left
operand of a binary operator lives in a temporary register when the right operand cannot call a
function, and on the stack otherwise; calls pass arguments in the Windows x64 integer registers,
which the same backend also uses internally.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from cairn import syntax
from cairn.image import ENTRY_LABEL, Artifact, ReadOnlyData, iat_label
from cairn.machine import ARGUMENT_REGISTERS, Assembler
from cairn.optimize import has_call
from cairn.runtime import DIV_ZERO_LABEL, PRINT_LABEL, emit_runtime
from cairn.sema import MAX_PARAMETERS

COMPARISON_CONDITIONS = {
    "==": "e", "!=": "ne", "<": "l", "<=": "le", ">": "g", ">=": "ge",
}

ARITHMETIC_OPCODES = {
    "+": 0x01, "&": 0x21, "|": 0x09, "^": 0x31,
}

# Volatile registers that hold a left operand across a nested expression, innermost last.
TEMPORARY_REGISTERS = ("r11", "r10")
# Scratch for division and shifting: never a temporary register and never live across a call.
SCRATCH = "rsi"


def function_label(name: str) -> str:
    """The assembler label of a Cairn function."""
    return f"fn.{name}"


def slot_offset(slot: int) -> int:
    """The rbp-relative offset of a frame slot; slot 0 is the first parameter."""
    return -8 * (slot + 1)


def align16(value: int) -> int:
    """Round a frame size up to the 16-byte stack alignment the ABI requires."""
    return (value + 15) & ~15


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
        """Prologue, zeroed frame, parameter spills, body, and an unreachable zero return."""
        asm = self.assembler
        frame = align16(function.slots * 8)
        asm.label(function_label(function.name))
        asm.push("rbp")
        asm.mov_reg_reg("rbp", "rsp")
        if frame:
            asm.sub_rsp(frame)
            self._zero_frame(function.slots)
        for index, parameter in enumerate(function.parameters[:MAX_PARAMETERS]):
            asm.mov_store_rbp(slot_offset(parameter.slot), ARGUMENT_REGISTERS[index])
        self.depth = 0
        self.temporaries = 0
        self._emit_block(function.body)
        asm.mov_imm64("rax", 0)
        asm.leave_ret()

    def _zero_frame(self, slots: int) -> None:
        """Clear every slot so a binding declared in a branch that never ran reads as zero.

        Cairn has one flat function scope, so a name declared inside an `if` is visible after it.
        Zeroing the frame makes that read defined instead of whatever the previous call left.
        """
        asm = self.assembler
        asm.xor_zero("rax")
        for slot in range(slots):
            asm.mov_store_rbp(slot_offset(slot), "rax")

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
            self._emit_expression(statement.value)
            asm.mov_store_rbp(slot_offset(statement.slot), "rax")
        elif isinstance(statement, syntax.Assign):
            self._emit_expression(statement.value)
            asm.mov_store_rbp(slot_offset(statement.slot), "rax")
        elif isinstance(statement, syntax.If):
            self._emit_if(statement)
        elif isinstance(statement, syntax.While):
            self._emit_while(statement)
        elif isinstance(statement, syntax.Return):
            self._emit_expression(statement.value)
            asm.leave_ret()
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
        """`if` as a test, a conditional jump and two labelled bodies."""
        asm = self.assembler
        otherwise = self._fresh("if.else")
        end = self._fresh("if.end")
        self._emit_expression(statement.condition)
        asm.test("rax")
        asm.jcc("z", otherwise)
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
        self._emit_expression(statement.condition)
        asm.test("rax")
        asm.jcc("z", end)
        self.break_targets.append(end)
        self.continue_targets.append(start)
        self._emit_block(statement.body)
        self.break_targets.pop()
        self.continue_targets.pop()
        asm.jmp(start)
        asm.label(end)

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
            asm.mov_load_rbp("rax", slot_offset(expression.slot))
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
        self._emit_left_operand(expression.left, expression.right)
        if expression.op in ("/", "%"):
            self._emit_division(expression.op)
        elif expression.op in COMPARISON_CONDITIONS:
            asm.binary(0x39, "rcx", "rax")
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

        A register is used when one is free and the right operand cannot call; a call would
        clobber it, so that case keeps the stack form the ABI already tolerates.
        """
        asm = self.assembler
        self._emit_expression(left)
        if self.temporaries < len(TEMPORARY_REGISTERS) and not has_call(right):
            temporary = TEMPORARY_REGISTERS[self.temporaries]
            asm.mov_reg_reg(temporary, "rax")
            self.temporaries += 1
            self._emit_expression(right)
            self.temporaries -= 1
            asm.mov_reg_reg("rcx", temporary)
            return
        asm.push("rax")
        self.depth += 8
        self._emit_expression(right)
        asm.pop("rcx")
        self.depth -= 8

    def _emit_division(self, operator: str) -> None:
        """Signed division with an explicit zero-divisor trap, quotient or remainder."""
        asm = self.assembler
        asm.test("rax")
        asm.jcc("z", DIV_ZERO_LABEL)
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
        """Evaluate arguments left to right, pop them into registers, then call."""
        asm = self.assembler
        if expression.name == "print":
            self._emit_expression(expression.arguments[0])
            asm.push("rax")
            self.depth += 8
            self._emit_call_site(PRINT_LABEL)
            asm.pop("rax")
            self.depth -= 8
            return
        for argument in expression.arguments:
            self._emit_expression(argument)
            asm.push("rax")
            self.depth += 8
        for index in reversed(range(len(expression.arguments))):
            asm.pop(ARGUMENT_REGISTERS[index])
            self.depth -= 8
        self._emit_call_site(function_label(expression.name))

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


__all__ = ["CodeGenerator", "align16", "function_label", "generate", "slot_offset"]
