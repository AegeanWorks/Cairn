# Codebase Map & Symbol Catalog 🗺️

> **AUTO-GENERATED & VERIFIED BY `scripts/update_knowledge_base.py`**
> This document catalogues Python modules, classes, and exported functions. See Map Coverage for any stack it does not index.

---

## 📊 Codebase Metrics
- **Total Python Modules**: `47`
- **Total Classes**: `208`
- **Total Top-Level Functions**: `241`
- **Total Class Methods**: `1125`
- **Total Source Lines**: `21304`

## 🧭 Map Coverage
- **Indexed**: python (`47` module(s))
- **Not indexed by this map**: cpp (`38` file(s)), rust (`19` file(s))

> Only Python symbols are catalogued. A language above is reported as not indexed, never as verified by this map.

---

## 📦 Modules & Exported Symbols

### [`benchmarks/__init__.py`](benchmarks/__init__.py)
> *Benchmarks for Cairn: case sources and the harness that measures them.*

**Size**: `1` lines

---

### [`benchmarks/paired.py`](benchmarks/paired.py)
> *Deterministic paired A/B verdict for the benchmark registry.*

**Size**: `116` lines

**Functions:**
- `def timed(executable, stdin)` - *One wall-clock run; returns milliseconds and stdout for parity checks.*
- `def gap(left, right, rounds, stdin)` - *Median ratio left/right and its samples (lower = left faster).*
- `def band(samples)` - *The central 80 percent of an identical-binary control run.*
- `def verdict(gap_value, control_band)` - *Win only when the gap is clearly outside the control band.*
- `def measure_case(case, folder, rounds)` - *Build every twin for one case and measure its gaps with a control band.*
- `def main(argv)`

---

### [`benchmarks/run_benchmarks.py`](benchmarks/run_benchmarks.py)
> *Benchmark harness: measure Cairn executables and, when one exists, C, C++ and Rust baselines.*

**Size**: `675` lines

**Classes:**
- `class Case` - *One benchmark: a Cairn source, equivalent C, C++ and Rust sources, and its unit.*

**Functions:**
- `def find_c_compiler()` - *The first C compiler already installed, or None when the machine has none.*
- `def find_rust_compiler()` - *The first Rust compiler already installed, or None when the machine has none.*
- `def find_cpp_compiler()` - *The first C++ compiler already installed, or None when the machine has none.*
- `def run_command(argv, timeout, cwd, stdin)` - *Run one command with a hard limit, capturing text.*
- `def cairn_build_command(source, destination, optimise)` - *The exact `cairn build` command for one case, with the folding pass optional.*
- `def build_cairn(source, destination, optimise)` - *Compile one Cairn case and return the compile time in milliseconds.*
- `def build_c(compiler, source, destination)` - *Compile one C case; None when this compiler does not produce an executable.*
- `def build_rust(compiler, source, destination)` - *Compile one Rust case; None when this compiler does not produce an executable.*
- `def build_cpp(compiler, source, destination)` - *Compile one C++ case; None when this compiler does not produce an executable.*
- `def measure(executable, rounds, stdin)` - *Wall-clock milliseconds for each run of one executable.*
- `def summarise(samples, iterations)` - *Best, median and per-unit cost from one set of wall-clock samples.*
- `def measure_floors(folder, compiler, cpp_compiler, rust_compiler, rounds, optimise)` - *Each language's startup floor, in ms: an empty program's best time.*
- `def compute_adjusted(reports, floors)` - *One line per case: each language's best minus that language's startup floor.*
- `def verdict(best_cairn, best_c)` - *The measured standing against one baseline: `faster`, `slower`, `tie` or `unavailable`.*
- `def compare_runs(current, previous, noise)` - *Per-case best-time deltas against a saved run; (readable lines, any regression).*
- `def window_note(record)` - *A warning when a saved record was measured in a loaded window, else None.*
- `def latest_history_record(path)` - *The newest valid JSONL record in the history file, or None without one.*
- `def record_from_reports(reports, compiler, rust_compiler, rounds, optimise, cpp_compiler)` - *One history record: the commit this run measured and each best time by language.*
- `def save_record(path, record)` - *Append one record as a single JSONL line.*
- `def benchmark_case(case, compiler, rounds, folder, optimise, rust_compiler, cpp_compiler)` - *Measure one case in Cairn and, when available, in C, C++ and Rust.*
- `def render(reports, compiler, optimise, rust_compiler, cpp_compiler)` - *The human table; one line per case and per measured language.*
- `def _goal_lines(reports, label, compiler, verdict_key, install_hint)` - *The scoreboard line for one baseline, decided only by measurements from this run.*
- `def _number(text)` - *A positive sample count from the command line.*
- `def main(argv)` - *Parse options, run every case and print the report.*

---

### [`cairn/__init__.py`](cairn/__init__.py)
> *Cairn: an agent-first systems language with its own x86-64 backend.*

**Size**: `11` lines

---

### [`cairn/__main__.py`](cairn/__main__.py)
> *`python -m cairn` entry point.*

**Size**: `8` lines

---

### [`cairn/builder.py`](cairn/builder.py)
> *Build pipeline: source text to executable bytes.*

**Size**: `83` lines

**Classes:**
- `class Build` - *One successful build: the parsed program, the linked artifact and the image bytes.*

**Functions:**
- `def compile_source(source, path, optimise)` - *Run every stage over one source text and return the executable image.*
- `def compile_file(path, optimise)` - *Read a `.crn` file as UTF-8 and compile it.*
- `def output_path(source, requested)` - *The executable path: `-o` when given, otherwise the source stem plus `.exe`.*
- `def _temporary_path(destination)` - *A sibling no other thread or process is writing: pid plus thread identity.*
- `def write_executable(destination, image)` - *Write the image to a temporary sibling, then rename it into place in one step.*

---

### [`cairn/capabilities.py`](cairn/capabilities.py)
> *The machine-readable description of what this compiler accepts and emits.*

**Size**: `110` lines

**Functions:**
- `def _operators()` - *Operators grouped by precedence tier, loosest first.*
- `def capabilities()` - *The whole surface as one JSON-serialisable object.*
- `def render_text()` - *A short human summary: the surface on one screen.*

---

### [`cairn/cli.py`](cairn/cli.py)
> *Command line: build, check and run Cairn programs.*

**Size**: `412` lines

**Functions:**
- `def _parser()` - *The whole command surface, declared in one place.*
- `def _add_optimise_flag(parser)` - *Every command shares one switch for the folding pass, so builds can be compared.*
- `def _read_source(path, json_mode)` - *The source text, or an explained usage failure (silent under `--json`).*
- `def _report_failure(error, source, json_mode, path)` - *Print every diagnostic, as JSON or as positioned text.*
- `def _report_unreadable(arguments, path, extra)` - *The `--json` envelope for input that could not be read.*
- `def _build_command(arguments)` - *`build`: compile a source file to a Windows executable, atomically.*
- `def _check_command(arguments)` - *`check`: compile in memory and report, writing nothing.*
- `def _run_envelope(path, *, ok, returncode, timed_out, stdout, stderr, error, diagnostics)` - *One JSON result object for `run`: the exit code and the captured streams.*
- `def _run_command(arguments)` - *`run`: compile into a temporary folder, execute, and forward the exit code.*
- `def _execute(executable, timeout, json_mode)` - *Run the produced executable; return its exit code, timeout flag and streams.*
- `def _read_executable(path, json_mode)` - *The artifact bytes, or an explained usage failure (silent under `--json`).*
- `def _verify_command(arguments)` - *`verify`: structural checks over a built image or over a source compiled in memory.*
- `def _listing_command(arguments)` - *`listing`: compile in memory and print the bytes of every label in `.text`.*
- `def _capabilities_command(arguments)` - *`capabilities`: the surface as JSON for an agent, or one screen of text for a human.*
- `def main(argv)` - *Parse the command line and dispatch one command.*
- `def _configure_streams()` - *Make source text and diagnostics survive a Windows pipe whatever the code page is.*
- `def command_line(argv)` - *The entry point used by `python -m cairn` and the installed console script.*

---

### [`cairn/codegen.py`](cairn/codegen.py)
> *Code generation: syntax tree to x86-64, one pass, values in rax.*

**Size**: `4731` lines

**Classes:**
- `class CodeGenerator` - *One lowering pass over a checked program.*
  - Method `.generate(program)` - *Emit the entry stub, every function, then the runtime helpers.*
  - Method `._fresh(stem)` - *A label name no other construct uses.*
  - Method `._string_label(text)` - *The .rodata label holding `text` NUL-terminated, creating it once.*
  - Method `._emit_entry()` - *The PE entry point: call main, drain stdout, then exit with main's return.*
  - Method `._emit_function(function)` - *Prologue, saved homes, cleared frame, parameter spills, body, and one epilogue.*
  - Method `._save_homes()` - *Keep the caller's values for every callee-saved register this function uses.*
  - Method `._restore_homes()` - *Put the caller's values back before returning.*
  - Method `._clear_frame(function)` - *Clear every slot so a binding declared in a branch that never ran reads as zero.*
  - Method `._home_slots(function, limit)` - *The slots that keep a register home, in register order.*
  - Method `._home(slot)` - *The register that holds `slot`, or None when the slot stays in the frame.*
  - Method `._temp_pool()` - *The spare registers a left operand may hold across a nested expression.*
  - Method `._emit_loop_head(label)` - *Pad onto a 16-byte boundary, then bind the loop's backedge target.*
  - Method `._load_slot(slot, destination)` - *Move one slot into `destination` from its register or its frame slot.*
  - Method `._store_slot(slot, source)` - *Write `source` into one slot's register or frame slot.*
  - Method `._emit_zero_extend_mask(expression, destination)` - *Lower `name & 255` / `name & 65535` as one movzx of the name's home, or refuse.*
  - Method `._emit_value(slot, expression)` - *Assign an expression to a slot, folding `slot = slot op value` in place.*
  - Method `._update_keeps_nonneg(slot, expression)` - *Whether an in-place update provably keeps the slot's value non-negative.*
  - Method `._fold_update(home, slot, expression)` - *Emit `home = home op value` when the expression updates its own slot.*
  - Method `._fold_frame_update(slot, expression)` - *Emit `slot = slot op value` as one memory-operand instruction on a frame slot.*
  - Method `._emit_home_offset(destination, expression)` - *Lower `homed name ± literal` as one `lea`, or return False when the shape differs.*
  - Method `._emit_into(expression, destination)` - *Lower an expression, using `destination` directly when it is a leaf.*
  - Method `._emit_binary_into(expression, destination)` - *Lower a call-free, trap-free binary expression with its result in `destination`.*
  - Method `._emit_left_into(expression, destination)` - *Compute the left operand with its value in `destination`, or refuse.*
  - Method `._computes_in_rax_only(expression, destination, parked_noop)` - *Whether a right operand's lowering leaves `destination` untouched.*
  - Method `._combines_in_rax_only(expression, destination, parked_noop)` - *Whether a commutative right operand lowers into rax alone.*
  - Method `._emit_commutative_into(operator, left, right, destination)` - *Fold `left op right` into `destination` for the commutative group.*
  - Method `._stripped(expression)` - *The expression without its wrapping parentheses.*
  - Method `._literal_multiply_operand(expression)` - *The constant and the other operand of `k * x` or `x * k`, for an imm32 k.*
  - Method `._emit_scaled_product(expression, destination)` - *Lower `name * 3|5|9` as one scaled `lea` into `destination`, or refuse.*
  - Method `._emit_block(block, tail)` - *Emit every statement of a block in order.*
  - Method `._invalidate_string_facts(statement)` - *A store to a fact-bearing slot ends whatever the slot proved.*
  - Method `._record_string_fact(statement)` - *Recognise `s = "literal"` and `len = str_len(s)` for bound facts.*
  - Method `._string_bounds(condition)` - *An index below a str_len value is in bounds, provided it is non-negative.*
  - Method `._builtin_statement(statement, name)` - *Is this a discarded call to a one-argument builtin?*
  - Method `._literal_argument(statement)` - *The integer a builtin statement is called with, if it is a literal.*
  - Method `._emit_statement(statement, tail)` - *Lower one statement; `tail` only concerns a final `return`.*
  - Method `._emit_if(statement, tail)` - *`if` as a conditional branch and two labelled bodies.*
  - Method `._emit_select_if(statement)` - *Lower `if t > c { t = t - c; } else { t = t + c; }` as lea, sub, cmov — or refuse.*
  - Method `._emit_swap_if(statement)` - *Lower the swap shape `if a > b { var t = a; a = b; b = t; }` as two cmovs.*
  - Method `._read_slots_outside(block, skip, acc)` - *Reads of every slot in `block`, ignoring the `skip` statement's subtree.*
  - Method `._plan_constant_hoists(function)` - *Reserve spare homes for each loop's invariant big literals, or return none.*
  - Method `._loops_within(block)` - *Every While statement at or below `block`, outer loops before nested ones.*
  - Method `._big_literal_counts(loop)` - *Distinct literals outside the immediate range (the movabs-only forms), counted.*
  - Method `._count_in_block(block, counter)`
  - Method `._count_in(expression, counter)`
  - Method `._emit_while(statement)` - *Load the loop's plan-hoisted constants into their spare homes, then lower.*
  - Method `._lowers_as_real_body()` - *Whether emission runs in the real body of a fully-homed, pair-free function.*
  - Method `._preceded_by_zero(target, slot)` - *Is `target` immediately preceded in its block by `slot = 0`?*
  - Method `._try_emit_array_rmw_vector(statement)` - *Lower a counted `a[i] = a[i] + invariant; i = i + 1` loop with two lanes.*
  - Method `._try_emit_stdin_scan(statement)` - *Rotate `while true { c = read_char(); if c < 0 break; total += c }`.*
  - Method `._emit_while_body(statement)` - *`while` with its own break and continue targets.*
  - Method `._try_emit_halving_loop(statement)` - *Emit a fused odd-pair body as one unconditional half-step, or refuse.*
  - Method `._is_nonnegative(slot)` - *Whether the slot provably holds a non-negative value at this emit point.*
  - Method `._expression_nonnegative(expression)` - *Whether the expression provably evaluates non-negative at this emit point.*
  - Method `._guard_nonnegative(condition)` - *Slots a `while slot > c` (or `slot >= c + 1`) guard proves non-negative in its body.*
  - Method `._countdown_slot(statement)` - *The slot a countdown loop's counter occupies.*
  - Method `._countdown_home(statement)` - *The callee-saved register a countdown loop's counter lives in, or None.*
  - Method `._emit_condition(expression, target, when_true)` - *Jump to `target` when the condition has the requested truth value.*
  - Method `._emit_logical_branch(expression, target, when_true)` - *Branch on `a && b` / `a || b` from each side's flags in turn.*
  - Method `._bit_test_operand(expression)` - *`(value, mask)` when the compare is `x % 2**b` or `x & m`, else None.*
  - Method `._logical_shift_shape(expression)` - *The `(operand, shift)` of a `(a >> k) & (2**(64-k) - 1)` tree, or None.*
  - Method `._emit_logical_shift(expression, destination)` - *Lower `(a >> k) & (2**(64-k) - 1)` as one logical `shr`, or refuse.*
  - Method `._shift_through_target(expression)` - *"direct" (operand expr, m) or "forward" (caller home, m) for `(a >> m) << m`.*
  - Method `._emit_shift_through_into_rax(expression)` - *Lower `(a >> m) << m` as one load-plus-and-immediate into rax, or refuse.*
  - Method `._emit_shift_through_into_destination(expression, destination)` - *Lower `(a >> m) << m` as one load-plus-and-immediate into `destination`.*
  - Method `._emit_comparison_branch(expression, target, when_true)` - *Compare two operands and branch, using an immediate form when the right side is small.*
  - Method `._emit_expression(expression)` - *Lower one expression, leaving its value in rax.*
  - Method `._emit_index_load(expression)` - *Leave element `index` of the array in rax; bound checks stay compile-time.*
  - Method `._index_register(expression)` - *The register holding a dynamic index's value when this call returns.*
  - Method `._emit_element_store(statement)` - *`a[i] = value;`: fold literal indices, park a dynamic address in the frame.*
  - Method `._emit_dynamic_element_address(statement)` - *Leave the element's address in rcx for `a[i]` with a non-literal `i`.*
  - Method `._direct_update_operand(expression)` - *The operand of a fused element update that needs no temporary register.*
  - Method `._store_immediate_or_register(operator, offset, direct)` - *One frame-slot ALU update whose operand is ready to use.*
  - Method `._inplace_element(statement)` - *Match `a[i] = a[i] op e` (or `e op a[i]` commutative) for one fused update.*
  - Method `._emit_array_init(statement)` - *Store every element of an array initializer into its consecutive slots.*
  - Method `._array_sizes(function)` - *The frame-slot span of every array binding: base slot -> element count.*
  - Method `._slot_in_array(slot)` - *Whether `slot` is one of a declared array's consecutive frame slots.*
  - Method `._emit_unary(expression)` - *Negate, complement, or compare with zero.*
  - Method `._emit_binary(expression)` - *Short-circuit `&&`/`||`, otherwise place the left operand, then combine.*
  - Method `._emit_left_operand(left, right)` - *Evaluate `left`, keep it across `right`, and leave it in rcx with right in rax.*
  - Method `._emit_power_of_two_division(expression)` - *Lower `x / 2**b` or `x % 2**b` as a shift fixup chain; True when emitted.*
  - Method `._emit_magic_division(expression)` - *Lower `x / d` or `x % d` for a positive literal `d` as a magic multiply.*
  - Method `._scaled_sum_terms(expression)` - *The scaled-item pattern of a `+` expression: `(Name, scale_log2, addend, with_base)`.*
  - Method `._emit_scaled_sum(expression)` - *Lower `c1 + k*c2` (either operand order) as one scaled `lea`; True when emitted.*
  - Method `._emit_home_sum(expression)` - *Lower `a + b` with both names homed as one `lea rax, [a + b]`; True when emitted.*
  - Method `._emit_division(operator, nonnegative_divisor)` - *Signed division with a guarded 32-bit fast path and an explicit zero trap.*
  - Method `._emit_and(expression)` - *`&&`: the right side is evaluated only when the left side is non-zero.*
  - Method `._emit_or(expression)` - *`||`: the right side is evaluated only when the left side is zero.*
  - Method `._emit_call(expression, copy_home)` - *Evaluate arguments left to right, place them in the ABI registers, then call.*
  - Method `._emit_foreign_call(expression, copy_home)` - *Bind or substitute the arguments, then copy the callee or call it for real.*
  - Method `._emit_foreign_arguments(expression, skip)` - *Bind every unsubstituted argument left to right into its ABI register.*
  - Method `._emit_parked_copy_arguments(expression, skip, guest)` - *Bind a copy call with more arguments than the ABI's four registers.*
  - Method `._plan_foreign_copy(callee)` - *The copy's guest, spill and rebind decisions, without emitting anything.*
  - Method `._return_slot_override(callee, guest, alias, copy_home)` - *Whether the copy's final result slot may simply use the return destination.*
  - Method `._ends_in_return(body)` - *Whether a body's last statement returns (the copy honors a return home).*
  - Method `._promote_stdin_cursor(function)` - *The two spare homes that keep the stdin buffer base and cursor in registers.*
  - Method `._emit_stack_call(expression)` - *Lower a five-or-more argument call: four registers, the rest via the stack.*
  - Method `._is_pure_argument(expression)` - *Whether an argument can bind straight into its ABI register.*
  - Method `._emit_pure_arguments(arguments, skip)` - *Bind call arguments straight into their registers, or return False.*
  - Method `._contains_division(expression)` - *Whether any subtree of `expression` divides — the one trapping operand kind.*
  - Method `._delta_hold_units(function)` - *The per-level merge slots delta inlining needs, in 8-byte units, or 0 when ineligible.*
  - Method `._assign_writes_parameter(node, slot)` - *Whether any statement in `node` may rebind or shadow the parameter in `slot`.*
  - Method `._has_delta_self_call(node, name, slot)` - *Whether `node` contains at least one self-call whose argument carries a constant delta.*
  - Method `._delta_argument(expression, slot)` - *The constant shift this call's argument adds to the inlined parameter, or None.*
  - Method `._emit_delta_self_call(expression, delta)` - *Lower a constant-delta self-call as pure text: the copy's parameter is the*
  - Method `._pair_inline_shape(function)` - *Whether the function fits the two-parameter pure-text inlining shape.*
  - Method `._has_value_zero_guard(node, slot)` - *Whether the body tests the second parameter against zero ahead of the tail.*
  - Method `._is_zero_test(condition, slot)` - *Whether the condition compares the slot-holding name against literal zero.*
  - Method `._is_chain_step(argument)` - *Whether the argument is `param1 - 1`: the exact one-step chain the loop replaces.*
  - Method `._emit_chain_loop(expression)` - *Lower the one-step delta-0 chain as an iterated loop: `R(j) = f**(j+1)(1)`.*
  - Method `._is_pair_body(node, function, depth)` - *Whether every path through `node` ends in an explicit pair-shaped `return`.*
  - Method `._is_pair_return(expression, function, depth)` - *Whether a `return` value fits the pair copies: no self-call, or one that*
  - Method `._is_pair_call(call, function, depth)` - *Whether one self-call matches the pair shape: first argument a constant shift*
  - Method `._emit_pair_self_call(expression, delta)` - *Lower a two-parameter shape self-call as pure text, binding the value first.*
  - Method `._is_inline_candidate(expression)` - *Whether this call may be lowered as an inline copy of the current function.*
  - Method `._emit_inlined_self_call(expression)` - *Lower a self-call as one inline copy: the argument to a shadow home, the body in place.*
  - Method `._foreign_inline_target(expression)` - *The function to copy into this call site, or None when the shape stays a call.*
  - Method `._body_is_copy_free(body)` - *Whether a whole statement tree calls or divides — any refuses a copy.*
  - Method `._substitution_plan(expression, target)` - *Which arguments a foreign-inline copy never binds: the bare names.*
  - Method `._shift_through_plan(expression, target, spill)` - *Parameter slots whose single `p << m` use forwards their fused argument.*
  - Method `._parameter_single_shift_use(body, slot, m)` - *Whether `slot` is read exactly once in `body`, and that read is `slot << m`.*
  - Method `._alias_substitution(substitution, borrowed)` - *Keep every alias whose home register the copy leaves intact.*
  - Method `._emit_foreign_inline(callee, alias, copy_home, bound, shift_through)` - *Lower a call to another function as one copy of its body, result in rax.*
  - Method `._rebinds_parameters(body, count)` - *Whether any statement assigns a parameter slot.*
  - Method `._expression_reads(expression, slot)` - *Whether an expression reads `slot`.*
  - Method `._statement_touches(statement, slot)` - *Whether a statement reads or writes `slot` in any position.*
  - Method `._statement_touches_branch(block, slot)` - *Whether any statement of a nested block, at any depth, touches `slot`.*
  - Method `._cleared_before_use(body, slot)` - *Whether a top-level assignment to `slot` comes before anything can touch it.*
  - Method `._body_may_hold(body)` - *Whether any merge in this body keeps its left value across a right operand that calls.*
  - Method `._emit_read_char_into(destination, done)` - *Emit the promoted stdin read into `destination`, or refuse.*
  - Method `._emit_call_site(target)` - *A call, padded so rsp is 16-byte aligned as the ABI requires.*
  - Method `._emit_direct_indirect_call(import_name)` - *An indirect call through the IAT, padded like `_emit_call_site`.*

