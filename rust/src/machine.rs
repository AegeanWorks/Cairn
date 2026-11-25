//! x86-64 emitter: the instruction subset Cairn lowers to, written as bytes.
//!
//! Every method is a fixed byte pattern an agent can verify by eye. Labels and 32-bit relative
//! fixups are resolved later, when section addresses are known.

use std::collections::BTreeMap;

/// General-purpose register codes.
pub fn register(name: &str) -> u8 {
    match name {
        "rax" => 0, "rcx" => 1, "rdx" => 2, "rbx" => 3,
        "rsp" => 4, "rbp" => 5, "rsi" => 6, "rdi" => 7,
        "r8" => 8, "r9" => 9, "r10" => 10, "r11" => 11,
        "r12" => 12, "r13" => 13, "r14" => 14, "r15" => 15,
        other => panic!("unknown register {}", other),
    }
}

/// The accessible 8-bit sub-registers.
pub fn eight_bit(name: &str) -> u8 {
    match name {
        "al" => 0, "cl" => 1, "dl" => 2, "bl" => 3,
        "spl" => 4, "bpl" => 5, "sil" => 6, "dil" => 7,
        "r8b" => 8, "r9b" => 9, "r10b" => 10, "r11b" => 11,
        other => panic!("unknown 8-bit register {}", other),
    }
}

/// The SSE2 registers the array vectorizer uses.
pub fn xmm_register(name: &str) -> u8 {
    match name {
        "xmm0" => 0, "xmm1" => 1, "xmm2" => 2, "xmm3" => 3,
        other => panic!("unknown xmm register {}", other),
    }
}

/// The four Windows x64 argument registers.
pub const ARGUMENT_REGISTERS: &[&str] = &["rcx", "rdx", "r8", "r9"];

/// Group-1 immediate extensions whose result sets ZF = (register == 0).
pub const ZF_IMM_EXTENSIONS: &[u8] = &[0, 1, 4, 5, 6];

/// Condition codes for short and near jumps.
pub fn condition(name: &str) -> u8 {
    match name {
        "e" | "z" => 0x4, "ne" | "nz" => 0x5, "l" => 0xC, "le" => 0xE,
        "g" => 0xF, "ge" => 0xD, "s" => 0x8, "ns" => 0x9,
        "c" | "b" => 0x2, "nc" | "ae" => 0x3, "a" => 0x7, "be" => 0x6,
        other => panic!("unknown condition {}", other),
    }
}

/// Condition codes for conditional moves.
pub fn cmov_condition(name: &str) -> u8 {
    match name {
        "e" => 0x40, "ne" => 0x45, "l" => 0x4C, "le" => 0x4E,
        "g" => 0x4F, "ge" => 0x4D, "s" => 0x48, "ns" => 0x49,
        "c" => 0x42, "nc" => 0x43,
        other => panic!("unknown cmov condition {}", other),
    }
}

fn rex(w: bool, r: u8, x: u8, b: u8) -> u8 {
    0x40 | (if w { 0x08 } else { 0 })
        | (if r != 0 { 0x04 } else { 0 })
        | (if x != 0 { 0x02 } else { 0 })
        | (if b != 0 { 0x01 } else { 0 })
}

fn modrm_byte(mod_bits: u8, reg: u8, rm: u8) -> u8 {
    (mod_bits << 6) | ((reg & 7) << 3) | (rm & 7)
}

fn rbp_displacement(reg: u8, offset: i64) -> Vec<u8> {
    if (-128..=127).contains(&offset) {
        vec![0x40 | ((reg & 7) << 3) | 5, (offset as u8 & 0xFF)]
    } else {
        let mut bytes = vec![0x80 | ((reg & 7) << 3) | 5];
        bytes.extend_from_slice(&((offset as u32) & 0xFFFFFFFF).to_le_bytes());
        bytes
    }
}

/// A byte buffer with named labels and forward-reference fixups.
#[derive(Debug, Clone, Default)]
pub struct Assembler {
    pub code: Vec<u8>,
    pub labels: BTreeMap<String, usize>,
    /// Label names in binding order, so ties at one offset keep source order.
    pub label_order: Vec<String>,
    pub fixups: Vec<(usize, String)>,
    /// The register whose current value ZF compares against zero, when usable.
    pub zf_status: Option<String>,
}

