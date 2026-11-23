//! Code generation, first slice: the entry stub and frameless functions with simple expressions.
//!
//! The byte-level parity gate compares `.text` with the Python generator over a curated set of
//! simple sources. Wider lowering shapes are added in later slices.

use crate::codegen::function_label;
use crate::image::{iat_label, Artifact, ENTRY_LABEL, ReadOnlyData, WritableData};
use crate::machine::Assembler;
use crate::runtime::{emit_runtime, OUT_FLUSH_LABEL, PRINT_CHAR_LABEL, PRINT_HEX_LABEL, PRINT_LABEL};
use crate::syntax::{Expr, Program, Stmt};

/// The array spans declared in one function: `(base slot, element count)`.
fn collect_arrays(block: &crate::syntax::Block, found: &mut Vec<(i64, usize)>) {
    for statement in &block.statements {
        match statement {
            Stmt::Let { slot, array_size: Some(size), .. } => found.push((*slot, *size)),
            Stmt::If { then_block, else_block, .. } => {
                collect_arrays(then_block, found);
                if let Some(otherwise) = else_block {
                    collect_arrays(otherwise, found);
                }
            }
            Stmt::While { body, .. } | Stmt::Block(body) => collect_arrays(body, found),
            _ => {}
        }
    }
}

/// Slots whose first top-level touch is an assignment, so no entry clear is needed.
fn assigned_slots(block: &crate::syntax::Block) -> std::collections::BTreeSet<i64> {
    let mut found = std::collections::BTreeSet::new();
    for statement in &block.statements {
        match statement {
            Stmt::Let { slot, .. } | Stmt::Assign { slot, .. } => {
                found.insert(*slot);
            }
            _ => {}
        }
    }
    found
}

fn in_array(slot: i64, arrays: &[(i64, usize)]) -> bool {
    arrays.iter().any(|(base, size)| slot >= *base && slot < *base + *size as i64)
}

fn expr_slot(expr: &Expr) -> Option<i64> {
    match crate::optimize::peel_parens(Some(expr)).unwrap() {
        Expr::Name { slot, .. } => Some(*slot),
        _ => None,
    }
}

fn expr_has_call(expr: &Expr) -> bool {
    match expr {
        Expr::Call { .. } => true,
        Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => expr_has_call(inner),
        Expr::Binary { left, right, .. } | Expr::Index { operand: left, index: right, .. } => {
            expr_has_call(left) || expr_has_call(right)
        }
        Expr::ArrayLiteral { elements, .. } => elements.iter().any(expr_has_call),
        _ => false,
    }
}

fn expression_may_hold(expr: &Expr) -> bool {
    match expr {
        Expr::Binary { op, right, .. } => {
            if op != "&&" && op != "||" && expr_has_call(right) {
                return true;
            }
            match expr {
                Expr::Binary { left, right, .. } => {
                    expression_may_hold(left) || expression_may_hold(right)
                }
                _ => false,
            }
        }
        Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => {
            expression_may_hold(inner)
        }
        Expr::Call { arguments, .. } => arguments.iter().any(expression_may_hold),
        _ => false,
    }
}

fn body_may_hold(block: &crate::syntax::Block) -> bool {
    for statement in &block.statements {
        match statement {
            Stmt::Let { value, .. } | Stmt::Assign { value, .. }
            | Stmt::Return { value, .. } | Stmt::ExprStmt { expression: value, .. } => {
                if expression_may_hold(value) {
                    return true;
                }
            }
            Stmt::If { condition, then_block, else_block, .. } => {
                if expression_may_hold(condition) || body_may_hold(then_block) {
                    return true;
                }
                if let Some(otherwise) = else_block {
                    if body_may_hold(otherwise) {
                        return true;
                    }
                }
            }
            Stmt::While { condition, body, .. } => {
                if expression_may_hold(condition) || body_may_hold(body) {
                    return true;
                }
            }
            Stmt::Block(inner) => {
                if body_may_hold(inner) {
                    return true;
                }
            }
            _ => {}
        }
    }
    false
}