**Functions:**
- `def _shape_equal(left, right)` - *Structural equality of two index sub-expressions, spans ignored.*
- `def function_label(name)` - *The assembler label of a Cairn function.*
- `def slot_offset(slot)` - *The rbp-relative offset of a frame slot; slot 0 is the first parameter.*
- `def array_element_base(base, size)` - *The lowest rbp displacement of an array span; element `i` sits at base + 8i.*
- `def align16(value)` - *Round a frame size up to the 16-byte stack alignment the ABI requires.*
- `def slot_of(expression)` - *The slot a parenthesised name refers to, or None for every other expression.*
- `def _is_plus_step(statement, level)` - *Whether the statement is `amount = amount + level` for its own slot.*
- `def is_leaf(expression)` - *Whether evaluating the expression is a load or a literal move and nothing else.*
- `def magic_division_parameters(divisor)` - *A `(multiplier, shift)` so the magic-multiply chain divides by `divisor` exactly.*
- `def generate(program)` - *Lower a checked program to a linked artifact.*

---

### [`cairn/codes.py`](cairn/codes.py)
> *The diagnostic code registry: one record per code the compiler can emit.*

**Size**: `84` lines

**Classes:**
- `class Code` - *One diagnostic code: its stage family and a one-line statement of what it means.*

**Functions:**
- `def codes_in_source(texts)` - *Every code literal that appears in source text, which is what the compiler emits.*
- `def codes_by_stage()` - *Registered codes grouped by stage, in registry order, for the capabilities manifest.*

---

### [`cairn/diagnostics.py`](cairn/diagnostics.py)
> *Diagnostics: every failure carries a stable code, a span and a fix.*

**Size**: `99` lines

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

### [`cairn/filetable.py`](cairn/filetable.py)
> *qwenFS toy for Cairn: a deterministic in-memory file table with integer handles.*

**Size**: `198` lines

**Classes:**
- `class FileTable` - *Deterministic exact-match file table plus a directed link graph.*
  - Method `.__init__()`
  - Method `.__len__()` - *The number of files currently stored.*
  - Method `.__contains__(handle)` - *Whether an integer handle names a stored file.*
  - Method `.write(handle, data)` - *Create or overwrite one file with its whole content.*
  - Method `.read(handle)` - *Return the whole content of one file, or raise KeyError when missing.*
  - Method `.list()` - *Every stored handle in sorted order (no search, exact enumeration).*
  - Method `.link(source, target)` - *Add one directed edge source -> target between two existing files.*
  - Method `.unlink_link(source, target)` - *Remove one directed edge, or raise KeyError when it is absent.*
  - Method `.unlink(handle)` - *Remove one file and every incident link edge.*
  - Method `.list_links(handle)` - *Outgoing edges of one file in sorted order.*
  - Method `.list_backlinks(handle)` - *Incoming edges of one file in sorted order.*

**Functions:**
- `def _check_handle(handle)` - *Validate one integer-handle path for the i64-only language.*
- `def _check_data(data)` - *Validate one file payload: whole bytes, never a partial view.*
- `def emit_filetable(assembler, rodata)` - *Emit the additive filetable descriptor, mirroring runtime.emit_runtime.*

---

### [`cairn/image.py`](cairn/image.py)
> *Image pieces shared by the backend: read-only data and the linked artifact.*

**Size**: `104` lines

**Classes:**
- `class ReadOnlyData` - *A byte blob with named offsets, placed in the `.rdata` section.*
  - Method `.__len__()` - *The current size in bytes.*
  - Method `.label(name)` - *Bind `name` to the current offset.*
  - Method `.add(name, payload)` - *Bind `name` and append `payload`.*
  - Method `.align(boundary)` - *Pad with zero bytes to the next multiple of `boundary`.*
- `class WritableData` - *A byte blob with named offsets, placed after the IAT in the writable `.data`.*
  - Method `.__len__()` - *The current size in bytes.*
  - Method `.label(name)` - *Bind `name` to the current offset.*
  - Method `.reserve(name, size)` - *Bind `name` to the current offset and append `size` zero bytes of static state.*
  - Method `.align(boundary)` - *Pad with zero bytes to the next multiple of `boundary`.*
- `class Artifact` - *Everything the PE writer needs: code, fixups, data and the entry label.*

**Functions:**
- `def iat_label(function)` - *The linker name of one import address table slot.*

---

### [`cairn/ir.py`](cairn/ir.py)
> *A deliberately minimal typed SSA IR between the AST and machine code.*

**Size**: `599` lines

**Classes:**
- `class DivZeroTrap` - *`/ 0` or `i64.min / -1`: the machine's message plus exit code 3.*
- `class Op` - *One instruction; the last op of every block is always a terminator.*
- `class Block` - *One basic block: straight-line ops, then exactly one terminator.*
- `class Function` - *One lowered function: parameter cells in call order, blocks in emit order.*
- `class Module` - *Every function of one program, callable by name.*
- `class _Translator` - *AST to IR: expressions become SSA values, control flow becomes blocks.*
  - Method `.__init__()`
  - Method `._fresh_value()`
  - Method `._fresh_cell()`
  - Method `._fresh_label(prefix)`
  - Method `.terminates(block)`
  - Method `.open_block(label)`
  - Method `.close_with(op)`
  - Method `.emit_jump_to(target)`
  - Method `.emit(op)`
  - Method `.const(value)`
  - Method `.value_of(expression)` - *Lower one expression, returning the SSA value id holding its result.*
  - Method `.short_circuit(expression)` - *`&&`/`||` lower to branches across one result cell; unstored is false.*
  - Method `.lower_function(function)`
  - Method `.emit_stmt(statement)`
  - Method `._fresh_cell_many(count)` - *Reserve `count` cell ids beyond the last one already issued.*
  - Method `._initializer_elements(statement)` - *The element expressions of an array binding, parens unwrapped.*
- `class Interpret` - *Run a translated module at full machine semantics.*
  - Method `.__init__(stdin)`
  - Method `.run(module, start)`
  - Method `._emit(chunk)` - *Append output bytes the way the runtime's buffer boards them.*
  - Method `._drain()`
  - Method `._call(module, name, arguments)`
  - Method `._builtin(module, op, vals)`
  - Method `._by_label(function, label)`
- `class _ProcessExit` - *`exit(code)` called from inside a user function: unwinds the whole walk.*
  - Method `.__init__(code)`

**Functions:**
- `def to_signed(raw)` - *The wrapped 64-bit pattern `raw` re-read as a signed integer.*
- `def trunc_divmod(left, right)` - *C semantics: quotient truncates toward zero, remainder keeps the sign.*
- `def apply_binop(op, left, right)` - *One `i64` binary operation at machine semantics, signed values in and out.*
- `def _static_index(expression)` - *A compile-time element position, or None; sema already range-checks this.*
- `def translate(program)` - *Lower one optimized program (for-loops already lowered) into a module.*
- `def encode_number(value)` - *The runtime's decimal form of one signed `i64` (no newline).*

---

### [`cairn/lexer.py`](cairn/lexer.py)
> *Lexer: source text to a flat token list.*

**Size**: `195` lines

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
  - Method `._string(start)` - *A quoted literal with `\\`, `\"`, `\n` and `\t` escapes; its text is decoded.*
  - Method `._consume(count)` - *Advance over `count` characters, tracking a newline inside the run.*
  - Method `._span_from(start)` - *The span between `start` and the current position.*
  - Method `._number(start)` - *A decimal integer literal that must fit in a signed 64-bit integer.*
  - Method `._identifier(start)` - *A name, or one of the fixed keywords.*

**Functions:**
- `def tokenize(source, path)` - *Tokenize one source text.*

---

### [`cairn/listing.py`](cairn/listing.py)
> *Machine-code listing: the emitted bytes, grouped by the label that owns them.*

**Size**: `46` lines

**Functions:**
- `def _rows(chunk, start)` - *Hex rows with the offset of each row's first byte.*
- `def render_listing(artifact)` - *Every label with its offset and the bytes up to the next label.*

---

### [`cairn/machine.py`](cairn/machine.py)
> *x86-64 emitter: the instruction subset Cairn lowers to, written as bytes.*

**Size**: `762` lines

