# Codebase Map & Symbol Catalog 🗺️

> **AUTO-GENERATED & VERIFIED BY `scripts/update_knowledge_base.py`**
> This document provides an automated, up-to-date registry of all modules, classes, and exported functions across this repository.

---

## 📊 Codebase Metrics
- **Total Python Modules**: `37`
- **Total Classes**: `60`
- **Total Top-Level Functions**: `104`
- **Total Class Methods**: `280`
- **Total Source Lines**: `4908`

---

## 📦 Modules & Exported Symbols

### [`benchmarks/__init__.py`](benchmarks/__init__.py)
> *Benchmarks for Cairn: case sources and the harness that measures them.*

**Size**: `1` lines (`77` bytes)

---

### [`benchmarks/run_benchmarks.py`](benchmarks/run_benchmarks.py)
> *Benchmark harness: measure Cairn executables and, when one exists, a C baseline.*

**Size**: `265` lines (`11044` bytes)

**Classes:**
- `class Case` - *One benchmark: a Cairn source, an equivalent C source, and what one iteration is.*

**Functions:**
- `def find_c_compiler()` - *The first C compiler already installed, or None when the machine has none.*
- `def run_command(argv, timeout, cwd)` - *Run one command with a hard limit, capturing text.*
- `def cairn_build_command(source, destination, optimise)` - *The exact `cairn build` command for one case, with the folding pass optional.*
- `def build_cairn(source, destination, optimise)` - *Compile one Cairn case and return the compile time in milliseconds.*
- `def build_c(compiler, source, destination)` - *Compile one C case; None when this compiler does not produce an executable.*
- `def measure(executable, rounds)` - *Wall-clock milliseconds for each run of one executable.*
- `def summarise(samples, iterations)` - *Best, median and per-unit cost from one set of wall-clock samples.*
- `def verdict(best_cairn, best_c)` - *The measured standing against C: `faster`, `slower`, `tie` or `unavailable`.*
- `def benchmark_case(case, compiler, rounds, folder, optimise)` - *Measure one case in Cairn and, when available, in C.*
- `def render(reports, compiler, optimise)` - *The human table; one line per case and per measured language.*
- `def _number(text)` - *A positive sample count from the command line.*
- `def main(argv)` - *Parse options, run every case and print the report.*

---

### [`cairn/__init__.py`](cairn/__init__.py)
> *Cairn: an agent-first systems language with its own x86-64 backend.*

**Size**: `11` lines (`477` bytes)

---

### [`cairn/__main__.py`](cairn/__main__.py)
> *`python -m cairn` entry point.*

**Size**: `8` lines (`154` bytes)

---

### [`cairn/builder.py`](cairn/builder.py)
> *Build pipeline: source text to executable bytes.*

**Size**: `61` lines (`2195` bytes)

**Classes:**
- `class Build` - *One successful build: the parsed program, the linked artifact and the image bytes.*

**Functions:**
- `def compile_source(source, path, optimise)` - *Run every stage over one source text and return the executable image.*
- `def compile_file(path, optimise)` - *Read a `.crn` file as UTF-8 and compile it.*
- `def output_path(source, requested)` - *The executable path: `-o` when given, otherwise the source stem plus `.exe`.*
- `def write_executable(destination, image)` - *Write the image to a temporary sibling, then rename it into place in one step.*

---

### [`cairn/capabilities.py`](cairn/capabilities.py)
> *The machine-readable description of what this compiler accepts and emits.*

**Size**: `104` lines (`5047` bytes)

**Functions:**
- `def _operators()` - *Operators grouped by precedence tier, loosest first.*
- `def capabilities()` - *The whole surface as one JSON-serialisable object.*
- `def render_text()` - *A short human summary: the surface on one screen.*

---

### [`cairn/cli.py`](cairn/cli.py)
> *Command line: build, check and run Cairn programs.*

**Size**: `298` lines (`13250` bytes)

**Functions:**
- `def _parser()` - *The whole command surface, declared in one place.*
- `def _add_optimise_flag(parser)` - *Every command shares one switch for the folding pass, so builds can be compared.*
- `def _read_source(path)` - *The source text, or an explained usage failure.*
- `def _report_failure(error, source, json_mode)` - *Print every diagnostic, as JSON or as positioned text.*
- `def _build_command(arguments)` - *`build`: compile, write the executable, report its size.*
- `def _check_command(arguments)` - *`check`: compile in memory and report, writing nothing.*
- `def _run_command(arguments)` - *`run`: compile into a temporary folder, execute, and forward the exit code.*
- `def _execute(executable, timeout)` - *Run the produced executable with inherited streams and a hard time limit.*
- `def _read_executable(path)` - *The artifact bytes, or an explained usage failure.*
- `def _verify_command(arguments)` - *`verify`: structural checks over a built image or over a source compiled in memory.*
- `def _listing_command(arguments)` - *`listing`: compile in memory and print the bytes of every label in `.text`.*
- `def _capabilities_command(arguments)` - *`capabilities`: the surface as JSON for an agent, or one screen of text for a human.*
- `def main(argv)` - *Parse the command line and dispatch one command.*
- `def _configure_streams()` - *Make source text and diagnostics survive a Windows pipe whatever the code page is.*
- `def command_line(argv)` - *The entry point used by `python -m cairn` and the installed console script.*

---

### [`cairn/codegen.py`](cairn/codegen.py)
> *Code generation: syntax tree to x86-64, one pass, values in rax.*

**Size**: `519` lines (`22025` bytes)

