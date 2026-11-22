//! Runtime helpers emitted into every executable.
//!
//! There is no runtime library to link: the printers, input builtins and divide trap are machine
//! code written here into the same `.text` section as the user's functions.

use crate::image::{iat_label, ReadOnlyData, WritableData};
use crate::machine::Assembler;

/// The divide-by-zero message written to stderr before exiting with code 3.
pub const DIV_ZERO_MESSAGE: &[u8] = b"cairn: runtime error: divide by zero\n";

pub const PRINT_LABEL: &str = "cairn.runtime.print_i64";
pub const PRINT_CHAR_LABEL: &str = "cairn.runtime.print_char";
pub const PRINT_HEX_LABEL: &str = "cairn.runtime.print_hex";
pub const PRINT_HEX_LINE_LABEL: &str = "cairn.runtime.print_hex_line";
pub const POPCOUNT_LABEL: &str = "cairn.runtime.popcount";
pub const CLZ_LABEL: &str = "cairn.runtime.clz";
pub const DIV_ZERO_LABEL: &str = "cairn.runtime.div_zero";
pub const ARGC_LABEL: &str = "cairn.runtime.argc";
pub const ARG_BYTE_LABEL: &str = "cairn.runtime.arg_byte";
pub const READ_CHAR_LABEL: &str = "cairn.runtime.read_char";
pub const PRINT_STR_LABEL: &str = "cairn.runtime.print_str";
pub const STR_LEN_LABEL: &str = "cairn.runtime.str_len";
pub const STR_BYTE_LABEL: &str = "cairn.runtime.str_byte";
pub const ARG_FILL_LABEL: &str = "cairn.runtime.arg_fill";
pub const ARGV_CACHE_LABEL: &str = "cairn.runtime.argv_cache";
pub const ARGV_ARENA_LABEL: &str = "cairn.runtime.argv_arena";
pub const ARGV_PTR0_LABEL: &str = "cairn.runtime.argv_ptr0";
pub const ARGV_LEN0_LABEL: &str = "cairn.runtime.argv_len0";
pub const STDIN_BUFFER_LABEL: &str = "cairn.runtime.stdin_buffer";
pub const STDIN_CURSOR_LABEL: &str = "cairn.runtime.stdin_cursor";
pub const STDIN_COUNT_LABEL: &str = "cairn.runtime.stdin_count";
pub const STDIN_HANDLE_LABEL: &str = "cairn.runtime.stdin_handle";
pub const STDOUT_BUFFER_LABEL: &str = "cairn.runtime.stdout_buffer";
pub const STDOUT_CURSOR_LABEL: &str = "cairn.runtime.stdout_cursor";
pub const STDOUT_HANDLE_LABEL: &str = "cairn.runtime.stdout_handle";
pub const OUT_FLUSH_LABEL: &str = "cairn.runtime.out_flush";
pub const OUT_FLUSH_WORK_LABEL: &str = "cairn.runtime.out_flush_work";
pub const OUT_APPEND_LABEL: &str = "cairn.runtime.out_append";
pub const NEWLINE_LABEL: &str = "cairn.runtime.newline";
pub const DIV_ZERO_DATA_LABEL: &str = "cairn.runtime.div_zero_text";

pub const FRAME_SIZE: i64 = 96;
pub const SHELL_FRAME: i64 = 32;
pub const READ_FRAME: i64 = 48;
pub const STR_FRAME: i64 = 48;
pub const PTR_SLOT: i64 = -48;
pub const BUFFER_TOP: i64 = -1;
pub const BUFFER_SLOT: i64 = -48;
pub const COUNT_SLOT: i64 = -56;
pub const BYTE_SLOT: i64 = -8;

pub const MAX_ARGS: i64 = 63;
pub const ARGV_CACHE_SIZE: usize = 16 + 16 * 63;
pub const ARGV_ARENA_SIZE: i64 = 1024;
pub const STDIN_BUFFER_SIZE: i64 = 65536;
pub const STDOUT_BUFFER_SIZE: i64 = 65536;