**Classes:**
- `class Assembler` - *A byte buffer with named labels and forward-reference fixups.*
  - Method `.here()` - *The current offset in the code buffer.*
  - Method `.emit(*parts)` - *Append raw bytes; any such append invalidates the flag summary.*
  - Method `.label(name)` - *Bind `name` to the current offset; a duplicate name is a compiler bug.*
  - Method `.rel32(target)` - *A placeholder displacement patched when the image is linked.*
  - Method `.call(target)` - *`call rel32` to a label in this image.*
  - Method `.call_indirect(target)` - *`call qword ptr [rip+disp32]`, used for imported functions through the IAT.*
  - Method `.jmp(target)` - *`jmp rel32`; a bound backward target within a byte takes `jmp rel8` (EB).*
  - Method `.jcc(condition, target)` - *`jcc rel32`; a bound backward target within a byte takes `jcc rel8` (70+r).*
  - Method `.push(reg)` - *`push r64`.*
  - Method `.pop(reg)` - *`pop r64`.*
  - Method `.lea_imm(destination, base, offset)` - *`lea r64, [base+disp]`, computing `base + offset` without touching flags.*
  - Method `.lea_scale(destination, base, index, scale_log2, displacement)` - *`lea r64, [base + index*2**scale_log2 + displacement]` (SIB byte).*
  - Method `.lea_index(destination, index, scale_log2, displacement)` - *`lea r64, [index*2**scale_log2 + displacement]` with no base register.*
  - Method `.mov_reg_reg(destination, source)` - *`mov r64, r64`.*
  - Method `.mov_imm64(reg, value)` - *`mov r64, imm64`, used for every integer literal.*
  - Method `.mov_imm32(reg, value)` - *`mov r32, imm32`; writes zero-extend into the 64-bit register.*
  - Method `.mov_load_rbp(destination, offset)` - *`mov r64, [rbp+disp]` for a frame slot; disp8 shrinks the encoding where it fits.*
  - Method `.mov_store_rbp(offset, source)` - *`mov [rbp+disp], r64` for a frame slot.*
  - Method `.binary_store_rbp(opcode, offset, source)` - *`op [rbp+disp], r64` for an in-place frame update (add, sub, and, or, xor).*
  - Method `.group1_store_rbp_imm(extension, offset, value)` - *`op [rbp+disp], imm8/imm32` sign-extended, in the register form's group-1 style.*
  - Method `.mov_store_rsp_imm32(offset, value)` - *`mov qword ptr [rsp+disp32], imm32` for outgoing ABI slots.*
  - Method `.mov_store_rsp(source)` - *`mov [rsp], r64`: a store into the topmost pad slot.*
  - Method `.mov_load_rsp(destination)` - *`mov r64, [rsp]`: a load from the topmost pad slot.*
  - Method `.mov_store_rsp_disp(offset, source)` - *`mov [rsp+disp8], r64`: a store into one of the real frame's hold slots.*
  - Method `.mov_load_rsp_disp(offset, destination)` - *`mov r64, [rsp+disp8]`: a load from one of the real frame's hold slots.*
  - Method `.lea_rbp(destination, offset)` - *`lea r64, [rbp+disp]` for a frame address.*
  - Method `.lea_rip(destination, target)` - *`lea r64, [rip+disp32]`, used for read-only data addresses.*
  - Method `.mov_load_rip(destination, target)` - *`mov r64, [rip+disp32]`: read a qword of state without an address register.*
  - Method `.mov_store_rip(source, target)` - *`mov [rip+disp32], r64`: write a qword of static state, no address register.*
  - Method `.cmp_reg_rip(left, target)` - *`cmp r64, [rip+disp32]`: compare a register with a qword of static state.*
  - Method `.mov_load(destination, base)` - *`mov r64, [base]`: read a qword through a pointer register.*
  - Method `.mov_store_reg(base, source)` - *`mov [base], r64`: write a qword through a pointer register.*
  - Method `.binary_store_reg(opcode, base, source)` - *`op [base], r64`: one in-place ALU update through a pointer register.*
  - Method `.group1_store_reg_imm(extension, base, value)` - *`op [base], imm8/imm32` sign-extended through a pointer register.*
  - Method `.mov_load_indexed(destination, base, index, scale_log2, displacement)` - *`mov r64, [base + index*2**scale_log2 + disp]`: a frame array element load.*
  - Method `.binary_store_indexed(opcode, base, index, scale_log2, displacement, source)` - *`op [base + index*scale + disp], r64`: a fused element update, no address register.*
  - Method `.mov_store_indexed(base, index, scale_log2, displacement, source)` - *`mov [base + index*scale + disp], r64`: a frame array element store in one instruction.*
  - Method `.mov_store_indexed_imm(base, index, scale_log2, displacement, value)` - *`mov qword [base + index*scale + disp], imm32`: a literal element store.*
  - Method `.group1_store_indexed_imm(extension, base, index, scale_log2, displacement, value)` - *`op [base + index*scale + disp], imm8/imm32`: a fused element update by a literal.*
  - Method `._indexed_memory(opcode, reg, base, index, scale_log2, displacement)` - *One 64-bit instruction with `[base + index*2**scale_log2 + disp]` as its operand.*
  - Method `.movzx_load8(destination, base)` - *`movzx r32, [base]`: read the byte at a pointer register, zero-extended.*
  - Method `.movzx_load8_sib(destination, base, index)` - *`movzx r32, [base + index]`: one byte load off two pointer registers.*
  - Method `.movzx_from8(destination, source)` - *`movzx r32, r8`: the `& 255` mask of a register's value in one instruction.*
  - Method `.movzx_from16(destination, source)` - *`movzx r32, r16`: the `& 65535` mask of a register's value in one instruction.*
  - Method `._movzx(extension, destination, source)` - *Shared `movzx r32, r/m16|8` register form; REX appears only for r8-r15.*
  - Method `.xor_zero(reg)` - *`xor r32, r32`: the zero idiom, which also clears the upper 32 bits.*
  - Method `.binary(opcode, destination, source)` - *A 64-bit `op r/m64, r64` form: add, sub, and, or, xor and cmp share it.*
  - Method `.cmp_reg_reg(left, right)` - *`cmp r64, r64`: compare `left` against `right` (G-002 named wrapper).*
  - Method `._group1_imm(extension, reg, value)` - *`op r/m64, imm8` (83 /n) or `op r/m64, imm32` (81 /n), sign-extended.*
  - Method `.add_imm(reg, value)` - *`add r64, imm`, used for `x = x + constant` without a register copy.*
  - Method `.sub_imm(reg, value)` - *`sub r64, imm`, used for `x = x - constant` without a register copy.*
  - Method `.adc_imm(reg, value)` - *`adc r64, imm` (group-1 extension 2): adds the immediate plus carry.*
  - Method `.cmov(condition, destination, source)` - *`cmovcc r64, r/m64`: conditional move, selected by the current flags.*
  - Method `.cmp_imm(reg, value)` - *`cmp r64, imm`, so a comparison with a small literal needs no second register.*
  - Method `.arith_imm(extension, reg, value)` - *`op r64, imm` by group-1 extension: add (0), or (1), and (4), sub (5), xor (6).*
  - Method `.imul(destination, source)` - *`imul r64, r/m64`: two-operand signed multiply.*
  - Method `.imul_imm(destination, source, literal)` - *`imul r64, r/m64, imm`: three-operand signed multiply by a constant.*
  - Method `.imul_high(reg)` - *One-operand `imul r64`: the signed 128-bit `rdx:rax = rax * reg`.*
  - Method `.test(reg)` - *`test r64, r64`, setting flags from a value without changing it.*
  - Method `.test_imm(reg, value)` - *`test r/m8, imm8` (F6 /0) for a small positive mask: flags only, value kept.*
  - Method `.setcc(condition)` - *`setcc al` followed by `movzx rax, al`: a comparison result as 0 or 1 in rax.*
  - Method `.cqo()` - *Sign-extend rax into rdx:rax before a signed division.*
  - Method `.divide(reg, signed)` - *`idiv`/`div r64`; the caller sets rdx and rax.*
  - Method `.divide32(reg)` - *`div r/m32`: unsigned 32-bit division of edx:eax, quotient eax, remainder edx.*
  - Method `.unary(extension, reg)` - *`F7 /n` forms: not (2), neg (3).*
  - Method `.shift_cl(extension)` - *`shl` (4), shr (5) or sar (7) of rax by cl.*
  - Method `.shift_imm(extension, reg, count)` - *`shl` (4), `shr` (5) or `sar` (7) of r64 by imm8, so a literal count needs no cl round-trip.*
  - Method `.add_imm8(reg, value)` - *`add r8, imm8`, used to turn a digit into ASCII.*
  - Method `.nop_pad(size)` - *`size` bytes of padding as canonical multi-byte nops.*
  - Method `.store_byte(base, source)` - *`mov [base], r8`.*
  - Method `.store_byte_imm(base, value)` - *`mov byte ptr [base], imm8`.*
  - Method `.store_qword(base, source)` - *`mov [base], r64`.*
  - Method `.step(reg, decrement)` - *`inc` (FF /0) or `dec` (FF /1) of a 64-bit register.*
  - Method `.add_rsp(amount)` - *`add rsp, imm32`.*
  - Method `.sub_rsp(amount)` - *`sub rsp, imm32`.*
  - Method `.align_rsp()` - *`and rsp, -16`, for a helper reached by a jump from an arbitrary stack depth.*
  - Method `.leave_ret()` - *`leave; ret`: restore the caller's frame and return.*
  - Method `.ret()` - *A bare `ret`.*
  - Method `.movq_from_reg(destination, source)` - *`movq xmm, r64` (66 REX.W 0F 6E /r), the GPR-to-XMM form gcc emits.*
  - Method `.punpcklqdq(destination, source)` - *`punpcklqdq xmm, xmm` (66 0F 6C): broadcast the low qword to both lanes.*
  - Method `.paddq_reg(destination, source)` - *`paddq xmm, xmm` (66 0F D4 /r): two wrapping i64 adds in one instruction.*
  - Method `.movdqu_load_indexed(destination, base, index, scale_log2, offset)` - *`movdqu xmm, [base + index*scale + disp32]` (F3 0F 6F /r), unaligned 16B.*
  - Method `.movdqu_store_indexed(base, index, scale_log2, offset, source)` - *`movdqu [base + index*scale + disp32], xmm` (F3 0F 7F /r), unaligned 16B.*
  - Method `.hlt()` - *`hlt`, used only on unreachable paths such as after ExitProcess.*

**Functions:**
- `def _rex(w, r, x, b)` - *The REX prefix byte from the four bits x86-64 adds to a legacy encoding.*
- `def _modrm(mod, reg, rm)` - *ModRM with an optional SIB and displacement handled by the caller.*
- `def _rbp_displacement(reg, offset)` - *`[rbp+disp8]` (mod 01) when it fits, otherwise `[rbp+disp32]` (mod 10).*

---

### [`cairn/optimize.py`](cairn/optimize.py)
> *Optimiser: constant folding and provably safe algebraic simplification.*

**Size**: `1965` lines

**Classes:**
- `class Optimizer` - *One folding pass, rewriting expressions in place and returning the same nodes.*
  - Method `.run(program)` - *Optimise every function body, then convert counter loops and fuse odd pairs.*
  - Method `._block(block)` - *Optimise each statement; splice merged branches and truncate what cannot run.*
  - Method `._statement(statement)` - *Fold one statement, replacing a constant `if` with the branch it always takes.*
  - Method `._choose_branch(statement)` - *Replace an `if` whose condition is a constant with the branch it always takes.*
  - Method `._expression(expression)` - *Fold one expression; returns the replacement node.*
  - Method `._fold_string_builtin(expression)` - *Fold `str_len`/`str_byte` of a literal handle into the specified answer.*
  - Method `._fold_unary(expression)` - *Fold a unary operator applied to a literal.*
  - Method `._fold_binary(expression)` - *Fold two literals, then apply an identity that is safe for every i64.*
  - Method `._identity(expression, left, right)` - *`x + 0`, `x * 1` and friends; a dropped operand must have no effect.*

**Functions:**
- `def wrap(value)` - *Reduce an exact integer to the i64 value the machine would hold.*
- `def truncate_division(left, right)` - *Signed division truncating toward zero, the way `idiv` does.*
- `def fold_constant(op, left, right)` - *The folded value of `left op right`, or None when folding would change behaviour.*
- `def fold_unary(op, value)` - *The folded value of a unary operator, or None when it must stay dynamic.*
- `def has_call(expression)` - *Whether evaluating `expression` can run a call, and therefore an effect.*
- `def _contains_index(expression)` - *Whether an element load sits anywhere under one expression tree.*
- `def _may_trap(expression)` - *Whether evaluating `expression` can trap on `/` or `%`.*
- `def _constant(expression)` - *The literal value of an expression, or None when it is not a constant.*
- `def _peel_parens(expression)`
- `def _mentions_slot(node, slot)` - *Whether a statement or expression reads or writes the variable in `slot`.*
- `def _contains_break_or_continue(node)` - *Whether a statement tree can leave the loop other than by falling out of its test.*
- `def _count_reads(node, slot)` - *How many times a statement tree reads the variable in `slot` (writes are not reads).*
- `def _find_increment(store, slot)` - *Locate the (list, index) of `v = v + 1` inside a statement list, or None.*
- `def _try_countdown_latch(statement, init, count, trailing)` - *Rewrite one `while v < C { ...; v = v + 1; ... }` as `while v > 0`, or refuse.*
- `def _countdown_block(block, trailing, parameters, changed)` - *Walk one block, converting eligible counter loops; `trailing` guards post-loop reads.*
- `def lower_for_loops(program)` - *Lower every `for` into a mutable `let` plus a `while`; runs before any folding.*
- `def _lower_block(block)` - *Lower one block in place; the loop body lowers before the loop itself.*
- `def _for_to_while(statement)` - *The two statements a `for` becomes: the mutable initialiser and the counted `while`.*
- `def _increment(name, slot, span)` - *`name = name + 1` on the loop's own slot.*
- `def _rewrite_continues(block, name, slot)` - *Prefix every `continue` bound to this loop with the increment, in place.*
- `def _read_slots(expression, acc)` - *Add every slot `expression` reads to `acc`.*
- `def _stripped(expression)` - *An expression without its wrapping parentheses.*
- `def _counter_step(statement, slot)` - *The function mapping a counter value to the next when `statement` is its step.*
- `def _counter_bound(condition)` - *`(limit, inclusive)` for `counter < bound` / `counter <= bound`, else None.*
- `def _writes_slot(statements, slot)` - *Whether a statement list writes `slot` anywhere below its own level.*
- `def _calls_named(expression, name)` - *Whether an expression tree calls `name` anywhere.*
- `def _calls_self(statements, name)` - *Whether a statement list calls the enclosing function `name` anywhere below it.*
- `def _jumps(statements)` - *Whether a break or a continue appears under a statement list.*
- `def _clone_with_literal(expression, slot, value)` - *A deep copy of `expression` with every read of `slot` replaced by `value`.*
- `def _clone_statement(statement, slot, value)` - *A deep copy of one statement with every read of `slot` replaced by `value`.*
- `def _clone_block(block, slot, value)` - *A deep copy of a block with every read of `slot` replaced by `value`.*
- `def _unrolled_loop(statement, loop, function_name)` - *The straight-line replacement for a literal-init statement and its loop.*
- `def apply_constant_loop_unrolling(program)` - *Unroll a short constant-trip while into clones with literal counter values.*
- `def _unroll_block(block, function_name)` - *Unroll the candidates in one block and in every block below it.*
- `def apply_copy_propagation(program)` - *Replace `x = t` with t's definition when the copy is t's only read.*
- `def _propagate_once(root, block)` - *Apply at most one copy propagation under `block`; returns whether one applied.*
- `def apply_countdown_latches(program)` - *Convert `while v < C` loops with a dead counter into countdown latches.*
- `def _is_plus_one(statement)` - *Whether the statement is `s = s + 1` (either operand order).*
- `def _scaled_odd_term(value, slot)` - *The odd multiplier of `slot * m` (either order), or None.*
- `def _odd_step_value(value, slot)` - *The `(m, c)` of `k = k*m + c` with both odd, or None.*
- `def _halving_value(value, slot)` - *Whether `value` is exactly `k / 2` for the slot.*
- `def slot_of_name(expression)` - *The slot of a (parenthesised) name, or None.*
- `def _fuse_odd_pair(statement)` - *Rewrite one collatz-shaped loop body so the odd step rides with the halving after it.*
- `def apply_odd_pair_fusion(program)` - *Fuse collatz-shaped odd steps with the halving that follows them, loop-wide.*
- `def _flatten(statements)` - *Splice a branch selected by folding into its parent, since Cairn has one flat scope.*
- `def _reachable(statements)` - *Drop statements after one that cannot fall through: `return`, `break`, `continue`.*
- `def optimize(program)` - *Run one folding pass over a checked program, then convert counter loops.*
- `def _linear_product(expression, slot)` - *The multiplier when expression is multiplier * slot, in either order.*
- `def _linear_in_index(expression, slot)` - *(multiplier, offset) when expression is multiplier * slot + offset.*
- `def _rewrite_linear(expression, slot, replacements)` - *Replace every matched linear expression with its induction local.*
- `def _collect_linear(expression, slot, found)`
- `def _statement_expression(statement)`
- `def _strength_reduction_plan(statement)` - *(index slot, linear terms) for a strict counted loop with no control flow.*
- `def apply_induction_strength_reduction(program)` - *Keep linear functions of a counted index in one incremented local each.*
- `def _node_count(expression)` - *How many expression nodes one tree carries.*
- `def _clone_expression(expression)` - *A deep copy of one expression node tree.*
- `def _single_return_bodies(program)` - *Callees whose body is exactly one `return <expr>;`, within the inline budget.*
- `def apply_pure_inlining(program)` - *Inline every call to a single-`return` function, substituting its arguments.*
- `def _inline_program(program, bodies)` - *One inlining round over every statement; answers how many statements changed.*
- `def _inline_statements(function, block, bodies, active)` - *Rewrite calls inside one block, descending into every statement kind.*
- `def _inline_attribute(function, statement, attribute, bodies, active)` - *Inline calls inside one statement's expression; answers whether it changed.*
- `def _inline_expression(function, expression, bodies, active)` - *Replace inlineable calls throughout one expression; None when unchanged.*
- `def _inlined_body(call, bodies, active)` - *The substituted body of one inlineable call, or None when a guard refuses.*
- `def _substitute_parameters(expression, substitutions)` - *Replace every read of a substituted parameter with a fresh argument clone.*
- `def _calls_in(expression)` - *Every Call node under one expression tree.*
- `def apply_algebraic_simplification(program)` - *Expand constant coefficients over add-chains and collect like terms.*
- `def _algebraic_statements(block)` - *Collect like terms in every statement of one block, recursing through shapes.*
- `def _algebraic_attribute(statement, attribute)` - *Collect one statement root; answers whether the tree changed.*
- `def _algebraic_expression(expression)` - *Collect beneath non-chain operators; a `+`/`-` chain collects as a whole.*
- `def _collect_chain(expression)` - *The collected `+`/`-` chain rooted at `expression`, or None when nothing improved.*
- `def _signature(expression)` - *A structural key for one expression subtree; sames group together.*
- `def _chain_terms(expression, sign)` - *Flatten a `+`/`-` chain into signed terms.*
- `def _product_term(expression, sign)` - *One non-chain term as a one-element list: literal-coefficient products distribute.*