impl Assembler {
    pub fn new() -> Self {
        Self::default()
    }

    /// The current offset in the code buffer.
    pub fn here(&self) -> usize {
        self.code.len()
    }

    fn emit<B: AsRef<[u8]>>(&mut self, part: B) {
        self.zf_status = None;
        self.code.extend_from_slice(part.as_ref());
    }

    /// Bind `name` to the current offset; a duplicate name is a compiler bug.
    pub fn label(&mut self, name: &str) {
        assert!(!self.labels.contains_key(name), "duplicate label {}", name);
        self.zf_status = None;
        self.label_order.push(name.to_string());
        self.labels.insert(name.to_string(), self.here());
    }

    /// A placeholder displacement patched when the image is linked.
    pub fn rel32(&mut self, target: &str) {
        self.fixups.push((self.here(), target.to_string()));
        self.emit([0u8; 4]);
    }

    pub fn call(&mut self, target: &str) {
        self.emit([0xE8]);
        self.rel32(target);
    }

    pub fn call_indirect(&mut self, target: &str) {
        self.emit([0xFF, 0x15]);
        self.rel32(target);
    }

    pub fn jmp(&mut self, target: &str) {
        if let Some(label) = self.labels.get(target) {
            let displacement = *label as i64 - (self.here() as i64 + 2);
            if (-128..=127).contains(&displacement) {
                self.emit([0xEB, displacement as u8]);
                return;
            }
        }
        self.emit([0xE9]);
        self.rel32(target);
    }

    pub fn jcc(&mut self, cond: &str, target: &str) {
        let code = condition(cond);
        if let Some(label) = self.labels.get(target) {
            let displacement = *label as i64 - (self.here() as i64 + 2);
            if (-128..=127).contains(&displacement) {
                self.emit([0x70 | code, displacement as u8]);
                return;
            }
        }
        self.emit([0x0F, 0x80 | code]);
        self.rel32(target);
    }

    pub fn push(&mut self, reg: &str) {
        let code = register(reg);
        if code >= 8 {
            self.emit([0x41]);
        }
        self.emit([0x50 + (code & 7)]);
    }

    pub fn pop(&mut self, reg: &str) {
        let code = register(reg);
        if code >= 8 {
            self.emit([0x41]);
        }
        self.emit([0x58 + (code & 7)]);
    }

    pub fn lea_imm(&mut self, destination: &str, base: &str, offset: i64) {
        let dst = register(destination);
        let base_code = register(base);
        assert_ne!(base_code, register("rsp"));
        let wide = (-128..=127).contains(&offset);
        self.emit([
            rex(true, dst >> 3, 0, base_code >> 3),
            0x8D,
            modrm_byte(if wide { 1 } else { 2 }, dst, base_code),
        ]);
        if base_code & 7 == 4 {
            self.emit([0x24]);
        }
        if wide {
            self.emit([offset as u8 & 0xFF]);
        } else {
            self.emit(((offset as u32) & 0xFFFFFFFF).to_le_bytes());
        }
    }

    pub fn lea_scale(&mut self, destination: &str, base: &str, index: &str,
                     scale_log2: u8, displacement: i64) {
        let dst = register(destination);
        let base_code = register(base);
        let index_code = register(index);
        assert_ne!(base_code, register("rsp"));
        assert_ne!(index_code, register("rsp"));
        let wide = !(-128..=127).contains(&displacement);
        self.emit([
            rex(true, dst >> 3, index_code >> 3, base_code >> 3),
            0x8D,
            modrm_byte(if wide { 2 } else { 1 }, dst, 4),
            (scale_log2 << 6) | ((index_code & 7) << 3) | (base_code & 7),
        ]);
        if wide {
            self.emit(((displacement as u32) & 0xFFFFFFFF).to_le_bytes());
        } else {
            self.emit([displacement as u8 & 0xFF]);
        }
    }

    pub fn lea_index(&mut self, destination: &str, index: &str, scale_log2: u8,
                     displacement: i64) {
        let dst = register(destination);
        let index_code = register(index);
        assert!(![register("rsp"), register("rbp")].contains(&index_code));
        self.emit([
            rex(true, dst >> 3, index_code >> 3, 0),
            0x8D,
            modrm_byte(0, dst, 4),
            (scale_log2 << 6) | ((index_code & 7) << 3) | 0b101,
        ]);
        self.emit(((displacement as u32) & 0xFFFFFFFF).to_le_bytes());
    }

