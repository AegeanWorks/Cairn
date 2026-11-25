//! Emitter tests ported from `tests/test_machine.py`: exact bytes of the encodings codegen relies on.

use cairn_rs::machine::Assembler;

fn code(build: impl FnOnce(&mut Assembler)) -> Vec<u8> {
    let mut assembler = Assembler::new();
    build(&mut assembler);
    assembler.code
}

#[test]
fn frame_slot_binary_in_place() {
    assert_eq!(code(|a| a.binary_store_rbp(0x01, -8, "rax")), [0x48, 0x01, 0x45, 0xF8]);
    assert_eq!(code(|a| a.binary_store_rbp(0x29, -8, "r8")), [0x4C, 0x29, 0x45, 0xF8]);
    assert_eq!(code(|a| a.binary_store_rbp(0x01, -200, "rbx")),
               [0x48, 0x01, 0x9D, 0x38, 0xFF, 0xFF, 0xFF]);
}

#[test]
fn frame_slot_group1_immediate() {
    assert_eq!(code(|a| a.group1_store_rbp_imm(0, -8, 1)), [0x48, 0x83, 0x45, 0xF8, 0x01]);
    assert_eq!(code(|a| a.group1_store_rbp_imm(5, -16, -1)), [0x48, 0x83, 0x6D, 0xF0, 0xFF]);
    assert_eq!(code(|a| a.group1_store_rbp_imm(0, -200, 1000)),
               [0x48, 0x81, 0x85, 0x38, 0xFF, 0xFF, 0xFF, 0xE8, 0x03, 0x00, 0x00]);
}

#[test]
fn indexed_element_encodings() {
    assert_eq!(code(|a| a.mov_load_indexed("rax", "rbp", "rbx", 3, -64)),
               [0x48, 0x8B, 0x44, 0xDD, 0xC0]);
    assert_eq!(code(|a| a.mov_load_indexed("rax", "rbp", "r12", 3, -524288)),
               [0x4A, 0x8B, 0x84, 0xE5, 0x00, 0x00, 0xF8, 0xFF]);
    assert_eq!(code(|a| a.binary_store_indexed(0x01, "rbp", "rbx", 3, -64, "r12")),
               [0x4C, 0x01, 0x64, 0xDD, 0xC0]);
    assert_eq!(code(|a| a.mov_store_indexed("rbp", "r12", 3, -524288, "r12")),
               [0x4E, 0x89, 0xA4, 0xE5, 0x00, 0x00, 0xF8, 0xFF]);
    assert_eq!(code(|a| a.mov_store_indexed_imm("rbp", "rbx", 3, -64, 1)),
               [0x48, 0xC7, 0x44, 0xDD, 0xC0, 0x01, 0x00, 0x00, 0x00]);
    assert_eq!(code(|a| a.group1_store_indexed_imm(0, "rbp", "rbx", 3, -64, 7)),
               [0x48, 0x83, 0x44, 0xDD, 0xC0, 0x07]);
    assert_eq!(code(|a| a.group1_store_indexed_imm(5, "rbp", "rbx", 3, -64, 1000)),
               [0x48, 0x81, 0x6C, 0xDD, 0xC0, 0xE8, 0x03, 0x00, 0x00]);
}

#[test]
fn movzx_sib_needs_disp8_for_an_rbp_base() {
    assert_eq!(code(|a| a.movzx_load8_sib("rax", "rbx", "rcx")),
               [0x0F, 0xB6, 0x04, 0x0B]);
    assert_eq!(code(|a| a.movzx_load8_sib("rax", "rbp", "rbx")),
               [0x0F, 0xB6, 0x44, 0x1D, 0x00]);
}

#[test]
fn calls_labels_and_short_branches() {
    let bytes = code(|a| {
        a.call("helper");
        a.label("helper");
        a.jmp("helper");
        a.jcc("e", "helper");
        a.ret();
    });
    assert_eq!(&bytes[0..1], &[0xE8]);
    assert_eq!(bytes[5], 0xEB);
    assert_eq!(bytes[7], 0x74);
    assert_eq!(bytes.last(), Some(&0xC3));
}