/// Emit `arg_fill`: tokenize the ANSI command line into the static table once.
pub fn emit_arg_fill(asm: &mut Assembler) {
    asm.label(ARG_FILL_LABEL);
    asm.push("rbp");
    asm.mov_reg_reg("rbp", "rsp");
    asm.sub_rsp(SHELL_FRAME);
    asm.lea_rip("r10", ARGV_CACHE_LABEL);
    asm.mov_load("rax", "r10");
    asm.test("rax");
    asm.jcc("nz", "arg_fill.done");
    let get_line = iat_label("GetCommandLineA");
    asm.call_indirect(&get_line);
    asm.push("rbx");
    asm.mov_reg_reg("rcx", "rax");
    asm.lea_rip("rbx", ARGV_ARENA_LABEL);
    asm.lea_rip("r10", ARGV_CACHE_LABEL);
    asm.lea_imm("r9", "r10", 8);
    asm.store_qword("r9", "rax");
    asm.mov_reg_reg("rdx", "rbx");
    asm.xor_zero("r11");
    asm.xor_zero("r9");
    asm.label("arg_fill.token");
    asm.label("arg_fill.skip");
    asm.movzx_load8("rax", "rcx");
    asm.cmp_imm("rax", 32);
    asm.jcc("e", "arg_fill.whitespace");
    asm.cmp_imm("rax", 9);
    asm.jcc("ne", "arg_fill.start");
    asm.label("arg_fill.whitespace");
    asm.add_imm("rcx", 1);
    asm.jmp("arg_fill.skip");
    asm.label("arg_fill.start");
    asm.movzx_load8("rax", "rcx");
    asm.test("rax");
    asm.jcc("z", "arg_fill.finish");
    asm.mov_reg_reg("r8", "rdx");
    asm.label("arg_fill.scan");
    asm.movzx_load8("rax", "rcx");
    asm.test("rax");
    asm.jcc("z", "arg_fill.store");
    asm.cmp_imm("rax", 34);
    asm.jcc("e", "arg_fill.quote");
    asm.test("r9");
    asm.jcc("nz", "arg_fill.copy");
    asm.cmp_imm("rax", 32);
    asm.jcc("e", "arg_fill.store");
    asm.cmp_imm("rax", 9);
    asm.jcc("e", "arg_fill.store");
    asm.label("arg_fill.copy");
    asm.lea_imm("rax", "rdx", -ARGV_ARENA_SIZE);
    asm.cmp_reg_reg("rax", "rbx");
    asm.jcc("ge", "arg_fill.step");
    asm.movzx_load8("rax", "rcx");
    asm.store_byte("rdx", "al");
    asm.add_imm("rdx", 1);
    asm.label("arg_fill.step");
    asm.add_imm("rcx", 1);
    asm.jmp("arg_fill.scan");
    asm.label("arg_fill.quote");
    asm.arith_imm(6, "r9", 1);
    asm.jmp("arg_fill.step");
    asm.label("arg_fill.store");
    asm.mov_reg_reg("rax", "rdx");
    asm.binary(0x29, "rax", "r8");
    asm.cmp_imm("r11", MAX_ARGS);
    asm.jcc("ge", "arg_fill.counted");
    asm.mov_reg_reg("rdx", "rax");
    asm.mov_reg_reg("rax", "r11");
    asm.shift_imm(4, "rax", 4);
    asm.lea_scale("r9", "r10", "rax", 0, 16);
    asm.store_qword("r9", "r8");
    asm.lea_imm("r9", "r9", 8);
    asm.store_qword("r9", "rdx");
    asm.binary(0x01, "rdx", "r8");
    asm.label("arg_fill.counted");
    asm.xor_zero("r9");
    asm.add_imm("r11", 1);
    asm.mov_reg_reg("r8", "rdx");
    asm.movzx_load8("rax", "rcx");
    asm.test("rax");
    asm.jcc("z", "arg_fill.finish");
    asm.add_imm("rcx", 1);
    asm.jmp("arg_fill.token");
    asm.label("arg_fill.finish");
    asm.mov_imm64("rax", 1);
    asm.store_qword("r10", "rax");
    asm.lea_imm("r9", "r10", 8);
    asm.store_qword("r9", "r11");
    asm.lea_imm("r9", "r10", 16);
    asm.mov_load("rax", "r9");
    asm.lea_rip("r8", ARGV_PTR0_LABEL);
    asm.store_qword("r8", "rax");
    asm.lea_imm("r9", "r9", 8);
    asm.mov_load("rax", "r9");
    asm.lea_rip("r8", ARGV_LEN0_LABEL);
    asm.store_qword("r8", "rax");
    asm.pop("rbx");
    asm.label("arg_fill.done");
    asm.add_rsp(SHELL_FRAME);
    asm.pop("rbp");
    asm.ret();
}