---

### [`cairn/parser.py`](cairn/parser.py)
> *Parser: recursive descent over the token list.*

**Size**: `451` lines

**Classes:**
- `class Parser` - *A cursor over one token list; every parse method consumes what it matches.*
  - Method `.parse_program()` - *Parse top-level functions until end of file.*
  - Method `._parse_function()` - *`fn name(a: i64, ...) -> i64 { ... }`.*
  - Method `._parse_parameter()` - *One `name: i64` parameter.*
  - Method `._parse_type(allow_array)` - *One type: `i64`, or `[N]i64` where arrays are allowed; answers (size, name).*
  - Method `._parse_array_literal()` - *A `[` elements `]` literal; the parser sees one element list.*
  - Method `._clone(expression, depth)` - *A deep copy of one expression; fill expansion needs one node per element.*
  - Method `._parse_block()` - *`{ statement* }`.*
  - Method `._parse_statement()` - *Dispatch on the first token of one statement.*
  - Method `._parse_binding()` - *`let|var name: i64 = expr;` or `let|var name: [N]i64 = literal;`.*
  - Method `._parse_if()` - *`if expr block [else (if | block)]`.*
  - Method `._parse_while()` - *`while expr block`.*
  - Method `._parse_for()` - *`for name in start..end block`, with `..=` for the inclusive upper bound.*
  - Method `._parse_return()` - *`return expr;`.*
  - Method `._parse_expression_statement()` - *An assignment to a name, or a call whose value is discarded.*
  - Method `._parse_expression(minimum)` - *Precedence climbing: parse operators at least as tight as `minimum`.*
  - Method `._parse_unary()` - *`-x`, `!x`, `~x`, otherwise a primary expression.*
  - Method `._parse_primary()` - *An integer, a string, a name, a call, a parenthesised expression, or `true`/`false`.*
  - Method `._parse_index(operand)` - *An `operand[index]` load; the opening bracket is the current token.*
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

**Size**: `199` lines

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

**Size**: `724` lines

**Functions:**
- `def emit_arg_fill(assembler)` - *Emit `arg_fill`: tokenize the ANSI command line into the static table once.*
- `def emit_argc(assembler)` - *Emit `argc()`: the token count, filling and caching the table on the first call.*
- `def emit_arg_byte(assembler)` - *Emit `arg_byte(i, j)`: byte j (zero-indexed) of argument i's content, or -1.*
- `def emit_read_char(assembler)` - *Emit `read_char()`: the next stdin byte, or -1 at end of stream.*
- `def emit_out_flush(assembler)` - *Emit `flush()`: drain the shared stdout buffer, answering the drained count.*
- `def emit_out_append(assembler)` - *Emit `out_append`: rax carries the source bytes, rdx their count.*
- `def emit_out_flush_work(assembler)` - *Emit `out_flush.work`: one WriteFile for everything the cursor holds.*
- `def emit_print_i64(assembler)` - *Emit `print_i64`: write rax in decimal plus a newline.*
- `def emit_print_char(assembler)` - *Emit `print_char`: write the low byte of rax to stdout, no newline.*
- `def emit_print_hex(assembler, label, with_newline)` - *Emit a lowercase-hex printer, with the newline riding in the same buffer on request.*
- `def emit_print_str(assembler)` - *Emit `print_str`: write the NUL-terminated text at rax to stdout, no newline.*
- `def emit_str_len(assembler)` - *Emit `str_len`: the number of bytes in the NUL-terminated text at rax.*
- `def emit_str_byte(assembler)` - *Emit `str_byte(s, j)`: byte j of the text at rcx, or -1.*
- `def emit_div_zero(assembler, rodata)` - *Emit the trap taken when a divisor is zero: message on stderr, exit code 3.*
- `def emit_runtime(assembler, rodata, writable)` - *Emit every helper routine and the data they reference, in a stable order.*

---

### [`cairn/sema.py`](cairn/sema.py)
> *Semantic analysis: names, arity, mutability and return-path checks.*

**Size**: `462` lines

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
  - Method `._check_for(statement)` - *A `for` declares one scoped, mutable loop variable for its body, then restores.*
  - Method `._check_binding(statement)` - *A `let`/`var` declares one new name: a scalar initialises from one i64*
  - Method `._check_binding_initializer(statement, size)` - *Check one binding's initializer against the declared shape.*
  - Method `._new_array_slots(count)` - *Allocate `count` consecutive 8-byte frame slots; the first is the base.*
  - Method `._check_assign(statement)` - *Assignment requires an existing `var` binding; `a[i] = e;` a `var` array.*
  - Method `._check_element_store(statement, binding)` - *`a[index] = value;` needs a declared `var` array; scalars refuse indexing.*
  - Method `._check_element_index(index, size)` - *Check one index expression; a literal index must land inside the array.*
  - Method `._check_expression(expression)` - *Dispatch one expression to its checker.*
  - Method `._check_index_load(expression)` - *`a[i]` loads one element; it needs a declared array, never a scalar.*
  - Method `._check_name(expression)` - *A name must be a declared local; `print` is callable but not a value.*
  - Method `._check_call(expression, allow_stop)` - *A call names a function (or the builtin) and passes exactly its arity of arguments.*

**Functions:**
- `def _literal_index(expression)` - *A statically known index, or None for a dynamic one; parens are unwrapped.*
- `def emitted_return(block)` - *Whether every path through `block` ends in a `return` or an `exit` statement.*
- `def analyze(program, path)` - *Run semantic analysis over one parsed program.*

---

### [`cairn/syntax.py`](cairn/syntax.py)
> *Syntax tree: one dataclass per construct, each carrying its source span.*

**Size**: `238` lines

**Classes:**
- `class Node` - *Every node carries the span it was parsed from.*
- `class Expr` - *Base class for value-producing nodes.*
- `class IntLiteral` - *A decimal integer literal, already range-checked by the lexer.*
- `class StrLiteral` - *A quoted string literal: read-only bytes in .rodata, NUL-terminated.*
- `class Name` - *A reference to a parameter or a local; `slot` is filled in by semantic analysis.*
- `class Unary` - *`-x`, `!x` or `~x`.*
- `class Binary` - *A binary operator; `op` is the source spelling.*
- `class Parens` - *A parenthesised expression; kept so spans and grouping survive into code generation.*
- `class Call` - *`name(arg, ...)`; the callee is a function name or the builtin `print`.*
- `class ArrayLiteral` - *An array literal `[e0, e1, ...]`; a fill `[v; N]` is expanded to N copies of `v`.*
- `class Index` - *`a[i]`; `a` must name an array binding. `slot` is the array's base slot and*
- `class Stmt` - *Base class for statements.*
- `class Block` - *A brace-delimited statement sequence.*
- `class Let` - *`let x: i64 = e;` (immutable) or `var x: i64 = e;` (mutable).*
- `class Assign` - *`x = e;`, valid only for a `var` binding; `a[i] = e;` stores into one element.*
- `class If` - *`if cond { ... } else { ... }`, where the else block is optional.*
- `class While` - *`while cond { ... }`; `countdown` marks a counter loop the optimiser converted.*
- `class For` - *`for name in start..end { ... }`, or `start..=end` for the inclusive bound.*
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

**Size**: `90` lines

**Classes:**
- `class Value` - *One core value: the claim, the command that shows it, and the test that defends it.*

**Functions:**
- `def values_manifest()` - *The values as JSON-ready records, in their frozen order.*

---

### [`cairn/verify.py`](cairn/verify.py)
> *Verification of a built artifact: structural checks that need no execution.*

**Size**: `255` lines

**Classes:**
- `class Problem` - *One failed invariant, addressed by its code.*
  - Method `.render()` - *The one-line report form.*

**Functions:**
- `def _u16(image, offset)`
- `def _u32(image, offset)`
- `def _sections(image)` - *`(name, header offset)` for every section table entry that fits in the file.*
- `def _rva_to_offset(image, rva)` - *Translate an RVA through the section table; -1 when it lands in no section.*
- `def _section_of(image, rva)` - *The section name an RVA lands in; the empty string when it lands in none.*
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

**Size**: `5` lines

---

### [`tests/__init__.py`](tests/__init__.py)
> *Test package for the Cairn bootstrap compiler.*

**Size**: `1` lines

---

### [`tests/test_benchmarks.py`](tests/test_benchmarks.py)
> *Benchmark harness tests: summarising and rendering, without running a benchmark.*

**Size**: `600` lines

**Classes:**
- `class PairedVerdictTest` - *A win is a gap outside the identical-binary band, not a raw best time.*
  - Method `.test_an_identical_binary_is_a_tie()`
  - Method `.test_a_gap_inside_the_band_is_a_tie()`
  - Method `.test_a_clear_gap_below_the_band_is_a_win()`
  - Method `.test_a_clear_gap_above_the_band_is_a_loss()`
  - Method `.test_the_control_band_trims_extremes()`
- `class SummaryTest` - *The arithmetic an agent reads from a report must be reproducible by hand.*
  - Method `.test_summary_reports_best_median_worst_and_per_unit_cost()`
  - Method `.test_zero_iterations_does_not_divide()`
  - Method `.test_summarise_rejects_empty_samples()`
  - Method `.test_every_case_declares_an_existing_cairn_and_c_source()`
  - Method `.test_every_case_declares_an_existing_rust_twin()`
  - Method `.test_every_case_declares_an_existing_cpp_twin()`
  - Method `.test_cpp_report_keys_are_present_on_the_error_path()`
  - Method `.test_a_measured_cpp_baseline_records_verdict_and_ratio()`
  - Method `.test_render_says_unavailable_when_there_is_no_cpp_compiler()`
  - Method `.test_render_states_the_cpp_goal_for_each_outcome()`
  - Method `.test_rust_report_keys_are_present_on_the_error_path()`
  - Method `.test_a_measured_rust_baseline_records_verdict_and_ratio()`
  - Method `.test_render_says_unavailable_when_there_is_no_rust_compiler()`
  - Method `.test_render_states_the_rust_goal_for_each_outcome()`
  - Method `.test_strict_exits_2_when_no_baseline_can_be_measured()`
  - Method `.test_strict_passes_when_the_measured_baseline_is_won()`
  - Method `.test_strict_fails_when_rust_wins_a_case()`
  - Method `.test_strict_fails_when_cpp_wins_a_case()`
  - Method `.test_json_goal_carries_both_baselines()`
  - Method `.test_json_goal_reports_the_cpp_baseline_when_measured()`
  - Method `.test_render_says_unavailable_when_there_is_no_c_compiler()`
  - Method `.test_the_loop_case_is_a_serial_xorshift_workload()` - *The integer-loop case must be optimizer-resistant, so neither side may fold it.*
  - Method `.test_a_errored_case_names_the_case_dynamically()`
  - Method `.test_build_command_skips_folding_only_when_asked()`
  - Method `.test_render_names_an_unoptimised_build()`
- `class VerdictTest` - *The `faster than C` goal is decided by measurements, never assumed.*
  - Method `.test_lower_best_time_wins()`
  - Method `.test_missing_baseline_is_unavailable_not_a_win()`
  - Method `.test_render_states_the_goal_for_each_outcome()`
  - Method `.test_a_report_without_a_verdict_is_treated_as_unmeasured()`
  - Method `.test_render_shows_c_image_size_when_present()`
  - Method `.test_errored_case_is_reported_not_raised()`
- `class CompilerProbeTest` - *Compiler detection must return a name the harness knows, or nothing at all.*
  - Method `.test_detected_compiler_is_one_of_the_known_families()`
  - Method `.test_detected_cpp_compiler_is_one_of_the_known_families()`
- `class RegistryShapeTest` - *The measured set: which cases the scoreboard commits to, by name and unit.*
  - Method `.test_the_registry_names_every_commitment()`
  - Method `.test_each_case_declares_a_unit_and_a_positive_iteration_count()`
- `class ManyParamsContractTest` - *ADR-071: no twin may let a compiler fold the whole program to one print.*
  - Method `.sources()`
  - Method `.compact(source)` - *One twin's text with the spellings that differ per language removed.*
  - Method `.test_every_twin_derives_its_bound_from_its_argument_count()`
  - Method `.test_the_bound_is_the_registry_iteration_count_plus_the_argument_count()`
  - Method `.test_the_mix_body_is_not_a_closed_form_recurrence()`
- `class QuietWindowProvenanceTest` - *A saved record carries the ADR-069 probe, so a loaded baseline stays auditable.*
  - Method `.record(probe)`
  - Method `.test_a_probe_under_the_criterion_marks_the_run_quiet()`
  - Method `.test_a_probe_over_the_criterion_marks_the_run_loaded()`
  - Method `.test_a_run_without_the_probe_answers_unknown()`
  - Method `.test_the_window_note_warns_only_for_a_loaded_baseline()`
- `class ComputeAdjustedTest` - *The floors view subtracts each language's own startup, and only that.*
  - Method `.test_each_language_subtracts_its_own_floor()`
  - Method `.test_a_missing_floor_omits_the_language_instead_of_guessing()`
  - Method `.test_a_best_below_the_floor_clamps_at_zero()`
  - Method `.test_the_floor_sources_are_the_four_empty_programs()`
- `class CompareRunsTest` - *The regression watch: a saved run's numbers diffed against the new run.*
  - Method `.test_a_cairn_regression_beyond_the_noise_band_is_flagged()`
  - Method `.test_drift_inside_the_noise_band_is_reported_but_not_flagged()`
  - Method `.test_an_improvement_is_named_and_never_flags()`
  - Method `.test_a_case_absent_from_the_previous_run_is_reported_as_new()`
  - Method `.test_a_case_missing_from_the_current_run_is_not_imagined()`
  - Method `.test_compare_is_symmetric_about_the_noise_band_edges()`
  - Method `.test_the_newest_valid_history_record_wins_over_corrupt_lines()`
  - Method `.test_missing_history_reads_as_no_record()`

---

### [`tests/test_capabilities.py`](tests/test_capabilities.py)
> *The machine-checkable core: registry, manifest, listing and the stdlib-only proof.*

**Size**: `115` lines

**Classes:**
- `class RegistryTest` - *ADR-003 makes codes a contract, so all three directions are asserted.*
  - Method `.test_every_code_the_source_emits_is_registered()`
  - Method `.test_every_registered_code_is_documented_in_the_readme()`
  - Method `.test_every_registered_code_is_in_the_capabilities_manifest()`
  - Method `.test_the_registry_has_no_duplicate_codes()`
  - Method `.test_codes_group_by_stage_in_registry_order()`
  - Method `.test_codes_found_regardless_of_quote_style()`
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

**Size**: `302` lines

**Classes:**
- `class CliTest` - *An agent drives the compiler through this surface, so it is tested as a contract.*
  - Method `.setUp()`
  - Method `.test_check_json_reports_success()`
  - Method `.test_check_json_lists_every_diagnostic()`
  - Method `.test_check_human_form_states_the_fix()`
  - Method `.test_build_writes_the_requested_path()`
  - Method `.test_build_twice_produces_identical_bytes()`
  - Method `.test_missing_input_is_a_usage_error()`
  - Method `.test_undecodable_input_is_a_usage_error_not_a_traceback()`
  - Method `.test_version_is_printed()`
  - Method `.test_capabilities_json_publishes_codes_and_commands()`
  - Method `.test_capabilities_text_names_the_surface()`
  - Method `.test_listing_json_carries_the_text_by_label()`
  - Method `.test_verify_accepts_a_source_and_a_built_executable()`
  - Method `.test_verify_reports_a_broken_image_with_its_code()`
  - Method `.test_verify_forwards_a_compile_failure()`
  - Method `.test_verify_missing_target_is_a_usage_error()`
  - Method `.test_concurrent_writes_use_distinct_temporaries()`
  - Method `.test_concurrent_writes_leave_no_temporaries_behind()`
  - Method `.test_run_rejects_a_non_positive_timeout()`
  - Method `.test_run_json_reports_success_with_captured_output()`
  - Method `.test_run_json_reports_a_trap_with_its_stderr()`
  - Method `.test_run_json_envelope_for_unreadable_input()`
  - Method `.test_run_json_envelope_for_a_bad_timeout()`
  - Method `.test_run_json_reports_a_timeout()`
  - Method `.test_json_envelope_for_unreadable_build_input()`
  - Method `.test_json_envelope_for_unreadable_check_input()`
  - Method `.test_json_envelope_for_unreadable_verify_target()`
  - Method `.test_json_envelope_for_unreadable_listing_input()`
  - Method `.test_check_json_failure_carries_path_and_error()`
  - Method `.test_run_json_compile_failure_keeps_the_run_envelope()`
  - Method `.test_verify_json_compile_failure_keeps_kind_and_empty_image()`
  - Method `.test_listing_json_compile_failure_keeps_empty_listing()`

