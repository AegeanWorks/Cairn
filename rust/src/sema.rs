//! Semantic analysis: names, arity, mutability and return-path checks.
//!
//! Every check that can be made before machine code is made here, so the backend never has to
//! report a user error. All findings are collected and reported together: an agent repairing a
//! file sees every problem in one pass instead of one per build.

use std::collections::HashMap;

use crate::diagnostics::{Diagnostic, DiagnosticError, Span};
use crate::lexer::INT64_MAX;
use crate::syntax::{Block, Expr, Function, Program, Stmt};

/// The whole standard library and each builtin's arity, in registry order.
pub const BUILTINS: &[(&str, usize)] = &[
    ("print", 1),
    ("print_char", 1),
    ("print_hex", 1),
    ("argc", 0),
    ("arg_byte", 2),
    ("read_char", 0),
    ("exit", 1),
    ("print_str", 1),
    ("str_len", 1),
    ("str_byte", 2),
    ("flush", 0),
    ("popcount", 1),
    ("clz", 1),
    ("ctz", 1),
    ("bswap", 1),
    ("rotl", 2),
];

/// `exit(code)` stops the program, so it has no value and may only stand as its own statement.
pub const STOPS_PROGRAM: &[&str] = &["exit"];

/// Four parameters travel in registers; the rest use the caller's outgoing stack area.
pub const MAX_PARAMETERS: usize = 8;

/// Largest array a local may declare.
pub const MAX_ARRAY_ELEMENTS: usize = 65536;

/// The arity of a builtin, if this name is one.
pub fn builtin_arity(name: &str) -> Option<usize> {
    BUILTINS.iter().find(|(builtin, _)| *builtin == name).map(|(_, arity)| *arity)
}

fn is_builtin(name: &str) -> bool {
    builtin_arity(name).is_some()
}

fn stops_program(name: &str) -> bool {
    STOPS_PROGRAM.contains(&name)
}

/// One name in a function: its frame slot, whether it can be assigned, and where it began.
#[derive(Debug, Clone, Copy)]
struct Binding {
    slot: i64,
    mutable: bool,
    span: Span,
    array_size: Option<usize>,
}

/// One pass over a parsed program, mutating slots and collecting diagnostics.
pub struct Analyzer {
    diagnostics: Vec<Diagnostic>,
    arities: HashMap<String, usize>,
    bindings: HashMap<String, Binding>,
    slots: i64,
    loop_depth: usize,
}

impl Analyzer {
    pub fn new() -> Self {
        Self {
            diagnostics: Vec::new(),
            arities: HashMap::new(),
            bindings: HashMap::new(),
            slots: 0,
            loop_depth: 0,
        }
    }

    /// Check the whole program; return every diagnostic at once if any was found.
    pub fn analyze(mut self, program: &mut Program, path: &str) -> Result<(), DiagnosticError> {
        self.collect_functions(program, path);
        self.check_entry_point(program, path);
        for function in &mut program.functions {
            self.check_function(function, path);
        }
        if self.diagnostics.is_empty() {
            Ok(())
        } else {
            Err(DiagnosticError { diagnostics: self.diagnostics })
        }
    }

    fn report(&mut self, code: &str, message: &str, fix: &str, span: Span, path: &str) {
        self.diagnostics.push(Diagnostic::new(code, message, fix, span, path));
    }

    /// Register top-level names and reject duplicates, redefinitions and arity overflow.
    fn collect_functions(&mut self, program: &Program, path: &str) {
        for function in &program.functions {
            if is_builtin(&function.name) {
                self.report(
                    "E3012",
                    &format!("'{}' is a builtin and cannot be redefined", function.name),
                    "Rename the function; the builtin keeps its meaning.",
                    function.span,
                    path,
                );
                continue;
            }
            if self.arities.contains_key(&function.name) {
                self.report(
                    "E3001",
                    &format!("function '{}' is defined twice", function.name),
                    "Keep one definition, or rename one of them.",
                    function.span,
                    path,
                );
                continue;
            }
            if function.parameters.len() > MAX_PARAMETERS {
                self.report(
                    "E3010",
                    &format!(
                        "function '{}' takes {} parameters; Cairn carries four in registers",
                        function.name,
                        function.parameters.len()
                    ),
                    &format!(
                        "Reduce the parameters to {} or fewer, packing the extras into one i64.",
                        MAX_PARAMETERS
                    ),
                    function.span,
                    path,
                );
            }
            self.arities.insert(function.name.clone(), function.parameters.len());
        }
    }