/// Emit `argc()`: the token count, filling and caching the table on the first call.
pub fn emit_argc(asm: &mut Assembler) {
    asm.label(ARGC_LABEL);
    asm.push("rbp");
    asm.mov_reg_reg("rbp", "rsp");
    asm.sub_rsp(SHELL_FRAME);
    asm.lea_rip("r10", ARGV_CACHE_LABEL);
    asm.mov_load("rax", "r10");
    asm.test("rax");
    asm.jcc("nz", "argc.ready");
    asm.call(ARG_FILL_LABEL);
    asm.lea_rip("r10", ARGV_CACHE_LABEL);
    asm.label("argc.ready");
    asm.lea_imm("r10", "r10", 8);
    asm.mov_load("rax", "r10");
    asm.add_rsp(SHELL_FRAME);
    asm.pop("rbp");
    asm.ret();
}

/// Emit `arg_byte(i, j)`: byte j of argument i's content, or -1.
pub fn emit_arg_byte(asm: &mut Assembler) {
    asm.label(ARG_BYTE_LABEL);
    asm.push("rbp");
    asm.mov_reg_reg("rbp", "rsp");
    asm.sub_rsp(SHELL_FRAME);
    asm.lea_rip("r10", ARGV_CACHE_LABEL);
    asm.mov_load("rax", "r10");
    asm.test("rax");
    asm.jcc("nz", "arg_byte.ready");
    asm.test("rcx");
    asm.jcc("s", "arg_byte.miss");
    asm.test("rdx");
    asm.jcc("s", "arg_byte.miss");
    asm.push("rcx");
    asm.push("rdx");
    asm.call(ARG_FILL_LABEL);
    asm.pop("rdx");
    asm.pop("rcx");
    asm.lea_rip("r10", ARGV_CACHE_LABEL);
    asm.label("arg_byte.ready");
    asm.lea_imm("r9", "r10", 8);
    asm.mov_load("rax", "r9");
    asm.cmp_reg_reg("rax", "rcx");
    asm.jcc("le", "arg_byte.miss");
    asm.cmp_imm("rcx", MAX_ARGS);
    asm.jcc("ge", "arg_byte.miss");
    asm.mov_reg_reg("rax", "rcx");
    asm.shift_imm(4, "rax", 4);
    asm.lea_scale("r9", "r10", "rax", 0, 16);
    asm.mov_load("r8", "r9");
    asm.lea_imm("r9", "r9", 8);
    asm.mov_load("rax", "r9");
    asm.test("r8");
    asm.jcc("z", "arg_byte.miss");
    asm.test("rdx");
    asm.jcc("s", "arg_byte.miss");
    asm.cmp_reg_reg("rax", "rdx");
    asm.jcc("le", "arg_byte.miss");
    asm.lea_scale("r9", "r8", "rdx", 0, 0);
    asm.movzx_load8("rax", "r9");
    asm.add_rsp(SHELL_FRAME);
    asm.pop("rbp");
    asm.ret();
    asm.label("arg_byte.miss");
    asm.mov_imm64("rax", -1);
    asm.add_rsp(SHELL_FRAME);
    asm.pop("rbp");
    asm.ret();
}