**Classes:**
- `class CodeGenerator` - *One lowering pass over a checked program.*
  - Method `.generate(program)` - *Emit the entry stub, every function, then the runtime helpers.*
  - Method `._fresh(stem)` - *A label name no other construct uses.*
  - Method `._emit_entry()` - *The PE entry point: call main, then exit with main's return value.*
  - Method `._emit_function(function)` - *Prologue, saved homes, cleared frame, parameter spills, body, and one epilogue.*
  - Method `._save_homes()` - *Keep the caller's values for every callee-saved register this function uses.*
  - Method `._restore_homes()` - *Put the caller's values back before returning.*
  - Method `._clear_frame(function)` - *Clear every slot so a binding declared in a branch that never ran reads as zero.*
  - Method `._home(slot)` - *The register that holds `slot`, or None when the slot stays in the frame.*
  - Method `._load_slot(slot, destination)` - *Move one slot into `destination` from its register or its frame slot.*
  - Method `._store_slot(slot, source)` - *Write `source` into one slot's register or frame slot.*
  - Method `._emit_value(slot, expression)` - *Assign an expression to a slot, folding `slot = slot op value` in place.*
  - Method `._fold_update(home, slot, expression)` - *Emit `home = home op value` when the expression updates its own slot.*
  - Method `._emit_into(expression, destination)` - *Lower an expression, using `destination` directly when it is a leaf.*
  - Method `._emit_block(block)` - *Emit every statement of a block in order.*
  - Method `._emit_statement(statement)` - *Lower one statement.*
  - Method `._emit_if(statement)` - *`if` as a conditional branch and two labelled bodies.*
  - Method `._emit_while(statement)` - *`while` with its own break and continue targets.*
  - Method `._emit_condition(expression, target, when_true)` - *Jump to `target` when the condition has the requested truth value.*
  - Method `._emit_comparison_branch(expression, target, when_true)` - *Compare two operands and branch, using an immediate form when the right side is small.*
  - Method `._emit_expression(expression)` - *Lower one expression, leaving its value in rax.*
  - Method `._emit_unary(expression)` - *Negate, complement, or compare with zero.*
  - Method `._emit_binary(expression)` - *Short-circuit `&&`/`||`, otherwise place the left operand, then combine.*
  - Method `._emit_left_operand(left, right)` - *Evaluate `left`, keep it across `right`, and leave it in rcx with right in rax.*
  - Method `._emit_division(operator)` - *Signed division with an explicit zero-divisor trap, quotient or remainder.*
  - Method `._emit_and(expression)` - *`&&`: the right side is evaluated only when the left side is non-zero.*
  - Method `._emit_or(expression)` - *`||`: the right side is evaluated only when the left side is zero.*
  - Method `._emit_call(expression)` - *Evaluate arguments left to right, pop them into registers, then call.*
  - Method `._emit_call_site(target)` - *A call, padded so rsp is 16-byte aligned as the ABI requires.*

**Functions:**
- `def function_label(name)` - *The assembler label of a Cairn function.*
- `def slot_offset(slot)` - *The rbp-relative offset of a frame slot; slot 0 is the first parameter.*
- `def align16(value)` - *Round a frame size up to the 16-byte stack alignment the ABI requires.*
- `def slot_of(expression)` - *The slot a parenthesised name refers to, or None for every other expression.*
- `def is_leaf(expression)` - *Whether evaluating the expression is a load or a literal move and nothing else.*
- `def generate(program)` - *Lower a checked program to a linked artifact.*

---

### [`cairn/codes.py`](cairn/codes.py)
> *The diagnostic code registry: one record per code the compiler can emit.*

**Size**: `76` lines (`3331` bytes)

**Classes:**
- `class Code` - *One diagnostic code: its stage family and a one-line statement of what it means.*

**Functions:**
- `def codes_in_source(texts)` - *Every code literal that appears in source text, which is what the compiler emits.*
- `def codes_by_stage()` - *Registered codes grouped by stage, in registry order, for the capabilities manifest.*

---

### [`cairn/diagnostics.py`](cairn/diagnostics.py)
> *Diagnostics: every failure carries a stable code, a span and a fix.*

**Size**: `99` lines (`3543` bytes)

**Classes:**
- `class Span` - *A half-open source range, in offsets and in 1-based line/column pairs.*
  - Method `.point(offset, line, column)` - *A span covering one position, used for a missing token.*
  - Method `.join(first, last)` - *The smallest span covering both inputs.*
- `class Diagnostic` - *One error or warning: what is wrong, where, and how to fix it.*
  - Method `.to_dict()` - *The JSON form an agent reads; keys and order are the contract.*
  - Method `.render(source)` - *The human form: a positioned line, the offending text, and a fix line.*
- `class DiagnosticError` - *Raised by a stage that cannot continue; carries every diagnostic found so far.*
  - Method `.__init__(diagnostics)`
  - Method `.render(source)` - *All diagnostics, one after another.*

**Functions:**
- `def _excerpt(source, span)` - *The source line the span starts on, without its line ending.*
- `def fail(code, message, fix, span, path)` - *One diagnostic as an exception, for the stage that cannot continue past it.*

---

### [`cairn/image.py`](cairn/image.py)
> *Image pieces shared by the backend: read-only data and the linked artifact.*

**Size**: `59` lines (`1759` bytes)

**Classes:**
- `class ReadOnlyData` - *A byte blob with named offsets, placed in the `.rdata` section.*
  - Method `.__len__()` - *The current size in bytes.*
  - Method `.label(name)` - *Bind `name` to the current offset.*
  - Method `.add(name, payload)` - *Bind `name` and append `payload`.*
  - Method `.align(boundary)` - *Pad with zero bytes to the next multiple of `boundary`.*
- `class Artifact` - *Everything the PE writer needs: code, fixups, data and the entry label.*

**Functions:**
- `def iat_label(function)` - *The linker name of one import address table slot.*

---

### [`cairn/lexer.py`](cairn/lexer.py)
> *Lexer: source text to a flat token list.*