**Functions:**
- `def _capture(call)` - *Run a CLI call with both streams redirected; returns their text and the exit code.*
- `def _remove_tree(folder)` - *Delete the test's temporary folder, ignoring files Windows may still hold.*

---

### [`tests/test_codegen.py`](tests/test_codegen.py)
> *Code-generation tests: where a value lives, and what the frame must carry for it.*

**Size**: `2807` lines

**Classes:**
- `class RecordingAssembler` - *An assembler that remembers every frame slot it reads or writes per function.*
  - Method `.__init__()`
  - Method `.label(name)` - *Track which function the next frame access belongs to.*
  - Method `.mov_load_rbp(destination, offset)` - *Record a frame load, then emit it.*
  - Method `.mov_store_rbp(offset, source)` - *Record a frame store, then emit it.*
  - Method `.push(reg)` - *Record a push, then emit it.*
  - Method `.pop(reg)` - *Record a pop, then emit it.*
  - Method `.mov_store_rsp(source)` - *Record a pad-slot store, then emit it.*
  - Method `.mov_load_rsp(destination)` - *Record a pad-slot load, then emit it.*
  - Method `.mov_reg_reg(destination, source)` - *Record hold-register writes, then emit them.*
  - Method `.pops_of(function)` - *Registers popped inside one function.*
  - Method `.pushes_of(function)` - *Registers pushed inside one function.*
  - Method `.loads_of(function)` - *Offsets of frame loads inside one function.*
  - Method `.stores_of(function)` - *Offsets of frame stores inside one function.*
- `class SlotHomeTest` - *A slot with a home register must not be read from or written to the stack.*
  - Method `.test_two_hot_locals_never_touch_their_frame_slots()`
  - Method `.test_used_homes_are_saved_and_restored()`
  - Method `.test_the_displaced_slot_keeps_the_stack_fallback()`
- `class ArgumentPathTest` - *Arguments and shifts by literals must not pay for registers they do not need.*
  - Method `.test_a_literal_subtrahend_uses_the_immediate_form()`
  - Method `.test_a_single_argument_moves_into_rcx_without_the_stack()`
  - Method `.test_a_shift_by_literal_shifts_in_place()`
- `class PowerOfTwoDivisionTest` - *`x / 2**b` and `x % 2**b` lower as a sar/shr fixup chain, never as `idiv`.*
  - Method `.test_division_by_a_power_of_two_literal_never_divides()`
  - Method `.test_a_proven_nonnegative_dividend_divides_as_one_shift()`
  - Method `.test_division_by_two_emits_the_shift_fixup_chain()`
  - Method `.test_a_remainder_by_two_masks_instead_of_dividing()`
  - Method `.test_a_non_power_of_two_literal_still_divides()`
  - Method `.test_a_negative_power_of_two_still_divides()`
- `class WideDivisionGuardTest` - *A runtime division takes 32-bit `div` behind a two-compare guard (ADR-085).*
  - Method `.test_a_runtime_divisor_takes_the_guarded_narrow_form()`
  - Method `.test_the_guard_uses_unsigned_compares_so_negatives_go_wide()`
  - Method `.test_a_literal_divisor_keeps_its_magic_or_shift_form()`
- `class HalvingPairLoopTest` - *The fused odd-pair collatz body lowers to one branchless halving pass.*
  - Method `.test_a_fused_pair_lowers_branchless()`
  - Method `.test_the_steps_counter_pays_both_steps_with_one_adc()`
  - Method `.test_a_negative_addend_stays_on_the_generic_path()`
  - Method `.test_a_body_with_break_stays_on_the_generic_path()`
  - Method `.test_the_swapped_parity_test_takes_the_same_form()`
  - Method `.test_a_non_zero_parity_target_stays_on_the_generic_path()`
- `class ScaledSumTest` - *`c1 + k*c2` lowers as one scaled `lea`; the multiply path stays for other factors.*
  - Method `.test_three_k_plus_one_is_one_lea()`
  - Method `.test_the_mirrored_operand_order_is_one_lea_too()`
  - Method `.test_two_k_plus_five_uses_a_scale_of_one()`
  - Method `.test_four_k_nine_takes_the_index_only_form()`
  - Method `.test_an_unsupported_factor_still_multiplies()`
  - Method `.test_an_unscaled_k_is_left_alone()`
- `class ParityCompareTest` - *`x % 2**b == 0` is `(x & (2**b-1)) == 0` for every sign: one `and`, one branch.*
  - Method `.test_an_even_test_is_a_test_mask_and_a_branch()`
  - Method `.test_an_uneven_test_branches_on_the_same_and()`
  - Method `.test_a_wider_mask_counts_to_eight()`
  - Method `.test_a_mask_beyond_imm32_lands_in_a_scratch_register()`
  - Method `.test_an_unequal_compare_against_a_nonzero_literal_still_divides()`
- `class ComparisonBranchHoldTest` - *A two-sided comparison branches on the left value where the lowering left it.*
  - Method `.test_a_non_leaf_right_operand_never_compares_a_stale_rcx()`
  - Method `.test_a_loop_condition_never_compares_a_stale_rcx()`
- `class FlagFusedConditionTest` - *Bit-test and short-circuit conditions branch from their flags, never setcc.*
  - Method `.test_a_bit_test_condition_branches_from_the_flags()`
  - Method `.test_conjoined_bit_tests_branch_from_two_flag_pairs()`
  - Method `.test_a_disjoined_condition_short_circuits_its_right_side()`
  - Method `.test_a_comparison_assigned_as_a_value_still_materialises_setcc()`
  - Method `.test_masks_above_the_byte_form_still_decide_the_branch()`
- `class LogicalShiftPeepholeTest` - *`(a >> k) & (2**(64-k) - 1)` needs no arithmetic-shift fixup or mask load.*
  - Method `.test_a_covering_mask_lowers_as_one_logical_shift()`
  - Method `.test_a_partial_mask_keeps_its_and()`
- `class LoopConstantHoistTest` - *Countdown loops read their big literals from pre-loaded spare homes.*
  - Method `.test_the_loop_never_rematerialises_its_big_constants()`
  - Method `.test_the_constants_load_into_spare_homes_before_the_loop()`
- `class SwapSelectTest` - *The gcd swap shape `if a > b { var t = a; a = b; b = t; }` lowers branchless.*
  - Method `.code(source)` - *Main's own bytes only: label to label, so runtime helpers never match.*
  - Method `.test_the_swap_shape_fuses_into_two_cmovs_and_no_branch()`
  - Method `.test_the_temp_being_read_after_the_if_refuses_the_fusion()`
  - Method `.test_a_two_statement_body_refuses_the_fusion()`
  - Method `.test_a_condition_on_other_slots_refuses_the_fusion()`
- `class ReadFuseTest` - *`read_char()` fuses its buffered fast path into the call site, drain stays in the helper.*
  - Method `._generate(source)`
  - Method `._calls_to(start, end, assembler, label)` - *Count `call rel32` fixups inside [start, end) that land on `label`.*
  - Method `.test_the_read_loop_promotes_base_index_and_count()`
  - Method `.test_two_spares_promote_the_pair_and_compare_against_the_word()`
  - Method `.test_no_spare_keeps_the_memory_fusion()`
  - Method `.test_a_read_char_inside_another_call_fuses_too()`
  - Method `.test_r10_borrowed_as_an_extra_home_refuses_the_fusion()`
  - Method `.test_a_user_call_in_the_body_keeps_the_memory_fusion()`
  - Method `.test_the_read_loop_never_bumps_the_cursor_with_a_memory_increment()`
  - Method `.test_a_break_only_if_jumps_straight_to_the_break_target()`
- `class MergedMaskMixTest` - *A masked xor merged into its home register lowers park-free, in place.*
  - Method `.test_the_mixer_never_materialises_or_spills_its_mask()`
  - Method `.test_the_xor_combines_in_place_right_after_the_shift()`
  - Method `.test_the_merged_mul_stays_in_its_home_register()`
- `class ArrayInPlaceUpdateTest` - *`a[k] = a[k] op e` is one memory-operand ALU instruction, not load-op-store.*
  - Method `.program_and_code(source)` - *Main's own code bytes, so runtime helpers never satisfy the asserts.*
  - Method `.test_a_static_index_update_is_one_memory_operand_alu()`
  - Method `.test_a_static_index_update_folds_a_literal_into_the_instruction()`
  - Method `.test_a_dynamic_index_update_computes_the_address_once()`
  - Method `.test_a_dynamic_index_update_folds_a_literal_into_the_instruction()`
  - Method `.test_a_reversed_commutative_update_fuses_with_the_other_side_only()`
  - Method `.test_a_subtraction_from_the_element_fuses_only_left_to_right()`
  - Method `.test_an_index_calling_update_never_fuses()`
- `class ArrayElementAddressTest` - *Element `i` sits at the span's lowest displacement plus `8i`, and rises with `i`.*
  - Method `.main_code(source)` - *Main's own code bytes, so runtime helpers never satisfy the asserts.*
  - Method `.test_a_dynamic_index_load_is_one_sib_load()`
  - Method `.test_a_dynamic_element_update_is_one_sib_alu()`
  - Method `.test_a_literal_store_to_a_dynamic_index_is_one_memory_write()`
  - Method `.test_a_homed_value_store_to_a_dynamic_index_is_one_memory_write()`
  - Method `.test_a_literal_index_uses_the_ascending_displacement()`
  - Method `.test_an_aggregate_literal_lands_ascending()`
- `class SelfRecursionInliningTest` - *The left operand of a merge may be a self-call lowered in place, not a real call.*
  - Method `.test_a_merge_left_self_call_is_inlined()`
  - Method `.test_a_comparison_with_a_small_literal_skips_the_rax_load()`
  - Method `.test_a_merge_over_calls_never_takes_the_stack_form()`
  - Method `.test_a_pad_slot_holds_a_merge_value_in_memory()`
  - Method `.test_only_the_real_merge_keeps_its_pad_slot()`
- `class DeltaInliningTest` - *A one-slot recursive function lowers its self-calls as pure text copies.*
  - Method `.test_delta_copies_never_touch_the_stack()`
  - Method `.test_a_delta_function_skips_the_shadow_copies()`
  - Method `.test_delta_labels_bloom_pure_text_copies()`
  - Method `.test_merges_hold_values_in_level_indexed_frame_slots()`
- `class TailReturnElisionTest` - *A `return` in tail position falls through into the epilogue label instead of jumping.*
  - Method `.test_a_tail_return_emits_no_jmp()`
  - Method `.test_a_non_tail_return_still_jumps_to_the_epilogue()`
- `class MultiArgumentCallTest` - *Pure arguments bind straight into their ABI registers, without the stack round-trip.*
  - Method `.test_both_pure_arguments_bind_without_the_stack()`
  - Method `.test_mixed_call_free_arguments_bind_reversed_without_the_stack()`
  - Method `.test_a_call_inside_an_argument_keeps_the_stack_fallback()`
  - Method `.test_a_division_inside_an_argument_keeps_the_stack_fallback()`
- `class StackParameterTest` - *Parameters five to eight travel through the caller's outgoing area.*
  - Method `.test_callee_pulls_stack_parameters_from_above_the_return_address()`
  - Method `.test_caller_stores_stack_arguments_into_the_outgoing_area()`
  - Method `.test_the_outgoing_area_is_one_reservation_with_call_alignment()`
- `class BuiltinPrintHelpersTest` - *print_char and print_hex dispatch to their own runtime helpers.*
  - Method `.test_the_helpers_are_emitted_into_every_executable()`
  - Method `.test_print_char_calls_its_helper_with_the_stack_round_trip()`
  - Method `.test_print_hex_calls_its_helper()`
- `class PrintHexNewlineFusionTest` - *A hex value immediately followed by a newline uses one fused helper.*
  - Method `.call_targets(source)`
  - Method `.test_hex_then_newline_uses_the_fused_helper()`
  - Method `.test_a_non_newline_character_does_not_fuse()`
  - Method `.test_a_used_hex_result_does_not_fuse()`
- `class ArrayVectorTest` - *A counted array read-modify-write loop uses two SSE2 lanes.*
  - Method `.test_an_even_trip_count_uses_paddq()`
  - Method `.test_an_odd_trip_count_is_refused()`
  - Method `.test_a_non_invariant_addend_is_refused()`
- `class StdinScanRotationTest` - *A read_char scan rotates so the EOF check is the loop's back-edge.*
  - Method `.test_the_scan_uses_a_rotated_back_edge()`
  - Method `.test_a_non_scan_loop_is_not_rotated()`
- `class StringInBoundsIndexTest` - *A str_len-guarded index needs no NUL test on each in-bounds byte.*
  - Method `.test_a_length_guarded_scan_has_no_nul_test()`
  - Method `.test_an_unguarded_read_keeps_its_nul_test()`
  - Method `.test_an_inclusive_guard_keeps_its_nul_test()`
- `class PairDeltaInliningTest` - *A two-parameter all-return-tree recursion inlines as pure text copies.*
  - Method `._pair_labels(source, name)`
  - Method `.test_pair_copies_carry_no_stack_state()`
  - Method `.test_the_n_chain_lowers_as_an_iterated_loop()`
  - Method `.test_the_value_register_rebinds_for_literal_copies()`
  - Method `.test_a_shifted_m_compare_reads_the_delta_form()`
  - Method `.test_a_parameter_rebind_stays_on_the_real_call_path()`
  - Method `.test_a_loop_in_the_body_stays_on_the_real_call_path()`
  - Method `.test_an_other_shaped_second_argument_stays_on_the_real_call_path()`
- `class ForeignInlineTest` - *A call to another small, call-free function lowers as one copy of its body.*
  - Method `.test_a_small_foreign_function_is_copied_into_its_caller()`
  - Method `.test_a_copy_labels_the_caller_namespace()`
  - Method `.test_free_homes_absorb_a_two_slot_copy_without_any_push()`
  - Method `.test_parameters_ride_their_argument_registers_through_a_copy()`
  - Method `.test_a_parameter_rebind_falls_back_to_the_bind_once_copy()`
  - Method `.test_a_looping_callee_without_breaks_copies()`
  - Method `.test_a_breaking_looping_callee_copies_too()`
  - Method `.test_a_calling_callee_stays_a_real_call()`
  - Method `.test_a_dividing_callee_stays_a_real_call()`
  - Method `.test_a_printing_callee_stays_a_real_call()`
  - Method `.test_a_frame_full_callee_stays_a_real_call()`
  - Method `.test_a_one_argument_callee_copies_with_a_direct_bind()`
- `class InsideACopyTest` - *A copy runs once per loop iteration, so prologue-once costs must not repeat in it.*
  - Method `.test_a_tail_returning_copy_skips_the_implicit_return_zero()`
  - Method `.test_a_copy_whose_last_statement_is_not_a_return_keeps_the_implicit_zero()`
  - Method `.test_two_homed_names_compare_in_place()`
  - Method `.test_a_folded_update_reads_the_other_home_directly()`
  - Method `.test_two_homed_names_compare_never_routes_through_rax()`
- `class ZeroExtendMaskTest` - *A mask of 255 or 65535 over a name is a zero extend, not an and-and-move.*
  - Method `._any_shape(source, builder)` - *Whether any dest/src register pair of the builder's shape lands in main.*
  - Method `.test_low_byte_mask_loads_the_home_without_mov_or_and()`
  - Method `.test_low_word_mask_loads_the_home_without_mov_or_and()`
  - Method `.test_a_masked_argument_binds_as_one_movzx()`
  - Method `.test_a_frame_slot_name_keeps_the_generic_mask()`
- `class PureArgumentSubstitutionTest` - *A bare-name argument aliases into the copy instead of binding to a register.*
  - Method `.test_the_call_chain_copy_binds_only_what_computes()`
  - Method `.test_the_substituted_factor_reads_the_caller_home_directly()`
  - Method `.test_a_twice_used_argument_copies_instead_of_calling()`
  - Method `.test_a_copy_whose_guest_borrows_caller_homes_still_copies()`