/// Emit `read_char()`: the next stdin byte, or -1 at end of stream.
pub fn emit_read_char(asm: &mut Assembler) {
    asm.label(READ_CHAR_LABEL);
    asm.lea_rip("r10", STDIN_CURSOR_LABEL);
    asm.mov_load("rax", "r10");
    asm.lea_rip("r11", STDIN_COUNT_LABEL);
    asm.mov_load("rdx", "r11");
    asm.cmp_reg_reg("rdx", "rax");
    asm.jcc("g", "read_char.have");
    asm.push("rbp");
    asm.mov_reg_reg("rbp", "rsp");
    asm.sub_rsp(READ_FRAME);
    asm.lea_rip("rax", STDIN_HANDLE_LABEL);
    asm.mov_load("rax", "rax");
    asm.test("rax");
    asm.jcc("z", "read_char.handle");
    asm.mov_reg_reg("rcx", "rax");
    asm.jmp("read_char.handle_ok");
    asm.label("read_char.handle");
    asm.mov_imm32("rcx", -10);
    let get_handle = iat_label("GetStdHandle");
    asm.call_indirect(&get_handle);
    asm.mov_reg_reg("rcx", "rax");
    asm.lea_rip("rax", STDIN_HANDLE_LABEL);
    asm.store_qword("rax", "rcx");
    asm.label("read_char.handle_ok");
    asm.lea_rip("rdx", STDIN_BUFFER_LABEL);
    asm.mov_imm32("r8", STDIN_BUFFER_SIZE);
    asm.lea_rip("r9", STDIN_COUNT_LABEL);
    asm.mov_store_rsp_imm32(32, 0);
    let read_file = iat_label("ReadFile");
    asm.call_indirect(&read_file);
    asm.xor_zero("rax");
    asm.lea_rip("r10", STDIN_CURSOR_LABEL);
    asm.store_qword("r10", "rax");
    asm.add_rsp(READ_FRAME);
    asm.pop("rbp");
    asm.lea_rip("r10", STDIN_CURSOR_LABEL);
    asm.mov_load("rax", "r10");
    asm.lea_rip("r11", STDIN_COUNT_LABEL);
    asm.mov_load("rdx", "r11");
    asm.cmp_reg_reg("rdx", "rax");
    asm.jcc("le", "read_char.empty");
    asm.label("read_char.have");
    asm.lea_rip("r9", STDIN_BUFFER_LABEL);
    asm.lea_scale("r8", "r9", "rax", 0, 0);
    asm.add_imm("rax", 1);
    asm.store_qword("r10", "rax");
    asm.movzx_load8("rax", "r8");
    asm.ret();
    asm.label("read_char.empty");
    asm.mov_imm64("rax", -1);
    asm.ret();
}

/// Emit `flush()`: drain the shared stdout buffer, answering the drained count.
pub fn emit_out_flush(asm: &mut Assembler) {
    asm.label(OUT_FLUSH_LABEL);
    asm.push("rbp");
    asm.mov_reg_reg("rbp", "rsp");
    asm.sub_rsp(32);
    asm.call(OUT_FLUSH_WORK_LABEL);
    asm.add_rsp(32);
    asm.pop("rbp");
    asm.ret();
}

/// Emit `out_append`: rax carries the source bytes, rdx their count.
pub fn emit_out_append(asm: &mut Assembler) {
    asm.label(OUT_APPEND_LABEL);
    asm.push("rbp");
    asm.mov_reg_reg("rbp", "rsp");
    asm.sub_rsp(32);
    asm.test("rdx");
    asm.jcc("z", "out_append.done");
    asm.label("out_append.top");
    asm.lea_rip("r10", STDOUT_CURSOR_LABEL);
    asm.mov_load("r9", "r10");
    asm.mov_imm32("rcx", STDOUT_BUFFER_SIZE);
    asm.binary(0x29, "rcx", "r9");
    asm.jcc("nz", "out_append.space");
    asm.push("rax");
    asm.push("rdx");
    asm.call(OUT_FLUSH_WORK_LABEL);
    asm.pop("rdx");
    asm.pop("rax");
    asm.jmp("out_append.top");
    asm.label("out_append.space");
    asm.mov_reg_reg("r11", "rdx");
    asm.cmp_reg_reg("r11", "rcx");
    asm.jcc("le", "out_append.copy");
    asm.mov_reg_reg("r11", "rcx");
    asm.label("out_append.copy");
    asm.lea_rip("r8", STDOUT_BUFFER_LABEL);
    asm.lea_scale("r8", "r8", "r9", 0, 0);
    asm.mov_reg_reg("r10", "r11");
    asm.label("out_append.byte");
    asm.movzx_load8("rcx", "rax");
    asm.store_byte("r8", "cl");
    asm.step("rax", false);
    asm.step("r8", false);
    asm.step("r10", true);
    asm.jcc("nz", "out_append.byte");
    asm.binary(0x01, "r9", "r11");
    asm.lea_rip("rcx", STDOUT_CURSOR_LABEL);
    asm.store_qword("rcx", "r9");
    asm.binary(0x29, "rdx", "r11");
    asm.test("rdx");
    asm.jcc("nz", "out_append.top");
    asm.label("out_append.done");
    asm.add_rsp(32);
    asm.pop("rbp");
    asm.ret();
}