**Size**: `142` lines (`5523` bytes)

**Classes:**
- `class Token` - *One lexical unit: its kind, its text, its value when it is an integer, and its span.*
  - Method `.is_symbol(text)` - *Whether this is the given punctuation or operator.*
  - Method `.is_keyword(text)` - *Whether this is the given keyword.*
- `class Lexer` - *A single-pass scanner over ASCII source.*
  - Method `.__init__(source, path)`
  - Method `.tokenize()` - *Every token in order, ending with one `eof` token.*
  - Method `._skip_blank()` - *Consume whitespace and `//` comments.*
  - Method `._advance(char)` - *Move one character forward, keeping the line and column accurate.*
  - Method `._next_token()` - *Scan one token that is not whitespace or a comment.*
  - Method `._consume(count)` - *Advance over `count` characters, tracking a newline inside the run.*
  - Method `._span_from(start)` - *The span between `start` and the current position.*
  - Method `._number(start)` - *A decimal integer literal that must fit in a signed 64-bit integer.*
  - Method `._identifier(start)` - *A name, or one of the fixed keywords.*

**Functions:**
- `def tokenize(source, path)` - *Tokenize one source text.*

---

### [`cairn/listing.py`](cairn/listing.py)
> *Machine-code listing: the emitted bytes, grouped by the label that owns them.*

**Size**: `42` lines (`1747` bytes)

**Functions:**
- `def _rows(chunk, start)` - *Hex rows with the offset of each row's first byte.*
- `def render_listing(artifact)` - *Every label with its offset and the bytes up to the next label.*

---

### [`cairn/machine.py`](cairn/machine.py)
> *x86-64 emitter: the instruction subset Cairn lowers to, written as bytes.*

**Size**: `285` lines (`12081` bytes)

**Classes:**
- `class Assembler` - *A byte buffer with named labels and forward-reference fixups.*
  - Method `.here()` - *The current offset in the code buffer.*
  - Method `.emit(*parts)` - *Append raw bytes.*
  - Method `.label(name)` - *Bind `name` to the current offset; a duplicate name is a compiler bug.*
  - Method `.rel32(target)` - *A placeholder displacement patched when the image is linked.*
  - Method `.call(target)` - *`call rel32` to a label in this image.*
  - Method `.call_indirect(target)` - *`call qword ptr [rip+disp32]`, used for imported functions through the IAT.*
  - Method `.jmp(target)` - *`jmp rel32`.*
  - Method `.jcc(condition, target)` - *`jcc rel32` for one of the named conditions.*
  - Method `.push(reg)` - *`push r64`.*
  - Method `.pop(reg)` - *`pop r64`.*
  - Method `.mov_reg_reg(destination, source)` - *`mov r64, r64`.*
  - Method `.mov_imm64(reg, value)` - *`mov r64, imm64`, used for every integer literal.*
  - Method `.mov_imm32(reg, value)` - *`mov r32, imm32`; writes zero-extend into the 64-bit register.*
  - Method `.mov_load_rbp(destination, offset)` - *`mov r64, [rbp+disp]` for a frame slot; disp8 shrinks the encoding where it fits.*
  - Method `.mov_store_rbp(offset, source)` - *`mov [rbp+disp], r64` for a frame slot.*
  - Method `.mov_store_rsp_imm32(offset, value)` - *`mov qword ptr [rsp+disp32], imm32` for outgoing ABI slots.*
  - Method `.lea_rbp(destination, offset)` - *`lea r64, [rbp+disp]` for a frame address.*
  - Method `.lea_rip(destination, target)` - *`lea r64, [rip+disp32]`, used for read-only data addresses.*
  - Method `.xor_zero(reg)` - *`xor r32, r32`: the zero idiom, which also clears the upper 32 bits.*
  - Method `.binary(opcode, destination, source)` - *A 64-bit `op r/m64, r64` form: add, sub, and, or, xor and cmp share it.*
  - Method `._group1_imm(extension, reg, value)` - *`op r/m64, imm8` (83 /n) or `op r/m64, imm32` (81 /n), sign-extended.*
  - Method `.add_imm(reg, value)` - *`add r64, imm`, used for `x = x + constant` without a register copy.*
  - Method `.sub_imm(reg, value)` - *`sub r64, imm`, used for `x = x - constant` without a register copy.*
  - Method `.cmp_imm(reg, value)` - *`cmp r64, imm`, so a comparison with a small literal needs no second register.*
  - Method `.imul(destination, source)` - *`imul r64, r/m64`: two-operand signed multiply.*
  - Method `.test(reg)` - *`test r64, r64`, setting flags from a value without changing it.*
  - Method `.setcc(condition)` - *`setcc al` followed by `movzx rax, al`: a comparison result as 0 or 1 in rax.*
  - Method `.cqo()` - *Sign-extend rax into rdx:rax before a signed division.*
  - Method `.divide(reg, signed)` - *`idiv`/`div r64`; the caller sets rdx and rax.*
  - Method `.unary(extension, reg)` - *`F7 /n` forms: not (2), neg (3).*
  - Method `.shift_cl(extension)` - *`shl` (4), shr (5) or sar (7) of rax by cl.*
  - Method `.add_imm8(reg, value)` - *`add r8, imm8`, used to turn a digit into ASCII.*
  - Method `.store_byte(base, source)` - *`mov [base], r8`.*
  - Method `.store_byte_imm(base, value)` - *`mov byte ptr [base], imm8`.*
  - Method `.store_qword(base, source)` - *`mov [base], r64`.*
  - Method `.step(reg, decrement)` - *`inc` (FF /0) or `dec` (FF /1) of a 64-bit register.*
  - Method `.add_rsp(amount)` - *`add rsp, imm32`.*
  - Method `.sub_rsp(amount)` - *`sub rsp, imm32`.*
  - Method `.align_rsp()` - *`and rsp, -16`, for a helper reached by a jump from an arbitrary stack depth.*
  - Method `.leave_ret()` - *`leave; ret`: restore the caller's frame and return.*
  - Method `.ret()` - *A bare `ret`.*
  - Method `.hlt()` - *`hlt`, used only on unreachable paths such as after ExitProcess.*

