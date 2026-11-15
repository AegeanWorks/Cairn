"""Runtime helpers emitted into every executable.

There is no runtime library to link: `print` and the divide-by-zero trap are machine code
written here, into the same `.text` section as the user's functions. They call only three
kernel32 imports, resolved through the import table the PE writer builds.
"""

from cairn.image import ReadOnlyData, iat_label
from cairn.machine import Assembler

DIV_ZERO_MESSAGE = b"cairn: runtime error: divide by zero\n"

PRINT_LABEL = "cairn.runtime.print_i64"
PRINT_CHAR_LABEL = "cairn.runtime.print_char"
PRINT_HEX_LABEL = "cairn.runtime.print_hex"
DIV_ZERO_LABEL = "cairn.runtime.div_zero"
NEWLINE_LABEL = "cairn.runtime.newline"
DIV_ZERO_DATA_LABEL = "cairn.runtime.div_zero_text"

FRAME_SIZE = 96
BUFFER_TOP = -1
WRITTEN_SLOT = -40
BUFFER_SLOT = -48
COUNT_SLOT = -56
BYTE_SLOT = -8


def emit_print_i64(assembler: Assembler) -> None:
    """Emit `print_i64`: write rax in decimal plus a newline.

    The digits are produced right-to-left in a 32-byte frame buffer and written with one
    `WriteFile` call, so printing costs one syscall for the number and one for the newline.
    """
    assembler.label(PRINT_LABEL)
    assembler.push("rbp")
    assembler.mov_reg_reg("rbp", "rsp")
    assembler.sub_rsp(FRAME_SIZE)
    assembler.lea_rbp("rcx", BUFFER_TOP)
    assembler.mov_imm32("r10", 10)
    assembler.xor_zero("r11")
    assembler.xor_zero("r9")
    assembler.test("rax")
    assembler.jcc("ns", "print.digits")
    assembler.mov_imm32("r9", 1)
    assembler.unary(3, "rax")
    assembler.label("print.digits")
    assembler.xor_zero("rdx")
    assembler.divide("r10", signed=False)
    assembler.add_imm8("dl", 0x30)
    assembler.step("rcx", decrement=True)
    assembler.store_byte("rcx", "dl")
    assembler.step("r11", decrement=False)
    assembler.test("rax")
    assembler.jcc("nz", "print.digits")
    assembler.test("r9")
    assembler.jcc("z", "print.write")
    assembler.step("rcx", decrement=True)
    assembler.store_byte_imm("rcx", 0x2D)
    assembler.step("r11", decrement=False)
    assembler.label("print.write")
    assembler.mov_store_rbp(BUFFER_SLOT, "rcx")
    assembler.mov_store_rbp(COUNT_SLOT, "r11")
    assembler.mov_imm32("rcx", -11)
    assembler.call_indirect(iat_label("GetStdHandle"))
    assembler.mov_reg_reg("rcx", "rax")
    assembler.mov_load_rbp("rdx", BUFFER_SLOT)
    assembler.mov_load_rbp("r8", COUNT_SLOT)
    assembler.lea_rbp("r9", WRITTEN_SLOT)
    assembler.mov_store_rsp_imm32(32, 0)
    assembler.call_indirect(iat_label("WriteFile"))
    assembler.mov_imm32("rcx", -11)
    assembler.call_indirect(iat_label("GetStdHandle"))
    assembler.mov_reg_reg("rcx", "rax")
    assembler.lea_rip("rdx", NEWLINE_LABEL)
    assembler.mov_imm32("r8", 1)
    assembler.lea_rbp("r9", WRITTEN_SLOT)
    assembler.mov_store_rsp_imm32(32, 0)
    assembler.call_indirect(iat_label("WriteFile"))
    assembler.add_rsp(FRAME_SIZE)
    assembler.pop("rbp")
    assembler.ret()


def emit_print_char(assembler: Assembler) -> None:
    """Emit `print_char`: write the low byte of rax to stdout, no newline.

    The byte is isolated by an unsigned division by 256 (the remainder is `rax & 0xFF`) and
    written from a one-byte frame buffer, exactly one `WriteFile` call. The clobbered `rax`
    never escapes: the code generator pops the caller's saved value after the call.
    """
    assembler.label(PRINT_CHAR_LABEL)
    assembler.push("rbp")
    assembler.mov_reg_reg("rbp", "rsp")
    assembler.sub_rsp(FRAME_SIZE)
    assembler.lea_rbp("rcx", BYTE_SLOT)
    assembler.xor_zero("rdx")
    assembler.mov_imm32("r10", 256)
    assembler.divide("r10", signed=False)
    assembler.store_byte("rcx", "dl")
    assembler.mov_imm32("rcx", -11)
    assembler.call_indirect(iat_label("GetStdHandle"))
    assembler.mov_reg_reg("rcx", "rax")
    assembler.lea_rbp("rdx", BYTE_SLOT)
    assembler.mov_imm32("r8", 1)
    assembler.lea_rbp("r9", WRITTEN_SLOT)
    assembler.mov_store_rsp_imm32(32, 0)
    assembler.call_indirect(iat_label("WriteFile"))
    assembler.add_rsp(FRAME_SIZE)
    assembler.pop("rbp")
    assembler.ret()