    pub fn mov_reg_reg(&mut self, destination: &str, source: &str) {
        let dst = register(destination);
        let src = register(source);
        self.emit([rex(true, src >> 3, 0, dst >> 3), 0x89, modrm_byte(3, src, dst)]);
    }

    pub fn mov_imm64(&mut self, reg: &str, value: i64) {
        let code = register(reg);
        let prefix = [rex(true, 0, 0, code >> 3)];
        self.emit(if code >= 8 { prefix.as_slice() } else { &[0x48] });
        self.emit([0xB8 + (code & 7)]);
        self.emit((value as u64).to_le_bytes());
    }

    pub fn mov_imm32(&mut self, reg: &str, value: i64) {
        let code = register(reg);
        if code >= 8 {
            self.emit([rex(false, 0, 0, code >> 3)]);
        }
        self.emit([0xB8 + (code & 7)]);
        self.emit(((value as u32) & 0xFFFFFFFF).to_le_bytes());
    }

    pub fn mov_load_rbp(&mut self, destination: &str, offset: i64) {
        let dst = register(destination);
        self.emit([rex(true, dst >> 3, 0, 0), 0x8B]);
        self.emit(rbp_displacement(dst, offset));
    }

    pub fn mov_store_rbp(&mut self, offset: i64, source: &str) {
        let src = register(source);
        self.emit([rex(true, src >> 3, 0, 0), 0x89]);
        self.emit(rbp_displacement(src, offset));
    }

    pub fn binary_store_rbp(&mut self, opcode: u8, offset: i64, source: &str) {
        let src = register(source);
        self.emit([rex(true, src >> 3, 0, 0), opcode]);
        self.emit(rbp_displacement(src, offset));
    }

    pub fn group1_store_rbp_imm(&mut self, extension: u8, offset: i64, value: i64) {
        assert!((-0x80000000..=0x7FFFFFFF).contains(&value), "immediate does not fit in 32 signed bits");
        if (-128..=127).contains(&value) {
            self.emit([rex(true, 0, 0, 0), 0x83]);
            self.emit(rbp_displacement(extension, offset));
            self.emit([value as u8 & 0xFF]);
        } else {
            self.emit([rex(true, 0, 0, 0), 0x81]);
            self.emit(rbp_displacement(extension, offset));
            self.emit(((value as u32) & 0xFFFFFFFF).to_le_bytes());
        }
    }

    pub fn mov_store_rsp_imm32(&mut self, offset: i64, value: i64) {
        self.emit([0x48, 0xC7, 0x84, 0x24]);
        self.emit((offset as u32).to_le_bytes());
        self.emit(((value as u32) & 0xFFFFFFFF).to_le_bytes());
    }

    pub fn mov_store_rsp(&mut self, source: &str) {
        let src = register(source);
        self.emit([rex(true, src >> 3, 0, 0), 0x89, 0x04 | ((src & 7) << 3), 0x24]);
    }

    pub fn mov_load_rsp(&mut self, destination: &str) {
        let dst = register(destination);
        self.emit([rex(true, dst >> 3, 0, 0), 0x8B, 0x04 | ((dst & 7) << 3), 0x24]);
    }

    pub fn mov_store_rsp_disp(&mut self, offset: i64, source: &str) {
        let src = register(source);
        self.emit([rex(true, src >> 3, 0, 0), 0x89, 0x44 | ((src & 7) << 3), 0x24,
                   offset as u8 & 0xFF]);
    }

    pub fn mov_load_rsp_disp(&mut self, offset: i64, destination: &str) {
        let dst = register(destination);
        self.emit([rex(true, dst >> 3, 0, 0), 0x8B, 0x44 | ((dst & 7) << 3), 0x24,
                   offset as u8 & 0xFF]);
    }

    pub fn lea_rbp(&mut self, destination: &str, offset: i64) {
        let dst = register(destination);
        self.emit([rex(true, dst >> 3, 0, 0), 0x8D]);
        self.emit(rbp_displacement(dst, offset));
    }