- `class CommutativeHomePeepholeTest` - *A commutative binary whose left operand already lives in a register skips its copy.*
  - Method `.test_an_and_of_a_homed_left_operand_skips_the_rcx_copy()`
  - Method `.test_an_add_of_a_homed_left_operand_skips_the_rcx_copy()`
  - Method `.test_a_zero_branch_after_an_and_skips_the_redundant_compare()`
- `class SameNameBinaryTest` - *`x * x` (and any same-name `+ & | ^`) loads its operand from a frame slot once.*
  - Method `.test_a_same_name_product_of_a_frame_slot_loads_once()`
  - Method `.test_a_same_name_product_of_a_homed_slot_loads_once()`
- `class MultiplyHomeFastPathTest` - *A homed left operand multiplies in place, exactly like the M2 commutative forms.*
  - Method `.test_a_homed_left_operand_imuls_in_its_own_register()`
- `class FramePathInlineTest` - *A frame-path function (more slots than homes) still foreign-copies its callees.*
  - Method `.test_a_frame_main_copies_a_call_free_callee()`
  - Method `.test_a_frame_main_copies_inside_a_loop_body_too()`
- `class FramePathRebindFallbackTest` - *A callee whose body calls another function keeps the real call from a frame function.*
  - Method `.test_the_calling_callee_stays_a_call()`
- `class StackParamCopyTest` - *A six-parameter callee's copy parks its fifth and sixth arguments, not a call.*
  - Method `.test_a_six_parameter_callee_copies()`
  - Method `.test_a_six_parameter_rebinding_callee_takes_the_bind_once_copy()`
  - Method `.test_a_six_parameter_calling_callee_keeps_the_stack_call()`
- `class FrameInPlaceUpdateTest` - *A frame slot's `slot = slot op value` is one memory-operand instruction.*
  - Method `.test_a_displaced_counter_adds_in_place()`
  - Method `.test_a_frame_add_of_a_frame_name_folds_into_the_store()`
  - Method `.test_updates_beyond_disp8_take_the_four_byte_displacement()`
- `class HomeMapContractTest` - *`_home` reads the slot-to-home map, not the positional register list.*
  - Method `.test_a_slot_loses_its_home_when_the_map_does_not_grant_it()`
  - Method `.test_the_map_grants_homes_beyond_the_positional_default()`
- `class HomePreferenceTest` - *A frame function grants the registers to its deepest-loop slots, not its first six.*
  - Method `.test_a_loop_slot_beyond_the_identity_prefix_homes_as_a_register()`
  - Method `.test_a_displaced_depth_zero_slot_takes_the_memory_form()`
  - Method `.test_a_leaf_function_keeps_its_declaration_order()`
- `class ImulImmediateShapeTest` - *A value-position `homed * literal` emits the imul, not a constant load.*
  - Method `.test_a_const_multiplied_home_takes_the_three_operand_form()`
- `class ExpressionTimesLiteralShapeTest` - *`expr * k` folds the constant into the three-operand imul; nothing parks.*
  - Method `.test_a_computed_left_times_a_literal_uses_the_three_operand_form()`
  - Method `.test_a_literal_on_the_left_takes_the_same_form()`
  - Method `.test_a_call_result_times_a_literal_never_parks()`
  - Method `.test_a_literal_beyond_imm32_keeps_the_general_multiply()`
- `class HomedRightOperandShapeTest` - *A homed right operand combines with the left value already in rax.*
  - Method `.test_a_computed_left_adds_a_homed_name_in_place()`
  - Method `.test_a_computed_left_subtracts_a_homed_name_in_place()`
  - Method `.test_a_computed_left_compares_a_homed_name_in_place()`
  - Method `.test_a_homed_right_after_a_power_of_two_division_never_parks()`
- `class CommutativeMergeShapeTest` - *A commutative chain folds every step into the destination register itself.*
  - Method `.test_a_chain_addition_folds_into_the_destination_home()`
  - Method `.test_a_multiply_accumulate_chain_never_parks_its_accumulator()`
- `class ParkedCopyArgumentTest` - *A five-or-six-argument copy binds its spare arguments into their guest homes.*
  - Method `.test_spare_arguments_land_in_their_guest_homes()`
  - Method `.test_the_copy_entry_skips_the_rebind_pair()`
- `class HoistedMagicDivisionTest` - *A loop's magic division reads its seed and divisor from hoisted spare homes.*
  - Method `.test_a_looped_modulo_hoists_the_divisor_and_seed()`
- `class ForeignLoopCopyTest` - *A foreign callee whose body loops copies in place like any other.*
  - Method `.test_the_callees_loop_lowers_inside_the_caller()`
- `class ShiftThroughFusionTest` - *`(a >> m) << m` fuses into one and-immediate, across call boundaries too.*
  - Method `.test_a_direct_shift_pair_lowers_as_one_and()`
  - Method `.test_unequal_shift_counts_stay_two_instructions()`
  - Method `.test_a_forwarded_parameter_leaves_no_sar_behind()`
  - Method `.test_a_twice_used_parameter_keeps_its_bind()`
- `class PrologueSlimTest` - *The entry prologue carries only what the body leaves undefined.*
  - Method `.test_a_final_return_skips_the_implicit_zero()`
  - Method `.test_a_body_without_a_final_return_keeps_the_implicit_zero()`
  - Method `.test_a_zero_provenance_region_emits_exactly_one_xor()`
- `class SelectIfOrderTest` - *The select lowering keeps its plus candidate off the critical path.*
  - Method `.test_the_lowering_selects_branchlessly()`
  - Method `.test_no_lea_between_the_flag_setting_sub_and_the_cmov()`
- `class StrByteInlineTest` - *`str_byte(s, j)` with a leaf index lowers inside the caller, with no call.*
  - Method `.test_a_leaf_index_loads_inside_the_function_body()`
  - Method `.test_the_inline_keeps_the_minus_one_answer()`
- `class DirectVarCopyTest` - *A variable-to-variable copy moves straight into the target's home register.*
  - Method `.reg_reg_movs(code)` - *Decode 3-byte REX.W register-to-register movs as (dst, src) register numbers.*
  - Method `.test_no_copy_round_trips_through_rax()`
- `class NameMaskBitTestTest` - *`if (free & bit) != 0` branches from `test free_home, bit_home` alone.*
  - Method `.test_the_masked_nonzero_condition_tests_two_registers()`
  - Method `.test_the_masked_nonzero_condition_keeps_no_dead_and()`
- `class DivisorNoOverflowGuardTest` - *A proven non-negative divisor skips idiv's minus-one overflow guards.*
  - Method `.test_a_nonnegative_divisor_keeps_only_the_zero_trap()`
  - Method `.test_a_negative_divisor_keeps_both_guards()`
- `class HomeSumLeaTest` - *`a + b` with both names homed is one lea, not a load plus an add.*
  - Method `.test_a_two_home_sum_is_one_lea_with_no_add()`
- `class ExtraHomeFrameTest` - *A frame function grants its seventh and eighth hot slots r10/r11.*
  - Method `.test_a_call_site_pushes_and_pops_the_extra_homes()`
  - Method `.test_str_byte_inline_bails_when_r10_is_a_home()`
- `class StrByteSibLoadTest` - *str_byte(s, j) with homed pointer and homed, proven-nonnegative index.*
  - Method `.test_the_byte_load_folds_into_one_sib_movzx()`
  - Method `.test_the_pointer_never_hops_through_rax()`
  - Method `.test_an_unproven_index_keeps_the_sign_test()`
- `class ArgByteInlineTest` - *arg_byte(0, j) loads its cache slot rip-relative right at the call site.*
  - Method `.test_the_slot0_blob_binds_rip_relative()`
  - Method `.test_the_byte_loads_off_the_loaded_pointer()`
  - Method `.test_a_masked_index_drops_the_sign_test()`
  - Method `.test_a_negative_index_never_leaves_the_table()`
- `class ZeroCompareFlagReuseTest` - *A homed name compared against zero right after its sub leaves ZF set.*
  - Method `._cmp_zero_forms()`
  - Method `.test_an_equal_zero_compare_after_sub_keeps_the_sub_flag()`
  - Method `.test_the_sub_and_the_branch_fall_directly_adjacent()`
  - Method `.test_an_inequal_zero_compare_reuses_the_flag_too()`
  - Method `.test_a_signed_compare_still_emits_its_own_compare()`
- `class ScanIndexSignGuardTest` - *A zero-initialized loop index never pays the inline's sign test.*
  - Method `.test_a_zero_initialized_index_skips_the_sign_test()`
  - Method `.test_an_unproven_index_keeps_the_sign_test()`
- `class TrueLoopBranchTest` - *A literal-true loop condition branches with one jmp, never by testing 1.*
  - Method `.test_a_while_true_back_edge_is_a_bare_branch()`
  - Method `.test_a_while_false_body_never_runs()`

**Functions:**
- `def lower(source)` - *Check and lower one source, returning the program and its recording assembler.*
- `def body_text(source, name)` - *The .text bytes of one function's body, sliced from the generated artifact.*
- `def region_text(source, name)` - *One function and its internal labels, stopping at the runtime helpers.*
- `def region_bytes(source, name)` - *The .text bytes of one function's body, its inline copies included.*
- `def region_of(source, name)` - *One function's bytes with its inline copies, as region_bytes does in helpers above.*
- `def has_setcc(blob)` - *Any setcc opcode (0F 90..0F 9F): a comparison materialised as 0 or 1.*
- `def count_test_imms(blob)` - *How many `test r/m8, imm8` byte tests (F6 with register-modrm, then a mask) appear.*
- `def _has_shr(blob, count)`
- `def _has_mask_movabs(blob)` - *The 0x3FFFFFFFF mask riding a movabs immediate.*
- `def _movabs_rax(value)` - *The full `movabs rax, value` encoding.*
- `def _hoisted_movabs_candidates(value)` - *`movabs <home>, value` for every home a hoist could pick.*
- `def _combine_right_after_shr(region, count)` - *Whether an update combine follows its peephole `shr rax, count` with no park.*
- `def calls_of(source, caller, callee)` - *How many real `call` sites inside `caller`'s region target `callee`.*

---

### [`tests/test_contract.py`](tests/test_contract.py)
> *The doc-law contract: SPEC.md, LLM_SYSTEM.md and the manifest describe one language.*

**Size**: `114` lines

**Classes:**
- `class SpecContractTest` - *SPEC.md names the whole surface: every builtin, limit and import it really has.*
  - Method `.test_the_standard_surface_lists_every_builtin()`
  - Method `.test_the_parameter_limit_matches_the_manifest()`
  - Method `.test_the_target_lists_every_import()`
  - Method `.test_no_section_forbids_the_legal_for_loop()`
  - Method `.test_the_trap_exit_and_the_cli_exits_do_not_collide()`
- `class SystemCardContractTest` - *LLM_SYSTEM.md is the compact face of the same manifest, never an older one.*
  - Method `.test_the_card_names_every_builtin()`
  - Method `.test_the_card_carries_the_real_parameter_limit()`
  - Method `.test_the_card_teaches_the_legal_for_loop()`
  - Method `.test_the_card_stays_compact()`
- `class ReadmeContractTest` - *The README publishes the benchmark scoreboard surface: all cases, named.*
  - Method `.test_the_readme_names_every_benchmark_case()`

**Functions:**
- `def spec_section(number)` - *One `## N.` section body, up to the next `## ` heading.*
- `def manifest_builtin_names()`

---

### [`tests/test_end_to_end.py`](tests/test_end_to_end.py)
> *End-to-end tests: build real executables, run them, and assert what they print.*

**Size**: `1596` lines

**Classes:**
- `class EndToEndTest` - *Each test compiles a source, writes it to a temporary folder and executes it.*
  - Method `.setUp()`
  - Method `._cleanup()` - *Remove the built executable and its folder.*
  - Method `.build(source)` - *Compile source text and return the executable path.*
  - Method `.execute(source)` - *Run a compiled program, capturing both streams.*
  - Method `.test_hello_prints_an_expression()`
  - Method `.test_arithmetic_truncates_shifts_and_returns_the_exit_code()`
  - Method `.test_recursion_and_loop_control()`
  - Method `.test_countdown_latches_match_count_up_semantics()`
  - Method `.test_power_of_two_division_truncates_like_the_machine_would()`
  - Method `.test_runtime_division_answers_on_both_the_narrow_and_wide_paths()`
  - Method `.test_division_by_zero_is_a_trap_with_a_message_and_exit_code_3()`
  - Method `.test_traps_behave_identically_without_the_folding_pass()`
  - Method `.test_run_forwards_the_programs_own_exit_code()`
  - Method `.test_shift_count_beyond_63_wraps_on_the_machine()`
  - Method `.test_more_slots_than_registers_spill_to_the_stack()`
  - Method `.test_min_int_divided_by_minus_one_traps_like_division_by_zero()`
  - Method `.test_min_int64_prints_with_its_sign()`
  - Method `.test_comparison_and_logical_operators_yield_one_or_zero()`
  - Method `.test_a_two_sided_comparison_branches_on_its_held_left_operand()`
  - Method `.test_four_argument_call_uses_every_argument_register()`
  - Method `.test_print_inside_an_argument_list_evaluates_left_to_right()`
  - Method `.test_calls_preserve_locals_held_in_callee_saved_registers()`
  - Method `.test_even_slot_frames_restore_homes_across_calls()`
  - Method `.test_forty_locals_use_wide_frame_displacements()`
  - Method `.test_a_binding_from_a_branch_that_never_ran_reads_as_zero()`
  - Method `.test_optimised_and_unoptimised_builds_behave_identically()`
  - Method `.test_print_char_writes_single_bytes_without_a_newline()`
  - Method `.test_print_char_used_as_a_value_returns_its_argument()`
  - Method `.test_print_char_wraps_to_the_low_byte()`
  - Method `.test_print_hex_prints_lowercase_hex_without_leading_zeros()`
  - Method `.test_print_hex_wraps_to_the_unsigned_sixty_four_bit_value()`
  - Method `.test_print_hex_used_as_a_value_returns_its_argument()`
  - Method `.test_print_builtins_compose_in_one_program()`
- `class StackParametersTest` - *Six-argument calls: four in registers, two through the outgoing stack area.*
  - Method `.test_stack_arguments_reach_the_callee_and_rotate()`
  - Method `.test_arguments_past_the_registers_evaluate_left_to_right()`
  - Method `.test_stack_parameters_are_mutable_locals()`
  - Method `.test_eight_parameters_pass()`
  - Method `.test_deep_recursion_rotates_its_stack_parameters()`
- `class FusedCollatzLoopTest` - *The fused odd-pair loop counts every reference step exactly, on every path.*
  - Method `._steps_source(k, odd_value, even_test)`
  - Method `.test_a_probe_starting_odd_counts_both_steps_of_the_pair()`
  - Method `.test_a_probe_starting_even_takes_the_halving_path()`
  - Method `.test_a_five_k_pair_counts_the_same_way()`
  - Method `.test_a_refused_pair_keeps_the_generic_shrink_chain()`
  - Method `.test_the_swapped_parity_test_counts_identically()`
- `class PairInlineAckermannTest` - *The two-parameter pair inlining answers Ackermann probes exactly.*
  - Method `.test_ackermann_probes_match_the_known_values()`
  - Method `.test_deep_recursion_meets_the_depth_cap_and_still_answers()`
- `class TwinParityTest` - *Every benchmark quadruple compiles to programs that print the same total.*
  - Method `.setUpClass()`
  - Method `.setUp()`
  - Method `._cleanup()`
  - Method `._assert_par(case)`
  - Method `.test_xorshift_twins_print_the_same_total()`
  - Method `.test_fib30_twins_print_the_same_total()`
  - Method `.test_folded_math_twins_print_the_same_total()`
  - Method `.test_collatz_twins_print_the_same_total()`
  - Method `.test_ackermann_twins_print_the_same_total()`
  - Method `.test_nqueens_twins_print_the_same_total()`
  - Method `.test_binary_gcd_twins_print_the_same_total()`
  - Method `.test_call_chain_twins_print_the_same_total()`
  - Method `.test_tak_twins_print_the_same_total()`
  - Method `.test_primes_twins_print_the_same_total()`
  - Method `.test_mandelbrot_twins_print_the_same_total()`
  - Method `.test_splitmix_twins_print_the_same_total()`
  - Method `.test_many_params_twins_print_the_same_total()`
- `class FlagFusedConditionRuntimeTest` - *Branches the fuser claims to handle stay semantically exact over negative values.*
  - Method `.test_bit_tests_and_masks_over_negative_values()`
  - Method `.test_short_circuit_branches_skip_call_sides()`
  - Method `.test_chained_conjunctions_and_alternations()`
  - Method `.test_mask_counts_over_a_negative_span()`
- `class OperandParkTest` - *A merged left value in its destination register must survive right-side evaluation.*
  - Method `.test_parked_survives_a_generic_right_side()`
