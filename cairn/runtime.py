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
PRINT_HEX_LINE_LABEL = "cairn.runtime.print_hex_line"
POPCOUNT_LABEL = "cairn.runtime.popcount"
CLZ_LABEL = "cairn.runtime.clz"
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
STDIN_HANDLE_LABEL = "cairn.runtime.stdin_handle"
STDOUT_BUFFER_LABEL = "cairn.runtime.stdout_buffer"
STDOUT_CURSOR_LABEL = "cairn.runtime.stdout_cursor"
STDOUT_HANDLE_LABEL = "cairn.runtime.stdout_handle"
OUT_FLUSH_LABEL = "cairn.runtime.out_flush"
OUT_FLUSH_WORK_LABEL = "cairn.runtime.out_flush_work"
OUT_APPEND_LABEL = "cairn.runtime.out_append"
NEWLINE_LABEL = "cairn.runtime.newline"
DIV_ZERO_DATA_LABEL = "cairn.runtime.div_zero_text"

FRAME_SIZE = 96
SHELL_FRAME = 32
READ_FRAME = 48
STR_FRAME = 48
PTR_SLOT = -48
BUFFER_TOP = -1
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
# A 64 KiB refill halves per-byte syscall overhead on long streams versus the
# historic 4096: a 4 MiB piped stdin refills 64 times instead of 1024, and the
# matching cache slot for the stdin handle rides at the image's end.
STDIN_BUFFER_SIZE = 65536
# The shared stdout buffer: every printer appends into it and one WriteFile
# drains it when it fills, on exit(code) and at normal termination, so a
# benchmark that prints one value and a newline per loop iteration costs one
# syscall per 64 KiB instead of two per value. flush() drains it early and
# answers the byte count. State rides after the stdin cache for the same
# reason ARGV_PTR0/ARGV_LEN0 do: appended last, no existing layout shifts.
STDOUT_BUFFER_SIZE = 65536


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

    The fast path answers from the static 64 KiB buffer; an empty buffer refills
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
    # The buffer drained: refill once, reset the cursor, then re-check. The
    # stdin handle is cached in static state (the loader seeds it as zero), so
    # long streams fetch it once instead of on every drained buffer.
    asm.push("rbp")
    asm.mov_reg_reg("rbp", "rsp")
    asm.sub_rsp(READ_FRAME)
    asm.lea_rip("rax", STDIN_HANDLE_LABEL)
    asm.mov_load("rax", "rax")
    asm.test("rax")
    asm.jcc("z", "read_char.handle")
    # The cached handle: every path must land on `handle_ok` with rcx carrying it.
    asm.mov_reg_reg("rcx", "rax")
    asm.jmp("read_char.handle_ok")
    asm.label("read_char.handle")
    asm.mov_imm32("rcx", -10)
    asm.call_indirect(iat_label("GetStdHandle"))
    asm.mov_reg_reg("rcx", "rax")
    asm.lea_rip("rax", STDIN_HANDLE_LABEL)
    asm.store_qword("rax", "rcx")
    asm.label("read_char.handle_ok")
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


def emit_out_flush(assembler: Assembler) -> None:
    """Emit `flush()`: drain the shared stdout buffer, answering the drained count.

    The public entry frames a 16-aligned call into the shared drain work; the
    work itself (`out_flush.work`) is also the internal entry `out_append` uses
    when the buffer fills, with rax and rdx parked on the stack across it.
    """
    asm = assembler
    asm.label(OUT_FLUSH_LABEL)
    asm.push("rbp")
    asm.mov_reg_reg("rbp", "rsp")
    asm.sub_rsp(32)
    asm.call(OUT_FLUSH_WORK_LABEL)
    asm.add_rsp(32)
    asm.pop("rbp")
    asm.ret()