    pub fn lea_rip(&mut self, destination: &str, target: &str) {
        let dst = register(destination);
        self.emit([rex(true, dst >> 3, 0, 0), 0x8D, (dst & 7) << 3 | 5]);
        self.rel32(target);
    }

    pub fn mov_load_rip(&mut self, destination: &str, target: &str) {
        let dst = register(destination);
        self.emit([rex(true, dst >> 3, 0, 0), 0x8B, (dst & 7) << 3 | 5]);
        self.rel32(target);
    }

    pub fn mov_store_rip(&mut self, source: &str, target: &str) {
        let src = register(source);
        self.emit([rex(true, src >> 3, 0, 0), 0x89, (src & 7) << 3 | 5]);
        self.rel32(target);
    }

    pub fn cmp_reg_rip(&mut self, left: &str, target: &str) {
        let dst = register(left);
        self.emit([rex(true, dst >> 3, 0, 0), 0x3B, (dst & 7) << 3 | 5]);
        self.rel32(target);
    }

    pub fn mov_load(&mut self, destination: &str, base: &str) {
        let dst = register(destination);
        let base_code = register(base);
        assert_ne!(base_code, register("rsp"));
        self.emit([rex(true, dst >> 3, 0, base_code >> 3), 0x8B,
                   modrm_byte(0, dst, base_code)]);
    }

    pub fn mov_store_reg(&mut self, base: &str, source: &str) {
        let src = register(source);
        let base_code = register(base);
        assert_ne!(base_code, register("rsp"));
        self.emit([rex(true, src >> 3, 0, base_code >> 3), 0x89,
                   modrm_byte(0, src, base_code)]);
    }

    pub fn binary_store_reg(&mut self, opcode: u8, base: &str, source: &str) {
        let src = register(source);
        let base_code = register(base);
        assert_ne!(base_code, register("rsp"));
        self.emit([rex(true, src >> 3, 0, base_code >> 3), opcode,
                   modrm_byte(0, src, base_code)]);
    }

    pub fn group1_store_reg_imm(&mut self, extension: u8, base: &str, value: i64) {
        assert!((-0x80000000..=0x7FFFFFFF).contains(&value), "immediate does not fit in 32 signed bits");
        let base_code = register(base);
        assert_ne!(base_code, register("rsp"));
        let prefix = rex(true, 0, 0, base_code >> 3);
        if (-128..=127).contains(&value) {
            self.emit([prefix, 0x83, modrm_byte(0, extension, base_code), value as u8 & 0xFF]);
        } else {
            self.emit([prefix, 0x81, modrm_byte(0, extension, base_code)]);
            self.emit(((value as u32) & 0xFFFFFFFF).to_le_bytes());
        }
    }

    pub fn mov_load_indexed(&mut self, destination: &str, base: &str, index: &str,
                            scale_log2: u8, displacement: i64) {
        self.indexed_memory(0x8B, register(destination), base, index, scale_log2, displacement);
    }

    pub fn binary_store_indexed(&mut self, opcode: u8, base: &str, index: &str,
                                scale_log2: u8, displacement: i64, source: &str) {
        self.indexed_memory(opcode, register(source), base, index, scale_log2, displacement);
    }

    pub fn mov_store_indexed(&mut self, base: &str, index: &str, scale_log2: u8,
                             displacement: i64, source: &str) {
        self.indexed_memory(0x89, register(source), base, index, scale_log2, displacement);
    }

    pub fn mov_store_indexed_imm(&mut self, base: &str, index: &str, scale_log2: u8,
                                 displacement: i64, value: i64) {
        assert!((-0x80000000..=0x7FFFFFFF).contains(&value), "immediate does not fit in 32 signed bits");
        self.indexed_memory(0xC7, 0, base, index, scale_log2, displacement);
        self.emit(((value as u32) & 0xFFFFFFFF).to_le_bytes());
    }

    pub fn group1_store_indexed_imm(&mut self, extension: u8, base: &str, index: &str,
                                    scale_log2: u8, displacement: i64, value: i64) {
        assert!((-0x80000000..=0x7FFFFFFF).contains(&value), "immediate does not fit in 32 signed bits");
        let small = (-128..=127).contains(&value);
        self.indexed_memory(if small { 0x83 } else { 0x81 }, extension, base, index,
                            scale_log2, displacement);
        if small {
            self.emit([value as u8 & 0xFF]);
        } else {
            self.emit(((value as u32) & 0xFFFFFFFF).to_le_bytes());
        }
    }