- `class LogicalShiftValueTest` - *Covering masks over every sign must read exactly the logical shift.*
  - Method `.test_masked_shifts_match_the_logical_result_everywhere()`
- `class MergedMaskLoopValueTest` - *The reclaimed mix lowering must stay exact across a real mixer loop.*
  - Method `.test_a_masked_xor_multiplier_loop_wraps_like_the_source()`
  - Method `.test_a_merged_left_update_never_leaks_into_a_right_home_read()`
- `class ForLoopValueTest` - *The for loop earns its place by running: ranges, control flow and scoped names.*
  - Method `.test_for_ranges_control_flow_and_scoped_variables()`
  - Method `.test_the_end_bound_is_re_evaluated_each_iteration_like_c()`
- `class LoopConstantHoistValueTest` - *Hoisted big constants must read the loaded value on every loop iteration.*
  - Method `.test_a_countdown_mixer_with_hoisted_constants_wraps_like_the_source()`
  - Method `.test_two_sequential_loops_hoist_their_own_constants()`
- `class ArrayRuntimeTest` - *Arrays: fill loops, aggregate literals, dynamic stores and callee scopes.*
  - Method `.test_dynamic_element_stores_and_reads_round_trip()`
  - Method `.test_aggregate_literal_initialises_in_order()`
  - Method `.test_static_store_load_and_callee_element_argument()`
  - Method `.test_nested_indices_index_loads_and_stores_compose()`
  - Method `.test_arrays_in_callee_scopes_survive_their_frame()`
  - Method `.test_index_reads_in_loops_accumulate_the_full_sum()`
  - Method `.test_read_modify_write_fusions_keep_the_values()`
  - Method `.test_dynamic_read_modify_write_keeps_the_values()`
  - Method `.test_large_array_spills_beyond_disp8()`
  - Method `.test_array_programs_behave_identically_unoptimised()`
- `class CommutativePeepholeValueTest` - *The commutative-home fast path and its ZF-fed branch print identical results.*
  - Method `.test_the_peephole_functions_preserve_output()`
- `class SameNameInPlaceValueTest` - *Same-name in-place products and homes-fed multiplies print identical values.*
  - Method `.test_same_name_and_multiply_forms_preserve_output()`
- `class FramePathInlineValueTest` - *A 7+-slot main that foreign-copies its callees prints the leaf-path answers.*
  - Method `.test_frame_path_copies_preserve_output()`
- `class StackParamCopyValueTest` - *Six-parameter copies print the same answers the call form computes.*
  - Method `.test_six_parameter_copies_preserve_output()`
- `class FrameInPlaceValueTest` - *In-place frame updates print the answers hand arithmetic agrees with.*
  - Method `.test_in_place_updates_preserve_output()`
- `class HomePreferenceValueTest` - *Preference reshuffles which slots sit in registers without changing answers.*
  - Method `.test_preference_preserves_output()`
- `class CopyPropagationValueTest` - *Single-read temps disappear into their readers without changing answers.*
  - Method `.test_propagated_copies_preserve_output()`
  - Method `.test_a_refused_propagation_keeps_the_materialised_value()`
- `class ForeignLoopCopyValueTest` - *A foreign callee containing loops copies in place and answers identically.*
  - Method `.test_looped_copies_preserve_output()`
- `class ShiftThroughValueTest` - *The fused drop-then-restore mask keeps signed and wrapping semantics.*
  - Method `.test_shift_through_values_match_two_complement()`
- `class InputSystemTest` - *The input builtins: argv walks the ANSI command line, stdin streams bytes.*
  - Method `.run_with(source, *, arguments, stdin)` - *Run the compiled program with extra arguments and optional stdin bytes.*
  - Method `.test_argc_counts_the_program_and_its_arguments()`
  - Method `.test_the_first_arg_byte_call_fills_the_cache_without_losing_its_arguments()`
  - Method `.test_a_quoted_argument_keeps_its_inner_space()`
  - Method `.test_read_char_streams_stdin_bytes_then_eof()`
  - Method `.test_read_char_without_stdin_reports_eof_at_once()`
  - Method `.test_read_char_wraps_a_large_piped_stream()`
  - Method `.test_the_handle_cache_survives_many_refills()`
- `class ExitValueTest` - *`exit(code)` stops the machine at once, from main's body and from any helper.*
  - Method `.test_exit_stops_with_the_given_code()`
  - Method `.test_exit_from_a_helper_stops_a_loop()`
- `class StringBuiltinsTest` - *String literals, `print_str`, `str_len` and `str_byte`, end to end.*
  - Method `.test_print_str_writes_the_literal_raw()`
  - Method `.test_escapes_decode_into_the_output()`
  - Method `.test_str_len_counts_to_the_terminator()`
  - Method `.test_str_byte_reads_inside_and_reports_outside()`
  - Method `.test_a_guarded_scan_and_an_out_of_range_read_agree()`
  - Method `.test_equal_literals_share_one_blob()`
  - Method `.test_str_byte_with_a_calling_index_takes_the_helper_path()`
  - Method `.test_an_assert_helper_prints_and_exits_on_mismatch()`
- `class StdoutBufferTest` - *The shared stdout buffer changes the syscall count, never the bytes.*
  - Method `.test_exit_flushes_the_shared_buffer()`
  - Method `.test_flush_returns_the_pending_byte_count()`
  - Method `.test_a_fresh_buffer_flushes_zero_bytes()`
  - Method `.test_flush_keeps_the_stream_in_order()`
  - Method `.test_two_hundred_kib_of_print_char_survive_the_buffer()`

**Functions:**
- `def _wrap(value)`
- `def _wrap64(value)`

---

### [`tests/test_filetable.py`](tests/test_filetable.py)
> *Scripted exactness proofs for the qwenFS toy file table.*

**Size**: `209` lines

**Classes:**
- `class ScriptedSessionTest`
  - Method `.test_write_overwrite_read_missing_list()`
  - Method `.test_link_duplicate_unlink()`
  - Method `.test_session_is_deterministic()`
- `class DenseGraphTest`
  - Method `.test_dense_graph_links_and_backlinks_stay_exact()`
- `class MountPageSimulationTest`
  - Method `.test_mount_copies_whole_values_under_a_base_offset()`
  - Method `.test_page_chunks_reassemble_to_the_exact_value()`
- `class CapAndHandlesTest`
  - Method `.test_32_file_cap_with_overwrite_allowed_when_full()`
  - Method `.test_integer_handles_are_i64_only()`
- `class EmitAdditiveTest`
  - Method `.test_emit_uses_rodata_add_and_lea_rip_with_no_new_section()`
  - Method `.test_additive_emit_on_a_real_build_still_verifies()`
  - Method `.test_default_build_path_never_invokes_the_filetable()`
  - Method `.test_second_emit_is_rejected_instead_of_orphaning_bytes()`
  - Method `.test_emit_rejects_wrong_types_without_assert()`

**Functions:**
- `def scripted_session()` - *One deterministic session every exactness test replays.*

---

### [`tests/test_ir.py`](tests/test_ir.py)
> *SSA IR foundation tests: differential execution and invariants.*

**Size**: `311` lines

**Classes:**
- `class DifferentialTest` - *Compiled machine behaviour and interpreted IR behaviour must agree.*
  - Method `.setUp()`
  - Method `._cleanup()`
  - Method `._machine_run(source, stdin)`
  - Method `.test_corpus_machine_and_interpreter_agree()`
  - Method `.test_interpreted_str_byte_answers_minus_one_past_the_nul()`
- `class InvariantTest` - *Shape the translator emits before anything runs.*
  - Method `._translate(source)`
  - Method `.test_every_block_ends_with_one_terminator()`
  - Method `.test_single_assignment_per_destination()`
  - Method `.test_uses_follow_definitions()`
  - Method `.test_terminator_ops_carry_no_destination()`
  - Method `.test_values_do_not_exceed_the_function_budget()`

---

### [`tests/test_lexer.py`](tests/test_lexer.py)
> *Lexer tests: token shape, spans, comments and the two literal failures.*

**Size**: `108` lines

**Classes:**
- `class LexerTest` - *The scanner is the first stage an agent's source touches, so its reports must be exact.*
  - Method `.test_tokens_carry_kind_text_value_and_span()`
  - Method `.test_comment_runs_to_end_of_line()`
  - Method `.test_two_character_operators_win_over_one()`
  - Method `.test_range_symbols_lex_before_the_number_boundary()`
  - Method `.test_integer_beyond_i64_is_a_positioned_error()`
  - Method `.test_two_to_the_63_lexes_so_unary_minus_can_fold_it()`
  - Method `.test_unexpected_character_names_the_fix()`
  - Method `.test_tokenize_reports_eof_at_the_end()`
  - Method `.test_a_string_literal_lexes_with_its_escapes_decoded()`
  - Method `.test_an_unterminated_string_is_a_positioned_error()`
  - Method `.test_an_unknown_escape_is_a_positioned_error()`
  - Method `.test_a_string_token_carries_its_column()`
  - Method `.test_the_full_pipeline_places_a_string_on_its_span()`
  - Method `.test_non_ascii_identifier_is_rejected_as_unexpected_character()`
  - Method `.test_long_digit_run_is_E1003_not_ValueError()`
  - Method `.test_zero_padded_literal_fits_when_value_fits()`

---

### [`tests/test_listing.py`](tests/test_listing.py)
> *Listing tests: the label-grouped view of the bytes the writer will place.*

**Size**: `27` lines

**Classes:**
- `class ListingTest` - *A corrupt artifact must fail loudly instead of printing negative sizes.*
  - Method `.test_label_past_the_text_is_refused()`
  - Method `.test_labels_render_with_their_bytes()`

---

### [`tests/test_llm_card.py`](tests/test_llm_card.py)
> *LLM card tests: the fenced programs in LLM_SYSTEM.md must actually compile.*

**Size**: `32` lines

**Classes:**
- `class LlmCardTest` - *If the card shows a program, that program is checked.*
  - Method `.test_card_has_a_canonical_program()`
  - Method `.test_every_card_program_compiles()`

**Functions:**
- `def main_programs()` - *Every fenced block that claims to be a complete program.*

---

### [`tests/test_machine.py`](tests/test_machine.py)
> *Emitter tests: the exact bytes of the encodings the generator relies on.*

**Size**: `461` lines

**Classes:**
- `class FrameInPlaceEncodingTest` - *A frame slot's in-place update encodes as one memory-operand instruction.*
  - Method `.code(build)`
  - Method `.test_frame_slot_binary_in_place()`
  - Method `.test_frame_slot_binary_in_place_beyond_disp8()`
  - Method `.test_frame_slot_group1_imm_in_place()`
  - Method `.test_frame_slot_group1_imm_rejects_wide_literals()`
- `class IndexedElementEncodingTest` - *An indexed element access is one SIB memory operand over `rbp` and the index.*
  - Method `.code(build)`
  - Method `.test_indexed_load_with_disp8()`
  - Method `.test_indexed_load_with_r12_as_index_and_disp32()`
  - Method `.test_indexed_binary_update()`
  - Method `.test_indexed_store_with_r12_source()`
  - Method `.test_indexed_immediate_store()`
  - Method `.test_indexed_immediate_store_rejects_wide_literals()`
  - Method `.test_indexed_group1_immediate_update()`
  - Method `.test_indexed_group1_rejects_wide_literals()`
  - Method `.test_indexed_memory_rejects_rsp()`
- `class SIBByteLoadTest` - *`movzx r32,[base+index]` needs the disp8 form for an rbp/r13 base.*
  - Method `.code(base, index)`
  - Method `.test_a_plain_base_takes_the_five_byte_form()`
  - Method `.test_an_r13_base_takes_the_disp8_zero_form()`
  - Method `.test_an_rbp_base_takes_the_disp8_zero_form()`
  - Method `.test_rsp_is_refused_on_either_side()`
- `class RipStateEncodingTest` - *`cmp r64,[rip]` and `mov [rip],r64` read and write static state in place.*
  - Method `.code(build)`
  - Method `.test_cmp_register_with_rip_state()`
  - Method `.test_cmp_extended_register_with_rip_state_takes_rex_r()`
  - Method `.test_store_of_rip_state()`
- `class NarrowDivisionEncodingTest` - *`div r/m32` is one F7 /6 form, with a REX prefix only for an extended register.*
  - Method `.code(register)`
  - Method `.test_plain_and_extended_forms()`
- `class EncodingTest` - *Each test names one encoding and its expected bytes.*
  - Method `.code(build)` - *Run a builder function against a fresh assembler and return its bytes.*
  - Method `.test_push_and_pop_extended_registers_mask_the_register_bits()`
  - Method `.test_integer_literal_is_a_full_imm64_move()`
  - Method `.test_frame_slot_load_and_store()`
  - Method `.test_frame_slot_beyond_disp8_uses_a_four_byte_displacement()`
  - Method `.test_signed_division_of_r10()`
  - Method `.test_arithmetic_shift_uses_cl()`
  - Method `.test_lea_with_small_offset_uses_disp8()`
  - Method `.test_lea_from_r12_takes_the_sib_form()`
  - Method `.test_lea_with_wide_offset_uses_disp32()`
  - Method `.test_shift_by_immediate_needs_no_count_register()`
  - Method `.test_comparison_result_is_zero_extended_into_rax()`
  - Method `.test_shell_alignment_is_one_instruction()`
  - Method `.test_register_immediate_add_uses_the_short_form_when_it_fits()`
  - Method `.test_register_immediate_sub_and_cmp_use_the_group_one_encoding()`
  - Method `.test_relative_call_records_a_fixup_at_the_displacement()`
  - Method `.test_label_records_the_current_offset()`
  - Method `.test_duplicate_label_is_a_compiler_bug()`
  - Method `.test_cmp_reg_reg_matches_the_cmp_encoding()`
  - Method `.test_movzx_word_selects_the_extended_register_bits()`
  - Method `.test_movzx_byte_selects_the_extended_register_bits()`
- `class ImulImmediateEncodingTest` - *`x * constant` lowers as one three-operand imul, source kept intact.*
  - Method `.code(build)`
  - Method `.test_imul_imm8_selects_the_byte_form()`
  - Method `.test_imul_imm32_selects_the_long_form()`
  - Method `.test_imul_imm_rejects_values_beyond_imm32()`
- `class NopPadEncodingTest` - *Loop-head padding composes canonical multi-byte no-ops, not nop trains.*
  - Method `.code(build)`
  - Method `.test_three_bytes_use_the_compact_nopl_form()`
  - Method `.test_two_bytes_use_xchg_arrays_swap()`
  - Method `.test_fifteen_bytes_compose_from_nine_and_six()`
  - Method `.test_zero_bytes_pad_nothing()`
  - Method `.test_every_size_rounds_to_its_requested_length()`
- `class IndirectStoreEncodingTest` - *Dynamic array stores write through a pointer register: `mov [base], r64`.*
  - Method `.code(build)`
  - Method `.test_pointer_store_encodes_the_base_register_directly()`
  - Method `.test_pointer_store_masks_extended_register_bits_into_rex()`
  - Method `.test_pointer_store_swaps_src_bits_into_the_reg_field()`
  - Method `.test_pointer_store_rejects_a_stack_base()`
- `class AluThroughRegisterPointerTest` - *A read-modify-write through a pointer register: `op [base], r64`.*
  - Method `.code(build)`
  - Method `.test_add_through_a_pointer_register()`
  - Method `.test_sub_through_an_extended_base_masks_into_rex()`
  - Method `.test_rejects_a_stack_base()`
- `class AluThroughPointerWithImmediateTest` - *`op qword [base], imm8/imm32` for a fused element update with a literal.*
  - Method `.code(build)`
  - Method `.test_add_an_imm8_through_a_pointer_register()`
  - Method `.test_sub_an_imm8_through_an_extended_base()`
  - Method `.test_an_imm32_extends_the_displacement_size()`
  - Method `.test_rejects_an_immediate_wider_than_32_signed_bits()`
  - Method `.test_rejects_a_stack_base()`
- `class Sse2VectorMethodTest` - *The five SSE2 encodings the array vectorizer emits, byte for byte.*
  - Method `.code(build)`
  - Method `.test_movq_from_a_gpr()`
  - Method `.test_punpcklqdq_broadcasts_the_low_lane()`
  - Method `.test_paddq_adds_two_lanes()`
  - Method `.test_unaligned_load_through_a_scaled_index()`
  - Method `.test_unaligned_store_through_a_scaled_index()`

---

### [`tests/test_magic_division.py`](tests/test_magic_division.py)
> *Magic-multiply division: the parameter search is exact, and the bytes back it up.*

**Size**: `226` lines

**Classes:**
- `class MagicParameterTest` - *The parameter search before any code generation.*
  - Method `.test_the_search_reproduces_gccs_parameters_for_the_benchmark_divisor()`
  - Method `.test_powers_of_two_and_degenerate_divisors_refuse()`
  - Method `.test_the_emitted_chain_matches_truncated_division_for_small_divisors()`
  - Method `.test_the_emitted_chain_matches_truncated_division_for_large_divisors()`
