"""Runtime helpers emitted into every executable.

There is no runtime library to link: `print`, the input builtins and the divide-by-zero
trap are machine code written here, into the same `.text` section as the user's functions.
They call only six kernel32 imports, resolved through the import table the PE writer
builds.
"""

from cairn.image import ReadOnlyData, WritableData, iat_label
from cairn.machine import Assembler

DIV_ZERO_MESSAGE = b"cairn: runtime error: divide by zero\n"

PRINT_LABEL = "cairn.runtime.print_i64"
PRINT_CHAR_LABEL = "cairn.runtime.print_char"
PRINT_HEX_LABEL = "cairn.runtime.print_hex"
DIV_ZERO_LABEL = "cairn.runtime.div_zero"
ARGC_LABEL = "cairn.runtime.argc"
ARG_BYTE_LABEL = "cairn.runtime.arg_byte"
READ_CHAR_LABEL = "cairn.runtime.read_char"
PRINT_STR_LABEL = "cairn.runtime.print_str"
STR_LEN_LABEL = "cairn.runtime.str_len"
STR_BYTE_LABEL = "cairn.runtime.str_byte"
ARG_FILL_LABEL = "cairn.runtime.arg_fill"
ARGV_CACHE_LABEL = "cairn.runtime.argv_cache"
ARGV_ARENA_LABEL = "cairn.runtime.argv_arena"
ARGV_PTR0_LABEL = "cairn.runtime.argv_ptr0"
ARGV_LEN0_LABEL = "cairn.runtime.argv_len0"
STDIN_BUFFER_LABEL = "cairn.runtime.stdin_buffer"
STDIN_CURSOR_LABEL = "cairn.runtime.stdin_cursor"
STDIN_COUNT_LABEL = "cairn.runtime.stdin_count"
NEWLINE_LABEL = "cairn.runtime.newline"
DIV_ZERO_DATA_LABEL = "cairn.runtime.div_zero_text"

FRAME_SIZE = 96
SHELL_FRAME = 32
READ_FRAME = 48
STR_FRAME = 48
PTR_SLOT = -48
BUFFER_TOP = -1
WRITTEN_SLOT = -40
BUFFER_SLOT = -48
COUNT_SLOT = -56
BYTE_SLOT = -8

# The argv cache lives in the writable blob: a flag, the token count, then a fixed
# table of (pointer, length) slots; the arena beside it holds the copied token bytes.
# Process arguments never change, so the first `argc` or `arg_byte` call tokenizes
# once and every later call reads straight from the table — the O(command line) walk
# happens at most once. Quotes are stripped on the copy, so bytes read back are the
# argument's content. Beyond the arena or the table the reader answers -1/zero bytes.
MAX_ARGS = 63
ARGV_CACHE_SIZE = 16 + 16 * MAX_ARGS
ARGV_ARENA_SIZE = 1024
STDIN_BUFFER_SIZE = 4096