    fn indexed_memory(&mut self, opcode: u8, reg: u8, base: &str, index: &str,
                      scale_log2: u8, displacement: i64) {
        let base_code = register(base);
        let index_code = register(index);
        assert_ne!(base_code, register("rsp"));
        assert_ne!(index_code, register("rsp"));
        let wide = !(-128..=127).contains(&displacement);
        self.emit([
            rex(true, reg >> 3, index_code >> 3, base_code >> 3),
            opcode,
            modrm_byte(if wide { 2 } else { 1 }, reg, 4),
            (scale_log2 << 6) | ((index_code & 7) << 3) | (base_code & 7),
        ]);
        if wide {
            self.emit(((displacement as u32) & 0xFFFFFFFF).to_le_bytes());
        } else {
            self.emit([displacement as u8 & 0xFF]);
        }
    }

    pub fn movzx_load8(&mut self, destination: &str, base: &str) {
        let dst = register(destination);
        let base_code = register(base);
        assert_ne!(base_code, register("rsp"));
        if dst >= 8 || base_code >= 8 {
            self.emit([rex(false, dst >> 3, 0, base_code >> 3)]);
        }
        self.emit([0x0F, 0xB6, modrm_byte(0, dst, base_code)]);
    }

    pub fn movzx_load8_sib(&mut self, destination: &str, base: &str, index: &str) {
        let dst = register(destination);
        let base_code = register(base);
        let index_code = register(index);
        assert_ne!(base_code, register("rsp"));
        assert_ne!(index_code, register("rsp"));
        let forced = base_code & 7 == 5;
        if dst >= 8 || index_code >= 8 || base_code >= 8 {
            self.emit([rex(false, dst >> 3, index_code >> 3, base_code >> 3)]);
        }
        self.emit([0x0F, 0xB6, modrm_byte(if forced { 1 } else { 0 }, dst, 4),
                   ((index_code & 7) << 3) | (base_code & 7)]);
        if forced {
            self.emit([0x00]);
        }
    }

    pub fn movzx_from8(&mut self, destination: &str, source: &str) {
        self.drop_redundant_source_move(destination, source);
        self.movzx(0xB6, destination, source);
    }

    pub fn movzx_from16(&mut self, destination: &str, source: &str) {
        self.drop_redundant_source_move(destination, source);
        self.movzx(0xB7, destination, source);
    }

    /// A `mov rax, src` immediately before `movzx rax, src` is redundant.
    fn drop_redundant_source_move(&mut self, destination: &str, source: &str) {
        if destination != "rax" {
            return;
        }
        let src = register(source);
        if src >= 8 {
            return;
        }
        let pattern = [0x48, 0x89, 0xC0 | (src << 3)];
        if self.code.ends_with(&pattern) {
            let length = self.code.len();
            let new_length = length - pattern.len();
            self.code.truncate(new_length);
            for (_, offset) in self.labels.iter_mut() {
                if *offset > new_length {
                    *offset = new_length;
                }
            }
            for entry in self.fixups.iter_mut() {
                if entry.0 > new_length {
                    entry.0 = new_length;
                }
            }
        }
    }

    fn movzx(&mut self, extension: u8, destination: &str, source: &str) {
        let dst = register(destination);
        let src = register(source);
        if dst >= 8 || src >= 8 {
            self.emit([rex(false, dst >> 3, 0, src >> 3)]);
        }
        self.emit([0x0F, extension, modrm_byte(3, dst, src)]);
    }

    pub fn xor_zero(&mut self, reg: &str) {
        let code = register(reg);
        self.emit([rex(false, code >> 3, 0, code >> 3), 0x31, modrm_byte(3, code, code)]);
    }

    /// `add, or, and, sub, xor` set ZF from the result.
    const ZF_SETTERS: &'static [u8] = &[0x01, 0x09, 0x21, 0x29, 0x31];

    pub fn binary(&mut self, opcode: u8, destination: &str, source: &str) {
        let dst = register(destination);
        let src = register(source);
        self.emit([rex(true, src >> 3, 0, dst >> 3), opcode, modrm_byte(3, src, dst)]);
        if Self::ZF_SETTERS.contains(&opcode) {
            self.zf_status = Some(destination.to_string());
        }
    }