**Functions:**
- `def _rex(w, r, x, b)` - *The REX prefix byte from the four bits x86-64 adds to a legacy encoding.*
- `def _modrm(mod, reg, rm)` - *ModRM with an optional SIB and displacement handled by the caller.*
- `def _rbp_displacement(reg, offset)` - *`[rbp+disp8]` (mod 01) when it fits, otherwise `[rbp+disp32]` (mod 10).*

---

### [`cairn/optimize.py`](cairn/optimize.py)
> *Optimiser: constant folding and provably safe algebraic simplification.*

**Size**: `252` lines (`10359` bytes)

**Classes:**
- `class Optimizer` - *One folding pass, rewriting expressions in place and returning the same nodes.*
  - Method `.run(program)` - *Optimise every function body in the program.*
  - Method `._block(block)` - *Optimise each statement; splice merged branches and truncate what cannot run.*
  - Method `._statement(statement)` - *Fold one statement, replacing a constant `if` with the branch it always takes.*
  - Method `._choose_branch(statement)` - *Replace an `if` whose condition is a constant with the branch it always takes.*
  - Method `._expression(expression)` - *Fold one expression; returns the replacement node.*
  - Method `._fold_unary(expression)` - *Fold a unary operator applied to a literal.*
  - Method `._fold_binary(expression)` - *Fold two literals, then apply an identity that is safe for every i64.*
  - Method `._identity(expression, left, right)` - *`x + 0`, `x * 1` and friends; a dropped operand must have no effect.*

**Functions:**
- `def wrap(value)` - *Reduce an exact integer to the i64 value the machine would hold.*
- `def truncate_division(left, right)` - *Signed division truncating toward zero, the way `idiv` does.*
- `def fold_constant(op, left, right)` - *The folded value of `left op right`, or None when folding would change behaviour.*
- `def fold_unary(op, value)` - *The folded value of a unary operator, or None when it must stay dynamic.*
- `def has_call(expression)` - *Whether evaluating `expression` can run a call, and therefore an effect.*
- `def _constant(expression)` - *The literal value of an expression, or None when it is not a constant.*
- `def _flatten(statements)` - *Splice a branch selected by folding into its parent, since Cairn has one flat scope.*
- `def _reachable(statements)` - *Drop statements after one that cannot fall through: `return`, `break`, `continue`.*
- `def optimize(program)` - *Run one folding pass over a checked program.*

---

### [`cairn/parser.py`](cairn/parser.py)
> *Parser: recursive descent over the token list.*

**Size**: `308` lines (`12547` bytes)

**Classes:**
- `class Parser` - *A cursor over one token list; every parse method consumes what it matches.*
  - Method `.parse_program()` - *Parse top-level functions until end of file.*
  - Method `._parse_function()` - *`fn name(a: i64, ...) -> i64 { ... }`.*
  - Method `._parse_parameter()` - *One `name: i64` parameter.*
  - Method `._parse_type()` - *`i64` is the only type in Cairn 0.1; anything else names what is missing.*
  - Method `._parse_block()` - *`{ statement* }`.*
  - Method `._parse_statement()` - *Dispatch on the first token of one statement.*
  - Method `._parse_binding()` - *`let|var name: i64 = expr;`.*
  - Method `._parse_if()` - *`if expr block [else (if | block)]`.*
  - Method `._parse_while()` - *`while expr block`.*
  - Method `._parse_return()` - *`return expr;`.*
  - Method `._parse_expression_statement()` - *An assignment to a name, or a call whose value is discarded.*
  - Method `._parse_expression(minimum)` - *Precedence climbing: parse operators at least as tight as `minimum`.*
  - Method `._parse_unary()` - *`-x`, `!x`, `~x`, otherwise a primary expression.*
  - Method `._parse_primary()` - *An integer, a name, a call, a parenthesised expression, or `true`/`false`.*
  - Method `._parse_call(name)` - *`name(arg, ...)`; the opening parenthesis is already the current token.*
  - Method `._peek()` - *The current token without consuming it.*
  - Method `._advance()` - *Consume and return the current token; `eof` stays put.*
  - Method `._at_end()` - *Whether the cursor reached the `eof` token.*
  - Method `._expect_symbol(text)` - *Consume the given punctuation, or report where it should have been.*
  - Method `._expect_keyword(text)` - *Consume the given keyword, or report where it should have been.*
  - Method `._expect_ident(what)` - *Consume an identifier, or report where it should have been.*
  - Method `._expected(what)` - *A positioned `E2001`: what the grammar wanted and what it found instead.*

**Functions:**
- `def parse(source, path)` - *Tokenize and parse one source text.*

---

### [`cairn/pe.py`](cairn/pe.py)
> *PE writer: link an artifact and emit a Windows PE32+ console executable.*

**Size**: `170` lines (`7444` bytes)