def emit_arg_fill(assembler: Assembler) -> None:
    """Emit `arg_fill`: tokenize the ANSI command line into the static table once.

    A token is a maximal run of non-whitespace bytes; whitespace is one space or one
    tab, and a byte inside double quotes is token content with the quotes stripped.
    The scanned bytes copy into the arena, so a slot's pointer plus length reads back
    clean content. The flag store comes after the table, and the table caps at
    `MAX_ARGS` slots while the count keeps rising, so an oversized command line
    degrades to -1 reads instead of reading a wrong token. The arena bounds each copy
    (`rbx` is saved and restored around the work); when it fills, later bytes drop.
    """
    asm = assembler
    asm.label(ARG_FILL_LABEL)
    asm.push("rbp")
    asm.mov_reg_reg("rbp", "rsp")
    asm.sub_rsp(SHELL_FRAME)
    asm.lea_rip("r10", ARGV_CACHE_LABEL)
    asm.mov_load("rax", "r10")
    asm.test("rax")
    asm.jcc("nz", "arg_fill.done")
    asm.call_indirect(iat_label("GetCommandLineA"))
    asm.push("rbx")                          # the runtime hands rbx back intact
    asm.mov_reg_reg("rcx", "rax")            # rcx walks the line; no call inside the walk
    asm.lea_rip("rbx", ARGV_ARENA_LABEL)
    asm.lea_rip("r10", ARGV_CACHE_LABEL)
    asm.lea_imm("r9", "r10", 8)
    asm.store_qword("r9", "rax")             # the raw line pointer, kept for debugging tools
    asm.mov_reg_reg("rdx", "rbx")            # the arena's next write position
    asm.xor_zero("r11")                      # tokens stored or counted
    asm.xor_zero("r9")                       # inside a quoted run
    asm.label("arg_fill.token")
    asm.label("arg_fill.skip")
    asm.movzx_load8("rax", "rcx")
    asm.cmp_imm("rax", 32)
    asm.jcc("e", "arg_fill.whitespace")
    asm.cmp_imm("rax", 9)
    asm.jcc("ne", "arg_fill.start")
    asm.label("arg_fill.whitespace")
    asm.add_imm("rcx", 1)
    asm.jmp("arg_fill.skip")
    asm.label("arg_fill.start")
    asm.movzx_load8("rax", "rcx")
    asm.test("rax")
    asm.jcc("z", "arg_fill.finish")
    asm.mov_reg_reg("r8", "rdx")             # the token's first arena byte
    asm.label("arg_fill.scan")
    asm.movzx_load8("rax", "rcx")
    asm.test("rax")
    asm.jcc("z", "arg_fill.store")
    asm.cmp_imm("rax", 34)
    asm.jcc("e", "arg_fill.quote")
    asm.test("r9")
    asm.jcc("nz", "arg_fill.copy")
    asm.cmp_imm("rax", 32)
    asm.jcc("e", "arg_fill.store")
    asm.cmp_imm("rax", 9)
    asm.jcc("e", "arg_fill.store")
    asm.label("arg_fill.copy")
    # Bounds first (rax is scratch), then reload the byte: the arena takes it only
    # while the write position still sits below base + size.
    asm.lea_imm("rax", "rdx", -ARGV_ARENA_SIZE)
    asm.cmp_reg_reg("rax", "rbx")
    asm.jcc("ge", "arg_fill.step")
    asm.movzx_load8("rax", "rcx")
    asm.store_byte("rdx", "al")
    asm.add_imm("rdx", 1)
    asm.label("arg_fill.step")
    asm.add_imm("rcx", 1)
    asm.jmp("arg_fill.scan")
    asm.label("arg_fill.quote")
    asm.arith_imm(6, "r9", 1)                # xor toggles the quote flag
    asm.jmp("arg_fill.step")
    asm.label("arg_fill.store")
    asm.mov_reg_reg("rax", "rdx")
    asm.binary(0x29, "rax", "r8")            # rax = arena next - start, the length
    asm.cmp_imm("r11", MAX_ARGS)
    asm.jcc("ge", "arg_fill.counted")
    asm.mov_reg_reg("rdx", "rax")            # the length rides rdx through the math
    asm.mov_reg_reg("rax", "r11")
    asm.shift_imm(4, "rax", 4)               # rax = token_index * 16
    asm.lea_scale("r9", "r10", "rax", 0, 16)
    asm.store_qword("r9", "r8")
    asm.lea_imm("r9", "r9", 8)
    asm.store_qword("r9", "rdx")
    asm.binary(0x01, "rdx", "r8")            # the arena next is back: start + length
    asm.label("arg_fill.counted")
    asm.xor_zero("r9")                       # r9 was the slot address; the next
                                             # token starts with a closed quote run
    asm.add_imm("r11", 1)
    asm.mov_reg_reg("r8", "rdx")             # the next token starts at the arena next
    asm.movzx_load8("rax", "rcx")
    asm.test("rax")
    asm.jcc("z", "arg_fill.finish")
    asm.add_imm("rcx", 1)
    asm.jmp("arg_fill.token")
    asm.label("arg_fill.finish")
    asm.mov_imm64("rax", 1)
    asm.store_qword("r10", "rax")            # the flag: the table is complete
    asm.lea_imm("r9", "r10", 8)
    asm.store_qword("r9", "r11")
    # Copy slot 0 out for the call-site arg_byte(0, j) inline: an unfilled or
    # empty table leaves both qwords zero, so the inline's length check misses
    # to the helper, which fills first and answers on its own contract.
    asm.lea_imm("r9", "r10", 16)
    asm.mov_load("rax", "r9")
    asm.lea_rip("r8", ARGV_PTR0_LABEL)
    asm.store_qword("r8", "rax")
    asm.lea_imm("r9", "r9", 8)
    asm.mov_load("rax", "r9")
    asm.lea_rip("r8", ARGV_LEN0_LABEL)
    asm.store_qword("r8", "rax")
    asm.pop("rbx")
    asm.label("arg_fill.done")
    asm.add_rsp(SHELL_FRAME)
    asm.pop("rbp")
    asm.ret()


