//! Code generation, first slice: the entry stub and frameless functions with simple expressions.
//!
//! The byte-level parity gate compares `.text` with the Python generator over a curated set of
//! simple sources. Wider lowering shapes are added in later slices.

use crate::codegen::function_label;
use crate::image::{iat_label, Artifact, ENTRY_LABEL, ReadOnlyData, WritableData};
use crate::machine::Assembler;
use crate::runtime::{emit_runtime, OUT_FLUSH_LABEL, PRINT_CHAR_LABEL, PRINT_HEX_LABEL, PRINT_LABEL};
use crate::syntax::{Expr, Program, Stmt};

/// One lowering pass over a checked program.
pub struct CodeGenerator {
    pub assembler: Assembler,
    pub rodata: ReadOnlyData,
    pub writable: WritableData,
    str_labels: std::collections::BTreeMap<String, String>,
    counter: usize,
    depth: i64,
    epilogue: String,
}

impl CodeGenerator {
    pub fn new() -> Self {
        Self {
            assembler: Assembler::new(),
            rodata: ReadOnlyData::default(),
            writable: WritableData::default(),
            str_labels: std::collections::BTreeMap::new(),
            counter: 0,
            depth: 0,
            epilogue: String::new(),
        }
    }

    fn fresh(&mut self, stem: &str) -> String {
        self.counter += 1;
        format!("{}.{}", stem, self.counter)
    }

    fn string_label(&mut self, text: &str) -> String {
        if let Some(label) = self.str_labels.get(text) {
            return label.clone();
        }
        let label = format!("cairn.str.{}", self.str_labels.len());
        let mut bytes = text.as_bytes().to_vec();
        bytes.push(0);
        self.rodata.add(&label, &bytes);
        self.str_labels.insert(text.to_string(), label.clone());
        label
    }

    /// A call padded so rsp stays 16-byte aligned at the ABI boundary.
    fn call_site(&mut self, target: &str) {
        let padding = if self.depth % 16 != 0 { 8 } else { 0 };
        if padding > 0 {
            self.assembler.sub_rsp(padding);
        }
        self.assembler.call(target);
        if padding > 0 {
            self.assembler.add_rsp(padding);
        }
    }

    fn direct_indirect_call(&mut self, import: &str) {
        let padding = if self.depth % 16 != 0 { 8 } else { 0 };
        if padding > 0 {
            self.assembler.sub_rsp(padding);
        }
        let name = iat_label(import);
        self.assembler.call_indirect(&name);
        if padding > 0 {
            self.assembler.add_rsp(padding);
        }
    }

    fn emit_entry(&mut self) {
        self.assembler.label(ENTRY_LABEL);
        self.assembler.sub_rsp(40);
        self.assembler.call(&function_label("main"));
        self.assembler.mov_reg_reg("rcx", "rax");
        self.assembler.push("rcx");
        self.depth += 8;
        let flush = OUT_FLUSH_LABEL.to_string();
        self.call_site(&flush);
        self.depth -= 8;
        self.assembler.pop("rcx");
        self.direct_indirect_call("ExitProcess");
        self.assembler.hlt();
    }

    /// Emit a function using the frameless path (all slots homed in registers).
    fn emit_function(&mut self, function: &crate::syntax::Function) {
        self.epilogue = format!("{}.epilogue", function_label(&function.name));
        self.assembler.label(&function_label(&function.name));
        // No homes and no held slots: one pad unit keeps the call alignment.
        self.assembler.sub_rsp(8);
        self.depth = 0;
        let body = function.body.clone();
        for (index, statement) in body.statements.iter().enumerate() {
            self.emit_statement(statement, index + 1 == body.statements.len());
        }
        self.assembler.label(&self.epilogue.clone());
        self.assembler.add_rsp(8);
        self.assembler.ret();
    }

    fn emit_statement(&mut self, statement: &Stmt, tail: bool) {
        match statement {
            Stmt::Return { value, .. } => {
                self.emit_expression(value);
                if !tail {
                    let epilogue = self.epilogue.clone();
                    self.assembler.jmp(&epilogue);
                }
            }
            Stmt::ExprStmt { expression, .. } => self.emit_expression(expression),
            Stmt::Block(block) => {
                for (index, inner) in block.statements.iter().enumerate() {
                    self.emit_statement(inner, tail && index + 1 == block.statements.len());
                }
            }
            Stmt::Break { .. } | Stmt::Continue { .. } => {}
            // Wider shapes are added in later slices; refusing beats emitting wrong bytes.
            Stmt::Let { .. } | Stmt::Assign { .. } | Stmt::If { .. } | Stmt::While { .. }
            | Stmt::For { .. } => {}
        }
    }

    fn emit_expression(&mut self, expression: &Expr) {
        match expression {
            Expr::IntLiteral { value, .. } => self.assembler.mov_imm64("rax", *value as i64),
            Expr::Parens { inner, .. } => self.emit_expression(inner),
            Expr::StrLiteral { text, .. } => {
                let label = self.string_label(text);
                self.assembler.lea_rip("rax", &label);
            }
            Expr::Call { name, arguments, .. } => self.emit_call(name, arguments),
            Expr::Name { .. } | Expr::Unary { .. } | Expr::Binary { .. } | Expr::Index { .. }
            | Expr::ArrayLiteral { .. } => {}
        }
    }

    fn emit_call(&mut self, name: &str, arguments: &[Expr]) {
        match name {
            "argc" => {
                let label = crate::runtime::ARGC_LABEL.to_string();
                self.call_site(&label);
            }
            "flush" => {
                let label = crate::runtime::OUT_FLUSH_LABEL.to_string();
                self.call_site(&label);
            }
            "print" | "print_char" | "print_hex" | "print_str" => {
                self.emit_expression(&arguments[0]);
                self.assembler.push("rax");
                self.depth += 8;
                let label = match name {
                    "print" => PRINT_LABEL,
                    "print_char" => PRINT_CHAR_LABEL,
                    "print_hex" => PRINT_HEX_LABEL,
                    _ => crate::runtime::PRINT_STR_LABEL,
                };
                self.call_site(label);
                self.depth -= 8;
                self.assembler.pop("rax");
            }
            _ => {}
        }
    }

    /// Emit the entry stub, every function, then the runtime helpers.
    pub fn generate(mut self, program: &Program) -> Artifact {
        self.emit_entry();
        for function in &program.functions {
            self.emit_function(function);
        }
        emit_runtime(&mut self.assembler, &mut self.rodata, &mut self.writable);
        let _ = self.fresh("");
        Artifact {
            text: self.assembler.code,
            text_labels: self.assembler.labels,
            fixups: self.assembler.fixups,
            rodata: self.rodata,
            entry: ENTRY_LABEL.to_string(),
            imports: crate::image::IMPORTS.iter().map(|name| name.to_string()).collect(),
            writable: self.writable.data,
            writable_labels: self.writable.labels,
        }
    }
}

impl Default for CodeGenerator {
    fn default() -> Self {
        Self::new()
    }
}