    /// `fn main() -> i64` is the entry point and must take no parameters.
    fn check_entry_point(&mut self, program: &Program, path: &str) {
        let main = program.functions.iter().find(|function| function.name == "main");
        match main {
            None => self.report(
                "E3011",
                "the program has no 'fn main() -> i64'",
                "Add 'fn main() -> i64 { return 0; }' as the entry point.",
                program.span,
                path,
            ),
            Some(main) if !main.parameters.is_empty() => self.report(
                "E3011",
                "main must take no parameters",
                "Declare 'fn main() -> i64' and read inputs from helpers instead.",
                main.span,
                path,
            ),
            Some(_) => {}
        }
    }

    /// Check one function body with a fresh flat scope.
    fn check_function(&mut self, function: &mut Function, path: &str) {
        self.bindings.clear();
        self.slots = 0;
        self.loop_depth = 0;
        for parameter in &mut function.parameters {
            if is_builtin(&parameter.name) {
                self.report(
                    "E3012",
                    &format!("'{}' is a builtin and cannot be redefined", parameter.name),
                    "Rename the parameter; the builtin keeps its meaning.",
                    parameter.span,
                    path,
                );
                continue;
            }
            if self.bindings.contains_key(&parameter.name) {
                self.report(
                    "E3006",
                    &format!("parameter '{}' is declared twice", parameter.name),
                    "Give each parameter a distinct name.",
                    parameter.span,
                    path,
                );
                continue;
            }
            parameter.slot = self.new_slot();
            self.bindings.insert(
                parameter.name.clone(),
                Binding {
                    slot: parameter.slot,
                    mutable: true,
                    span: parameter.span,
                    array_size: None,
                },
            );
        }
        self.check_block(&mut function.body, path);
        function.slots = self.slots as usize;
        if !emitted_return(&function.body) {
            self.report(
                "E3009",
                &format!("function '{}' can finish without returning", function.name),
                "End every path with 'return expression;'.",
                function.span,
                path,
            );
        }
    }

    /// Allocate the next 8-byte frame slot.
    fn new_slot(&mut self) -> i64 {
        let slot = self.slots;
        self.slots += 1;
        slot
    }

    /// Allocate `count` consecutive 8-byte frame slots; the first is the base.
    fn new_array_slots(&mut self, count: usize) -> i64 {
        let slot = self.slots;
        self.slots += count as i64;
        slot
    }

    /// Check statements in order; the scope is flat and function-wide.
    fn check_block(&mut self, block: &mut Block, path: &str) {
        for statement in &mut block.statements {
            self.check_statement(statement, path);
        }
    }

    fn check_statement(&mut self, statement: &mut Stmt, path: &str) {
        match statement {
            Stmt::Let { .. } => self.check_binding(statement, path),
            Stmt::Assign { .. } => self.check_assign(statement, path),
            Stmt::If { condition, then_block, else_block, .. } => {
                self.check_expression(condition, path);
                self.check_block(then_block, path);
                if let Some(else_block) = else_block {
                    self.check_block(else_block, path);
                }
            }
            Stmt::While { condition, body, .. } => {
                self.check_expression(condition, path);
                self.loop_depth += 1;
                self.check_block(body, path);
                self.loop_depth -= 1;
            }
            Stmt::For { .. } => self.check_for(statement, path),
            Stmt::Return { value, .. } => self.check_expression(value, path),
            Stmt::Break { span } if self.loop_depth == 0 => self.report(
                "E3008",
                "'break' is only valid inside a 'while' loop",
                "Move it inside a loop, or remove it.",
                *span,
                path,
            ),
            Stmt::Continue { span } if self.loop_depth == 0 => self.report(
                "E3008",
                "'continue' is only valid inside a 'while' loop",
                "Move it inside a loop, or remove it.",
                *span,
                path,
            ),
            Stmt::Break { .. } | Stmt::Continue { .. } => {}
            Stmt::Block(block) => self.check_block(block, path),
            Stmt::ExprStmt { expression, .. } => {
                if let Expr::Call { name, .. } = expression.unwrap_parens() {
                    if stops_program(name) {
                        self.check_call(expression, true, path);
                        return;
                    }
                }
                self.check_expression(expression, path);
            }
        }
    }