def emit_print_hex(assembler: Assembler) -> None:
    """Emit `print_hex`: write rax in lowercase hex, no newline, no leading zeros.

    The nibbles fall out right-to-left of unsigned divisions by 16 (remainder and shift in
    one `div`), each turned into ASCII by a conditional move — `+ 0x30`, or `+ 0x57` when the
    nibble cleared 10 — and written with one `WriteFile` call. Zero prints as a single `0`.
    """
    assembler.label(PRINT_HEX_LABEL)
    assembler.push("rbp")
    assembler.mov_reg_reg("rbp", "rsp")
    assembler.sub_rsp(FRAME_SIZE)
    assembler.lea_rbp("rcx", BUFFER_TOP)
    assembler.mov_imm32("r10", 16)
    assembler.xor_zero("r11")
    assembler.label("print_hex.digits")
    assembler.xor_zero("rdx")
    assembler.divide("r10", signed=False)
    assembler.mov_reg_reg("r9", "rdx")
    assembler.add_imm8("dl", 0x30)
    assembler.add_imm8("r9b", 0x57)
    assembler.cmp_imm("rdx", 58)
    assembler.cmov("ge", "rdx", "r9")
    assembler.step("rcx", decrement=True)
    assembler.store_byte("rcx", "dl")
    assembler.step("r11", decrement=False)
    assembler.test("rax")
    assembler.jcc("nz", "print_hex.digits")
    assembler.mov_store_rbp(BUFFER_SLOT, "rcx")
    assembler.mov_store_rbp(COUNT_SLOT, "r11")
    assembler.mov_imm32("rcx", -11)
    assembler.call_indirect(iat_label("GetStdHandle"))
    assembler.mov_reg_reg("rcx", "rax")
    assembler.mov_load_rbp("rdx", BUFFER_SLOT)
    assembler.mov_load_rbp("r8", COUNT_SLOT)
    assembler.lea_rbp("r9", WRITTEN_SLOT)
    assembler.mov_store_rsp_imm32(32, 0)
    assembler.call_indirect(iat_label("WriteFile"))
    assembler.add_rsp(FRAME_SIZE)
    assembler.pop("rbp")
    assembler.ret()


def emit_div_zero(assembler: Assembler, rodata: ReadOnlyData) -> None:
    """Emit the trap taken when a divisor is zero: message on stderr, exit code 3."""
    assembler.label(DIV_ZERO_LABEL)
    assembler.push("rbp")
    assembler.mov_reg_reg("rbp", "rsp")
    assembler.align_rsp()
    assembler.sub_rsp(48)
    assembler.mov_imm32("rcx", -12)
    assembler.call_indirect(iat_label("GetStdHandle"))
    assembler.mov_reg_reg("rcx", "rax")
    assembler.lea_rip("rdx", DIV_ZERO_DATA_LABEL)
    assembler.mov_imm32("r8", len(DIV_ZERO_MESSAGE))
    assembler.lea_rbp("r9", -8)
    assembler.mov_store_rsp_imm32(32, 0)
    assembler.call_indirect(iat_label("WriteFile"))
    assembler.mov_imm32("rcx", 3)
    assembler.call_indirect(iat_label("ExitProcess"))
    assembler.hlt()


def emit_runtime(assembler: Assembler, rodata: ReadOnlyData) -> None:
    """Emit every helper routine and the data they reference, in a stable order."""
    rodata.add(NEWLINE_LABEL, b"\n")
    rodata.add(DIV_ZERO_DATA_LABEL, DIV_ZERO_MESSAGE)
    emit_print_i64(assembler)
    emit_print_char(assembler)
    emit_print_hex(assembler)
    emit_div_zero(assembler, rodata)


__all__ = [
    "DIV_ZERO_DATA_LABEL", "DIV_ZERO_LABEL", "DIV_ZERO_MESSAGE", "NEWLINE_LABEL",
    "PRINT_CHAR_LABEL", "PRINT_HEX_LABEL", "PRINT_LABEL", "emit_div_zero", "emit_print_char",
    "emit_print_hex", "emit_print_i64", "emit_runtime",
]
