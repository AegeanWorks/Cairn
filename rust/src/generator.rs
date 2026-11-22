//! Code generation, first slice: the entry stub and frameless functions with simple expressions.
//!
//! The byte-level parity gate compares `.text` with the Python generator over a curated set of
//! simple sources. Wider lowering shapes are added in later slices.

use crate::codegen::function_label;
use crate::image::{iat_label, Artifact, ENTRY_LABEL, ReadOnlyData, WritableData};
use crate::machine::Assembler;
use crate::runtime::{emit_runtime, OUT_FLUSH_LABEL, PRINT_CHAR_LABEL, PRINT_HEX_LABEL, PRINT_LABEL};
use crate::syntax::{Expr, Program, Stmt};

/// The register home of a slot, when the slot is kept in a register.
fn home_for<'a>(slot: i64, homes: &'a [(i64, &'a str)]) -> Option<&'a str> {
    homes.iter().find(|(found, _)| *found == slot).map(|(_, register)| *register)
}

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

    /// Slots that keep a register home, in register order (frame functions rank by loop depth,
    /// ties by descending slot; all slots at depth zero here).
    fn home_slots(function: &crate::syntax::Function) -> Vec<i64> {
        if (function.slots as usize) <= crate::codegen::HOME_REGISTERS.len() {
            return (0..function.slots as i64).collect();
        }
        let mut ranked: Vec<i64> = (0..function.slots as i64).collect();
        ranked.sort_by(|a, b| b.cmp(a));
        ranked
    }

    /// Emit one function: frameless when every slot has a home, otherwise a frame function.
    fn emit_function(&mut self, function: &crate::syntax::Function) {
        self.epilogue = format!("{}.epilogue", function_label(&function.name));
        self.assembler.label(&function_label(&function.name));
        let ranked = Self::home_slots(function);
        let callee_homes = crate::codegen::HOME_REGISTERS.len();
        let callee_count = ranked.len().min(callee_homes);
        let extra_count = ranked.len().saturating_sub(callee_homes);
        let mut homes: Vec<(i64, &str)> = ranked[..callee_count]
            .iter()
            .zip(crate::codegen::HOME_REGISTERS.iter().take(callee_count))
            .map(|(slot, register)| (*slot, *register))
            .collect();
        homes.extend(
            ranked[callee_count..callee_count + extra_count]
                .iter()
                .zip(crate::codegen::EXTRA_HOME_REGISTERS.iter().take(extra_count))
                .map(|(slot, register)| (*slot, *register)),
        );
        let frame_function = function.slots > callee_homes;
        let callee_registers: Vec<&str> = homes[..callee_count]
            .iter()
            .map(|(_, register)| *register)
            .collect();
        if frame_function {
            let frame = crate::codegen::align16((function.slots as usize + callee_count) * 8);
            self.assembler.push("rbp");
            self.assembler.mov_reg_reg("rbp", "rsp");
            self.assembler.sub_rsp(frame as i64);
            for (index, register) in callee_registers.iter().enumerate() {
                self.assembler.mov_store_rbp(
                    crate::codegen::slot_offset(function.slots as i64 + index as i64),
                    register,
                );
            }
        } else {
            for register in &callee_registers {
                self.assembler.push(register);
            }
            if callee_count % 2 == 0 {
                self.assembler.sub_rsp(8);
            }
        }
        self.depth = 0;
        for (index, parameter) in function.parameters.iter().enumerate() {
            if index < 4 {
                let source = ["rcx", "rdx", "r8", "r9"][index];
                self.store_slot(parameter.slot, source, &homes);
            } else if frame_function {
                self.assembler.mov_load_rbp("rax", 16 + 8 * (index - 4) as i64);
                self.store_slot(parameter.slot, "rax", &homes);
            }
        }
        let body = function.body.clone();
        let ends_in_return = matches!(body.statements.last(), Some(Stmt::Return { .. }));
        if !ends_in_return {
            self.assembler.mov_imm64("rax", 0);
        }
        for (index, statement) in body.statements.iter().enumerate() {
            self.emit_statement(statement, index + 1 == body.statements.len(), &homes);
        }
        self.assembler.label(&self.epilogue.clone());
        if frame_function {
            for (index, register) in callee_registers.iter().enumerate() {
                self.assembler.mov_load_rbp(
                    register,
                    crate::codegen::slot_offset(function.slots as i64 + index as i64),
                );
            }
            self.assembler.leave_ret();
        } else {
            if callee_count % 2 == 0 {
                self.assembler.add_rsp(8);
            }
            for register in callee_registers.iter().rev() {
                self.assembler.pop(register);
            }
            self.assembler.ret();
        }
    }



    fn load_slot(&mut self, slot: i64, destination: &str, homes: &[(i64, &str)]) {
        if let Some(home) = home_for(slot, homes) {
            if home != destination {
                self.assembler.mov_reg_reg(destination, home);
            }
        } else {
            self.assembler.mov_load_rbp(destination, crate::codegen::slot_offset(slot));
        }
    }

    fn store_slot(&mut self, slot: i64, source: &str, homes: &[(i64, &str)]) {
        if let Some(home) = home_for(slot, homes) {
            if home != source {
                self.assembler.mov_reg_reg(home, source);
            }
        } else {
            self.assembler.mov_store_rbp(crate::codegen::slot_offset(slot), source);
        }
    }

    fn emit_value(&mut self, slot: i64, expression: &Expr, homes: &[(i64, &str)]) {
        match expression {
            Expr::IntLiteral { value, .. } => {
                if *value == 0 {
                    if let Some(home) = home_for(slot, homes) {
                        self.assembler.xor_zero(home);
                        return;
                    }
                }
                self.assembler.mov_imm64("rax", *value as i64);
                self.store_slot(slot, "rax", homes);
            }
            Expr::Name { slot: source, .. } => {
                if let Some(home) = home_for(slot, homes) {
                    self.load_slot(*source, home, homes);
                } else {
                    self.load_slot(*source, "rax", homes);
                    self.store_slot(slot, "rax", homes);
                }
            }
            Expr::Parens { inner, .. } => self.emit_value(slot, inner, homes),
            Expr::Binary { op, left, right, .. }
                if op == "+"
                    && matches!(left.as_ref(), Expr::Name { slot: src, .. } if *src == slot)
                    && matches!(right.as_ref(), Expr::IntLiteral { .. }) =>
            {
                let Expr::IntLiteral { value, .. } = right.as_ref() else { unreachable!() };
                if let Some(home) = home_for(slot, homes) {
                    self.assembler.add_imm(home, *value as i64);
                } else {
                    self.load_slot(slot, "rax", homes);
                    self.assembler.add_imm("rax", *value as i64);
                    self.store_slot(slot, "rax", homes);
                }
            }
            other => {
                self.emit_expression(other, homes);
                self.store_slot(slot, "rax", homes);
            }
        }
    }

    fn emit_statement(&mut self, statement: &Stmt, tail: bool, homes: &[(i64, &str)]) {
        match statement {
            Stmt::Return { value, .. } => {
                self.emit_expression(value, homes);
                if !tail {
                    let epilogue = self.epilogue.clone();
                    self.assembler.jmp(&epilogue);
                }
            }
            Stmt::ExprStmt { expression, .. } => self.emit_expression(expression, homes),
            Stmt::Let { slot, value, .. } | Stmt::Assign { slot, value, .. } => {
                self.emit_value(*slot, value, homes);
            }
            Stmt::Block(block) => {
                for (index, inner) in block.statements.iter().enumerate() {
                    self.emit_statement(inner, tail && index + 1 == block.statements.len(), homes);
                }
            }
            Stmt::If { condition, then_block, else_block, .. } => {
                self.emit_if(condition, then_block, else_block.as_ref(), tail, homes);
            }
            Stmt::While { condition, body, .. } => self.emit_while(condition, body, homes),
            Stmt::Break { .. } | Stmt::Continue { .. } => {}
            // Wider shapes are added in later slices; refusing beats emitting wrong bytes.
            Stmt::For { .. } => {}
        }
    }

    fn emit_block(&mut self, block: &crate::syntax::Block, tail: bool, homes: &[(i64, &str)]) {
        for (index, statement) in block.statements.iter().enumerate() {
            self.emit_statement(statement, tail && index + 1 == block.statements.len(), homes);
        }
    }

    fn emit_while(&mut self, condition: &Expr, body: &crate::syntax::Block, homes: &[(i64, &str)]) {
        let start = self.fresh("while.start");
        let end = self.fresh("while.end");
        self.emit_condition_polarity(condition, &end, homes, false);
        let pad = (16 - self.assembler.here() % 16) % 16;
        if pad > 0 {
            self.assembler.nop_pad(pad);
        }
        let start_label = start.clone();
        self.assembler.label(&start_label);
        self.emit_block(body, false, homes);
        let latch = self.fresh("while.latch");
        let latch_label = latch.clone();
        self.assembler.label(&latch_label);
        self.emit_condition_polarity(condition, &start, homes, true);
        let end_label = end.clone();
        self.assembler.label(&end_label);
    }

    /// Emit a comparison that jumps to `target` when the relation holds.
    fn emit_condition(&mut self, expression: &Expr, target: &str, homes: &[(i64, &str)]) {
        self.emit_condition_polarity(expression, target, homes, true);
    }

    fn emit_condition_polarity(&mut self, expression: &Expr, target: &str, homes: &[(i64, &str)],
                               when_true: bool) {
        let Expr::Binary { op, left, right, .. } = expression else {
            return;
        };
        let (name_slot, literal) = match (left.as_ref(), right.as_ref()) {
            (Expr::Name { slot, .. }, Expr::IntLiteral { value, .. }) => (*slot, *value as i64),
            (Expr::IntLiteral { value, .. }, Expr::Name { slot, .. }) => (*slot, *value as i64),
            _ => return,
        };
        if let Some(home) = home_for(name_slot, homes) {
            self.assembler.cmp_imm(home, literal);
        } else {
            self.load_slot(name_slot, "rax", homes);
            self.assembler.cmp_imm("rax", literal);
        }
        let condition = match op.as_str() {
            "==" => "e", "!=" => "ne", "<" => "l", "<=" => "le", ">" => "g", ">=" => "ge",
            _ => return,
        };
        let condition = if when_true {
            condition
        } else {
            match condition {
                "e" => "ne", "ne" => "e", "l" => "ge", "ge" => "l", "le" => "g", "g" => "le",
                other => other,
            }
        };
        self.assembler.jcc(condition, target);
    }

    fn emit_if(&mut self, condition: &Expr, then_block: &crate::syntax::Block,
               else_block: Option<&crate::syntax::Block>, tail: bool, homes: &[(i64, &str)]) {
        let then_label = self.fresh("if.then");
        let end_label = self.fresh("if.end");
        self.emit_condition(condition, &then_label, homes);
        if let Some(otherwise) = else_block {
            self.emit_block(otherwise, tail, homes);
            let end = end_label.clone();
            self.assembler.jmp(&end);
        }
        let then = then_label.clone();
        self.assembler.label(&then);
        self.emit_block(then_block, tail, homes);
        let end = end_label.clone();
        self.assembler.label(&end);
    }

    fn emit_expression(&mut self, expression: &Expr, homes: &[(i64, &str)]) {
        match expression {
            Expr::IntLiteral { value, .. } => self.assembler.mov_imm64("rax", *value as i64),
            Expr::Parens { inner, .. } => self.emit_expression(inner, homes),
            Expr::StrLiteral { text, .. } => {
                let label = self.string_label(text);
                self.assembler.lea_rip("rax", &label);
            }
            Expr::Name { slot, .. } => self.load_slot(*slot, "rax", homes),
            Expr::Call { name, arguments, .. } => self.emit_call(name, arguments, homes),
            Expr::Binary { op, left, right, .. }
                if op == "+" && matches!(left.as_ref(), Expr::Name { .. })
                    && matches!(right.as_ref(), Expr::Name { .. }) =>
            {
                let Expr::Name { slot: left_slot, .. } = left.as_ref() else { unreachable!() };
                let Expr::Name { slot: right_slot, .. } = right.as_ref() else { unreachable!() };
                match (home_for(*left_slot, homes), home_for(*right_slot, homes)) {
                    (Some(base), Some(index)) => {
                        self.assembler.lea_scale("rax", base, index, 0, 0);
                    }
                    _ => {}
                }
            }
            Expr::Unary { .. } | Expr::Binary { .. } | Expr::Index { .. }
            | Expr::ArrayLiteral { .. } => {}
        }
    }

    fn emit_call(&mut self, name: &str, arguments: &[Expr], homes: &[(i64, &str)]) {
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
                self.emit_expression(&arguments[0], homes);
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
            _ => {
                // Pure arguments: a literal goes straight into its ABI register; any other form is
                // evaluated into rax and moved. Wider shapes are added in later slices.
                if arguments.len() <= 4 {
                    for (index, argument) in arguments.iter().enumerate() {
                        let register = ["rcx", "rdx", "r8", "r9"][index];
                        if let Expr::IntLiteral { value, .. } = argument {
                            self.assembler.mov_imm64(register, *value as i64);
                        } else if let Expr::Name { slot, .. } = argument {
                            if let Some(home) = home_for(*slot, homes) {
                                self.assembler.mov_reg_reg(register, home);
                            } else {
                                self.load_slot(*slot, "rax", homes);
                                self.assembler.mov_reg_reg(register, "rax");
                            }
                        } else {
                            self.emit_expression(argument, homes);
                            self.assembler.mov_reg_reg(register, "rax");
                        }
                    }
                    let label = function_label(name);
                    self.call_site(&label);
                }
            }
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