def emit_argc(assembler: Assembler) -> None:
    """Emit `argc()`: the token count, filling and caching the table on the first call."""
    asm = assembler
    asm.label(ARGC_LABEL)
    asm.push("rbp")
    asm.mov_reg_reg("rbp", "rsp")
    asm.sub_rsp(SHELL_FRAME)
    asm.lea_rip("r10", ARGV_CACHE_LABEL)
    asm.mov_load("rax", "r10")
    asm.test("rax")
    asm.jcc("nz", "argc.ready")
    asm.call(ARG_FILL_LABEL)
    asm.lea_rip("r10", ARGV_CACHE_LABEL)
    asm.label("argc.ready")
    asm.lea_imm("r10", "r10", 8)
    asm.mov_load("rax", "r10")
    asm.add_rsp(SHELL_FRAME)
    asm.pop("rbp")
    asm.ret()


def emit_arg_byte(assembler: Assembler) -> None:
    """Emit `arg_byte(i, j)`: byte j (zero-indexed) of argument i's content, or -1.

    Argument 0 is the program path. A token index past the real count, past the
    table cap, a negative index, or a j at or past the token's length all answer -1.
    """
    asm = assembler
    asm.label(ARG_BYTE_LABEL)
    asm.push("rbp")
    asm.mov_reg_reg("rbp", "rsp")
    asm.sub_rsp(SHELL_FRAME)
    asm.lea_rip("r10", ARGV_CACHE_LABEL)
    asm.mov_load("rax", "r10")
    asm.test("rax")
    asm.jcc("nz", "arg_byte.ready")
    asm.test("rcx")
    asm.jcc("s", "arg_byte.miss")            # arg_fill clobbers rcx/rdx, so the
    asm.test("rdx")                          # index checks run before any fill
    asm.jcc("s", "arg_byte.miss")
    # The fill walks rcx and squats rdx: both answers pass through the call's
    # own values, so they cross the fill on the stack (two pushes keep the
    # rsp distance a multiple of 16 at the call into arg_fill).
    asm.push("rcx")
    asm.push("rdx")
    asm.call(ARG_FILL_LABEL)
    asm.pop("rdx")
    asm.pop("rcx")
    asm.lea_rip("r10", ARGV_CACHE_LABEL)
    asm.label("arg_byte.ready")
    asm.lea_imm("r9", "r10", 8)
    asm.mov_load("rax", "r9")                # the stored token count
    asm.cmp_reg_reg("rax", "rcx")
    asm.jcc("le", "arg_byte.miss")
    asm.cmp_imm("rcx", MAX_ARGS)
    asm.jcc("ge", "arg_byte.miss")
    asm.mov_reg_reg("rax", "rcx")
    asm.shift_imm(4, "rax", 4)               # rax = argument index * 16
    asm.lea_scale("r9", "r10", "rax", 0, 16)
    asm.mov_load("r8", "r9")                 # the token's arena pointer
    asm.lea_imm("r9", "r9", 8)
    asm.mov_load("rax", "r9")                # the token's length
    asm.test("r8")
    asm.jcc("z", "arg_byte.miss")            # a cap-skipped slot reads nothing
    asm.test("rdx")
    asm.jcc("s", "arg_byte.miss")
    asm.cmp_reg_reg("rax", "rdx")
    asm.jcc("le", "arg_byte.miss")           # j at or past the end
    asm.lea_scale("r9", "r8", "rdx", 0, 0)
    asm.movzx_load8("rax", "r9")
    asm.add_rsp(SHELL_FRAME)
    asm.pop("rbp")
    asm.ret()
    asm.label("arg_byte.miss")
    asm.mov_imm64("rax", -1)
    asm.add_rsp(SHELL_FRAME)
    asm.pop("rbp")
    asm.ret()