**Functions:**
- `def align(value, boundary)` - *Round `value` up to the next multiple of `boundary`.*
- `def import_blob(imports)` - *The import descriptor, ILT, DLL name and hint/name table, with their `.rdata` offsets.*
- `def patch_imports(blob, imports, offsets, rdata_rva, data_rva)` - *Write the descriptor and the ILT entries now that both section RVAs are known.*
- `def _section_header(name, virtual_size, virtual_address, raw_size, raw_pointer, characteristics)` - *One 40-byte section table entry.*
- `def _optional_header(size_of_code, size_of_data, entry_rva, size_of_image, import_rva, import_size)` - *The 240-byte PE32+ optional header with one data directory filled in.*
- `def headers(text_size, text_raw, entry_rva, rdata_size, rdata_raw, rdata_rva, data_size, data_raw, data_rva, import_rva, import_size)` - *The complete header block, padded to the file alignment.*
- `def _import_address_table(imports, offsets, rdata_rva)` - *The IAT: one hint/name RVA per import, terminated by a null slot.*
- `def link(artifact)` - *Lay out sections, resolve fixups and return the executable image bytes.*

---

### [`cairn/runtime.py`](cairn/runtime.py)
> *Runtime helpers emitted into every executable.*

**Size**: `112` lines (`4113` bytes)

**Functions:**
- `def emit_print_i64(assembler)` - *Emit `print_i64`: write rax in decimal plus a newline.*
- `def emit_div_zero(assembler, rodata)` - *Emit the trap taken when a divisor is zero: message on stderr, exit code 3.*
- `def emit_runtime(assembler, rodata)` - *Emit every helper routine and the data they reference, in a stable order.*

---

### [`cairn/sema.py`](cairn/sema.py)
> *Semantic analysis: names, arity, mutability and return-path checks.*

**Size**: `245` lines (`11435` bytes)

**Classes:**
- `class Binding` - *One name in a function: its frame slot, whether it can be assigned, and where it began.*
- `class Analyzer` - *One pass over a parsed program, mutating slots and collecting diagnostics.*
  - Method `.analyze(program)` - *Check the whole program; raise every diagnostic at once if any was found.*
  - Method `._report(code, message, fix, span)` - *Record one diagnostic and keep going.*
  - Method `._collect_functions(program)` - *Register top-level names and reject duplicates, redefinitions and arity overflow.*
  - Method `._check_entry_point(program)` - *`fn main() -> i64` is the executable's entry point and must take no parameters.*
  - Method `._check_function(function)` - *Check one function body with a fresh flat scope.*
  - Method `._new_slot()` - *Allocate the next 8-byte frame slot.*
  - Method `._check_block(block)` - *Check statements in order; the scope is flat and function-wide.*
  - Method `._check_statement(statement)` - *Dispatch one statement to its checker.*
  - Method `._check_binding(statement)` - *A `let`/`var` declares one new name and initialises it from an i64 expression.*
  - Method `._check_assign(statement)` - *Assignment requires an existing `var` binding.*
  - Method `._check_expression(expression)` - *Dispatch one expression to its checker.*
  - Method `._check_name(expression)` - *A name must be a declared local; `print` is callable but not a value.*
  - Method `._check_call(expression)` - *A call names a function (or the builtin) and passes exactly its arity of arguments.*

**Functions:**
- `def emitted_return(block)` - *Whether every path through `block` ends in a `return` statement.*
- `def analyze(program, path)` - *Run semantic analysis over one parsed program.*

---

### [`cairn/syntax.py`](cairn/syntax.py)
> *Syntax tree: one dataclass per construct, each carrying its source span.*

**Size**: `177` lines (`3709` bytes)

**Classes:**
- `class Node` - *Every node carries the span it was parsed from.*
- `class Expr` - *Base class for value-producing nodes.*
- `class IntLiteral` - *A decimal integer literal, already range-checked by the lexer.*
- `class Name` - *A reference to a parameter or a local; `slot` is filled in by semantic analysis.*
- `class Unary` - *`-x`, `!x` or `~x`.*
- `class Binary` - *A binary operator; `op` is the source spelling.*
- `class Parens` - *A parenthesised expression; kept so spans and grouping survive into code generation.*
- `class Call` - *`name(arg, ...)`; the callee is a function name or the builtin `print`.*
- `class Stmt` - *Base class for statements.*
- `class Block` - *A brace-delimited statement sequence.*
- `class Let` - *`let x: i64 = e;` (immutable) or `var x: i64 = e;` (mutable).*
- `class Assign` - *`x = e;`, valid only for a `var` binding.*
- `class If` - *`if cond { ... } else { ... }`, where the else block is optional.*
- `class While` - *`while cond { ... }`.*
- `class Return` - *`return e;`; every function returns an i64.*
- `class Break` - *Leave the innermost loop.*
- `class Continue` - *Start the next iteration of the innermost loop.*
- `class ExprStmt` - *An expression evaluated for its effect, which in Cairn means a call.*
- `class Parameter` - *One parameter: a name and its declared type.*
- `class Function` - *A top-level function; `slots` is the number of 8-byte frame slots it needs.*
- `class Program` - *A whole source file.*

---

### [`cairn/values.py`](cairn/values.py)
> *The six core values, each bound to the command and the test that prove it.*

**Size**: `90` lines (`3845` bytes)

**Classes:**
- `class Value` - *One core value: the claim, the command that shows it, and the test that defends it.*

**Functions:**
- `def values_manifest()` - *The values as JSON-ready records, in their frozen order.*

---

### [`cairn/verify.py`](cairn/verify.py)
> *Verification of a built artifact: structural checks that need no execution.*

**Size**: `229` lines (`10632` bytes)

**Classes:**
- `class Problem` - *One failed invariant, addressed by its code.*
  - Method `.render()` - *The one-line report form.*