    /// A `for` declares one scoped, mutable loop variable for its body, then restores.
    fn check_for(&mut self, statement: &mut Stmt, path: &str) {
        let Stmt::For { name, start, end, body, slot, span, .. } = statement else {
            return;
        };
        if is_builtin(name) {
            self.report(
                "E3012",
                &format!("'{}' is a builtin and cannot be redefined", name),
                "Rename the loop variable.",
                *span,
                path,
            );
            return;
        }
        self.check_expression(start, path);
        let outer = self.bindings.get(name).copied();
        *slot = self.new_slot();
        self.bindings.insert(
            name.clone(),
            Binding { slot: *slot, mutable: true, span: *span, array_size: None },
        );
        self.check_expression(end, path);
        self.loop_depth += 1;
        self.check_block(body, path);
        self.loop_depth -= 1;
        match outer {
            None => {
                self.bindings.remove(name);
            }
            Some(binding) => {
                self.bindings.insert(name.clone(), binding);
            }
        }
    }

    /// A `let`/`var` declares one new name: a scalar from one i64 expression, an array from a
    /// literal of exactly the declared length, reserving that many consecutive frame slots.
    fn check_binding(&mut self, statement: &mut Stmt, path: &str) {
        let Stmt::Let { name, span, value, slot, array_size, mutable, .. } = statement else {
            return;
        };
        if is_builtin(name) {
            self.report(
                "E3012",
                &format!("'{}' is a builtin and cannot be redefined", name),
                "Rename the variable.",
                *span,
                path,
            );
            self.check_binding_initializer(value, name, *span, None, path);
            return;
        }
        if self.bindings.contains_key(name) {
            self.report(
                "E3006",
                &format!("'{}' is already declared in this function", name),
                "Use a new name; Cairn has no shadowing.",
                *span,
                path,
            );
            self.check_binding_initializer(value, name, *span, None, path);
            return;
        }
        let mut size = *array_size;
        if let Some(count) = size {
            if !(1..=MAX_ARRAY_ELEMENTS).contains(&count) {
                self.report(
                    "E3014",
                    &format!("array '{}' declares {} element(s)", name, count),
                    "Declare between 1 and 65536 elements.",
                    *span,
                    path,
                );
                size = None;
            }
        }
        self.check_binding_initializer(value, name, *span, size, path);
        *slot = match size {
            Some(count) => self.new_array_slots(count),
            None => self.new_slot(),
        };
        self.bindings.entry(name.clone()).or_insert(Binding {
            slot: *slot,
            mutable: *mutable,
            span: *span,
            array_size: size,
        });
    }

    /// Check one binding's initializer against the declared shape. `size is None` means a scalar
    /// initializer or a refused array declaration: elements are still walked for their own errors.
    fn check_binding_initializer(
        &mut self,
        value: &mut Expr,
        name: &str,
        span: Span,
        size: Option<usize>,
        path: &str,
    ) {
        match value.unwrap_parens_mut() {
            Expr::ArrayLiteral { elements, .. } => {
                for element in elements.iter_mut() {
                    self.check_expression(element, path);
                }
                if let Some(count) = size {
                    if elements.len() != count {
                        self.report(
                            "E3014",
                            &format!(
                                "the literal for '{}' has {} element(s), declared [{}]",
                                name,
                                elements.len(),
                                count
                            ),
                            &format!("Write exactly {} element(s) in the literal.", count),
                            value.span(),
                            path,
                        );
                    }
                }
            }
            Expr::IntLiteral { .. } | Expr::StrLiteral { .. } | Expr::Name { .. }
            | Expr::Unary { .. } | Expr::Binary { .. } | Expr::Parens { .. }
            | Expr::Call { .. } | Expr::Index { .. } => {
                if let Some(count) = size {
                    self.report(
                        "E3014",
                        &format!(
                            "array '{}' of {} element(s) needs an array literal",
                            name, count
                        ),
                        &format!("Write '[e0, e1, ...]' with exactly {} element(s).", count),
                        span,
                        path,
                    );
                    return;
                }
                self.check_expression(value, path);
            }
        }
    }

    /// Assignment requires an existing `var` binding; `a[i] = e;` a `var` array.
    fn check_assign(&mut self, statement: &mut Stmt, path: &str) {
        let Stmt::Assign { name, span, value, slot, index, temp_slot, array_size } = statement
        else {
            return;
        };
        let binding = if is_builtin(name) {
            self.report(
                "E3012",
                &format!("'{}' is a builtin and cannot be redefined", name),
                "Rename the variable; the builtin keeps its meaning.",
                *span,
                path,
            );
            None
        } else {
            self.bindings.get(name).copied()
        };
        if index.is_some() {
            self.check_element_store(
                name,
                binding,
                slot,
                index.as_mut(),
                value,
                temp_slot,
                array_size,
                *span,
                path,
            );
            return;
        }
        match binding {
            None => {
                if !is_builtin(name) {
                    self.report(
                        "E3007",
                        &format!("'{}' is not declared", name),
                        "Declare it first with 'var name: i64 = value;'.",
                        *span,
                        path,
                    );
                }
            }
            Some(binding) if !binding.mutable => self.report(
                "E3005",
                &format!("'{}' is immutable", name),
                "Declare it with 'var' if it must change, otherwise remove the assignment.",
                *span,
                path,
            ),
            Some(binding) => *slot = binding.slot,
        }
        self.check_expression(value, path);
    }