/// Emit `out_flush.work`: one WriteFile for everything the cursor holds.
pub fn emit_out_flush_work(asm: &mut Assembler) {
    asm.label(OUT_FLUSH_WORK_LABEL);
    asm.push("rbp");
    asm.mov_reg_reg("rbp", "rsp");
    asm.sub_rsp(64);
    asm.lea_rip("r10", STDOUT_CURSOR_LABEL);
    asm.mov_load("r8", "r10");
    asm.lea_rip("rcx", STDOUT_HANDLE_LABEL);
    asm.mov_load("rcx", "rcx");
    asm.test("rcx");
    asm.jcc("nz", "out_flush.handle");
    asm.mov_imm32("rcx", -11);
    let get_handle = iat_label("GetStdHandle");
    asm.call_indirect(&get_handle);
    asm.mov_reg_reg("rcx", "rax");
    asm.lea_rip("rax", STDOUT_HANDLE_LABEL);
    asm.store_qword("rax", "rcx");
    asm.label("out_flush.handle");
    asm.test("r8");
    asm.jcc("z", "out_flush.fresh");
    asm.mov_store_rbp(-24, "r8");
    asm.lea_rip("rdx", STDOUT_BUFFER_LABEL);
    asm.lea_rbp("r9", -8);
    asm.mov_store_rsp_imm32(32, 0);
    let write_file = iat_label("WriteFile");
    asm.call_indirect(&write_file);
    asm.mov_load_rbp("rax", -24);
    asm.lea_rip("r10", STDOUT_CURSOR_LABEL);
    asm.xor_zero("rdx");
    asm.store_qword("r10", "rdx");
    asm.jmp("out_flush.ret");
    asm.label("out_flush.fresh");
    asm.xor_zero("rax");
    asm.label("out_flush.ret");
    asm.add_rsp(64);
    asm.pop("rbp");
    asm.ret();
}

/// Emit `print_i64`: write rax in decimal plus a newline.
pub fn emit_print_i64(asm: &mut Assembler) {
    asm.label(PRINT_LABEL);
    asm.push("rbp");
    asm.mov_reg_reg("rbp", "rsp");
    asm.sub_rsp(FRAME_SIZE);
    asm.lea_rbp("rcx", BUFFER_TOP);
    asm.mov_imm32("r10", 10);
    asm.xor_zero("r11");
    asm.xor_zero("r9");
    asm.test("rax");
    asm.jcc("ns", "print.digits");
    asm.mov_imm32("r9", 1);
    asm.unary(3, "rax");
    asm.label("print.digits");
    asm.xor_zero("rdx");
    asm.divide("r10", false);
    asm.add_imm8("dl", 0x30);
    asm.step("rcx", true);
    asm.store_byte("rcx", "dl");
    asm.step("r11", false);
    asm.test("rax");
    asm.jcc("nz", "print.digits");
    asm.test("r9");
    asm.jcc("z", "print.write");
    asm.step("rcx", true);
    asm.store_byte_imm("rcx", 0x2D);
    asm.step("r11", false);
    asm.label("print.write");
    asm.mov_store_rbp(BUFFER_SLOT, "rcx");
    asm.mov_store_rbp(COUNT_SLOT, "r11");
    asm.mov_load_rbp("rax", BUFFER_SLOT);
    asm.mov_load_rbp("rdx", COUNT_SLOT);
    asm.call(OUT_APPEND_LABEL);
    asm.lea_rip("rax", NEWLINE_LABEL);
    asm.mov_imm32("rdx", 1);
    asm.call(OUT_APPEND_LABEL);
    asm.add_rsp(FRAME_SIZE);
    asm.pop("rbp");
    asm.ret();
}