**Functions:**
- `def _u16(image, offset)`
- `def _u32(image, offset)`
- `def _sections(image)` - *`(name, header offset)` for every section table entry that fits in the file.*
- `def _rva_to_offset(image, rva)` - *Translate an RVA through the section table; -1 when it lands in no section.*
- `def _check_signatures(image, problems)` - *MZ at the start, e_lfanew at 0x3C, and `PE\0\0` where it points.*
- `def _check_coff(image, problems)` - *Machine, section count and the optional header size.*
- `def _check_optional(image, problems)` - *PE32+ magic and the console subsystem.*
- `def _check_section_names(image, problems)` - *The three sections in order, so code, data and imports are where the writer puts them.*
- `def _check_section_geometry(image, problems)` - *File alignment, pointer ordering and the file actually containing each section.*
- `def _check_permissions(image, problems)` - *Code is executable and read-only; data is writable and never executable.*
- `def _check_entry(image, problems)` - *The entry point must fall inside `.text`, or Windows would start in data.*
- `def _check_image_size(image, problems)` - *`SizeOfImage` must cover every section, aligned to the section alignment.*
- `def _check_imports(image, problems)` - *The descriptor sits in `.rdata`, names kernel32, and its IAT ends with a null slot.*
- `def verify_image(image)` - *Every structural invariant of one produced executable; an empty list means verified.*

---

### [`cairn/version.py`](cairn/version.py)
> *The one place the compiler version is written.*

**Size**: `5` lines (`104` bytes)

---

### [`tests/__init__.py`](tests/__init__.py)
> *Test package for the Cairn bootstrap compiler.*

**Size**: `1` lines (`53` bytes)

---

### [`tests/test_benchmarks.py`](tests/test_benchmarks.py)
> *Benchmark harness tests: summarising and rendering, without running a benchmark.*

**Size**: `87` lines (`4289` bytes)

**Classes:**
- `class SummaryTest` - *The arithmetic an agent reads from a report must be reproducible by hand.*
  - Method `.test_summary_reports_best_median_worst_and_per_unit_cost()`
  - Method `.test_zero_iterations_does_not_divide()`
  - Method `.test_every_case_declares_an_existing_cairn_and_c_source()`
  - Method `.test_render_says_unavailable_when_there_is_no_c_compiler()`
  - Method `.test_build_command_skips_folding_only_when_asked()`
  - Method `.test_render_names_an_unoptimised_build()`
- `class VerdictTest` - *The `faster than C` goal is decided by measurements, never assumed.*
  - Method `.test_lower_best_time_wins()`
  - Method `.test_missing_baseline_is_unavailable_not_a_win()`
  - Method `.test_render_states_the_goal_for_each_outcome()`
  - Method `.test_a_report_without_a_verdict_is_treated_as_unmeasured()`
- `class CompilerProbeTest` - *Compiler detection must return a name the harness knows, or nothing at all.*
  - Method `.test_detected_compiler_is_one_of_the_known_families()`

---

### [`tests/test_capabilities.py`](tests/test_capabilities.py)
> *The machine-checkable core: registry, manifest, listing and the stdlib-only proof.*

**Size**: `110` lines (`4752` bytes)

**Classes:**
- `class RegistryTest` - *ADR-003 makes codes a contract, so all three directions are asserted.*
  - Method `.test_every_code_the_source_emits_is_registered()`
  - Method `.test_every_registered_code_is_documented_in_the_readme()`
  - Method `.test_every_registered_code_is_in_the_capabilities_manifest()`
  - Method `.test_the_registry_has_no_duplicate_codes()`
  - Method `.test_codes_group_by_stage_in_registry_order()`
- `class CapabilitiesTest` - *The manifest is the first thing an agent reads, so it must be complete and valid.*
  - Method `.test_manifest_is_json_serialisable_and_names_every_command()`
  - Method `.test_manifest_publishes_the_frozen_surface()`
  - Method `.test_text_form_fits_one_screen()`
- `class ListingTest` - *The listing is read-only and shows the labels the linker resolves.*
  - Method `.test_listing_names_entry_main_and_runtime_helpers()`
  - Method `.test_listing_rows_are_hex_at_six_digit_offsets()`
- `class DependencyTest` - *The compiler may import the Python standard library and nothing else.*
  - Method `.test_the_package_imports_only_stdlib_and_itself()`

**Functions:**
- `def package_sources()` - *Every Python module of the compiler, not its tests or benchmarks.*

---

### [`tests/test_cli.py`](tests/test_cli.py)
> *CLI tests: exit codes, JSON diagnostics, build output and atomic overwrite.*

**Size**: `149` lines (`6454` bytes)

**Classes:**
- `class CliTest` - *An agent drives the compiler through this surface, so it is tested as a contract.*
  - Method `.setUp()`
  - Method `.test_check_json_reports_success()`
  - Method `.test_check_json_lists_every_diagnostic()`
  - Method `.test_check_human_form_states_the_fix()`
  - Method `.test_build_writes_the_requested_path()`
  - Method `.test_build_twice_produces_identical_bytes()`
  - Method `.test_missing_input_is_a_usage_error()`
  - Method `.test_version_is_printed()`
  - Method `.test_capabilities_json_publishes_codes_and_commands()`
  - Method `.test_capabilities_text_names_the_surface()`
  - Method `.test_listing_json_carries_the_text_by_label()`
  - Method `.test_verify_accepts_a_source_and_a_built_executable()`
  - Method `.test_verify_reports_a_broken_image_with_its_code()`
  - Method `.test_verify_forwards_a_compile_failure()`
  - Method `.test_verify_missing_target_is_a_usage_error()`

**Functions:**
- `def _capture(call)` - *Run a CLI call with both streams redirected; returns their text and the exit code.*
- `def _remove_tree(folder)` - *Delete the test's temporary folder, ignoring files Windows may still hold.*

---

### [`tests/test_codegen.py`](tests/test_codegen.py)
> *Code-generation tests: where a value lives, and what the frame must carry for it.*

**Size**: `110` lines (`4122` bytes)