    /// `a[index] = value;` needs a declared `var` array; scalars refuse indexing. A dynamic index
    /// reserves one scratch slot so the computed address survives the value expression.
    #[allow(clippy::too_many_arguments)]
    fn check_element_store(
        &mut self,
        name: &str,
        binding: Option<Binding>,
        slot: &mut i64,
        index: Option<&mut Expr>,
        value: &mut Expr,
        temp_slot: &mut Option<usize>,
        array_size: &mut Option<usize>,
        span: Span,
        path: &str,
    ) {
        match binding {
            None => self.report(
                "E3007",
                &format!("'{}' is not declared", name),
                "Declare it first with 'var a: [N]i64 = [...];'.",
                span,
                path,
            ),
            Some(binding) if binding.array_size.is_none() => self.report(
                "E3015",
                &format!("'{}' is not an array", name),
                "Index only arrays declared as '[N]i64'.",
                span,
                path,
            ),
            Some(binding) if !binding.mutable => self.report(
                "E3005",
                &format!("'{}' is immutable", name),
                "Declare the array with 'var' to change its elements.",
                span,
                path,
            ),
            Some(binding) => *slot = binding.slot,
        }
        let size = binding.and_then(|binding| binding.array_size);
        if *slot >= 0 {
            *array_size = size;
        }
        let dynamic = index.as_ref().and_then(|index| literal_index(index)).is_none();
        if let Some(index) = index {
            self.check_element_index(index, size, path);
        }
        self.check_expression(value, path);
        if *slot >= 0 && size.is_some() && dynamic {
            *temp_slot = Some(self.new_slot() as usize);
        }
    }

    /// Check one index expression; a literal index must land inside the array. A dynamic index
    /// stays unchecked at compile time, matching Cairn's zero-overhead memory contract.
    fn check_element_index(&mut self, index: &mut Expr, size: Option<usize>, path: &str) {
        self.check_expression(index, path);
        if let Some(count) = size {
            if let Some(position) = literal_index(index) {
                if position < 0 || position >= count as i128 {
                    self.report(
                        "E3017",
                        &format!(
                            "index {} is outside an array of {} element(s)",
                            position, count
                        ),
                        &format!("Use an index between 0 and {}.", count - 1),
                        index.span(),
                        path,
                    );
                }
            }
        }
    }

    /// Dispatch one expression to its checker.
    fn check_expression(&mut self, expression: &mut Expr, path: &str) {
        match expression {
            Expr::IntLiteral { span, value } => {
                if *value > INT64_MAX {
                    self.report(
                        "E1003",
                        &format!("integer literal {} does not fit in i64", value),
                        "Write a value between -9223372036854775808 and 9223372036854775807.",
                        *span,
                        path,
                    );
                }
            }
            Expr::StrLiteral { .. } => {}
            Expr::Parens { inner, .. } => self.check_expression(inner, path),
            Expr::Name { .. } => self.check_name(expression, path),
            Expr::Unary { operand, .. } => self.check_expression(operand, path),
            Expr::Binary { left, right, .. } => {
                self.check_expression(left, path);
                self.check_expression(right, path);
            }
            Expr::Index { .. } => self.check_index_load(expression, path),
            Expr::Call { .. } => self.check_call(expression, false, path),
            Expr::ArrayLiteral { elements, .. } => {
                for element in elements.iter_mut() {
                    self.check_expression(element, path);
                }
            }
        }
    }