#[test]
fn register_and_immediate_forms() {
    assert_eq!(code(|a| a.push("rax")), [0x50]);
    assert_eq!(code(|a| a.push("r8")), [0x41, 0x50]);
    assert_eq!(code(|a| a.pop("r15")), [0x41, 0x5F]);
    assert_eq!(code(|a| a.mov_reg_reg("rax", "rbx")), [0x48, 0x89, 0xD8]);
    assert_eq!(code(|a| a.mov_imm64("rax", -1)),
               [0x48, 0xB8, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]);
    assert_eq!(code(|a| a.mov_imm32("r8", 7)), [0x41, 0xB8, 0x07, 0x00, 0x00, 0x00]);
}

#[test]
fn bit_builtin_bytes() {
    assert_eq!(code(|a| a.popcnt("rax", "rbx")),
               [0xF3, 0x48, 0x0F, 0xB8, 0xC3]);
    assert_eq!(code(|a| a.lzcnt("rax", "rbx")),
               [0xF3, 0x48, 0x0F, 0xBD, 0xC3]);
    assert_eq!(code(|a| a.tzcnt("rax", "rbx")),
               [0xF3, 0x48, 0x0F, 0xBC, 0xC3]);
    assert_eq!(code(|a| a.bswap("rax")), [0x48, 0x0F, 0xC8]);
    assert_eq!(code(|a| a.bswap("r15")), [0x49, 0x0F, 0xCF]);
    assert_eq!(code(|a| a.rotate_cl(0)), [0x48, 0xD3, 0xC0]);
}

#[test]
fn nop_pad_uses_the_largest_form() {
    assert_eq!(code(|a| a.nop_pad(21)),
               [0x66, 0x0F, 0x1F, 0x84, 0x00, 0x00, 0x00, 0x00, 0x00,
                0x66, 0x0F, 0x1F, 0x84, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x1F, 0x00]);
}

#[test]
fn xmm_encodings() {
    assert_eq!(code(|a| a.movq_from_reg("xmm0", "rax")),
               [0x66, 0x48, 0x0F, 0x6E, 0xC0]);
    assert_eq!(code(|a| a.paddq_reg("xmm0", "xmm1")), [0x66, 0x0F, 0xD4, 0xC1]);
    assert_eq!(code(|a| a.movdqu_load_indexed("xmm0", "rbp", "rbx", 3, -64)),
               [0xF3, 0x40, 0x0F, 0x6F, 0x84, 0xDD, 0xC0, 0xFF, 0xFF, 0xFF]);
}

#[test]
fn zf_tracking_follows_the_emitted_instruction() {
    let mut assembler = Assembler::new();
    assembler.binary(0x01, "rax", "rbx");
    assert_eq!(assembler.zf_status.as_deref(), Some("rax"));
    assembler.mov_imm64("rcx", 1);
    assert_eq!(assembler.zf_status, None);
    assembler.arith_imm(0, "rcx", 1);
    assert_eq!(assembler.zf_status.as_deref(), Some("rcx"));
}

#[test]
#[should_panic]
fn an_immediate_beyond_32_bits_is_refused() {
    let mut assembler = Assembler::new();
    assembler.group1_store_rbp_imm(0, -8, 1i64 << 32);
}

#[test]
fn bit_count_and_rotate_encodings() {
    assert_eq!(code(|a| a.popcnt("rax", "rax")),
               [0xF3, 0x48, 0x0F, 0xB8, 0xC0]);
    assert_eq!(code(|a| a.popcnt("r13", "r13")),
               [0xF3, 0x4D, 0x0F, 0xB8, 0xED]);
    assert_eq!(code(|a| a.rotate_cl_reg("r13", 0)), [0x49, 0xD3, 0xC5]);
    assert_eq!(code(|a| a.rotate_cl_reg("rax", 0)), [0x48, 0xD3, 0xC0]);
    assert_eq!(code(|a| a.shift_cl_reg("r14", 7)), [0x49, 0xD3, 0xFE]);
}

#[test]
fn indexed_compare_against_a_literal() {
    // cmp qword [rbp+rbx*8-524288], 0 with an imm8
    assert_eq!(code(|a| a.group1_store_indexed_imm(7, "rbp", "r13", 3, -524288, 0)),
               [0x4A, 0x83, 0xBC, 0xED, 0x00, 0x00, 0xF8, 0xFF, 0x00]);
    // cmp qword [rbp+rbx*8-64], 1000 with an imm32
    assert_eq!(code(|a| a.group1_store_indexed_imm(7, "rbp", "rbx", 3, -64, 1000)),
               [0x48, 0x81, 0x7C, 0xDD, 0xC0, 0xE8, 0x03, 0x00, 0x00]);
}