**Classes:**
- `class RecordingAssembler` - *An assembler that remembers every frame slot it reads or writes per function.*
  - Method `.__init__()`
  - Method `.label(name)` - *Track which function the next frame access belongs to.*
  - Method `.mov_load_rbp(destination, offset)` - *Record a frame load, then emit it.*
  - Method `.mov_store_rbp(offset, source)` - *Record a frame store, then emit it.*
  - Method `.loads_of(function)` - *Offsets of frame loads inside one function.*
  - Method `.stores_of(function)` - *Offsets of frame stores inside one function.*
- `class SlotHomeTest` - *A slot with a home register must not be read from or written to the stack.*
  - Method `.test_two_hot_locals_never_touch_their_frame_slots()`
  - Method `.test_used_homes_are_saved_and_restored()`
  - Method `.test_the_seventh_slot_keeps_the_stack_fallback()`

**Functions:**
- `def lower(source)` - *Check and lower one source, returning the program and its recording assembler.*

---

### [`tests/test_end_to_end.py`](tests/test_end_to_end.py)
> *End-to-end tests: build real executables, run them, and assert what they print.*

**Size**: `192` lines (`6637` bytes)

**Classes:**
- `class EndToEndTest` - *Each test compiles a source, writes it to a temporary folder and executes it.*
  - Method `.setUp()`
  - Method `._cleanup()` - *Remove the built executable and its folder.*
  - Method `.build(source)` - *Compile source text and return the executable path.*
  - Method `.execute(source)` - *Run a compiled program, capturing both streams.*
  - Method `.test_hello_prints_an_expression()`
  - Method `.test_arithmetic_truncates_shifts_and_returns_the_exit_code()`
  - Method `.test_recursion_and_loop_control()`
  - Method `.test_division_by_zero_is_a_trap_with_a_message_and_exit_code_3()`
  - Method `.test_min_int64_prints_with_its_sign()`
  - Method `.test_comparison_and_logical_operators_yield_one_or_zero()`
  - Method `.test_four_argument_call_uses_every_argument_register()`
  - Method `.test_print_inside_an_argument_list_evaluates_left_to_right()`
  - Method `.test_calls_preserve_locals_held_in_callee_saved_registers()`
  - Method `.test_forty_locals_use_wide_frame_displacements()`
  - Method `.test_a_binding_from_a_branch_that_never_ran_reads_as_zero()`
  - Method `.test_optimised_and_unoptimised_builds_behave_identically()`

---

### [`tests/test_lexer.py`](tests/test_lexer.py)
> *Lexer tests: token shape, spans, comments and the two literal failures.*

**Size**: `54` lines (`2253` bytes)

**Classes:**
- `class LexerTest` - *The scanner is the first stage an agent's source touches, so its reports must be exact.*
  - Method `.test_tokens_carry_kind_text_value_and_span()`
  - Method `.test_comment_runs_to_end_of_line()`
  - Method `.test_two_character_operators_win_over_one()`
  - Method `.test_integer_beyond_i64_is_a_positioned_error()`
  - Method `.test_unexpected_character_names_the_fix()`
  - Method `.test_tokenize_reports_eof_at_the_end()`

---

### [`tests/test_machine.py`](tests/test_machine.py)
> *Emitter tests: the exact bytes of the encodings the generator relies on.*

**Size**: `77` lines (`3494` bytes)

**Classes:**
- `class EncodingTest` - *Each test names one encoding and its expected bytes.*
  - Method `.code(build)` - *Run a builder function against a fresh assembler and return its bytes.*
  - Method `.test_push_and_pop_extended_registers_mask_the_register_bits()`
  - Method `.test_integer_literal_is_a_full_imm64_move()`
  - Method `.test_frame_slot_load_and_store()`
  - Method `.test_frame_slot_beyond_disp8_uses_a_four_byte_displacement()`
  - Method `.test_signed_division_of_r10()`
  - Method `.test_arithmetic_shift_uses_cl()`
  - Method `.test_comparison_result_is_zero_extended_into_rax()`
  - Method `.test_shell_alignment_is_one_instruction()`
  - Method `.test_register_immediate_add_uses_the_short_form_when_it_fits()`
  - Method `.test_register_immediate_sub_and_cmp_use_the_group_one_encoding()`
  - Method `.test_relative_call_records_a_fixup_at_the_displacement()`
  - Method `.test_label_records_the_current_offset()`

---

### [`tests/test_optimize.py`](tests/test_optimize.py)
> *Optimiser tests: folding arithmetic, refusals and effect preservation.*

**Size**: `138` lines (`6359` bytes)

**Classes:**
- `class FoldTest` - *Folding must match the machine exactly, including wrapping and the division trap.*
  - Method `.test_arithmetic_wraps_at_the_i64_boundary()`
  - Method `.test_division_truncates_toward_zero_and_remainder_follows_the_dividend()`
  - Method `.test_division_by_zero_is_never_folded()`
  - Method `.test_the_one_overflowing_division_is_never_folded()`
  - Method `.test_shifts_mask_the_count_the_way_the_hardware_does()`
  - Method `.test_comparisons_and_logical_operators_yield_zero_or_one()`
  - Method `.test_bitwise_operators_keep_two_complement_meaning()`
  - Method `.test_unary_folding()`
- `class RewriteTest` - *Whole-program rewrites: the tree the backend sees afterwards.*
  - Method `.test_constant_expression_becomes_one_literal()`
  - Method `.test_neutral_right_operand_disappears()`
  - Method `.test_neutral_left_operand_disappears_except_for_subtraction()`
  - Method `.test_zero_multiply_is_dropped_only_when_nothing_calls()`
  - Method `.test_constant_condition_selects_the_taken_branch()`
  - Method `.test_constant_false_while_loop_disappears()`
  - Method `.test_statements_after_return_are_unreachable_and_dropped()`
  - Method `.test_a_call_is_never_removed()`
  - Method `.test_folded_count_reports_what_changed()`
  - Method `.test_constant_heavy_code_generates_fewer_instructions()`