def emit_read_char(assembler: Assembler) -> None:
    """Emit `read_char()`: the next stdin byte, or -1 at end of stream.

    The fast path answers from the static 4096-byte buffer; an empty buffer refills
    with one `ReadFile`, resets the cursor and falls back into the check, so repeated
    calls at a true end of stream cost exactly one syscall each and keep answering -1.
    """
    asm = assembler
    asm.label(READ_CHAR_LABEL)
    asm.lea_rip("r10", STDIN_CURSOR_LABEL)
    asm.mov_load("rax", "r10")
    asm.lea_rip("r11", STDIN_COUNT_LABEL)
    asm.mov_load("rdx", "r11")
    asm.cmp_reg_reg("rdx", "rax")
    asm.jcc("g", "read_char.have")
    # The buffer drained: refill once, reset the cursor, then re-check.
    asm.push("rbp")
    asm.mov_reg_reg("rbp", "rsp")
    asm.sub_rsp(READ_FRAME)
    asm.mov_imm32("rcx", -10)
    asm.call_indirect(iat_label("GetStdHandle"))
    asm.mov_reg_reg("rcx", "rax")
    asm.lea_rip("rdx", STDIN_BUFFER_LABEL)
    asm.mov_imm32("r8", STDIN_BUFFER_SIZE)
    asm.lea_rip("r9", STDIN_COUNT_LABEL)
    asm.mov_store_rsp_imm32(32, 0)
    asm.call_indirect(iat_label("ReadFile"))
    asm.xor_zero("rax")
    asm.lea_rip("r10", STDIN_CURSOR_LABEL)
    asm.store_qword("r10", "rax")
    asm.add_rsp(READ_FRAME)
    asm.pop("rbp")
    asm.lea_rip("r10", STDIN_CURSOR_LABEL)
    asm.mov_load("rax", "r10")
    asm.lea_rip("r11", STDIN_COUNT_LABEL)
    asm.mov_load("rdx", "r11")
    asm.cmp_reg_reg("rdx", "rax")
    asm.jcc("le", "read_char.empty")
    asm.label("read_char.have")
    asm.lea_rip("r9", STDIN_BUFFER_LABEL)
    asm.lea_scale("r8", "r9", "rax", 0, 0)   # buffer + cursor
    asm.add_imm("rax", 1)                    # the load must not destroy the old cursor
    asm.store_qword("r10", "rax")
    asm.movzx_load8("rax", "r8")
    asm.ret()
    asm.label("read_char.empty")
    asm.mov_imm64("rax", -1)
    asm.ret()


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


def emit_print_str(assembler: Assembler) -> None:
    """Emit `print_str`: write the NUL-terminated text at rax to stdout, no newline.

    The length comes from one scan to the terminator, so no byte is written past the
    literal's end and the scan costs no call — the `WriteFile` is the only clobber.
    The pointer returns in rax, kept in a frame slot across the call.
    """
    assembler.label(PRINT_STR_LABEL)
    assembler.push("rbp")
    assembler.mov_reg_reg("rbp", "rsp")
    assembler.sub_rsp(STR_FRAME)
    assembler.mov_store_rbp(PTR_SLOT, "rax")
    assembler.mov_reg_reg("rcx", "rax")
    assembler.xor_zero("r8")
    assembler.label("print_str.scan")
    assembler.movzx_load8("rdx", "rcx")
    assembler.test("rdx")
    assembler.jcc("z", "print_str.write")
    assembler.step("rcx", decrement=False)
    assembler.step("r8", decrement=False)
    assembler.jmp("print_str.scan")
    assembler.label("print_str.write")
    assembler.mov_imm32("rcx", -11)
    assembler.call_indirect(iat_label("GetStdHandle"))
    assembler.mov_reg_reg("rcx", "rax")
    assembler.mov_load_rbp("rdx", PTR_SLOT)
    assembler.lea_rbp("r9", WRITTEN_SLOT)
    assembler.mov_store_rsp_imm32(32, 0)
    assembler.call_indirect(iat_label("WriteFile"))
    assembler.mov_load_rbp("rax", PTR_SLOT)
    assembler.add_rsp(STR_FRAME)
    assembler.pop("rbp")
    assembler.ret()