def emit_out_append(assembler: Assembler) -> None:
    """Emit `out_append`: rax carries the source bytes, rdx their count.

    The append copies what fits under the 64 KiB cap, advances the cursor and
    repeats while bytes remain; when the buffer is full the pending bytes drain
    through the flush work (rax and rdx cross it on the stack). Order follows
    the call sequence because every printer steps through the same cursor.
    Clobbers rax, rcx, rdx and r8-r11 per the runtime call contract; rbx, rbp,
    rdi and rsi are untouched.
    """
    asm = assembler
    asm.label(OUT_APPEND_LABEL)
    asm.push("rbp")
    asm.mov_reg_reg("rbp", "rsp")
    asm.sub_rsp(32)
    asm.test("rdx")
    asm.jcc("z", "out_append.done")
    asm.label("out_append.top")
    asm.lea_rip("r10", STDOUT_CURSOR_LABEL)
    asm.mov_load("r9", "r10")                # the cursor
    asm.mov_imm32("rcx", STDOUT_BUFFER_SIZE)
    asm.binary(0x29, "rcx", "r9")            # the spare capacity
    asm.jcc("nz", "out_append.space")
    asm.push("rax")                          # src and len cross the drain
    asm.push("rdx")
    asm.call(OUT_FLUSH_WORK_LABEL)
    asm.pop("rdx")
    asm.pop("rax")
    asm.jmp("out_append.top")
    asm.label("out_append.space")
    asm.mov_reg_reg("r11", "rdx")
    asm.cmp_reg_reg("r11", "rcx")
    asm.jcc("le", "out_append.copy")         # everything left fits now
    asm.mov_reg_reg("r11", "rcx")            # the chunk is what fits
    asm.label("out_append.copy")
    asm.lea_rip("r8", STDOUT_BUFFER_LABEL)
    asm.lea_scale("r8", "r8", "r9", 0, 0)    # dest = buffer + cursor
    asm.mov_reg_reg("r10", "r11")            # the countdown
    asm.label("out_append.byte")
    asm.movzx_load8("rcx", "rax")
    asm.store_byte("r8", "cl")
    asm.step("rax", decrement=False)
    asm.step("r8", decrement=False)
    asm.step("r10", decrement=True)
    asm.jcc("nz", "out_append.byte")
    asm.binary(0x01, "r9", "r11")            # cursor += copied bytes
    asm.lea_rip("rcx", STDOUT_CURSOR_LABEL)
    asm.store_qword("rcx", "r9")
    asm.binary(0x29, "rdx", "r11")           # count -= copied bytes
    asm.test("rdx")
    asm.jcc("nz", "out_append.top")
    asm.label("out_append.done")
    asm.add_rsp(32)
    asm.pop("rbp")
    asm.ret()


def emit_out_flush_work(assembler: Assembler) -> None:
    """Emit `out_flush.work`: one WriteFile for everything the cursor holds.

    The stdout handle is cached in static state exactly like the stdin handle:
    the loader seeds it zero, the first drain fetches it, later drains reuse it.
    An empty buffer answers zero without a syscall. The cursor resets after the
    write (best-effort, like every plain conout path here), and the drained
    count is the answer in rax.
    """
    asm = assembler
    asm.label(OUT_FLUSH_WORK_LABEL)
    asm.push("rbp")
    asm.mov_reg_reg("rbp", "rsp")
    asm.sub_rsp(64)
    asm.lea_rip("r10", STDOUT_CURSOR_LABEL)
    asm.mov_load("r8", "r10")                # the pending byte count
    asm.lea_rip("rcx", STDOUT_HANDLE_LABEL)
    asm.mov_load("rcx", "rcx")
    asm.test("rcx")
    asm.jcc("nz", "out_flush.handle")
    asm.mov_imm32("rcx", -11)
    asm.call_indirect(iat_label("GetStdHandle"))
    asm.mov_reg_reg("rcx", "rax")
    asm.lea_rip("rax", STDOUT_HANDLE_LABEL)
    asm.store_qword("rax", "rcx")
    asm.label("out_flush.handle")
    asm.test("r8")
    asm.jcc("z", "out_flush.fresh")
    # The drained count parks in [rbp-24]: the call's shadow reaches [rbp-64,
    # rbp-32), the 5th argument rides [rsp+32] = [rbp-32], and the out slot at
    # [rbp-8] — the park stays clear of every window WriteFile may scribble.
    asm.mov_store_rbp(-24, "r8")
    asm.lea_rip("rdx", STDOUT_BUFFER_LABEL)
    asm.lea_rbp("r9", -8)                    # the bytes-written out slot
    asm.mov_store_rsp_imm32(32, 0)
    asm.call_indirect(iat_label("WriteFile"))
    asm.mov_load_rbp("rax", -24)
    asm.lea_rip("r10", STDOUT_CURSOR_LABEL)
    asm.xor_zero("rdx")
    asm.store_qword("r10", "rdx")            # the cursor resets, drained or not
    asm.jmp("out_flush.ret")
    asm.label("out_flush.fresh")
    asm.xor_zero("rax")
    asm.label("out_flush.ret")
    asm.add_rsp(64)
    asm.pop("rbp")
    asm.ret()