**Functions:**
- `def folded_value(op, left, right)` - *`fold_constant` for one pair, through the public entry point.*
- `def optimized_body(source)` - *Optimise a single-function body; a trailing `return 0;` is added and then hidden.*

---

### [`tests/test_parser.py`](tests/test_parser.py)
> *Parser tests: shapes, precedence and the grammar's positioned refusals.*

**Size**: `74` lines (`3225` bytes)

**Classes:**
- `class ParserTest` - *The tree shape decides code shape, so these assertions are about generated code too.*
  - Method `.test_function_signature_and_body()`
  - Method `.test_multiplication_binds_tighter_than_addition()`
  - Method `.test_parentheses_override_precedence()`
  - Method `.test_else_if_nests_as_a_block()`
  - Method `.test_assignment_is_a_statement_only_for_a_name()`
  - Method `.test_unknown_type_is_refused_with_the_one_type_cairn_has()`
  - Method `.test_missing_semicolon_reports_what_was_expected()`
  - Method `.test_expression_statement_must_be_a_call()`
  - Method `.test_true_and_false_are_integers()`

**Functions:**
- `def body(source)` - *The body of the single function in a tiny program.*

---

### [`tests/test_pe.py`](tests/test_pe.py)
> *Backend and PE tests: labels, fixups, determinism and the produced image's structure.*

**Size**: `114` lines (`5087` bytes)

**Classes:**
- `class CodegenTest` - *The backend is checked through the artifact it produces.*
  - Method `.test_artifact_names_entry_main_and_runtime()`
  - Method `.test_every_fixup_resolves()`
  - Method `.test_unresolved_label_is_a_compiler_bug_report()`
  - Method `.test_build_is_byte_reproducible()`
- `class PeHeaderTest` - *Byte-level checks, because there is no loader to ask at test time.*
  - Method `.setUp()`
  - Method `.test_dos_and_pe_signatures()`
  - Method `.test_coff_header_names_amd64_and_three_sections()`
  - Method `.test_optional_header_is_pe32_plus_console()`
  - Method `.test_sections_are_text_rdata_and_writable_data()`
  - Method `.test_entry_point_lies_inside_text()`
  - Method `.test_import_directory_and_iat_are_filled()`
  - Method `.test_section_raw_pointers_follow_the_headers()`

**Functions:**
- `def artifact_for(source)` - *The linked artifact for a source, without going through the file writer.*
- `def rva_to_offset(image, rva)` - *Translate one RVA through the section table.*

---

### [`tests/test_sema.py`](tests/test_sema.py)
> *Semantic tests: every reachable rule, one failing source each.*

**Size**: `83` lines (`3734` bytes)

**Classes:**
- `class SemaTest` - *Semantic analysis is the last stage that sees names, so its rules are the language's.*
  - Method `.test_valid_program_takes_slots_in_declaration_order()`
  - Method `.test_names_resolve_to_the_parameter_slot()`
  - Method `.test_missing_main_is_the_entry_point_error()`
  - Method `.test_duplicate_function()`
  - Method `.test_more_than_four_parameters()`
  - Method `.test_unknown_name_and_use_before_declaration()`
  - Method `.test_assignment_to_a_let_is_refused()`
  - Method `.test_duplicate_binding_without_shadowing()`
  - Method `.test_unknown_function()`
  - Method `.test_wrong_argument_count()`
  - Method `.test_print_is_callable_not_redefinable()`
  - Method `.test_break_outside_a_loop()`
  - Method `.test_function_must_return_on_every_path()`
  - Method `.test_if_else_returning_both_ways_is_accepted()`
  - Method `.test_every_diagnostic_is_collected_in_one_pass()`

**Functions:**
- `def codes(source)` - *The diagnostic codes a source produces; asserts it is refused.*

---

### [`tests/test_values.py`](tests/test_values.py)
> *The core values are a contract: six values, each with a live guard and a proving command.*

**Size**: `97` lines (`4066` bytes)

**Classes:**
- `class CoreValuesTest` - *The six values and their order are frozen; evidence and guards must stay real.*
  - Method `.test_the_six_values_are_frozen_in_order()`
  - Method `.test_every_value_has_a_claim_an_evidence_command_and_a_distinct_guard()`
  - Method `.test_every_hygiene_guard_names_a_real_test_method()`
  - Method `.test_every_evidence_command_names_a_published_subcommand()`
  - Method `.test_keys_are_unique_and_indexed()`
  - Method `.test_capabilities_publishes_every_value_with_its_evidence()`
  - Method `.test_readme_states_every_value_in_its_frozen_order()`

**Functions:**
- `def guard_source(guard)` - *Parse the test file a guard names, so the guard is checked without being run again.*
- `def guard_names(guard)` - *The dotted names after the file, for example `DependencyTest` then its test method.*
- `def evidence_command(evidence)` - *The `cairn` subcommand, or the test module, that an evidence line runs.*

---

### [`tests/test_verify.py`](tests/test_verify.py)
> *Verifier tests: every artifact invariant is decided from bytes and names its code.*

**Size**: `64` lines (`2383` bytes)

**Classes:**
- `class VerifyTest` - *Each test breaks one rule and expects exactly the code that names it.*
  - Method `.test_a_fresh_build_has_no_problems()`
  - Method `.test_every_verification_code_is_reachable()`
  - Method `.test_a_problem_renders_with_its_code()`

**Functions:**
- `def image_bytes()` - *A real build, which verification must accept unchanged.*
- `def patched(offset, payload)` - *A build with one field overwritten, to isolate the invariant it belongs to.*

---