def emit_str_len(assembler: Assembler) -> None:
    """Emit `str_len`: the number of bytes in the NUL-terminated text at rax."""
    assembler.label(STR_LEN_LABEL)
    assembler.mov_reg_reg("rcx", "rax")
    assembler.xor_zero("rdx")
    assembler.label("str_len.scan")
    assembler.movzx_load8("r8", "rcx")
    assembler.test("r8")
    assembler.jcc("z", "str_len.done")
    assembler.step("rcx", decrement=False)
    assembler.step("rdx", decrement=False)
    assembler.jmp("str_len.scan")
    assembler.label("str_len.done")
    assembler.mov_reg_reg("rax", "rdx")
    assembler.ret()


def emit_str_byte(assembler: Assembler) -> None:
    """Emit `str_byte(s, j)`: byte j of the text at rcx, or -1.

    A negative index and the terminator position all answer -1, so a caller that
    counts with `str_len` first never reads past the literal.
    """
    asm = assembler
    asm.label(STR_BYTE_LABEL)
    asm.test("rdx")
    asm.jcc("s", "str_byte.miss")
    asm.lea_scale("r8", "rcx", "rdx", 0, 0)
    asm.movzx_load8("rax", "r8")
    asm.test("rax")
    asm.jcc("z", "str_byte.miss")
    asm.ret()
    asm.label("str_byte.miss")
    asm.mov_imm64("rax", -1)
    asm.ret()


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


def emit_runtime(assembler: Assembler, rodata: ReadOnlyData,
                 writable: WritableData) -> None:
    """Emit every helper routine and the data they reference, in a stable order."""
    rodata.add(NEWLINE_LABEL, b"\n")
    rodata.add(DIV_ZERO_DATA_LABEL, DIV_ZERO_MESSAGE)
    writable.reserve(ARGV_CACHE_LABEL, ARGV_CACHE_SIZE)
    writable.reserve(ARGV_ARENA_LABEL, ARGV_ARENA_SIZE)
    writable.reserve(STDIN_BUFFER_LABEL, STDIN_BUFFER_SIZE)
    writable.reserve(STDIN_CURSOR_LABEL, 8)
    writable.reserve(STDIN_COUNT_LABEL, 8)
    # Slot 0's pointer and length copied out at fill time, so the literal-token
    # call-site inline (arg_byte(0, j)) reads them with plain rip-relative loads.
    # Appended last on purpose: earlier labels keep their image offsets, so no
    # existing hot layout shifts because of the two extra qwords.
    writable.reserve(ARGV_PTR0_LABEL, 8)
    writable.reserve(ARGV_LEN0_LABEL, 8)
    emit_print_i64(assembler)
    emit_print_char(assembler)
    emit_print_hex(assembler)
    emit_print_str(assembler)
    emit_str_len(assembler)
    emit_str_byte(assembler)
    emit_div_zero(assembler, rodata)
    emit_arg_fill(assembler)
    emit_argc(assembler)
    emit_arg_byte(assembler)
    emit_read_char(assembler)


__all__ = [
    "ARGC_LABEL", "ARG_BYTE_LABEL", "ARG_FILL_LABEL", "ARGV_ARENA_LABEL",
    "ARGV_CACHE_LABEL", "ARGV_LEN0_LABEL", "ARGV_PTR0_LABEL",
    "DIV_ZERO_DATA_LABEL", "DIV_ZERO_LABEL", "DIV_ZERO_MESSAGE",
    "NEWLINE_LABEL", "PRINT_CHAR_LABEL", "PRINT_HEX_LABEL", "PRINT_LABEL",
    "PRINT_STR_LABEL", "READ_CHAR_LABEL", "STDIN_BUFFER_LABEL", "STDIN_COUNT_LABEL",
    "STDIN_CURSOR_LABEL", "STR_BYTE_LABEL", "STR_LEN_LABEL",
    "emit_arg_fill", "emit_arg_byte", "emit_argc", "emit_div_zero", "emit_print_char",
    "emit_print_hex", "emit_print_i64", "emit_print_str", "emit_read_char",
    "emit_runtime", "emit_str_byte", "emit_str_len",
]