    /// `a[i]` loads one element; it needs a declared array, never a scalar.
    fn check_index_load(&mut self, expression: &mut Expr, path: &str) {
        let Expr::Index { operand, index, slot, size, .. } = expression else {
            return;
        };
        let mut binding = None;
        match operand.as_ref() {
            Expr::Name { name, span, .. } if is_builtin(name) => self.report(
                "E3015",
                &format!("'{}' is not an array", name),
                "Index only arrays declared as '[N]i64'.",
                *span,
                path,
            ),
            Expr::Name { name, span, .. } => match self.bindings.get(name).copied() {
                None => self.report(
                    "E3007",
                    &format!("'{}' is not declared", name),
                    "Declare it with 'let a: [N]i64 = [...];' before this line.",
                    *span,
                    path,
                ),
                Some(found) if found.array_size.is_none() => self.report(
                    "E3015",
                    &format!("'{}' is not an array", name),
                    "Index only arrays declared as '[N]i64'.",
                    *span,
                    path,
                ),
                Some(found) => binding = Some(found),
            },
            other => self.report(
                "E3015",
                "only an array name can be indexed",
                "Write 'a[index]' with `a` an array variable.",
                other.span(),
                path,
            ),
        }
        let array_size = binding.and_then(|binding| binding.array_size);
        self.check_element_index(index, array_size, path);
        if let Some(binding) = binding {
            *slot = binding.slot;
            *size = array_size.unwrap_or(0);
        }
    }

    /// A name must be a declared local; a builtin is callable but not a value.
    fn check_name(&mut self, expression: &mut Expr, path: &str) {
        let Expr::Name { span, name, slot } = expression else {
            return;
        };
        if is_builtin(name) {
            self.report(
                "E3012",
                &format!("'{}' is a builtin function, not a value", name),
                &format!("Call it as '{}(value)'.", name),
                *span,
                path,
            );
            return;
        }
        match self.bindings.get(name).copied() {
            None => self.report(
                "E3007",
                &format!("'{}' is not declared", name),
                "Declare it with 'let name: i64 = value;' before this line.",
                *span,
                path,
            ),
            Some(binding) => {
                *slot = binding.slot;
                if binding.array_size.is_some() {
                    self.report(
                        "E3016",
                        &format!("array '{}' is not a value", name),
                        "Index it ('a[i]'); a whole array cannot be read or passed.",
                        *span,
                        path,
                    );
                }
            }
        }
    }

    /// A call names a function (or a builtin) and passes exactly its arity of arguments. A
    /// stop-program builtin carries no value, so only a statement may walk it in directly.
    fn check_call(&mut self, expression: &mut Expr, allow_stop: bool, path: &str) {
        let Expr::Call { span, name, arguments } = expression else {
            return;
        };
        if stops_program(name) && !allow_stop {
            self.report(
                "E3013",
                &format!("'{}()' stops the program and has no value", name),
                "Call it as its own statement, not inside another expression.",
                *span,
                path,
            );
        }
        for argument in arguments.iter_mut() {
            self.check_expression(argument, path);
        }
        let expected = builtin_arity(name).or_else(|| self.arities.get(name).copied());
        match expected {
            None => self.report(
                "E3003",
                &format!("function '{}' is not defined", name),
                "Define it with 'fn', or call a name that exists.",
                *span,
                path,
            ),
            Some(expected) if arguments.len() != expected => self.report(
                "E3004",
                &format!(
                    "'{}' takes {} argument(s), not {}",
                    name,
                    expected,
                    arguments.len()
                ),
                &format!("Pass exactly {} i64 value(s).", expected),
                *span,
                path,
            ),
            Some(_) => {}
        }
    }
}

impl Default for Analyzer {
    fn default() -> Self {
        Self::new()
    }
}

/// A statically known index, or None for a dynamic one; parens are unwrapped.
fn literal_index(expression: &Expr) -> Option<i128> {
    match expression.unwrap_parens() {
        Expr::IntLiteral { value, .. } => Some(*value),
        _ => None,
    }
}

/// Whether every path through `block` ends in a `return` or an `exit` statement.
pub fn emitted_return(block: &Block) -> bool {
    let Some(last) = block.statements.last() else {
        return false;
    };
    match last {
        Stmt::Return { .. } => true,
        Stmt::ExprStmt { expression, .. } => matches!(
            expression.unwrap_parens(),
            Expr::Call { name, .. } if stops_program(name)
        ),
        Stmt::Block(inner) => emitted_return(inner),
        Stmt::If { then_block, else_block: Some(else_block), .. } => {
            emitted_return(then_block) && emitted_return(else_block)
        }
        _ => false,
    }
}

/// Run semantic analysis over one parsed program, mutating its slots.
pub fn analyze(program: &mut Program, path: &str) -> Result<(), DiagnosticError> {
    Analyzer::new().analyze(program, path)
}

/// Convenience: tokenize, parse and analyze a source text in one step.
pub fn analyze_source(source: &str, path: &str) -> Result<Program, DiagnosticError> {
    let mut program = crate::parser::parse(source, path)?;
    analyze(&mut program, path)?;
    Ok(program)
}