/// Emit `print_char`: write the low byte of rax to stdout, no newline.
pub fn emit_print_char(asm: &mut Assembler) {
    asm.label(PRINT_CHAR_LABEL);
    asm.push("rbp");
    asm.mov_reg_reg("rbp", "rsp");
    asm.sub_rsp(FRAME_SIZE);
    asm.lea_rbp("rcx", BYTE_SLOT);
    asm.mov_reg_reg("rdx", "rax");
    asm.arith_imm(4, "rdx", 0xFF);
    asm.store_byte("rcx", "dl");
    asm.lea_rbp("rax", BYTE_SLOT);
    asm.mov_imm32("rdx", 1);
    asm.call(OUT_APPEND_LABEL);
    asm.add_rsp(FRAME_SIZE);
    asm.pop("rbp");
    asm.ret();
}

/// Emit a lowercase-hex printer, with the newline riding in the same buffer on request.
pub fn emit_print_hex(asm: &mut Assembler, label: &str, with_newline: bool) {
    let digits_label = if with_newline { "print_hex_line.digits" } else { "print_hex.digits" };
    asm.label(label);
    asm.push("rbp");
    asm.mov_reg_reg("rbp", "rsp");
    asm.sub_rsp(FRAME_SIZE);
    asm.lea_rbp("rcx", BUFFER_TOP);
    asm.xor_zero("r11");
    asm.label(digits_label);
    asm.mov_reg_reg("rdx", "rax");
    asm.arith_imm(4, "rdx", 15);
    asm.shift_imm(5, "rax", 4);
    asm.mov_reg_reg("r9", "rdx");
    asm.add_imm8("dl", 0x30);
    asm.add_imm8("r9b", 0x57);
    asm.cmp_imm("rdx", 58);
    asm.cmov("ge", "rdx", "r9");
    asm.step("rcx", true);
    asm.store_byte("rcx", "dl");
    asm.step("r11", false);
    asm.test("rax");
    asm.jcc("nz", digits_label);
    if with_newline {
        asm.lea_rbp("r8", BUFFER_TOP);
        asm.mov_imm32("r9", 0x0A);
        asm.store_byte("r8", "r9b");
        asm.step("r11", false);
    }
    asm.mov_reg_reg("rax", "rcx");
    asm.mov_reg_reg("rdx", "r11");
    asm.call(OUT_APPEND_LABEL);
    asm.add_rsp(FRAME_SIZE);
    asm.pop("rbp");
    asm.ret();
}

/// Emit `popcount`: one POPCNT instruction.
pub fn emit_popcount(asm: &mut Assembler) {
    asm.label(POPCOUNT_LABEL);
    asm.popcnt("rax", "rax");
    asm.ret();
}

/// Emit `clz`: one LZCNT instruction.
pub fn emit_clz(asm: &mut Assembler) {
    asm.label(CLZ_LABEL);
    asm.lzcnt("rax", "rax");
    asm.ret();
}

/// Emit `print_str`: write the NUL-terminated text at rax to stdout, no newline.
pub fn emit_print_str(asm: &mut Assembler) {
    asm.label(PRINT_STR_LABEL);
    asm.push("rbp");
    asm.mov_reg_reg("rbp", "rsp");
    asm.sub_rsp(STR_FRAME);
    asm.mov_store_rbp(PTR_SLOT, "rax");
    asm.mov_reg_reg("rcx", "rax");
    asm.xor_zero("r8");
    asm.label("print_str.scan");
    asm.movzx_load8("rdx", "rcx");
    asm.test("rdx");
    asm.jcc("z", "print_str.write");
    asm.step("rcx", false);
    asm.step("r8", false);
    asm.jmp("print_str.scan");
    asm.label("print_str.write");
    asm.mov_reg_reg("rdx", "r8");
    asm.call(OUT_APPEND_LABEL);
    asm.mov_load_rbp("rax", PTR_SLOT);
    asm.add_rsp(STR_FRAME);
    asm.pop("rbp");
    asm.ret();
}

/// Emit `str_len`: the number of bytes in the NUL-terminated text at rax.
pub fn emit_str_len(asm: &mut Assembler) {
    asm.label(STR_LEN_LABEL);
    asm.mov_reg_reg("rcx", "rax");
    asm.xor_zero("rdx");
    asm.label("str_len.scan");
    asm.movzx_load8("r8", "rcx");
    asm.test("r8");
    asm.jcc("z", "str_len.done");
    asm.step("rcx", false);
    asm.step("rdx", false);
    asm.jmp("str_len.scan");
    asm.label("str_len.done");
    asm.mov_reg_reg("rax", "rdx");
    asm.ret();
}