    pub fn cmp_reg_reg(&mut self, left: &str, right: &str) {
        self.binary(0x39, left, right);
    }

    fn group1_imm(&mut self, extension: u8, reg: &str, value: i64) {
        assert!((-0x80000000..=0x7FFFFFFF).contains(&value), "immediate does not fit in 32 signed bits");
        let code = register(reg);
        let prefix = rex(true, 0, 0, code >> 3);
        let modrm = 0xC0 | (extension << 3) | (code & 7);
        if (-128..=127).contains(&value) {
            self.emit([prefix, 0x83, modrm, value as u8 & 0xFF]);
        } else {
            self.emit([prefix, 0x81, modrm]);
            self.emit(((value as u32) & 0xFFFFFFFF).to_le_bytes());
        }
    }

    pub fn add_imm(&mut self, reg: &str, value: i64) {
        self.group1_imm(0, reg, value);
    }

    pub fn sub_imm(&mut self, reg: &str, value: i64) {
        self.group1_imm(5, reg, value);
    }

    pub fn adc_imm(&mut self, reg: &str, value: i64) {
        self.group1_imm(2, reg, value);
    }

    pub fn cmov(&mut self, cond: &str, destination: &str, source: &str) {
        let code = cmov_condition(cond);
        let dst = register(destination);
        let src = register(source);
        self.emit([rex(true, dst >> 3, 0, src >> 3), 0x0F, code, modrm_byte(3, dst, src)]);
    }

    pub fn cmp_imm(&mut self, reg: &str, value: i64) {
        self.group1_imm(7, reg, value);
    }

    pub fn arith_imm(&mut self, extension: u8, reg: &str, value: i64) {
        self.group1_imm(extension, reg, value);
        if ZF_IMM_EXTENSIONS.contains(&extension) {
            self.zf_status = Some(reg.to_string());
        }
    }

    pub fn imul(&mut self, destination: &str, source: &str) {
        let dst = register(destination);
        let src = register(source);
        self.emit([rex(true, dst >> 3, 0, src >> 3), 0x0F, 0xAF, modrm_byte(3, dst, src)]);
    }

    pub fn imul_imm(&mut self, destination: &str, source: &str, literal: i64) {
        let dst = register(destination);
        let src = register(source);
        if (-2i64.pow(31)..2i64.pow(31)).contains(&literal) {
            if (-128..128).contains(&literal) {
                self.emit([rex(true, dst >> 3, 0, src >> 3), 0x6B, modrm_byte(3, dst, src),
                           literal as u8 & 0xFF]);
            } else {
                self.emit([rex(true, dst >> 3, 0, src >> 3), 0x69, modrm_byte(3, dst, src)]);
                self.emit((literal as i32).to_le_bytes());
            }
            return;
        }
        panic!("literal does not fit the three-operand imul form");
    }

    pub fn imul_high(&mut self, reg: &str) {
        let code = register(reg);
        self.emit([rex(true, 0, 0, code >> 3), 0xF7, 0xC0 | (0x5 << 3) | (code & 7)]);
    }

    pub fn test(&mut self, reg: &str) {
        let code = register(reg);
        self.emit([rex(true, code >> 3, 0, code >> 3), 0x85, modrm_byte(3, code, code)]);
        self.zf_status = Some(reg.to_string());
    }

    pub fn test_imm(&mut self, reg: &str, value: i64) {
        assert!((0..=127).contains(&value), "test mask does not fit in 8 bits");
        let code = register(reg);
        self.emit([rex(false, 0, 0, code >> 3), 0xF6, 0xC0 | (code & 7), value as u8 & 0xFF]);
    }

    pub fn setcc(&mut self, cond: &str) {
        self.emit([0x0F, 0x90 | condition(cond), 0xC0]);
        self.emit([0x48, 0x0F, 0xB6, 0xC0]);
    }

    pub fn cqo(&mut self) {
        self.emit([0x48, 0x99]);
    }

    pub fn divide(&mut self, reg: &str, signed: bool) {
        let code = register(reg);
        let extension = if signed { 0x7 } else { 0x6 };
        self.emit([rex(true, 0, 0, code >> 3), 0xF7, 0xC0 | (extension << 3) | (code & 7)]);
    }