- `class MagicDivisionBytesTest` - *The lowering replaces the idiv and its trap branches.*
  - Method `.main_region(body)`
  - Method `.test_a_literal_remainder_carries_idiv_or_trap_branches_nowhere()`
  - Method `.test_the_benchmark_chain_matches_the_captured_gcc_bytes()`
  - Method `.test_a_quotient_keeps_the_sign_word_and_no_remainder_tail()`
- `class MagicDivisionExecutionTest` - *Real executables agree with truncated division, extremes included.*
  - Method `.execute(source, name)`
  - Method `._cleanup(folder)`
  - Method `.expected_battery()` - *The 18 printed lines of MAGIC_PROGRAM under truncated division semantics.*
  - Method `.test_a_battery_of_dividends_matches_truncated_semantics()`
  - Method `.test_plain_builds_agree_on_the_battery()`

**Functions:**
- `def truncdiv(n, d)` - *C-truncated division, the language's own / and % semantics.*
- `def machine_chain(n, m_signed, s)` - *What the emitted instructions compute, modelled exactly.*
- `def dividend_battery(d)` - *Every structured int64 edge a magic chain can go wrong on, both signs.*

---

### [`tests/test_optimize.py`](tests/test_optimize.py)
> *Optimiser tests: folding arithmetic, refusals and effect preservation.*

**Size**: `771` lines

**Classes:**
- `class InductionStrengthReductionTest` - *A linear function of a counted index stays in an incremented local.*
  - Method `.literal_values(body)`
  - Method `.test_the_linear_term_is_hoisted_and_incremented()`
  - Method `.test_a_non_unit_step_is_refused()`
  - Method `.test_a_break_in_the_body_is_refused()`
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
  - Method `.test_zero_fold_never_drops_a_division_trap()`
  - Method `.test_constant_condition_selects_the_taken_branch()`
  - Method `.test_constant_false_while_loop_disappears()`
  - Method `.test_while_zero_counts_its_rewrite()`
  - Method `.test_dropped_unreachable_statements_count()`
  - Method `.test_double_negation_folds_to_the_value()`
  - Method `.test_statements_after_return_are_unreachable_and_dropped()`
  - Method `.test_a_call_is_never_removed()`
  - Method `.test_folded_count_reports_what_changed()`
  - Method `.test_constant_heavy_code_generates_fewer_instructions()`
- `class ConstantLoopUnrollingTest` - *A short constant-trip loop around a self-call becomes clones with literals.*
  - Method `.program(loop)`
  - Method `.statements(loop)`
  - Method `.loops(loop)`
  - Method `.calls(statements)` - *Count the `f(...)` calls anywhere under a statement list.*
  - Method `.test_a_self_calling_constant_loop_unrolls_into_clones()`
  - Method `.test_each_clone_substitutes_the_counter_literal()`
  - Method `.test_the_counter_keeps_its_post_loop_value()`
  - Method `.test_a_body_without_a_self_call_keeps_its_loop()`
  - Method `.test_a_jump_in_the_body_keeps_its_loop()`
  - Method `.test_a_second_write_to_the_counter_keeps_its_loop()`
  - Method `.test_a_trip_count_past_the_limit_keeps_its_loop()`
- `class CountdownLatchTest` - *`while v < C` loops whose counter is dead outside the body lower as countdown latches.*
  - Method `.test_a_counter_loop_counts_down_instead_of_up()`
  - Method `.test_the_loop_condition_shape_survives_the_rewrite()`
  - Method `.test_a_counter_read_after_the_loop_refuses_the_rewrite()`
  - Method `.test_a_body_that_reads_the_counter_refuses_the_rewrite()`
  - Method `.test_an_index_read_of_the_counter_refuses_the_rewrite()`
  - Method `.test_a_parameter_counter_refuses_the_rewrite()`
  - Method `.test_a_loop_with_break_refuses_the_rewrite()`
  - Method `.test_a_loop_with_continue_refuses_the_rewrite()`
  - Method `.test_a_countdown_loop_latches_with_a_bottom_dec_and_jnz()`
- `class ForLoweringTest` - *The for sugar lowers to Let + While before any other pass; no For survives lowering.*
  - Method `.lowered(source)`
  - Method `.test_an_exclusive_for_becomes_let_plus_while()`
  - Method `.test_an_inclusive_for_compares_less_equal()`
  - Method `.test_a_continue_inside_the_for_steps_before_jumping()`
  - Method `.test_a_continue_in_a_nested_while_is_not_touched()`
  - Method `.test_nested_fors_bind_the_continue_to_the_inner_loop()`
  - Method `.test_no_for_survives_anywhere_in_a_mixed_program()`
- `class CopyPropagationTest` - *One read of a temp substitutes the definition; every second use refuses.*
  - Method `.test_a_single_read_temp_folds_into_its_reader()`
  - Method `.test_a_second_read_refuses()`
  - Method `.test_a_reassignment_between_definition_and_copy_refuses()`
  - Method `.test_a_calling_definition_refuses()`
  - Method `.test_a_trapping_definition_refuses()`
  - Method `.test_a_clobbered_operand_between_definition_and_copy_refuses()`
  - Method `.test_a_loop_body_temp_propagates_per_iteration()`
- `class InliningTest` - *Single-return pure functions inline into their call sites.*
  - Method `.test_pure_single_return_call_inlines_into_a_literal()`
  - Method `.test_multi_use_call_argument_keeps_the_call()`
  - Method `.test_multi_use_pure_argument_inlines_one_clone_per_read()`
  - Method `.test_self_recursion_is_never_inlined()`
  - Method `.test_multi_statement_functions_are_never_inlined()`
- `class StringFoldTest` - *str_len/str_byte of literal handles fold to the exact specified answers.*
  - Method `.test_str_len_of_a_literal_folds_to_its_length()`
  - Method `.test_str_byte_of_a_literal_index_folds_to_the_byte()`
  - Method `.test_str_byte_at_the_terminator_folds_to_minus_one()`
  - Method `.test_a_negative_index_folds_to_minus_one()`
  - Method `.test_str_byte_past_the_terminator_is_never_folded()`
  - Method `.test_a_non_literal_handle_is_never_folded()`
- `class AlgebraicTest` - *Distributive expansion and like-term collection must be ring-exact.*
  - Method `.test_like_terms_collect()`
  - Method `.test_distribution_expands_and_collects()`
  - Method `.test_a_chain_nested_under_another_operator_still_collects()`
  - Method `.test_cancelling_terms_leave_zero()`
  - Method `.test_calls_are_never_grouped_or_duplicated()`

**Functions:**
- `def folded_value(op, left, right)` - *`fold_constant` for one pair, through the public entry point.*
- `def optimized_body(source)` - *Optimise a single-function body; a trailing `return 0;` is added and then hidden.*
- `def count_calls(expression)` - *Every Call node under one expression.*
- `def _peel_name_or_product(expression)`

---

### [`tests/test_parser.py`](tests/test_parser.py)
> *Parser tests: shapes, precedence and the grammar's positioned refusals.*

**Size**: `202` lines

**Classes:**
- `class ParserTest` - *The tree shape decides code shape, so these assertions are about generated code too.*
  - Method `.test_function_signature_and_body()`
  - Method `.test_multiplication_binds_tighter_than_addition()`
  - Method `.test_parentheses_override_precedence()`
  - Method `.test_else_if_nests_as_a_block()`
  - Method `.test_assignment_is_a_statement_only_for_a_name()`
  - Method `.test_assignment_to_a_literal_or_a_call_is_E2003()`
  - Method `.test_bare_literal_is_E2004()`
  - Method `.test_unknown_type_is_refused_with_the_one_type_cairn_has()`
  - Method `.test_missing_semicolon_reports_what_was_expected()`
  - Method `.test_expression_statement_must_be_a_call()`
  - Method `.test_parens_call_statement_is_accepted()`
  - Method `.test_bare_token_list_is_E2001_not_an_index_error()`
  - Method `.test_break_and_continue_spans_include_the_semicolon()`
  - Method `.test_min_int_is_spellable_as_negated_two_to_the_63()`
  - Method `.test_parenthesised_min_int_folds_like_the_bare_form()`
  - Method `.test_true_and_false_are_integers()`
- `class ForTest` - *`for name in start..end block` parses into a real node; `..=` is the inclusive bound.*
  - Method `.test_an_exclusive_range_parses()`
  - Method `.test_an_inclusive_range_parses()`
  - Method `.test_a_missing_range_symbol_is_a_positioned_refusal()`
  - Method `.test_the_loop_variable_must_be_a_name()`
  - Method `.test_the_loop_needs_the_in_keyword()`
- `class ArrayTest` - *`[N]i64` bindings, array literals and indexing reach the tree as designed.*
  - Method `.test_a_fill_literal_expands_to_one_node_per_element()`
  - Method `.test_an_aggregate_literal_keeps_its_element_expressions()`
  - Method `.test_index_store_targets_the_binding_with_its_index()`
  - Method `.test_fill_counts_must_be_integer_literals()`
  - Method `.test_array_types_stay_local_bindings()`
  - Method `.test_an_unclosed_index_is_a_positioned_refusal()`

**Functions:**
- `def body(source)` - *The body of the single function in a tiny program.*

---

### [`tests/test_pe.py`](tests/test_pe.py)
> *Backend and PE tests: labels, fixups, determinism and the produced image's structure.*

**Size**: `165` lines

**Classes:**
- `class CodegenTest` - *The backend is checked through the artifact it produces.*
  - Method `.test_artifact_names_entry_main_and_runtime()`
  - Method `.test_every_fixup_resolves()`
  - Method `.test_unresolved_label_is_a_compiler_bug_report()`
  - Method `.test_fixup_past_the_text_is_a_compiler_bug_report()`
  - Method `.test_entry_outside_text_is_refused_at_link()`
  - Method `.test_text_and_rodata_share_no_label()`
  - Method `.test_reserved_import_names_are_refused()`
  - Method `.test_empty_text_is_refused_at_link()`
  - Method `.test_align_rejects_a_zero_boundary()`
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
- `class ImageDataTest` - *The read-only blob must not silently orphan bytes or divide by zero.*
  - Method `.test_rodata_duplicate_label_is_refused()`
  - Method `.test_rodata_align_rejects_a_zero_boundary()`

**Functions:**
- `def artifact_for(source)` - *The linked artifact for a source, without going through the file writer.*
- `def rva_to_offset(image, rva)` - *Translate one RVA through the section table.*

---

### [`tests/test_runtime.py`](tests/test_runtime.py)
> *Tests for the runtime helpers' emitted shape and their static state.*

**Size**: `161` lines

**Classes:**
- `class StdinRefillTest` - *The stdin refill path pays one cached handle and 64 KiB per syscall.*
  - Method `.build_runtime()`
  - Method `.test_the_stdin_buffer_holds_sixty_four_kib()`
  - Method `.test_the_handle_cache_is_static_state()`
  - Method `.test_the_refill_path_references_the_handle_cache()`
- `class ReadCharPrologueTest` - *read_char's state loads stay lea+mov; the rip-direct rewrite loses here.*
  - Method `.test_the_prologue_keeps_its_address_registers()`
- `class StdoutBufferTest` - *The stdout buffer is static writable state the print family shares.*
  - Method `.test_the_stdout_buffer_holds_sixty_four_kib()`
  - Method `.test_the_stdout_state_rides_the_writable_blob()`
  - Method `.test_every_printer_routes_through_out_append()`
  - Method `.test_out_flush_carries_a_cached_handle_and_the_write()`
- `class PowerOfTwoPrinterTest` - *The fixed-width printers mask and shift instead of dividing by a power of two.*
  - Method `.helper_code(label)`
  - Method `.has_divide(code)`
  - Method `.test_print_hex_peels_a_nibble_with_and_and_shr()`
  - Method `.test_print_char_masks_the_low_byte()`

---

### [`tests/test_sema.py`](tests/test_sema.py)
> *Semantic tests: every reachable rule, one failing source each.*

**Size**: `260` lines

**Classes:**
- `class SemaTest` - *Semantic analysis is the last stage that sees names, so its rules are the language's.*
  - Method `.test_valid_program_takes_slots_in_declaration_order()`
  - Method `.test_names_resolve_to_the_parameter_slot()`
  - Method `.test_missing_main_is_the_entry_point_error()`
  - Method `.test_duplicate_function()`
  - Method `.test_five_to_eight_parameters_are_accepted()`
  - Method `.test_more_than_eight_parameters()`
  - Method `.test_unknown_name_and_use_before_declaration()`
  - Method `.test_assignment_to_a_let_is_refused()`
  - Method `.test_duplicate_binding_without_shadowing()`
  - Method `.test_unknown_function()`
  - Method `.test_wrong_argument_count()`
  - Method `.test_five_arguments_hits_the_four_parameter_limit()`
  - Method `.test_print_is_callable_not_redefinable()`
  - Method `.test_builtin_cannot_be_a_parameter_or_an_assignment_target()`
  - Method `.test_print_char_and_print_hex_are_one_argument_builtins()`
  - Method `.test_new_builtins_take_exactly_one_argument()`
  - Method `.test_new_builtins_are_callable_not_values_redefinable()`
  - Method `.test_input_builtins_have_their_arities()`
  - Method `.test_input_builtins_are_not_values()`
  - Method `.test_string_builtins_have_their_arities()`
  - Method `.test_exit_takes_one_code_and_stops_the_program()`
  - Method `.test_exit_is_never_a_value()`
  - Method `.test_an_exit_statement_ends_the_return_path()`
  - Method `.test_literal_above_max_plus_one_is_refused()`
  - Method `.test_break_outside_a_loop()`
  - Method `.test_function_must_return_on_every_path()`
  - Method `.test_if_else_returning_both_ways_is_accepted()`
  - Method `.test_every_diagnostic_is_collected_in_one_pass()`
- `class ForLoopTest` - *The for variable is scoped to its loop: reusable across loops, restored outside.*
  - Method `.test_two_loops_may_reuse_the_loop_variable()`
  - Method `.test_the_loop_variable_shadows_an_outer_binding_and_restores_it()`
  - Method `.test_assigning_the_loop_variable_inside_the_body_is_allowed()`
  - Method `.test_the_body_may_not_shadow_the_loop_variable()`
  - Method `.test_a_loop_variable_named_like_a_builtin_is_refused()`
  - Method `.test_the_loop_variable_is_gone_after_the_loop()`
  - Method `.test_continue_and_break_are_valid_inside_a_for()`
  - Method `.test_the_range_bounds_must_be_i64_expressions()`
- `class FlushBuiltinTest` - *flush() drains the shared stdout buffer: zero arguments, a real value.*
  - Method `.test_flush_takes_no_arguments_and_runs_as_a_statement()`
  - Method `.test_flush_is_a_value_builtin_that_cannot_be_renamed()`
- `class ArraySemaTest` - *`[N]i64` bindings reserve N consecutive slots and index only within range.*
  - Method `.test_an_array_binding_reserves_consecutive_slots()`
  - Method `.test_a_non_literal_initializer_is_refused()`
  - Method `.test_a_literal_length_mismatch_is_refused()`
  - Method `.test_an_empty_or_oversized_array_is_refused()`
  - Method `.test_indexing_a_scalar_is_refused()`
  - Method `.test_an_array_name_is_not_a_value()`
  - Method `.test_a_static_out_of_range_index_is_refused()`
  - Method `.test_storing_into_an_immutable_array_element_is_refused()`
  - Method `.test_storing_into_an_undeclared_array_is_refused()`

**Functions:**
- `def codes(source)` - *The diagnostic codes a source produces; asserts it is refused.*

---

### [`tests/test_values.py`](tests/test_values.py)
> *The core values are a contract: six values, each with a live guard and a proving command.*

**Size**: `97` lines

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

**Size**: `116` lines

**Classes:**
- `class VerifyTest` - *Each test breaks one rule and expects exactly the code that names it.*
  - Method `.test_a_fresh_build_has_no_problems()`
  - Method `.test_every_verification_code_is_reachable()`
  - Method `.test_a_problem_renders_with_its_code()`
  - Method `.test_import_directory_must_live_in_rdata()`
  - Method `.test_iat_must_live_in_data()`
  - Method `.test_entry_is_checked_against_the_text_section()`
  - Method `.test_text_must_be_readable()`

**Functions:**
- `def image_bytes()` - *A real build, which verification must accept unchanged.*
- `def patched(offset, payload)` - *A build with one field overwritten, to isolate the invariant it belongs to.*
- `def section_field(image, index, field)` - *One dword from a section header: field 8 is size, 12 is VA, 20 is the raw pointer.*
- `def file_offset(image, rva)` - *Translate one RVA through the section table.*

---