/// The register home of a slot, when the slot is kept in a register.
fn home_for<'a>(slot: i64, homes: &'a [(i64, &'a str)]) -> Option<&'a str> {
    homes.iter().find(|(found, _)| *found == slot).map(|(_, register)| *register)
}

/// The constant shift a self-call's argument adds to the inlined parameter.
fn delta_argument(expression: &Expr, slot: i64) -> Option<i64> {
    match crate::optimize::peel_parens(Some(expression)).unwrap() {
        Expr::Name { slot: found, .. } if *found == slot => Some(0),
        Expr::Binary { op, left, right, .. } if op == "+" || op == "-" => {
            let left = crate::optimize::peel_parens(Some(left)).unwrap();
            let right = crate::optimize::peel_parens(Some(right)).unwrap();
            if let (Expr::Name { slot: found, .. }, Expr::IntLiteral { value, .. }) = (left, right) {
                if *found == slot {
                    return Some(if op == "+" { *value as i64 } else { -(*value as i64) });
                }
            }
            None
        }
        _ => None,
    }
}

fn body_assigns(block: &crate::syntax::Block) -> bool {
    block.statements.iter().any(|statement| match statement {
        Stmt::Let { .. } | Stmt::Assign { .. } => true,
        Stmt::If { then_block, else_block, .. } => {
            body_assigns(then_block)
                || else_block.as_ref().map_or(false, body_assigns)
        }
        Stmt::While { body, .. } | Stmt::Block(body) => body_assigns(body),
        _ => false,
    })
}

fn has_delta_self_call(block: &crate::syntax::Block, name: &str, slot: i64) -> bool {
    fn in_expr(expression: &Expr, name: &str, slot: i64) -> bool {
        if let Expr::Call { name: found, arguments, .. } = expression {
            if found == name && arguments.len() == 1 && delta_argument(&arguments[0], slot).is_some() {
                return true;
            }
            return arguments.iter().any(|argument| in_expr(argument, name, slot));
        }
        match expression {
            Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => in_expr(inner, name, slot),
            Expr::Binary { left, right, .. } | Expr::Index { operand: left, index: right, .. } => {
                in_expr(left, name, slot) || in_expr(right, name, slot)
            }
            Expr::ArrayLiteral { elements, .. } => elements.iter().any(|element| in_expr(element, name, slot)),
            _ => false,
        }
    }
    block.statements.iter().any(|statement| match statement {
        Stmt::Let { value, .. } | Stmt::Assign { value, .. } | Stmt::Return { value, .. } => {
            in_expr(value, name, slot)
        }
        Stmt::ExprStmt { expression, .. } => in_expr(expression, name, slot),
        Stmt::If { condition, then_block, else_block, .. } => {
            in_expr(condition, name, slot)
                || has_delta_self_call(then_block, name, slot)
                || else_block.as_ref().map_or(false, |block| has_delta_self_call(block, name, slot))
        }
        Stmt::While { condition, body, .. } => {
            in_expr(condition, name, slot) || has_delta_self_call(body, name, slot)
        }
        Stmt::Block(inner) => has_delta_self_call(inner, name, slot),
        _ => false,
    })
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
    delta_slot: i64,
    delta_value: i64,
    inline_depth: usize,
    current_name: String,
    current_body: crate::syntax::Block,
    arrays: Vec<(i64, usize)>,
    /// Per-loop invariant big-literal hoists, keyed by the While statement address.
    hoist_plans: std::collections::BTreeMap<usize, Vec<(i128, String)>>,
    /// Active value -> spare home map while a hoisted loop body lowers.
    hoist_imm: std::collections::BTreeMap<i128, String>,
    /// Spare homes saved by the frameless prologue for hoisted constants.
    hoist_homes: Vec<String>,
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
            delta_slot: -1,
            delta_value: 0,
            inline_depth: 0,
            current_name: String::new(),
            current_body: crate::syntax::Block {
                span: crate::diagnostics::Span::point(0, 1, 1),
                statements: Vec::new(),
            },
            arrays: Vec::new(),
            hoist_plans: std::collections::BTreeMap::new(),
            hoist_imm: std::collections::BTreeMap::new(),
            hoist_homes: Vec::new(),
        }
    }

    fn fresh(&mut self, stem: &str) -> String {
        self.counter += 1;
        format!("{}.{}", stem, self.counter)
    }

    fn loops_within(block: &crate::syntax::Block, found: &mut Vec<*const Stmt>) {
        for statement in &block.statements {
            match statement {
                Stmt::While { body, .. } => {
                    found.push(statement as *const Stmt);
                    Self::loops_within(body, found);
                }
                Stmt::If { then_block, else_block, .. } => {
                    Self::loops_within(then_block, found);
                    if let Some(otherwise) = else_block {
                        Self::loops_within(otherwise, found);
                    }
                }
                _ => {}
            }
        }
    }

    fn count_add(counts: &mut Vec<(i128, usize)>, value: i128) {
        if value < -0x80000000 || value > 0x7FFFFFFF {
            if let Some(entry) = counts.iter_mut().find(|(found, _)| *found == value) {
                entry.1 += 1;
            } else {
                counts.push((value, 1));
            }
        }
    }

    fn count_literals(expr: &Expr, counts: &mut Vec<(i128, usize)>) {
        match expr {
            Expr::IntLiteral { value, .. } => Self::count_add(counts, *value),
            Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => {
                Self::count_literals(inner, counts);
            }
            Expr::Binary { left, right, .. } | Expr::Index { operand: left, index: right, .. } => {
                Self::count_literals(left, counts);
                Self::count_literals(right, counts);
            }
            Expr::Call { arguments, .. } | Expr::ArrayLiteral { elements: arguments, .. } => {
                for argument in arguments {
                    Self::count_literals(argument, counts);
                }
            }
            _ => {}
        }
    }

    fn count_literals_block(block: &crate::syntax::Block, counts: &mut Vec<(i128, usize)>) {
        for statement in &block.statements {
            match statement {
                Stmt::Let { value, .. } | Stmt::Assign { value, .. } => {
                    Self::count_literals(value, counts);
                }
                Stmt::If { condition, then_block, else_block, .. } => {
                    Self::count_literals(condition, counts);
                    Self::count_literals_block(then_block, counts);
                    if let Some(otherwise) = else_block {
                        Self::count_literals_block(otherwise, counts);
                    }
                }
                Stmt::While { condition, body, .. } => {
                    Self::count_literals(condition, counts);
                    Self::count_literals_block(body, counts);
                }
                Stmt::Return { value, .. } | Stmt::ExprStmt { expression: value, .. } => {
                    Self::count_literals(value, counts);
                }
                _ => {}
            }
        }
    }

    /// Reserve spare homes for each loop's most-used big literals, outer loops first.
    fn plan_constant_hoists(&mut self, body: &crate::syntax::Block, homes_len: usize) {
        self.hoist_plans.clear();
        self.hoist_imm.clear();
        self.hoist_homes.clear();
        let spare: Vec<String> = crate::codegen::HOME_REGISTERS
            .iter().skip(homes_len).map(|name| name.to_string()).collect();
        if spare.is_empty() {
            return;
        }
        let mut loops = Vec::new();
        Self::loops_within(body, &mut loops);
        let mut cursor = 0usize;
        for statement in loops {
            let Stmt::While { condition, body: loop_body, .. } =
                (unsafe { &*statement }) else { continue };
            let mut counts = Vec::new();
            Self::count_literals(condition, &mut counts);
            Self::count_literals_block(loop_body, &mut counts);
            if counts.is_empty() || cursor >= spare.len() {
                continue;
            }
            counts.sort_by(|a, b| b.1.cmp(&a.1)); // stable: ties keep first-seen order
            let take = 3.min(spare.len() - cursor);
            let mut plan = Vec::new();
            for ((value, _), home) in counts.into_iter().take(take)
                .zip(spare[cursor..cursor + take].iter()) {
                plan.push((value, home.clone()));
                self.hoist_homes.push(home.clone());
            }
            cursor += take;
            self.hoist_plans.insert(statement as usize, plan);
            if cursor >= spare.len() {
                break;
            }
        }
    }

    /// Materialise a literal from its hoisted spare home when one is active.
    fn emit_literal(&mut self, value: i128, destination: &str) {
        if let Some(home) = self.hoist_imm.get(&value).cloned() {
            if home != destination {
                self.assembler.mov_reg_reg(destination, &home);
            }
        } else {
            self.assembler.mov_imm64(destination, value as i64);
        }
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
    fn home_slots(function: &crate::syntax::Function, arrays: &[(i64, usize)]) -> Vec<i64> {
        if (function.slots as usize) <= crate::codegen::HOME_REGISTERS.len() {
            return (0..function.slots as i64)
                .filter(|slot| !in_array(*slot, arrays))
                .collect();
        }
        let mut ranks = std::collections::BTreeMap::new();
        fn touch(slot: i64, depth: usize, ranks: &mut std::collections::BTreeMap<i64, usize>) {
            ranks.entry(slot).and_modify(|value| *value = (*value).max(depth)).or_insert(depth);
        }
        fn expression(expr: &Expr, depth: usize, ranks: &mut std::collections::BTreeMap<i64, usize>) {
            match expr {
                Expr::Name { slot, .. } => touch(*slot, depth, ranks),
                Expr::Parens { inner, .. } | Expr::Unary { operand: inner, .. } => {
                    expression(inner, depth, ranks);
                }
                Expr::Binary { left, right, .. } | Expr::Index { operand: left, index: right, .. } => {
                    expression(left, depth, ranks);
                    expression(right, depth, ranks);
                }
                Expr::Call { arguments, .. } | Expr::ArrayLiteral { elements: arguments, .. } => {
                    for argument in arguments { expression(argument, depth, ranks); }
                }
                _ => {}
            }
        }
        fn block(body: &crate::syntax::Block, depth: usize,
                 ranks: &mut std::collections::BTreeMap<i64, usize>) {
            for statement in &body.statements {
                match statement {
                    Stmt::Let { value, slot, .. } | Stmt::Assign { value, slot, .. } => {
                        expression(value, depth, ranks);
                        touch(*slot, depth, ranks);
                    }
                    Stmt::If { condition, then_block, else_block, .. } => {
                        expression(condition, depth, ranks);
                        block(then_block, depth, ranks);
                        if let Some(otherwise) = else_block { block(otherwise, depth, ranks); }
                    }
                    Stmt::While { condition, body, .. } => {
                        expression(condition, depth, ranks);
                        block(body, depth + 1, ranks);
                    }
                    Stmt::Return { value, .. } | Stmt::ExprStmt { expression: value, .. } => {
                        expression(value, depth, ranks);
                    }
                    Stmt::Block(inner) => block(inner, depth, ranks),
                    _ => {}
                }
            }
        }
        block(&function.body, 0, &mut ranks);
        let mut ranked: Vec<i64> = (0..function.slots as i64).collect();
        ranked.sort_by(|a, b| {
            let ra = ranks.get(a).copied().unwrap_or(0);
            let rb = ranks.get(b).copied().unwrap_or(0);
            rb.cmp(&ra).then_with(|| b.cmp(a))
        });
        ranked.retain(|slot| !in_array(*slot, arrays));
        ranked
    }

    /// Emit one function: frameless when every slot has a home, otherwise a frame function.
    fn emit_function(&mut self, function: &crate::syntax::Function) {
        self.epilogue = format!("{}.epilogue", function_label(&function.name));
        self.assembler.label(&function_label(&function.name));
        let mut array_spans = Vec::new();
        collect_arrays(&function.body, &mut array_spans);
        let ranked = Self::home_slots(function, &array_spans);
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
        let frame_function = function.slots > callee_homes || !array_spans.is_empty();
        let delta_eligible = function.slots == 1
            && function.parameters.len() == 1
            && !body_assigns(&function.body)
            && has_delta_self_call(&function.body, &function.name, function.parameters[0].slot);
        self.delta_slot = if delta_eligible { function.parameters[0].slot } else { -1 };
        self.delta_value = 0;
        self.inline_depth = 0;
        self.current_name = function.name.clone();
        self.current_body = function.body.clone();
        self.arrays = array_spans.clone();
        let callee_registers: Vec<&str> = homes[..callee_count]
            .iter()
            .map(|(_, register)| *register)
            .collect();
        let body = function.body.clone();
        if !frame_function && !delta_eligible && self.inline_depth == 0 {
            self.plan_constant_hoists(&body, callee_count);
        } else {
            self.hoist_plans.clear();
            self.hoist_imm.clear();
            self.hoist_homes.clear();
        }
        let saved_units = callee_count + self.hoist_homes.len();
        let merging = !frame_function && !delta_eligible && body_may_hold(&body);
        let pad = frame_function || saved_units % 2 == 0 || merging;
        let filler = merging && saved_units % 2 == 1;
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
            let assigned = assigned_slots(&function.body);
            for (slot, register) in &homes {
                if *slot as usize >= function.parameters.len() && !assigned.contains(slot) {
                    self.assembler.xor_zero(register);
                }
            }
        } else {
            for register in &callee_registers {
                self.assembler.push(register);
            }
            for register in &self.hoist_homes {
                self.assembler.push(register);
            }
            if delta_eligible {
                self.assembler.sub_rsp(8 * 8);
            } else {
                if filler {
                    self.assembler.push(crate::codegen::HOME_REGISTERS.last().unwrap());
                }
                if pad {
                    self.assembler.sub_rsp(8);
                }
            }
            let assigned = assigned_slots(&body);
            for (slot, register) in &homes {
                if *slot as usize >= function.parameters.len() && !assigned.contains(slot) {
                    self.assembler.xor_zero(register);
                }
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
            if delta_eligible {
                self.assembler.add_rsp(8 * 8);
            } else {
                if pad {
                    self.assembler.add_rsp(8);
                }
                if filler {
                    self.assembler.pop(crate::codegen::HOME_REGISTERS.last().unwrap());
                }
            }
            for register in self.hoist_homes.iter().rev() {
                self.assembler.pop(register);
            }
            for register in callee_registers.iter().rev() {
                self.assembler.pop(register);
            }
            self.assembler.ret();
        }
        self.delta_slot = -1;
        self.delta_value = 0;
        self.inline_depth = 0;
        self.current_name.clear();
    }



    fn load_slot(&mut self, slot: i64, destination: &str, homes: &[(i64, &str)]) {
        if let Some(home) = home_for(slot, homes) {
            if slot == self.delta_slot && self.delta_value != 0 {
                self.assembler.lea_imm(destination, home, -self.delta_value);
            } else if home != destination {
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

    /// Leave array element `a[index]` in `rax`, matching Python's `_emit_index_load`.
    fn emit_index_load(&mut self, index: &Expr, slot: i64, size: usize,
                       homes: &[(i64, &str)]) -> bool {
        match index {
            Expr::IntLiteral { value, .. } => {
                self.assembler.mov_load_rbp(
                    "rax", crate::codegen::slot_offset(slot + size as i64 - 1 - *value as i64));
                true
            }
            Expr::Name { slot: index_slot, .. } => {
                match home_for(*index_slot, homes) {
                    Some(index_home) => {
                        self.assembler.mov_load_indexed(
                            "rax", "rbp", index_home, 3,
                            crate::codegen::array_element_base(slot, size as i64));
                        true
                    }
                    None => false,
                }
            }
            Expr::Parens { inner, .. } => self.emit_index_load(inner, slot, size, homes),
            _ => false,
        }
    }

    fn emit_left_into(&mut self, expression: &Expr, destination: &str,
                      homes: &[(i64, &str)]) -> bool {
        let expr = crate::optimize::peel_parens(Some(expression)).unwrap();
        match expr {
            Expr::IntLiteral { value, .. } => {
                self.emit_literal(*value, destination);
                true
            }
            Expr::Name { slot, .. } => match home_for(*slot, homes) {
                Some(home) if home == destination => true,
                Some(home) if destination == "rax" => {
                    self.assembler.mov_reg_reg("rax", home);
                    true
                }
                Some(home) => {
                    self.assembler.mov_reg_reg(destination, home);
                    true
                }
                None => {
                    self.load_slot(*slot, destination, homes);
                    true
                }
            },
            Expr::Binary { .. } => self.try_emit_binary_into(expr, destination, homes),
            _ => false,
        }
    }

    fn combines_in_rax_only(&self, expression: &Expr, destination: &str,
                            parked_noop: bool, homes: &[(i64, &str)]) -> bool {
        let expr = crate::optimize::peel_parens(Some(expression)).unwrap();
        match expr {
            Expr::IntLiteral { .. } => true,
            Expr::Name { slot, .. } => match home_for(*slot, homes) {
                Some(home) => home != destination || parked_noop,
                None => true,
            },
            Expr::Binary { op, left, right, .. } => {
                if matches!(op.as_str(), "*" | "/" | "%" | "&&" | "||"
                    | "==" | "!=" | "<" | "<=" | ">" | ">=") {
                    return false;
                }
                let right = crate::optimize::peel_parens(Some(right)).unwrap();
                let Expr::IntLiteral { value, .. } = right else { return false };
                let in_range = *value >= -0x80000000 && *value <= 0x7FFFFFFF;
                if matches!(op.as_str(), "+" | "-" | "&" | "|" | "^") && in_range {
                    return self.combines_in_rax_only(left, destination, parked_noop, homes);
                }
                if matches!(op.as_str(), "<<" | ">>") && *value >= 0 && *value <= 63 {
                    return self.combines_in_rax_only(left, destination, parked_noop, homes);
                }
                false
            }
            _ => false,
        }
    }

    /// Compute a binary expression straight into `destination`, matching Python's
    /// `_emit_binary_into`; refuses shapes it cannot keep destination untouched.
    fn try_emit_binary_into(&mut self, expression: &Expr, destination: &str,
                            homes: &[(i64, &str)]) -> bool {
        if destination == "rax" {
            return false;
        }
        let Expr::Binary { op, left, right, .. } =
            crate::optimize::peel_parens(Some(expression)).unwrap() else { return false };
        let right = crate::optimize::peel_parens(Some(right)).unwrap();
        if let Expr::IntLiteral { value, .. } = right {
            let ext = match op.as_str() {
                "+" => Some(0), "-" => Some(5), "&" => Some(4), "|" => Some(1), "^" => Some(6),
                _ => None,
            };
            if let Some(ext) = ext {
                if *value >= -0x80000000 && *value <= 0x7FFFFFFF
                    && self.emit_left_into(left, destination, homes) {
                    self.assembler.arith_imm(ext, destination, *value as i64);
                    return true;
                }
            }
        }
        if op == "*" {
            let peeled_left = crate::optimize::peel_parens(Some(left)).unwrap();
            if let Expr::Name { slot, .. } = peeled_left {
                if let (Some(home), Expr::IntLiteral { value, .. }) =
                    (home_for(*slot, homes), right) {
                    if *value >= -2147483648 && *value <= 2147483647 {
                        self.assembler.imul_imm(destination, home, *value as i64);
                        return true;
                    }
                }
            }
            if self.emit_left_into(left, destination, homes) {
                if let Expr::Name { slot, .. } = right {
                    if let Some(home) = home_for(*slot, homes) {
                        self.assembler.imul(destination, home);
                        return true;
                    }
                }
                self.emit_expression(right, homes);
                self.assembler.imul(destination, "rax");
                return true;
            }
            return false;
        }
        if op == "<<" || op == ">>" {
            if let Expr::IntLiteral { value, .. } = right {
                if *value >= 0 && *value <= 63 && self.emit_left_into(left, destination, homes) {
                    self.assembler.shift_imm(if op == "<<" { 4 } else { 7 },
                                             destination, *value as u8);
                    return true;
                }
            }
            return false;
        }
        if let Some(opcode) = match op.as_str() {
            "+" => Some(0x01), "&" => Some(0x21), "|" => Some(0x09), "^" => Some(0x31),
            _ => None,
        } {
            let peeled_left = crate::optimize::peel_parens(Some(left)).unwrap();
            let parked_noop = matches!(peeled_left, Expr::Name { slot, .. }
                if home_for(*slot, homes) == Some(destination));
            if self.combines_in_rax_only(right, destination, parked_noop, homes)
                && self.emit_left_into(left, destination, homes) {
                if let Expr::Name { slot, .. } = right {
                    if let Some(home) = home_for(*slot, homes) {
                        self.assembler.binary(opcode, destination, home);
                        return true;
                    }
                }
                self.emit_expression(right, homes);
                self.assembler.binary(opcode, destination, "rax");
                return true;
            }
        }
        false
    }

    /// Emit `home = home op value` directly when the expression updates its own slot.
    fn try_fold_update(&mut self, slot: i64, expression: &Expr, home: &str,
                       homes: &[(i64, &str)]) -> bool {
        let Expr::Binary { op, left, right, .. } =
            crate::optimize::peel_parens(Some(expression)).unwrap() else { return false };
        let commutative = matches!(op.as_str(), "+" | "*" | "&" | "|" | "^");
        let other = if commutative {
            if expr_slot(left) == Some(slot) {
                Some(right.as_ref())
            } else if expr_slot(right) == Some(slot) {
                Some(left.as_ref())
            } else {
                None
            }
        } else if op == "-" && expr_slot(left) == Some(slot) {
            Some(right.as_ref())
        } else {
            None
        };
        let Some(other) = other else { return false };
        let opcode = match op.as_str() {
            "+" => Some(0x01), "-" => Some(0x29), "&" => Some(0x21),
            "|" => Some(0x09), "^" => Some(0x31), _ => None,
        };
        let other = crate::optimize::peel_parens(Some(other)).unwrap();
        if matches!(op.as_str(), "+" | "-") {
            if let Expr::IntLiteral { value, .. } = other {
                if *value >= -0x80000000 && *value <= 0x7FFFFFFF {
                    if op == "+" {
                        self.assembler.add_imm(home, *value as i64);
                    } else {
                        self.assembler.sub_imm(home, *value as i64);
                    }
                    return true;
                }
            }
        }
        if let Expr::Name { slot: other_slot, .. } = other {
            if let Some(other_home) = home_for(*other_slot, homes) {
                if op == "*" {
                    self.assembler.imul(home, other_home);
                } else {
                    self.assembler.binary(opcode.unwrap(), home, other_home);
                }
                return true;
            }
        }
        self.emit_expression(other, homes);
        if op == "*" {
            self.assembler.imul(home, "rax");
        } else {
            self.assembler.binary(opcode.unwrap(), home, "rax");
        }
        true
    }

    fn emit_value(&mut self, slot: i64, expression: &Expr, homes: &[(i64, &str)]) {
        if let Some(home) = home_for(slot, homes) {
            if self.try_fold_update(slot, expression, home, homes) {
                return;
            }
        }
        match expression {
            Expr::IntLiteral { value, .. } => {
                if *value == 0 {
                    if let Some(home) = home_for(slot, homes) {
                        self.assembler.xor_zero(home);
                        return;
                    }
                }
                self.emit_literal(*value, "rax");
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
            Expr::ArrayLiteral { elements, .. } => {
                let fill = if elements.len() > crate::codegen::ARRAY_UNROLL_LIMIT {
                    match elements.first() {
                        Some(Expr::IntLiteral { value, .. })
                            if elements.iter().all(|element| matches!(element,
                                Expr::IntLiteral { value: found, .. } if found == value)) =>
                        {
                            Some(*value)
                        }
                        _ => None,
                    }
                } else {
                    None
                };
                if let Some(value) = fill {
                    let loop_label = self.fresh("array_fill");
                    self.assembler.mov_imm64("rdx", elements.len() as i64);
                    self.assembler.mov_imm64("rax", value as i64);
                    let base = crate::codegen::array_element_base(slot, elements.len() as i64);
                    self.assembler.lea_rbp("rcx", base);
                    let label = loop_label.clone();
                    self.assembler.label(&label);
                    self.assembler.mov_store_reg("rcx", "rax");
                    self.assembler.add_imm("rcx", 8);
                    self.assembler.sub_imm("rdx", 1);
                    let again = loop_label.clone();
                    self.assembler.jcc("nz", &again);
                } else {
                    for (index, element) in elements.iter().enumerate() {
                        self.emit_expression(element, homes);
                        self.assembler.mov_store_rbp(
                            crate::codegen::slot_offset(
                                slot + elements.len() as i64 - 1 - index as i64),
                            "rax");
                    }
                }
            }
            Expr::Binary { op, left, right, .. }
                if op == "+"
                    && matches!(left.as_ref(), Expr::Name { slot: src, .. } if *src == slot)
                    && matches!(right.as_ref(), Expr::IntLiteral { .. }) =>
            {
                let Expr::IntLiteral { value, .. } = right.as_ref() else { unreachable!() };
                if *value >= -0x80000000 && *value <= 0x7FFFFFFF {
                    if let Some(home) = home_for(slot, homes) {
                        self.assembler.add_imm(home, *value as i64);
                        return;
                    }
                }
                if let Some(home) = home_for(slot, homes) {
                    self.emit_literal(*value, "rax");
                    self.assembler.binary(0x01, home, "rax");
                } else {
                    self.load_slot(slot, "rax", homes);
                    self.emit_literal(*value, "rcx");
                    self.assembler.binary(0x01, "rax", "rcx");
                    self.store_slot(slot, "rax", homes);
                }
            }
            Expr::Binary { op, left, right, .. }
                if op == "+" && matches!(left.as_ref(), Expr::Name { slot: src, .. } if *src == slot)
                    && matches!(right.as_ref(), Expr::Index { .. }) =>
            {
                let Expr::Index { slot: array_slot, size, index, .. } = right.as_ref()
                    else { unreachable!() };
                let home = home_for(slot, homes);
                if let Some(home) = home {
                    if self.emit_index_load(index, *array_slot, *size, homes) {
                        self.assembler.binary(0x01, home, "rax");
                        return;
                    }
                }
            }
            Expr::Binary { op, left, right, .. }
                if op == "+" && matches!(right.as_ref(), Expr::Name { slot: src, .. } if *src == slot)
                    && matches!(left.as_ref(), Expr::Index { .. }) =>
            {
                let Expr::Index { slot: array_slot, size, index, .. } = left.as_ref()
                    else { unreachable!() };
                let home = home_for(slot, homes);
                if let Some(home) = home {
                    if self.emit_index_load(index, *array_slot, *size, homes) {
                        self.assembler.binary(0x01, home, "rax");
                        return;
                    }
                }
            }
            other => {
                if let Some(home) = home_for(slot, homes) {
                    if self.try_emit_binary_into(other, home, homes) {
                        return;
                    }
                }
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
            Stmt::Assign { slot, value, index: Some(index), array_size: Some(size), .. }
                if matches!(value, Expr::Name { .. })
                    && matches!(index, Expr::Name { .. }) =>
            {
                let Expr::Name { slot: index_slot, .. } = index else { unreachable!() };
                let Expr::Name { slot: value_slot, .. } = value else { unreachable!() };
                match (home_for(*index_slot, homes), home_for(*value_slot, homes)) {
                    (Some(index_home), Some(value_home)) if index_home == value_home => {
                        let displacement = crate::codegen::array_element_base(*slot, *size as i64);
                        self.assembler.mov_store_indexed(
                            "rbp", index_home, 3, displacement, value_home);
                    }
                    _ => self.emit_value(*slot, value, homes),
                }
            }
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
            Stmt::While { condition, body, countdown, .. } => {
                let plan = self.hoist_plans.get(&(statement as *const Stmt as usize)).cloned();
                let saved = self.hoist_imm.clone();
                if let Some(plan) = plan {
                    for (value, home) in &plan {
                        self.assembler.mov_imm64(home, *value as i64);
                    }
                    for (value, home) in &plan {
                        self.hoist_imm.insert(*value, home.clone());
                    }
                }
                if !self.try_emit_rmw_vector(condition, body, homes) {
                    self.emit_while(condition, body, *countdown, homes);
                }
                self.hoist_imm = saved;
            }
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

    /// Lower `while i < N { a[i] = a[i] + r; i = i + 1; }` with two SSE2 lanes.
    fn try_emit_rmw_vector(&mut self, condition: &Expr, body: &crate::syntax::Block,
                           homes: &[(i64, &str)]) -> bool {
        let Expr::Binary { op, left, right, .. } = condition else { return false };
        if op != "<" { return false; }
        let (index_slot, bound) = match (left.as_ref(), right.as_ref()) {
            (Expr::Name { slot, .. }, Expr::IntLiteral { value, .. }) => (*slot, *value),
            _ => return false,
        };
        if bound < 2 || bound % 2 != 0 || body.statements.len() != 2 { return false; }
        let Stmt::Assign { slot: store_slot, index: Some(store_index), value: store_value,
                           array_size, .. } = &body.statements[0] else { return false; };
        let Some(array_size) = array_size else { return false; };
        let Expr::Binary { op: add_op, left: add_left, right: add_right, .. } = store_value else {
            return false;
        };
        if add_op != "+" { return false; }
        let element = match (add_left.as_ref(), add_right.as_ref()) {
            (Expr::Index { .. }, _) => Some(add_left.as_ref()),
            (_, Expr::Index { .. }) => Some(add_right.as_ref()),
            _ => None,
        };
        let Some(Expr::Index { operand, index, .. }) = element else { return false; };
        if !matches!(operand.as_ref(), Expr::Name { .. }) { return false; }
        if !matches!(index.as_ref(), Expr::Name { slot, .. } if *slot == index_slot) { return false; }
        if !matches!(store_index, Expr::Name { slot, .. } if *slot == index_slot) { return false; }
        let addend = if matches!(add_left.as_ref(), Expr::Index { .. }) {
            add_right.as_ref()
        } else {
            add_left.as_ref()
        };
        let Expr::Name { slot: addend_slot, .. } = addend else { return false; };
        if *addend_slot == index_slot { return false; }
        let Stmt::Assign { slot: step_slot, value: step_value, .. } = &body.statements[1] else {
            return false;
        };
        if *step_slot != index_slot { return false; }
        let Expr::Binary { op: step_op, left: step_left, right: step_right, .. } = step_value else {
            return false;
        };
        if step_op != "+" { return false; }
        if !matches!(step_left.as_ref(), Expr::Name { slot, .. } if *slot == index_slot) { return false; }
        if !matches!(step_right.as_ref(), Expr::IntLiteral { value, .. } if *value == 1) { return false; }
        let (Some(index_home), Some(addend_home)) =
            (home_for(index_slot, homes), home_for(*addend_slot, homes)) else { return false };
        if index_home == addend_home { return false; }

        let start = self.fresh("vec_rmw.start");
        let end = self.fresh("vec_rmw.end");
        let displacement = crate::codegen::array_element_base(*store_slot, *array_size as i64);
        self.assembler.movq_from_reg("xmm1", addend_home);
        self.assembler.punpcklqdq("xmm1", "xmm1");
        let start_label = start.clone();
        self.assembler.label(&start_label);
        self.assembler.cmp_imm(index_home, bound as i64);
        let done = end.clone();
        self.assembler.jcc("ge", &done);
        self.assembler.movdqu_load_indexed("xmm0", "rbp", index_home, 3, displacement);
        self.assembler.paddq_reg("xmm0", "xmm1");
        self.assembler.movdqu_store_indexed("rbp", index_home, 3, displacement, "xmm0");
        self.assembler.add_imm(index_home, 2);
        let again = start.clone();
        self.assembler.jmp(&again);
        let end_label = end.clone();
        self.assembler.label(&end_label);
        true
    }

    fn emit_while(&mut self, condition: &Expr, body: &crate::syntax::Block,
                  countdown: bool, homes: &[(i64, &str)]) {
        let start = self.fresh("while.start");
        let end = self.fresh("while.end");
        self.emit_condition_polarity(condition, &end, homes, false);
        let pad = (16 - self.assembler.here() % 16) % 16;
        if pad > 0 {
            self.assembler.nop_pad(pad);
        }
        let start_label = start.clone();
        self.assembler.label(&start_label);
        if countdown {
            self.emit_block(body, false, homes);
            let counter = match crate::optimize::peel_parens(Some(condition)).unwrap() {
                Expr::Binary { left, right, .. } => {
                    expr_slot(left).or_else(|| expr_slot(right))
                }
                _ => None,
            };
            if let Some(slot) = counter {
                if let Some(home) = home_for(slot, homes) {
                    self.assembler.sub_imm(home, 1);
                } else {
                    self.load_slot(slot, "rax", homes);
                    self.assembler.sub_imm("rax", 1);
                    self.store_slot(slot, "rax", homes);
                }
            }
            let again = start.clone();
            self.assembler.jcc("nz", &again);
            let end_label = end.clone();
            self.assembler.label(&end_label);
            return;
        }
        let latch = self.fresh("while.latch");
        self.emit_block(body, false, homes);
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
        let literal = if name_slot == self.delta_slot {
            literal + self.delta_value
        } else {
            literal
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
        if let Some(otherwise_block) = else_block {
            let then_label = self.fresh("if.then");
            let end_label = self.fresh("if.end");
            self.emit_condition(condition, &then_label, homes);
            self.emit_block(otherwise_block, tail, homes);
            let end = end_label.clone();
            self.assembler.jmp(&end);
            let then = then_label.clone();
            self.assembler.label(&then);
            self.emit_block(then_block, tail, homes);
            let end = end_label.clone();
            self.assembler.label(&end);
        } else {
            let otherwise = self.fresh("if.else");
            self.emit_condition_polarity(condition, &otherwise, homes, false);
            self.emit_block(then_block, tail, homes);
            let label = otherwise.clone();
            self.assembler.label(&label);
        }
    }

    fn emit_expression(&mut self, expression: &Expr, homes: &[(i64, &str)]) {
        match expression {
            Expr::IntLiteral { value, .. } => self.emit_literal(*value, "rax"),
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
            Expr::Index { operand, index, slot: base, .. }
                if matches!(operand.as_ref(), Expr::Name { .. })
                    && matches!(index.as_ref(), Expr::IntLiteral { .. }) =>
            {
                let Expr::IntLiteral { value, .. } = index.as_ref() else { unreachable!() };
                let size = self.arrays.iter()
                    .find(|(span_base, _)| span_base == base)
                    .map(|(_, size)| *size)
                    .unwrap_or(1);
                self.assembler.mov_load_rbp(
                    "rax", crate::codegen::slot_offset(*base + size as i64 - 1 - *value as i64));
            }
            Expr::Index { index, slot, size, .. }
                if matches!(index.as_ref(), Expr::Name { .. }) =>
            {
                let Expr::Name { slot: index_slot, .. } = index.as_ref() else { unreachable!() };
                if let Some(index_home) = home_for(*index_slot, homes) {
                    self.assembler.mov_load_indexed(
                        "rax", "rbp", index_home, 3,
                        crate::codegen::array_element_base(*slot, *size as i64));
                }
            }
            Expr::Binary { op, left, right, .. } => {
                self.emit_binary(op, left, right, homes);
            }
            Expr::Unary { .. } | Expr::Index { .. } | Expr::ArrayLiteral { .. } => {}
        }
    }

    /// General binary lowering into `rax`, mirroring Python's `_emit_binary` core.
    fn emit_binary(&mut self, op: &str, left: &Expr, right: &Expr,
                   homes: &[(i64, &str)]) {
        let arith: Option<u8> = match op {
            "+" => Some(0x01), "&" => Some(0x21), "|" => Some(0x09), "^" => Some(0x31),
            _ => None,
        };
        let imm_ext: Option<u8> = match op {
            "+" => Some(0), "-" => Some(5), "&" => Some(4), "|" => Some(1), "^" => Some(6),
            _ => None,
        };
        if let Expr::IntLiteral { value, .. } = right {
            if *value >= -0x80000000 && *value <= 0x7FFFFFFF {
                if let Some(ext) = imm_ext {
                    self.emit_expression(left, homes);
                    self.assembler.arith_imm(ext, "rax", *value as i64);
                    return;
                }
            }
            if *value >= 0 && *value <= 63 {
                let ext = match op { "<<" => Some(4), ">>" => Some(7), _ => None };
                if let Some(ext) = ext {
                    self.emit_expression(left, homes);
                    self.assembler.shift_imm(ext, "rax", *value as u8);
                    return;
                }
            }
        }
        if let Some(opcode) = arith {
            if let Expr::Name { slot, .. } = left {
                if let Some(left_home) = home_for(*slot, homes) {
                    self.emit_expression(right, homes);
                    self.assembler.binary(opcode, "rax", left_home);
                    return;
                }
            }
        }
        let mut right_home: Option<String> = None;
        if let Expr::Name { slot, .. } = right {
            right_home = home_for(*slot, homes).map(str::to_string);
        } else if let Expr::IntLiteral { value, .. } = right {
            right_home = self.hoist_imm.get(value).cloned();
        }
        if let Some(right_home) = right_home {
            self.emit_expression(left, homes);
            if op == "-" {
                self.assembler.binary(0x29, "rax", &right_home);
            } else if op == "*" {
                self.assembler.imul("rax", &right_home);
            } else if let Some(opcode) = arith {
                self.assembler.binary(opcode, "rax", &right_home);
            }
            return;
        }
        self.emit_expression(left, homes);
        self.assembler.mov_reg_reg("rcx", "rax");
        self.emit_expression(right, homes);
        match op {
            "+" | "&" | "|" | "^" => self.assembler.binary(arith.unwrap(), "rax", "rcx"),
            "-" => {
                self.assembler.binary(0x29, "rcx", "rax");
                self.assembler.mov_reg_reg("rax", "rcx");
            }
            "*" => self.assembler.imul("rax", "rcx"),
            _ => {}
        }
    }

    /// Lower a constant-delta self-call as a pure-text copy of the current body.
    fn emit_delta_self_call(&mut self, delta: i64, homes: &[(i64, &str)]) {
        let saved_epilogue = self.epilogue.clone();
        let saved_value = self.delta_value;
        let saved_depth = self.inline_depth;
        let label = self.fresh(&format!("{}.delta", function_label(&self.current_name)));
        self.epilogue = label;
        self.inline_depth += 1;
        self.delta_value -= delta;
        let body = self.current_body.clone();
        self.emit_block(&body, true, homes);
        let end = self.epilogue.clone();
        self.assembler.label(&end);
        self.epilogue = saved_epilogue;
        self.delta_value = saved_value;
        self.inline_depth = saved_depth;
    }

    fn emit_call(&mut self, name: &str, arguments: &[Expr], homes: &[(i64, &str)]) {
        match name {
            "clz" => {
                self.emit_expression(&arguments[0], homes);
                self.assembler.lzcnt("rax", "rax");
                return;
            }
            "ctz" => {
                self.emit_expression(&arguments[0], homes);
                self.assembler.tzcnt("rax", "rax");
                return;
            }
            "bswap" => {
                self.emit_expression(&arguments[0], homes);
                self.assembler.bswap("rax");
                return;
            }
            "rotl" => {
                let value = &arguments[0];
                let mut count = crate::optimize::peel_parens(Some(&arguments[1])).unwrap();
                if let Expr::Binary { op, left, right, .. } = count {
                    if op == "&" {
                        if let Expr::IntLiteral { value: mask, .. } =
                            crate::optimize::peel_parens(Some(right)).unwrap() {
                            if *mask & 63 == 63 {
                                count = crate::optimize::peel_parens(Some(left)).unwrap();
                            }
                        }
                    }
                }
                let leaf_value = matches!(crate::optimize::peel_parens(Some(value)).unwrap(),
                    Expr::Name { .. } | Expr::IntLiteral { .. });
                let simple_count = matches!(count, Expr::Name { .. } | Expr::IntLiteral { .. });
                if leaf_value && simple_count {
                    match count {
                        Expr::IntLiteral { value: number, .. } => {
                            self.emit_literal(*number, "rcx");
                        }
                        Expr::Name { slot, .. } => {
                            if let Some(home) = home_for(*slot, homes) {
                                if home != "rcx" {
                                    self.assembler.mov_reg_reg("rcx", home);
                                }
                            } else {
                                self.load_slot(*slot, "rcx", homes);
                            }
                        }
                        _ => {}
                    }
                    self.emit_expression(value, homes);
                    self.assembler.rotate_cl(0);
                    return;
                }
                self.emit_expression(value, homes);
                self.assembler.push("rax");
                self.depth += 8;
                self.emit_expression(count, homes);
                self.assembler.mov_reg_reg("rcx", "rax");
                self.assembler.pop("rax");
                self.depth -= 8;
                self.assembler.rotate_cl(0);
                return;
            }
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
                if name == &self.current_name
                    && self.delta_slot >= 0
                    && self.inline_depth < 7
                    && arguments.len() == 1
                {
                    if let Some(delta) = delta_argument(&arguments[0], self.delta_slot) {
                        self.emit_delta_self_call(delta, homes);
                        return;
                    }
                }
                // Pure arguments: a literal goes straight into its ABI register; any other form is
                // evaluated into rax and moved. Wider shapes are added in later slices.
                if arguments.len() <= 4 {
                    for (index, argument) in arguments.iter().enumerate() {
                        let register = ["rcx", "rdx", "r8", "r9"][index];
                        if let Expr::IntLiteral { value, .. } = argument {
                            self.assembler.mov_imm64(register, *value as i64);
                        } else if let Expr::Name { slot, .. } = argument {
                            if let Some(home) = home_for(*slot, homes) {
                                if *slot == self.delta_slot && self.delta_value != 0 {
                                    self.assembler.lea_imm(register, home, -self.delta_value);
                                } else {
                                    self.assembler.mov_reg_reg(register, home);
                                }
                            } else {
                                self.load_slot(*slot, "rax", homes);
                                self.assembler.mov_reg_reg(register, "rax");
                            }
                        } else if let Some(offset) = delta_argument(argument, self.delta_slot) {
                            if self.delta_slot >= 0 {
                                if let Some(home) = home_for(self.delta_slot, homes) {
                                    let effective = -(self.delta_value - offset);
                                    self.assembler.lea_imm(register, home, effective);
                                }
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
            text_label_order: self.assembler.label_order,
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