    pub fn divide32(&mut self, reg: &str) {
        let code = register(reg);
        if code >= 8 {
            self.emit([rex(false, 0, 0, code >> 3)]);
        }
        self.emit([0xF7, 0xC0 | (0x6 << 3) | (code & 7)]);
    }

    pub fn unary(&mut self, extension: u8, reg: &str) {
        let code = register(reg);
        self.emit([rex(true, 0, 0, code >> 3), 0xF7, 0xC0 | (extension << 3) | (code & 7)]);
    }

    pub fn shift_cl(&mut self, extension: u8) {
        self.shift_cl_reg("rax", extension);
    }

    /// `r/m64 shift-by-cl` into any register: extension 4 is SHL, 5 SHR, 7 SAR.
    pub fn shift_cl_reg(&mut self, reg: &str, extension: u8) {
        let code = register(reg);
        self.emit([rex(true, 0, 0, code >> 3), 0xD3,
                   0xC0 | (extension << 3) | (code & 7)]);
    }

    /// `rol reg, cl` (extension 0) or `ror reg, cl` (extension 1) into any register.
    pub fn rotate_cl_reg(&mut self, reg: &str, extension: u8) {
        self.shift_cl_reg(reg, extension);
    }

    pub fn rotate_cl(&mut self, extension: u8) {
        self.rotate_cl_reg("rax", extension);
    }

    pub fn shift_imm(&mut self, extension: u8, reg: &str, count: u8) {
        assert!(count <= 63, "shift count does not fit in six bits");
        let code = register(reg);
        if count == 1 {
            self.emit([rex(true, 0, 0, code >> 3), 0xD1, 0xC0 | (extension << 3) | (code & 7)]);
        } else {
            self.emit([rex(true, 0, 0, code >> 3), 0xC1,
                       0xC0 | (extension << 3) | (code & 7), count & 0xFF]);
        }
    }

    pub fn add_imm8(&mut self, reg: &str, value: i64) {
        let code = eight_bit(reg);
        self.emit([rex(false, 0, 0, code >> 3), 0x80, 0xC0 | (code & 7), value as u8 & 0xFF]);
    }