/// Emit `str_byte(s, j)`: byte j of the text at rcx, or -1.
pub fn emit_str_byte(asm: &mut Assembler) {
    asm.label(STR_BYTE_LABEL);
    asm.test("rdx");
    asm.jcc("s", "str_byte.miss");
    asm.lea_scale("r8", "rcx", "rdx", 0, 0);
    asm.movzx_load8("rax", "r8");
    asm.test("rax");
    asm.jcc("z", "str_byte.miss");
    asm.ret();
    asm.label("str_byte.miss");
    asm.mov_imm64("rax", -1);
    asm.ret();
}

/// Emit the trap taken when a divisor is zero: message on stderr, exit code 3.
pub fn emit_div_zero(asm: &mut Assembler) {
    asm.label(DIV_ZERO_LABEL);
    asm.push("rbp");
    asm.mov_reg_reg("rbp", "rsp");
    asm.align_rsp();
    asm.sub_rsp(48);
    asm.mov_imm32("rcx", -12);
    let get_handle = iat_label("GetStdHandle");
    asm.call_indirect(&get_handle);
    asm.mov_reg_reg("rcx", "rax");
    asm.lea_rip("rdx", DIV_ZERO_DATA_LABEL);
    asm.mov_imm32("r8", DIV_ZERO_MESSAGE.len() as i64);
    asm.lea_rbp("r9", -8);
    asm.mov_store_rsp_imm32(32, 0);
    let write_file = iat_label("WriteFile");
    asm.call_indirect(&write_file);
    asm.mov_imm32("rcx", 3);
    let exit_process = iat_label("ExitProcess");
    asm.call_indirect(&exit_process);
    asm.hlt();
}

/// Emit every helper routine and the data they reference, in a stable order.
pub fn emit_runtime(asm: &mut Assembler, rodata: &mut ReadOnlyData, writable: &mut WritableData) {
    rodata.add(NEWLINE_LABEL, b"\n");
    rodata.add(DIV_ZERO_DATA_LABEL, DIV_ZERO_MESSAGE);
    writable.reserve(ARGV_CACHE_LABEL, ARGV_CACHE_SIZE);
    writable.reserve(ARGV_ARENA_LABEL, ARGV_ARENA_SIZE as usize);
    writable.reserve(STDIN_BUFFER_LABEL, STDIN_BUFFER_SIZE as usize);
    writable.reserve(STDIN_CURSOR_LABEL, 8);
    writable.reserve(STDIN_COUNT_LABEL, 8);
    writable.reserve(ARGV_PTR0_LABEL, 8);
    writable.reserve(ARGV_LEN0_LABEL, 8);
    writable.reserve(STDIN_HANDLE_LABEL, 8);
    writable.reserve(STDOUT_BUFFER_LABEL, STDOUT_BUFFER_SIZE as usize);
    writable.reserve(STDOUT_CURSOR_LABEL, 8);
    writable.reserve(STDOUT_HANDLE_LABEL, 8);
    emit_out_flush(asm);
    emit_out_append(asm);
    emit_out_flush_work(asm);
    emit_print_i64(asm);
    emit_print_char(asm);
    emit_print_hex(asm, PRINT_HEX_LABEL, false);
    emit_print_hex(asm, PRINT_HEX_LINE_LABEL, true);
    emit_print_str(asm);
    emit_popcount(asm);
    emit_str_len(asm);
    emit_str_byte(asm);
    emit_div_zero(asm);
    emit_arg_fill(asm);
    emit_argc(asm);
    emit_arg_byte(asm);
    emit_read_char(asm);
}

/// A convenience: emit all helpers into a fresh assembler with fresh data blobs.
pub fn emit_all() -> (Assembler, ReadOnlyData, WritableData) {
    let mut asm = Assembler::new();
    let mut rodata = ReadOnlyData::default();
    let mut writable = WritableData::default();
    emit_runtime(&mut asm, &mut rodata, &mut writable);
    (asm, rodata, writable)
}