def emit_print_i64(assembler: Assembler) -> None:
    """Emit `print_i64`: write rax in decimal plus a newline.

    The digits are produced right-to-left in a 32-byte frame buffer and appended
    to the shared stdout buffer, so batching — not the WriteFile — is the syscall
    boundary: flush dates are decided by the buffer's 64 KiB cap, exit and the
    program's end.
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
    assembler.mov_load_rbp("rax", BUFFER_SLOT)
    assembler.mov_load_rbp("rdx", COUNT_SLOT)
    assembler.call(OUT_APPEND_LABEL)
    assembler.lea_rip("rax", NEWLINE_LABEL)
    assembler.mov_imm32("rdx", 1)
    assembler.call(OUT_APPEND_LABEL)
    assembler.add_rsp(FRAME_SIZE)
    assembler.pop("rbp")
    assembler.ret()


def emit_print_char(assembler: Assembler) -> None:
    """Emit `print_char`: write the low byte of rax to stdout, no newline.

    A division by 256 is a power of two, so the byte is the remainder `rax & 0xFF`: one
    register copy and one mask replace a slow `div r64`. The byte is appended to the shared
    stdout buffer. The clobbered `rax` never escapes: the code generator pops the caller's
    saved value after the call.
    """
    assembler.label(PRINT_CHAR_LABEL)
    assembler.push("rbp")
    assembler.mov_reg_reg("rbp", "rsp")
    assembler.sub_rsp(FRAME_SIZE)
    assembler.lea_rbp("rcx", BYTE_SLOT)
    assembler.mov_reg_reg("rdx", "rax")
    assembler.arith_imm(4, "rdx", 0xFF)       # and rdx, 255: the remainder mod 256
    assembler.store_byte("rcx", "dl")
    assembler.lea_rbp("rax", BYTE_SLOT)
    assembler.mov_imm32("rdx", 1)
    assembler.call(OUT_APPEND_LABEL)
    assembler.add_rsp(FRAME_SIZE)
    assembler.pop("rbp")
    assembler.ret()


def emit_print_hex(assembler: Assembler, label: str = PRINT_HEX_LABEL,
                   with_newline: bool = False) -> None:
    """Emit a lowercase-hex printer, with the newline riding in the same buffer on request.

    Sixteen is a power of two, so each nibble is the low four bits (`value & 15`) and the
    next value is a logical shift right by four. Eleven serial `div r64` instructions become
    eleven and/shift pairs with no divide latency, preserving the unsigned 64-bit reading.
    Each nibble is turned into ASCII by a conditional move — `+ 0x30`, or `+ 0x57` when the
    nibble cleared 10 — and appended to the shared stdout buffer. Zero prints as a single `0`.

    The `print_hex_line` twin stores one more byte just below the digits and makes a single
    `out_append` call, so `print_hex(x); print_char(10)` costs one append instead of two.
    """
    digits_label = "print_hex_line.digits" if with_newline else "print_hex.digits"
    assembler.label(label)
    assembler.push("rbp")
    assembler.mov_reg_reg("rbp", "rsp")
    assembler.sub_rsp(FRAME_SIZE)
    assembler.lea_rbp("rcx", BUFFER_TOP)
    assembler.xor_zero("r11")
    assembler.label(digits_label)
    assembler.mov_reg_reg("rdx", "rax")
    assembler.arith_imm(4, "rdx", 15)         # and rdx, 15: the low nibble
    assembler.shift_imm(5, "rax", 4)          # shr rax, 4: the unsigned quotient
    assembler.mov_reg_reg("r9", "rdx")
    assembler.add_imm8("dl", 0x30)
    assembler.add_imm8("r9b", 0x57)
    assembler.cmp_imm("rdx", 58)
    assembler.cmov("ge", "rdx", "r9")
    assembler.step("rcx", decrement=True)
    assembler.store_byte("rcx", "dl")
    assembler.step("r11", decrement=False)
    assembler.test("rax")
    assembler.jcc("nz", digits_label)
    if with_newline:
        # Digits were written downward from BUFFER_TOP, so the byte just above the
        # most significant digit is BUFFER_TOP itself: the newline trails the value.
        assembler.lea_rbp("r8", BUFFER_TOP)
        assembler.mov_imm32("r9", 0x0A)
        assembler.store_byte("r8", "r9b")
        assembler.step("r11", decrement=False)
    assembler.mov_reg_reg("rax", "rcx")
    assembler.mov_reg_reg("rdx", "r11")
    assembler.call(OUT_APPEND_LABEL)
    assembler.add_rsp(FRAME_SIZE)
    assembler.pop("rbp")
    assembler.ret()


def emit_popcount(assembler: Assembler) -> None:
    """Emit `popcount`: a portable SWAR count of the set bits of unsigned rax.

    Five mask-and-shift stages fold the 64 bits into eight byte lanes, then one
    multiply by 0x0101...01 sums those lanes into the high byte. No CPU feature
    check is needed; a POPCNT fast path is the recorded speed follow-up. The
    helper clobbers rdx and r10, both inside the runtime call contract.
    """
    assembler.label(POPCOUNT_LABEL)
    assembler.popcnt("rax", "rax")
    assembler.ret()


def emit_clz(assembler: Assembler) -> None:
    """Emit `clz` as one LZCNT instruction: leading zeros of unsigned rax.

    LZCNT returns 64 for a zero input and is part of ABM, like POPCNT (ADR-108).
    """
    assembler.label(CLZ_LABEL)
    assembler.lzcnt("rax", "rax")
    assembler.ret()


def emit_print_str(assembler: Assembler) -> None:
    """Emit `print_str`: write the NUL-terminated text at rax to stdout, no newline.

    The length comes from one scan to the terminator, so no byte is written past the
    literal's end and the scan costs no call — the append copies those bytes into
    the shared stdout buffer. The pointer returns in rax, kept in a frame slot
    across the call, so `print_str(s)` still answers `s` as a value.
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
    assembler.mov_reg_reg("rdx", "r8")
    assembler.call(OUT_APPEND_LABEL)
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
    # existing hot layout shifts because of the two extra qwords. The stdin
    # handle cache stays after them for the same reason.
    writable.reserve(ARGV_PTR0_LABEL, 8)
    writable.reserve(ARGV_LEN0_LABEL, 8)
    writable.reserve(STDIN_HANDLE_LABEL, 8)
    # The shared stdout buffer, its cursor and the cached console handle, in
    # that order: appended after the stdin and argv state so existing labels
    # keep their image offsets and no hot layout shifts because of the batcher.
    writable.reserve(STDOUT_BUFFER_LABEL, STDOUT_BUFFER_SIZE)
    writable.reserve(STDOUT_CURSOR_LABEL, 8)
    writable.reserve(STDOUT_HANDLE_LABEL, 8)
    emit_out_flush(assembler)
    emit_out_append(assembler)
    emit_out_flush_work(assembler)
    emit_print_i64(assembler)
    emit_print_char(assembler)
    emit_print_hex(assembler)
    emit_print_hex(assembler, PRINT_HEX_LINE_LABEL, with_newline=True)
    emit_print_str(assembler)
    emit_popcount(assembler)
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
    "CLZ_LABEL", "NEWLINE_LABEL", "POPCOUNT_LABEL", "PRINT_CHAR_LABEL", "PRINT_HEX_LABEL",
    "PRINT_HEX_LINE_LABEL", "PRINT_LABEL",
    "PRINT_STR_LABEL", "READ_CHAR_LABEL", "STDIN_BUFFER_LABEL", "STDIN_COUNT_LABEL",
    "STDIN_CURSOR_LABEL", "STR_BYTE_LABEL", "STR_LEN_LABEL",
    "OUT_APPEND_LABEL", "OUT_FLUSH_LABEL", "OUT_FLUSH_WORK_LABEL",
    "STDOUT_BUFFER_LABEL", "STDOUT_CURSOR_LABEL", "STDOUT_HANDLE_LABEL",
    "STDOUT_BUFFER_SIZE",
    "emit_arg_fill", "emit_arg_byte", "emit_argc", "emit_div_zero",
    "emit_out_append", "emit_out_flush", "emit_out_flush_work",
    "emit_clz", "emit_popcount", "emit_print_char", "emit_print_hex", "emit_print_i64", "emit_print_str",
    "emit_read_char", "emit_runtime", "emit_str_byte", "emit_str_len",
]