    /// Canonical multi-byte nops, largest first, so hot fall-through paths stay fast.
    const NOP_PATTERNS: &'static [(usize, &'static [u8])] = &[
        (9, &[0x66, 0x0F, 0x1F, 0x84, 0x00, 0x00, 0x00, 0x00, 0x00]),
        (8, &[0x0F, 0x1F, 0x84, 0x00, 0x00, 0x00, 0x00, 0x00]),
        (7, &[0x0F, 0x1F, 0x80, 0x00, 0x00, 0x00, 0x00]),
        (6, &[0x66, 0x0F, 0x1F, 0x44, 0x00, 0x00]),
        (5, &[0x0F, 0x1F, 0x44, 0x00, 0x00]),
        (4, &[0x0F, 0x1F, 0x40, 0x00]),
        (3, &[0x0F, 0x1F, 0x00]),
        (2, &[0x66, 0x90]),
        (1, &[0x90]),
    ];

    pub fn nop_pad(&mut self, mut size: usize) {
        while size > 0 {
            for (length, pattern) in Self::NOP_PATTERNS {
                if *length <= size {
                    self.emit(*pattern);
                    size -= *length;
                    break;
                }
            }
        }
    }

    pub fn store_byte(&mut self, base: &str, source: &str) {
        let base_code = register(base);
        let src_code = eight_bit(source);
        self.emit([rex(false, src_code >> 3, 0, base_code >> 3), 0x88,
                   modrm_byte(0, src_code, base_code)]);
    }

    pub fn store_byte_imm(&mut self, base: &str, value: i64) {
        let base_code = register(base);
        self.emit([rex(false, 0, 0, base_code >> 3), 0xC6, modrm_byte(0, 0, base_code),
                   value as u8 & 0xFF]);
    }

    pub fn store_qword(&mut self, base: &str, source: &str) {
        let base_code = register(base);
        let src_code = register(source);
        self.emit([rex(true, src_code >> 3, 0, base_code >> 3), 0x89,
                   modrm_byte(0, src_code, base_code)]);
    }

    pub fn step(&mut self, reg: &str, decrement: bool) {
        let code = register(reg);
        let extension = if decrement { 1 } else { 0 };
        self.emit([rex(true, 0, 0, code >> 3), 0xFF, 0xC0 | (extension << 3) | (code & 7)]);
    }

    pub fn add_rsp(&mut self, amount: i64) {
        self.emit([0x48, 0x81, 0xC4]);
        self.emit((amount as u32).to_le_bytes());
    }

    pub fn sub_rsp(&mut self, amount: i64) {
        self.emit([0x48, 0x81, 0xEC]);
        self.emit((amount as u32).to_le_bytes());
    }

    pub fn align_rsp(&mut self) {
        self.emit([0x48, 0x83, 0xE4, 0xF0]);
    }

    pub fn leave_ret(&mut self) {
        self.emit([0xC9, 0xC3]);
    }

    pub fn ret(&mut self) {
        self.emit([0xC3]);
    }

    pub fn movq_from_reg(&mut self, destination: &str, source: &str) {
        let dst = xmm_register(destination);
        let src = register(source);
        self.emit([0x66, rex(true, 0, 0, src >> 3), 0x0F, 0x6E, 0xC0 | (dst << 3) | (src & 7)]);
    }

    pub fn punpcklqdq(&mut self, destination: &str, source: &str) {
        let dst = xmm_register(destination);
        let src = xmm_register(source);
        self.emit([0x66, 0x0F, 0x6C, 0xC0 | (dst << 3) | src]);
    }

    pub fn paddq_reg(&mut self, destination: &str, source: &str) {
        let dst = xmm_register(destination);
        let src = xmm_register(source);
        self.emit([0x66, 0x0F, 0xD4, 0xC0 | (dst << 3) | src]);
    }

    pub fn movdqu_load_indexed(&mut self, destination: &str, base: &str, index: &str,
                               scale_log2: u8, offset: i64) {
        let dst = xmm_register(destination);
        let base_code = register(base);
        let index_code = register(index);
        let prefix = rex(false, 0, (index_code >> 3) & 1, (base_code >> 3) & 1);
        self.emit([0xF3, prefix, 0x0F, 0x6F,
                   0x80 | (dst << 3) | 4,
                   (scale_log2 << 6) | ((index_code & 7) << 3) | (base_code & 7)]);
        self.emit(((offset as u32) & 0xFFFFFFFF).to_le_bytes());
    }

    pub fn movdqu_store_indexed(&mut self, base: &str, index: &str, scale_log2: u8,
                                offset: i64, source: &str) {
        let src = xmm_register(source);
        let base_code = register(base);
        let index_code = register(index);
        let prefix = rex(false, 0, (index_code >> 3) & 1, (base_code >> 3) & 1);
        self.emit([0xF3, prefix, 0x0F, 0x7F,
                   0x80 | (src << 3) | 4,
                   (scale_log2 << 6) | ((index_code & 7) << 3) | (base_code & 7)]);
        self.emit(((offset as u32) & 0xFFFFFFFF).to_le_bytes());
    }

    pub fn popcnt(&mut self, destination: &str, source: &str) {
        let dst = register(destination);
        let src = register(source);
        self.emit([0xF3, rex(true, dst >> 3, 0, src >> 3), 0x0F, 0xB8,
                   modrm_byte(3, dst, src)]);
    }

    pub fn lzcnt(&mut self, destination: &str, source: &str) {
        let dst = register(destination);
        let src = register(source);
        self.emit([0xF3, rex(true, dst >> 3, 0, src >> 3), 0x0F, 0xBD,
                   modrm_byte(3, dst, src)]);
    }

    pub fn tzcnt(&mut self, destination: &str, source: &str) {
        let dst = register(destination);
        let src = register(source);
        self.emit([0xF3, rex(true, dst >> 3, 0, src >> 3), 0x0F, 0xBC,
                   modrm_byte(3, dst, src)]);
    }

    pub fn bswap(&mut self, reg: &str) {
        let code = register(reg);
        self.emit([rex(true, 0, 0, code >> 3), 0x0F, 0xC8 | (code & 7)]);
    }

    pub fn hlt(&mut self) {
        self.emit([0xF4]);
    }
}
