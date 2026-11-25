# Decisions

Why this project is built the way it is, oldest first. One entry per decision that changes a
contract, a dependency, a data format or the architecture; IDs are consecutive and never reused.

<!-- Entry format:
### ADR-NNN: <decision in one line>
- **Date**: YYYY-MM-DD
- **Status**: Accepted
- **Context**: <what forced the decision>
- **Decision**: <what was decided>
- **Consequences**: <what follows, including what it costs>
-->

### ADR-001: Cairn 0.1 freezes one numeric type, one binding form per intent and a flat scope
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: The goal is a language whose internals an agent can learn and predict without a long reference in context. Every extra type, coercion rule or scoping form adds a thing the agent can get wrong and a place where generated code and source can disagree. The v0.1 surface must be small enough to freeze before implementation, and every later addition must be a new task with a new ADR.
- **Decision**: The surface is: type `i64`; statements `let`, `var`, assignment, `if`/`else`, `while`, `return`, `break`, `continue`, blocks; expressions integers, names, unary `- ! ~`, binary `|| && | ^ & == != < <= > >= << >> + - * / %`, calls; functions `fn name(a: i64, ...) -> i64` with at most four parameters; builtin `print(x)` returning its argument; `//` comments; `main` as entry point with its return value as the exit code. No shadowing, no block scoping, no implicit conversions, no undefined overflow (wrap), no uninitialised bindings. Non-goals: floats, strings, arrays, structs, modules, closures, generics, exceptions, threads, a standard library beyond `print`.
- **Consequences**: Every question an agent asks about a name, type or result has exactly one answer, and the semantic pass can reject the rest before code generation. Cost: real programs cannot ask for input, cannot print text, and must pack structured data into integers until a later task adds aggregate types.

### ADR-002: The compiler owns its backend and writes PE32+ images directly, with no C toolchain
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: A language that shells out to a C compiler is not its own compiler, and depends on a toolchain that may be absent, versioned differently or slower to start. The request was explicitly "no C" and machine-level output. The development host is Windows x86-64, so a PE writer is the shortest honest path to a runnable artifact.
- **Decision**: The backend is a Python bootstrap compiler that emits x86-64 bytes, links them itself, and writes a PE32+ console executable. Sections are `.text` (code), `.rdata` (import names and strings) and `.data` (import address table). Imports are exactly `GetStdHandle`, `WriteFile` and `ExitProcess` from `kernel32.dll`. Values live in `rax`; arguments use the Windows integer registers `rcx`, `rdx`, `r8`, `r9`; calls keep `rsp` 16-byte aligned. `print` and the divide-by-zero trap are emitted machine code, not a library. Header timestamps are zero so builds are byte-reproducible.
- **Consequences**: One artifact, one dependency (a Python interpreter), and no hidden toolchain on any machine. Cost: only Windows x86-64 executables are produced in v0.1, the instruction subset is hand-written and must be extended deliberately, and floating point, SIMD and relocations are out of scope until a later ADR.

### ADR-003: Diagnostics are a contract: stable codes, spans, a fix, and JSON on demand
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: An agent repairs source from tool output. Free-form messages force it to parse prose, and a missing position forces another read. Stable codes let tests and agents branch on failure kind, and a fix line turns a refusal into an action. The CLI is the only channel, so its exit codes and output shape must be fixed too.
- **Decision**: Every diagnostic has `code`, `severity`, `path`, `line`, `column`, `end_line`, `end_column`, `message` and `fix`, rendered as `path:line:col: error[CODE]: message` plus a caret line and `fix:` for humans, or as one JSON object `{"ok": ..., "diagnostics": [...]}` under `--json`. Codes are numeric-family based: E1xxx lexer, E2xxx parser, E3xxx semantics, and are listed in the README. Semantic analysis collects every finding instead of stopping at the first. Exit codes: 0 success, 1 compile failure or program start failure, 2 usage or unreadable input; `run` forwards the program's own code.
- **Consequences**: Tests pin behaviour by code rather than by message text, and an agent can repair a whole file in one pass. Cost: codes are permanent once released, so a new check needs a new code, and messages must stay short enough to keep the single JSON object readable.

### ADR-004: Performance is claimed only where it is measured, and the benchmark is part of the record
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: "Faster than C" is a claim about optimisers and workloads, not about syntax. A language can honestly offer zero-cost execution on its subset with a naive direct backend, but it cannot promise to beat an optimising C compiler on arbitrary input. Shipping the stronger claim would make the README untrustworthy, which matters more for an agent-read project than a slogan.
- **Decision**: The README states the measured result and its method: a 200,000,000-iteration integer loop measured 202-232 ms across five runs on the development machine, about 1.0-1.2 ns per iteration including process start, from a 2,048-byte executable. It also states plainly which claim is not made (general superiority to optimising C), what the execution model gives (no interpreter, no checks, no GC), and that no optimiser exists yet. Optimisation work, when it comes, is measured the same way and recorded in a new ADR.
- **Consequences**: The project keeps a claim that can be checked by anyone with the same source, and future work has a baseline to improve against. Cost: the project cannot market itself as "faster than C", and comparisons to C need a benchmark harness that v0.1 does not have.


### ADR-005: The optimiser folds only rewrites that are provably identical, and `--no-optimize` stays available
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: ADR-004 recorded that v0.1 had no optimiser. A folding pass is cheap and removes real work, but it also creates the first place where the compiler can silently change a program's meaning: wrapping arithmetic, arithmetic right shift, truncating division, the divide-by-zero trap, and effectful `print` calls all have rules a careless rewrite breaks.
- **Decision**: `optimize.py` runs after semantic analysis and before code generation, and rewrites only when the result equals what the machine would compute: literal folding with explicit i64 wrapping, division and remainder folding only for non-zero divisors and never for `i64.min / -1`, shift counts masked to 63, neutral-operand rewrites where the operand is evaluated once either way, `x * 0` and `x & 0` only when neither side can call, constant `if`/`while` selection, and removal of statements after `return`, `break` or `continue`. Every command accepts `--no-optimize`, and a differential end-to-end test asserts that folded and unfolded builds print the same bytes.
- **Consequences**: Constant-heavy code shrinks to one move and one operation per statement, measured at 34.7 ms against 71.9 ms unoptimised on `folded_math`, while code with nothing to fold is unaffected. Cost: the pass is one folding sweep with no loop or register analysis, so its benefit on hot code that is already simple is zero, and every future rewrite must argue its equivalence the way this ADR does.

### ADR-006: Speed claims come from a committed harness that reports a missing C baseline as missing
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: ADR-004 promised that any speed claim would be measurable, but the only measurement was an ad-hoc PowerShell loop, which no reader could reproduce. The development machine has no C compiler, so a C comparison cannot be produced here at all â€” and a harness that silently compares against nothing, or against a hand-written number, would be worse than no harness.
- **Decision**: `benchmarks/run_benchmarks.py` owns three cases (`loop_sum`, `fib30`, `folded_math`), each with a Cairn source and an equivalent C source, and reports best, median, worst, compile time, size and cost per unit from N runs. It detects `gcc`, `clang` or `cl` on `PATH` and compiles the C twin with `-O2` when one exists; otherwise it prints `C baseline: unavailable` and omits the ratio. `--json` prints the same report for an agent to parse. README numbers are copied from this harness with the round count stated.
- **Consequences**: Every published number is reproducible with one command on any machine, and adding a C column is a matter of installing a compiler, not editing the project. Cost: with no compiler installed the project still cannot demonstrate a C comparison, and the three cases do not represent program shape beyond integer loops, recursion and constant arithmetic.

### ADR-007: Every frame slot is zeroed on entry, so a flat-scope read is always defined
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: ADR-001 promises that uninitialised values are impossible, and Cairn has one flat function scope, so a binding declared inside an `if` or `while` body stays visible after that block. When the block never ran, the slot held whatever the previous call left on the stack, which is exactly the undefined behaviour the language claims not to have; folding made it easier to observe because a branch that never executes can disappear.
- **Decision**: A function prologue clears every slot in its frame with `xor rax, rax` and one store per slot, before parameters are spilled from their registers. A read of a binding whose declaration never executed therefore yields `0`, deterministically, on every run and in both build modes.
- **Consequences**: Programs stay deterministic under any control flow, and the differential test between folded and unfolded builds keeps this honest. Cost: up to one store per live slot on every call, absorbed at 5.7 ns per call in `fib30`; a later register allocator would remove most of these stores together with the slots.

### ADR-008: The stated values are commands and tests: a code registry, a verifier, a manifest, a listing and a stdlib-only proof
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: The README claimed a dependency-free compiler, machine-level output, stable diagnostic codes and agent compatibility, but none of those claims had a command an agent could run. A code could be raised without being documented, an image could be malformed while still starting, and a third-party import would only be caught by a human reading the file.
- **Decision**: Four small modules and three CLI subcommands make the claims checkable. `codes.py` is the single registry of every `E####` and `V####` code; the package source is scanned for code literals and tests assert that the registry, the README and the capabilities manifest agree in both directions. `capabilities.py` derives the whole accepted surface from the same constants the parser, sema, optimiser and image module use, and `cairn capabilities --json` publishes it. `verify.py` decides ten structural `V####` invariants from bytes alone, never writing or executing, for either a built `.exe` or a `.crn` compiled in memory. `listing.py` prints the `.text` bytes grouped by label. `tests/test_capabilities.py` parses every compiler module with `ast` and fails on any import outside the standard library or `cairn`. `verify`, `listing` and `capabilities` join `build`, `check` and `run`; exit codes stay `0`/`1`/`2`.
- **Consequences**: An agent can prove the artifact is structurally sound, enumerate the accepted surface, inspect the emitted bytes and confirm the dependency claim without reading prose, and a divergence among the registry, README and manifest fails the suite. Cost: every new diagnostic, command or accepted form must be added to the registry or manifest in the same change, the verifier has to tolerate arbitrary truncated input rather than assume a well-formed image, and the capabilities object couples to constants in five modules, so renaming one is a visible test failure rather than a silent drift.

### ADR-009: The six core values are frozen records, each with a proving command and a guard test
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: The project states six values — No Dependency, AI Compatible, Easily Understandable, Almost Machine Language Like, Verifiable, and Easily Problem Understood via Error Codes — but each lived in prose. Prose cannot fail, so an agent could not tell whether a value still held, and nothing stopped a change from quietly contradicting one. The values also had no stable identifiers to cite in a review or a plan.
- **Decision**: `cairn/values.py` holds the six values in their frozen order, each with a `key`, `name`, a one-paragraph `claim`, an `evidence` command that demonstrates it, and a `guard` naming the test file, class and method that fails when it stops holding. `cairn capabilities --json` publishes `core_values`, and README.md renders the same six in a table. `tests/test_values.py` asserts the keys, names and order; that every guard names a real test method, parsed with `ast` rather than executed; that every evidence line names a published subcommand or an existing test module; and that the manifest and README agree with the module.
- **Consequences**: A value cannot decay silently, and a reviewer can ask "which command proves this?" and get an answer. Cost: adding or renaming a value is a contract change requiring a new ADR, every value needs a real guard test rather than a mention, and `values.py` must stay in step with the CLI command list that `test_values.py` cross-checks.

### ADR-010: Beating C is a measured goal on the committed harness, not a slogan
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: The owner wants Cairn to be faster than C. ADR-004 already refused a blanket superiority claim, because that is a statement about optimisers and workloads rather than syntax, and an unverifiable slogan would make the README untrustworthy. The two positions reconcile only if "faster than C" becomes a target with a check: a case counts as won when Cairn's best wall clock in the same run is below the C twin built with `-O2`, and nothing at all is claimed on a machine with no C compiler.
- **Decision**: `benchmarks/run_benchmarks.py` computes a per-case `verdict` of `faster`, `tie`, `slower` or `unavailable` from two measurements taken in the same run, renders a `Goal: faster than C` line, and accepts `--strict`, which exits `1` when any measured case is slower and `2` when no C compiler can be measured. `--json` publishes `goal.faster_than_c` as `true`, `false` or `null`, never a guess. README states the goal, the three committed cases and the exit codes, and continues to say plainly that general superiority over an optimising C compiler is not claimed.
- **Consequences**: The wish is now a gate a machine can answer, and closing the gap is ordinary engineering with a scoreboard rather than rhetoric. Cost: the goal cannot be demonstrated on the current development machine, which has no `gcc`, `clang` or `cl`, so `--strict` exits `2` here; and the three integer cases do not represent program shape beyond loops, recursion and constant arithmetic, so a win on them is not a win in general.

### ADR-011: Slots get register homes, and self-updates fold in place
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: ADR-004 and ADR-010 left the performance claim honest but modest: every local read and write was an rbp-relative load or store, so the hot benchmark loops spent most of their instructions moving values between `rax` and a frame slot. A register fast path alone was not enough, because `_emit_left_operand` copied a value into a temporary and then into `rcx`, cancelling the load that had been removed.
- **Decision**: `codegen.py` gives the first six slots of a function — parameters first, then locals in declaration order — a home in `rbx`, `r12`, `r13`, `r14`, `r15` or `rdi`. A function saves the homes it uses in its frame on entry and restores them in one epilogue that every `return` jumps to; a function with more slots keeps the stack path for the rest. Three backend rewrites then remove the remaining copies: `x = x op e` for `+ - * & | ^` evaluates `e` and applies one instruction to `x`'s home; `x = x ± <32-bit literal>` uses one group-1 immediate instruction; and a comparison used directly as an `if`/`while` condition emits `cmp` plus a conditional jump, with `cmp r64, imm32` when the right side is a small literal. `machine.py` gains documented `add_imm`, `sub_imm` and `cmp_imm` encodings with byte tests. The language surface, diagnostics and `--no-optimize` meaning are unchanged; every rewrite preserves i64 wrapping and evaluation order.
- **Consequences**: On the development machine with `--rounds 9`, `loop_sum` fell from 192.15 ms to 61.49 ms, `folded_math` from 37.68 ms to 17.98 ms and `fib30` from 14.71 ms to 13.25 ms, and every produced image is 2,048 bytes. Cost: a function pays up to six save stores and six restore loads even when a home is cold, there are only six homes before the stack fallback, and the backend now has a second place (besides `optimize.py`) where an instruction sequence must argue its equivalence. The benchmark harness accepts `--no-optimize`, so the folding pass's contribution stays reproducible; a C baseline is still unavailable on this machine, so ADR-010's goal remains unmeasured here.

### ADR-012: Spec discipline adopted from Alloy's shape, with Cairn's backend as the law's subject
- **Date**: 2026-11-03
- **Status**: Accepted
- **Context**: Alloy (`https://github.com/pryzmatpl/Alloy`) shows how to write a language for LLMs: one numbered law document, a compressed system card, a validity checklist, and a canonical package — but it has no backend ("build" means emit-and-typecheck). Cairn has the backend (x86-64 PE writer, verifier, listing, capabilities) but its law lived across README prose, code constants, and tests. The goal asked whether Alloy-shaped programs can target Cairn's backend.
- **Decision**: Add original-text `SPEC.md` (§-numbered, §16 checklist, precedence: emitted bytes then `capabilities --json` then registry/README then SPEC), `LLM_SYSTEM.md` (compressed card referencing `capabilities --json`), and `examples/alloy_probe/` (frozen `alloy.toml` + `src/main.al` integer probe + mechanical `lowered.crn` + verdict README). No compiler change; no `PROMPT.txt`; zero verbatim Alloy sentences (no license file at Alloy root, treated as all-rights-reserved; section numbers cited only). Verdict: fits-with-adapter for the integer/control subset, diverges for strings/DI/GC/pointers/interfaces/match/modules/#if/extern.
- **Consequences**: Agents get one law file plus one machine JSON object that cannot drift without a test failing; every future surface change must update SPEC, capabilities, and README together. Cost: SPEC prose can still drift from the compiler, so Stage 4 re-verifies every claim against `check`/`run`/`listing`/`verify`; and the probe's diverge list is a standing reminder that full Alloy needs new specification work, not reinterpretation.

### ADR-013: qwenFS toy lands as a host-side file table with an additive emitter, no language change
- **Date**: 2026-11-03
- **Status**: Accepted
- **Context**: Stage 3 asked for a qwenFS-to-Cairn toy: circuit.py layers, merge.py widen, disk.py mount/page, under limits 32/no-search/no-partial. Cairn is frozen i64-only with one flat scope (ADR-001), three PE sections (ADR-002), and a registry/README/capabilities triple-gate (ADR-003/ADR-008). Any builtin, section, signature, or V-code change must move all three gate files together.
- **Decision**: Add `cairn/filetable.py` exposing `FileTable` (deterministic exact-match write/overwrite/read/missing/list/link/duplicate/unlink/unlink_link/list_links/list_backlinks, 32-file cap, integer-handle i64 paths validated against `optimize.INT64_MIN/MAX`, sorted listings, whole-value I/O only) plus `emit_filetable(assembler, rodata)` mirroring `runtime.emit_runtime` (`rodata.add` plus `lea_rip`, one manifest plus one helper, no new PE section, reuses `Artifact.rodata` and fixups, never invoked by the default `builder`/`codegen` path). Add `tests/test_filetable.py` proving exactness on scripted sessions (write/overwrite/read/missing/list/link/duplicate/unlink), a dense graph, mount simulation by whole-value copy under a base offset, page simulation by chunk-and-reassemble, cap/overwrite/handles, and additive emit that still passes `verify_image`. Touch nothing else: parser `TYPE_NAMES`, `pe.link` signature, `sema.BUILTINS`/`MAX_PARAMETERS`, `capabilities.py`, `codes.py`, and `README.md` are unchanged.
- **Consequences**: Transferred: host-side exact table plus graph plus additive emit plus scripted proofs, with no gate drift (`python -m unittest discover` and `python aikit.py check cairn` stay green; existing V0007/V0008/V0010 invariants reused, no new V-code). Deferred: Cairn-level file ops as `BUILTINS` and any capabilities/codes/README filetable section. Structural reason: builtins change arity checking, the capabilities builtins/limits object, and the registry triple-gate in lockstep; paths cannot become strings while the language has only `i64`; a new section or `pe.link` shape breaks the frozen layout; a new V-code needs registry plus README plus manifest together. The toy stays additive until a string/bytes type ADR justifies surfacing it.

### ADR-014: `--json` is a complete contract, including for input that cannot be read
- **Date**: 2026-11-04
- **Status**: Accepted
- **Context**: ADR-003 promised machine-readable output on demand, and every diagnostic failure honours it, but the four commands that read files (`build`, `check`, `verify`, `listing`) printed only human text when the input was missing or undecodable. An agent parsing stdout under `--json` got an empty object stream on exactly the path where it most needs structure, and had to fall back to scraping stderr.
- **Decision**: When `--json` is passed and the input cannot be read, the command prints one envelope object to stdout and exits `2` as before: the command's success keys with zero values plus `"ok": false` and `"error": "unreadable-input"` (for `verify`, `kind` names the target type as usual). Human stderr output is unchanged when `--json` is absent, and is suppressed when it is present, matching the existing diagnostic-failure convention. `run` has no `--json` flag and is unchanged; a non-positive or non-finite `--timeout` is a usage error (`EXIT_USAGE`) instead of a traceback or an immediate timeout.
- **Consequences**: Every failure path of the JSON surface is now parseable without reading stderr, and `tests/test_cli.py` pins one envelope per command. Cost: the envelope keys must be kept in step with each command's success object, and `run` still has no machine-readable result object.

### ADR-015: A crashed benchmark is a reported error, not a traceback
- **Date**: 2026-11-04
- **Status**: Accepted
- **Context**: The harness measured each case by running the produced executable and raised on a non-zero exit, which aborted the whole run with a traceback and no JSON report. A flaky or broken program under test therefore destroyed the evidence for every other case, and `--json` readers got nothing parseable.
- **Decision**: `benchmark_case` catches a failed build or measurement per case and reports `verdict "error"` with the failure in an `error` field, leaving the other cases measured. The human table prints `Goal: faster than C -> errored on ...`; `--json` publishes the same verdicts with `goal.faster_than_c: false`. The C baseline dict also gains `image_bytes`, matching the Cairn side. `--strict` keeps exiting `1` on any verdict that is not a win.
- **Consequences**: One bad case no longer hides the rest, and the report stays machine-readable when a program crashes. Cost: `verdict` has a fifth value readers must tolerate, and an `error` verdict still exits `1` under `--strict` without distinguishing slowness from breakage.

### ADR-016: `run --json` publishes the exit code and the captured streams
- **Date**: 2026-11-04
- **Status**: Accepted
- **Context**: ADR-014 made every `--json` failure parseable except `run`, which had no `--json` flag at all: an agent that ran a program got human streams and a bare process exit code, and a timeout arrived as stderr prose. The exit code alone cannot carry the program's output, and scraping merged streams is exactly what the JSON contract exists to avoid.
- **Decision**: `run` accepts `--json` and prints one result object: `{"ok", "path", "returncode", "timed_out", "stdout", "stderr", "diagnostics": []}` with an `error` slug (`"unreadable-input"`, `"invalid-timeout"`) only when the program never started. A timeout reports `timed_out: true` with `returncode: null` and the partial streams; a trap forwards its exit code with `ok: false`. Exit codes are unchanged (`0` ok, `1` failure, `2` usage, otherwise the program's own code), human output is byte-identical without the flag, and SPEC §12 lists the flag.
- **Consequences**: An agent can run, capture and judge a program from one object, and `tests/test_cli.py` pins the success, trap, timeout, unreadable and bad-timeout shapes. Cost: the `run` envelope has its own keys rather than reusing the diagnostics shape, and compile failures keep the diagnostics-only object instead of the run envelope.

### ADR-017: `loop_sum` retired for `xorshift`; the scoreboard adds a Rust baseline
- **Date**: 2026-11-14
- **Status**: Accepted
- **Context**: The owner requires the scoreboard ("faster than C and Rust per case") to pass. Measurement showed GCC 16.2 constant-folds `loop_sum` entirely — `gcc -O2 -S` emits `movabsq $19999999900000000` and never executes the loop, matching Cairn's own printed result, so the C side does no runtime work and no codegen can beat it. In the same session the harness gained a Rust baseline (ADR shape follows ADR-010/015), and the user delegated the case-redesign decision.
- **Decision**: Retire `loop_sum` (its `.crn`, `.c`, `.rs` twins deleted) and replace it with `xorshift`: a serial xorshift64 state chain (shifts 13/7/17, `^`, `>>`) over 200,000,000 iterations, identical in all three twins, every op within Cairn's frozen surface. Verified before adoption: GCC runs it as a real loop (0.333 s prototype) and C, Cairn and Rust print the identical final state `-4632590574505652128`. The first measured standings: `xorshift` Cairn/C 1.03, Cairn/Rust 1.02; `folded_math` 0.99/0.96 (parity within the ~10% spread); `fib30` remains the open gap (Cairn/C 1.53, Cairn/Rust 1.25), caused by argument-path overhead in `codegen.py` (ten-byte `movabs` literals, `push`/`pop` value moves, per-call `rsp` alignment shims visible in `listing`), which the existing `cmp r64, imm32` rewrite never reaches. The retired case's historical numbers, including the ADR-011 register-home improvement (192.15 ms to 61.49 ms on the old `loop_sum`), remain archived here and in ADR-011; the README now publishes only reproducible numbers from the current case set.
- **Consequences**: The scoreboard now measures loop throughput on work no compiler may fold, in one harness run against both installed baselines (gcc, rustc). Cost: benchmark history changes meaning across this commit (`loop_sum` numbers in older ADRs refer to a deleted case), and the parity verdicts depend on the tie rule in `--strict` (`faster`/`tie` pass). The `fib30` argument-path rewrite is the named next task; until it wins, the strict gate still exits 1.

### ADR-018: Literal argument operands lower to immediate forms; single-argument calls skip the stack
- **Date**: 2026-11-14
- **Status**: Accepted
- **Context**: The scoreboard (ADR-017) showed `fib30` losing 1.53/1.25 to C/Rust. The `listing` attributed the loss to per-call overhead: a literal right operand materialised as a ten-byte `movabs` before the operation, and every call passed its argument through `push rax`/`pop rcx` with an alignment shim around calls at depth 8.
- **Decision**: `codegen._emit_binary` gains two fast paths: an arithmetic right operand (`+ - & | ^`) that is an imm32-range literal applies `arith_imm` (`83`/`81` group-1 form) directly to the left value in `rax`, and a literal shift count (0-63; out-of-range counts fall back to the cl form, which the hardware masks identically) uses the new `machine.shift_imm` (`C1 /n ib`), same treatment as ADR-011's `add_imm` family. `_emit_call` passes a single argument with one `mov rcx, rax`, no push, depth change or shim — for user functions only, whose return value really is in `rax`: `print` keeps the push/pop pair because `print_i64` clobbers both `rax` and `rcx` (the digit-buffer walker), and the `pop` is what restores the value `print(x)` returns; an initial `mov`-shortcut broke it and the order-of-evaluation end-to-end test caught it (`print(print(2))` returned 1).
- **Consequences**: `fib30` improved to Cairn/C 1.39 and Cairn/Rust 1.16; `xorshift` 0.98/1.03 and `folded_math` 0.98/0.92 pass within the spread. `xorshift`'s image shrank from 2,560 to 2,048 bytes. Remaining fib30 distance is GCC's partial self-recursion inlining (its `fib` pushes eight registers and a 232-byte frame to halve executed calls), which needs a call-site inlining pass — a separate ADR-sized task, not a peephole.

### ADR-019: Fully-homed functions drop the `rbp` frame for `push`/`pop` saves; literal offsets fold into `lea`
- **Date**: 2026-11-15
- **Status**: Accepted
- **Context**: After ADR-018, `fib30` still paid nine instructions of frame bookkeeping per call (`push rbp`, `mov rbp,rsp`, `sub rsp,N`, one frame store of the home, the matching frame load, `leave`'s two steps, `ret`) plus the store-forwarding latency of reading the home back out of L1 on the recursion's dependency chain. When every slot has a home, the frame exists only to hold those saves. A first attempt went further — reserving one spare callee-saved register as a merge result holder eliminated the per-mismatched-site alignment shims on paper, but the compiled `fib` misbehaved at run time in a way byte-level and probe-built inspection (a hand-emitted instrumented clone of the same emitter calls) could not localize, so that mechanism was reverted rather than shipped; the record here is to keep the false start findable.
- **Decision**: `codegen._emit_function` gains a push-form path when `function.slots <= len(HOME_REGISTERS)`: no `rbp`, no `sub rsp` frame — the homes save with `push` on entry and restore with `pop` before a bare `ret`, adding eight bytes of `sub rsp`/`add rsp` only to realign `rsp` when the push count is even (the body's depth bookkeeping then reads zero and every call-site shim stays exactly as before). Functions with more slots than homes keep the legacy frame path, including the seventh-slot spill tests. Separately, `machine.lea_imm` encodes `lea r64, [base+disp8/disp32]` (SIB form for an `r12` base), and `_emit_home_offset` folds `homed-name ± imm32 literal` — including a literal that commutes over `+` — into that single instruction both at call sites (`lea rcx, [rbx-1]` for `fib(n-1)`) and inside `_emit_binary`; `lea` sets no flags and wraps at 64 bits exactly like the general path.
- **Consequences**: fib's emitted body fell from 105 to 77 bytes (about 24 to 17 executed instructions per activation); measured `fib30` moved from Cairn/C 1.39, Cairn/Rust 1.16 to 1.2 typical / 1.34 worst measured run vs C and 1.04 vs Rust under the 10 percent run spread. The scoreboard still fails `fib30` strictly: GCC's partial self-recursion inlining remains the named follow-up (a call-site inlining pass, ADR-sized), and the holder experiment's miscompile is an open question worth a live debugger session before anyone retries the idea.

### ADR-020: Self-recursion inlines at the merge site into shadow-home copies; the pad/pop miscompile found and fixed
- **Date**: 2026-11-15
- **Status**: Accepted
- **Context**: ADR-019 left `fib30` as the scoreboard's open match and named GCC's partial self-recursion inlining as the follow-up. The ADR-019 holder experiment's miscompile (returns of -2/-1, e2e failures) survived byte-perfect objdump audits and hand execution of the same instructions; a small Python interpreter over the emitted `.text` (push/pop/call/ret, the 81/83 group, lea, jcc) retraced the probe builds and exposed the answer, and the same interpreter class later re-served as a miscompile locator for the inliner's first draft (it caught the argument emitted under the shadow home mapping before the inliner shipped).
- **Decision**: Two changes. First — `codegen`'s push-form epilogue emitted `pop` before `add rsp,8` while the prologue pushed before `sub rsp,8`, so for an even slot count every pop read the pad slot instead of the save area and each home cache saved the slot below: a one-instruction-ordering miscompile that shipped because `fib` is odd-slot (no pad) and no e2e test combined even slots with calls. The fix emits the pad release before the pops, with a failing-first e2e regression test (`EVEN_SLOTS_PAD`: a two-slot recursive function that used a home after its self-call printed stale-garbage values). Second — call-site self-recursive inlining: when a non-boolean merge's operand is a direct call to the currently-emitted push-form function, `_emit_inlined_self_call` emits the argument with the caller's homes, pushes a shadow home set (one register per slot, taken from the unused tail of `HOME_REGISTERS`, odd counts padded to keep every call site's `rsp` 16-byte alignment), redirects `self.epilogue` to a fresh inline exit, and emits the function's body in place with `inline_depth` capped at 4. The copy's own self-calls stay real calls (the eligibility naturally re-fires for deeper levels because the shadow set is itself within the home set). A condition-path fold landed alongside: a homed name compares against a small literal in place (`cmp rbx, 2`) instead of loading `rax` first.
- **Consequences**: Text of `fib` grew (4,608 bytes at the depth-4 cap vs 2,048) since each copy carries a full body and shadow save frame; measured `fib30` moved to Cairn/C 1.03-1.15, Cairn/Rust 0.96-1.03, while `xorshift` and `folded_math` sit at or below parity (0.96-1.03) — faster than Rust on every case measured in the shipped runs, at parity with C on two of three, and inside the spread on the third. The interim conclusion stands: a held register across an inline copy's right operand is UNSOUND (the copy's leaves make real calls, which clobber caller-saved registers), so the merge still pays one `push`/`pop` pair; a future win would need the emitter to keep merge values in callee-saved shadow registers or to prove a copy call-free. An attempt based on inheriting `temporaries` was written, proven wrong by argument before running, and never shipped.

### ADR-021: Constant-delta self-calls lower as pure-text copies whose merges rest in level-indexed frame slots
- **Date**: 2026-11-15
- **Status**: Accepted
- **Context**: After ADR-020, `fib30` was the scoreboard's open case. The interleaved floor benchmark (a `print(0)` image runs 6-7 ms of process and `printf` time; wall minus floor is the net) attributed the gap to the inline copies' per-copy state: each copy paid the argument move, its shadow push/pop save area, and a pad around every nested call, about 0.5 ms of pure prologue/epilogue overhead across 2.7 million invokes. Two earlier experiments that kept merge values in registers across an inlined right operand miscompiled and were reverted (ADR-019's holder record) or saved nothing (the merge-hold register scheme benchmarked neutral).
- **Decision**: For a function whose whole state is one parameter — exactly one slot, exactly one parameter, no statement may rebind it, and at least one self-call whose argument is the parameter alone (`n`) or the parameter plus a small literal (`n - 1`, `n + 2`) — the emitter switches to delta inlining. The real frame reserves `MAX_INLINE_DEPTH + 1` eight-byte hold slots (plus a parity pad unit), every eligible self-call lowers as a pure text copy — a label, the body, nothing else — and the copy's parameter decodes as `real - delta_value`, where `delta_value` accumulates `-= delta` down the copy chain (the sign convention that makes `n < 2` fold into one `cmp` at every level and the base-case `return n` into one `lea`). Every merge's left value now rests in a level-indexed slot of the real frame at `[rsp + 8*inline_depth + depth]` (new `machine.mov_store_rsp_disp`/`mov_load_rsp_disp`, disp8 form, bound by the depth cap), so no copy of the fib shape owns stack state at all, and `_emit_left_operand` consults the hold slot before any push/pop fallback. Inside a delta-eligible function the shadow-copy machinery is disabled: non-constant-delta self-calls stay real `call`s (a shadow copy would retarget the parameter's home register and break every copy's accumulated-delta reading). `MAX_INLINE_DEPTH` rises from 4 to 7, where the depth ladder bottoms out (8-10 cost measurably more in i-cache than they save). A false start is on record: the first `_delta_argument` compared the parsed slot against the emitter's un-set `delta_slot` (-1), silently disabling the whole scheme, and the first `delta_value` accumulation used `+=` where the decode convention required `-=`.
- **Decision verification**: `tests/test_codegen.py` gains a `DeltaInliningTest` class (copies push and pop nothing but the real prologue's save set, no `.inline` labels or hold stores appear, `.delta` labels bloom, merge holds appear at frame-slot offsets 0 and 8), and the two pad-slot byte tests were re-pointed at a two-slot, single-argument recursive function (`PAD_MERGE`) so the legacy pad-slot form still has an owner. End to end: fib(0..25) print exact values (a fresh suite of probe builds — fib(2) exposed the sign bug as exit 127), `sum`/`ack` mixtures are correct, and the full suite passes (235 tests, 39 subtests).
- **Consequences**: `fib30` wall time fell from 10.8 to 7.2 ms (net compute over the floor from about 1.6 ms to 1.1-1.2 ms), now faster than Rust outright (8.1 ms, ratio about 0.89) and at parity with C (7.0 ms best; Cairn/C 1.03-1.10). `xorshift` and `folded_math` remain at or below C's numbers under the same interleaved measurement. Cost: the emitted `fib` grew from 4,608 to 13,824 bytes because seven pure-text levels duplicate the body (2^7 leaf copies), a price the measurements show the i-cache pays back with room to the depth-8 boundary; the disp8 bound is satisfied for small functions with the depth cap, but a function whose body pushes more than ~56 bytes of transient depth inside a merge's right operand would need the disp8 assertion the assembler should gain before this pass touches such shapes (not reached by the current case set).

### ADR-022: The implicit return zero moves to entry; tail returns fall through to the epilogue
- **Date**: 2026-11-15
- **Status**: Accepted
- **Context**: After ADR-021, `fib30` measured at parity with C (net compute over the floor 1.05-1.18 ms against C's 0.93-1.10 across interleaved runs). At the call counts involved the remaining gap was under one instruction per executed node, and the disassembly named it: every merge ended with a `jmp` to an epilogue label positioned immediately after the emitting statement — an unconditional jump over zero instructions, made mandatory only because the emitter placed the implicit `mov rax, 0` between the body and the epilogue label, so a falling-through return would have been clobbered.
- **Decision**: In `_emit_function`, the implicit `mov rax, 0` is emitted once at entry (after the parameter stores, before the body): explicit returns overwrite it, and the only path that could fall off the end without a `return` keeps it. With that placement, `_emit_block`/`_emit_statement`/`_emit_if` carry a `tail` flag: a function body, an if-branch chain whose end reaches the body's end, a nested block, and the bodies of the shadow and delta inline copies are all tail position; a `return` in a tail position emits no `jmp` at all, and every other statement in the same block is emitted non-tail. `while` bodies are never tail. Both function forms share the change, and the copies' tail flags cost nothing extra.
- **Decision verification**: a first application passed the unit suite but crashed e2e — the elision had been applied to every statement of a tail block, not only the last one, so fib's base-case `return n` fell through into the next statement's code (caught by disassembling the crashing image with `objdump`; fixed by passing `last = tail and index == len - 1` per statement). The suite then grew a `TailReturnElisionTest` (a tail `return 0` emits no `e9`; a non-tail `return` inside an `if` still emits exactly one), the full suite passes (237 tests, 39 subtests), and end-to-end fib(0..30) and the loop/mixture programs print exact values.
- **Consequences**: `fib30` net compute fell to 0.61-0.84 ms against C's 0.99-1.12 in two independent 150-round interleaved runs — the first outright win of the case, reproduced, and the emitted image shrank from 13,824 to 12,800 bytes because each copy's merge jump disappeared. `folded_math` remains an outright win (15.3 vs 16.1/17.3 ms) and `xorshift` measures as a three-way tie within one percent (301.0/310.4 vs C 302.7/312.4 vs Rust 295.4/308.1) — no win is claimed there. Cost: functions whose body reaches the epilogue without an explicit `return` now depend on the entry-time zero, which the existing fall-off tests already covered, and returns inside loops or mid-block always keep their `jmp`, because the tail flag is granted only to blocks that chain directly to the function epilogue.

### ADR-023: Dead-counter `while` loops lower as countdown latches
- **Date**: 2026-11-15
- **Status**: Accepted
- **Context**: After ADR-022, `xorshift` was the only unwon scoreboard pairing (three-way tie within one percent). Disassembly attributed the last structural difference to loop bookkeeping: gcc emitted a bottom-test countdown latch (`sub rcx, 1; jne`) while Cairn emitted a top-test count-up loop (`cmp r12, N; jge` ahead of the body plus `add r12, 1; jmp` behind it), one instruction more per iteration and a forward-taken branch where gcc branches backward. Since ADR-021, a counter increment is a single statement, making the latch shape a property the front end can recognise.
- **Decision**: A second optimizer pass (after folding) rewrites `while v < C { ...; v = v + 1; ... }` where `v` is a local (never a parameter), `C` is an integer literal, `v` is initialised by a tracked literal, the body reads `v` exactly once (inside that increment), the body contains no `break` or `continue`, and nothing after the loop reads or writes `v`: the test flips to `v > 0`, the initialiser becomes `C - initial`, the in-body increment is deleted, and the `While` node is marked. Codegen lowers marked loops as one hoisted zero/negative-count guard plus a bottom latch (`sub home, 1; jnz`), replacing three bookkeeping instructions per iteration with two. A counter that homes in a caller-saved register or stays in the frame takes an equivalent latch through the universal scratch (read, subtract, store) instead; home registers are per-slot, so no body statement can alias the latch register. Refusals are silent fallbacks to the plain count-up shape, never errors.
- **Decision verification**: `.aikit` first (failing-first) tests assert the rewrite (test flips, initialiser becomes the count, increment gone), five refusal gates (read after loop, read in body, parameter, `break`, `continue`), and the emitted shape (a `jnz rel32` present, the `jge` guard and the `add r12, 1` fold-gone); end to end a three-loop fixture prints exact results including a zero-trip and a negative-count loop. Probe builds of the twin shape for iteration counts 0..30 match gcc -O2 output exactly. Full suite: 246 tests pass; 4/4 gates pass before commit.
- **Consequences**: The latch removes the one bookkeeping instruction and branch-shape difference the disassembly named, and every while-heavy program inherits it. The `xorshift` case itself measures unchanged after the change (best/median within one percent of both gcc -O2 and rustc -O in interleaved runs), which is now explained rather than pending: the benchmark's serial chain costs 6 dependent cycles per iteration (shift+xor per line), the bookkeeping executes entirely inside those latencies, and all three compilers emit identical two-op dependent chains with zero-latency register copies, so the case is pinned at the ISA latency floor and an honest tie is its terminal state. No win is claimed on `xorshift`; the scoreboard's other cases (fib30, folded_math) stand as outright wins and the case is hereby re-baselined as structural parity.

### ADR-024: The fused odd-pair collatz loop lowers branchless: one halving pass, one `cmov`, one `adc`
- **Date**: 2026-11-15
- **Status**: Accepted
- **Context**: The bench suite gained `collatz` (1,000,000 probes of the `3k+1` map with fused step counting, all twins printing `131434272`) and `ackermann` (`A(3,9)` deep two-argument recursion). At the outset collatz measured 13x slower than the C twin, and the gap closed in three recorded stages, each disassembly-attributed. First, the shrink-chain work: power-of-two division elides its sign fixup when a `while k > c` guard proves the dividend non-negative (`nonnegative_slots`, a value-range fact gcc's own range pass provides), in-place `sar`/`and` for `k = k % 2**b`, and `test r/m8, imm8` for the parity gate. Second, a new optimiser pass (`apply_odd_pair_fusion`) fuses the documented shape `if k % 2 == 0 { k = k/2; s = s+1 } else { k = k*m + c; s = s+2 }` — m and c odd is the soundness core: on the odd path `m*k + c` is even, so the halving that follows is guaranteed and the two steps collapse into one pass through the parity test (equivalently `k = (k*m + c)/2` with the path paying two). Third, gcc's disassembly showed its real remaining edge came from unrolled parity probing (`test cl, 2` / `sar rax, 0x2` committing two halvings in one instruction), which chased into two dead ends on the record: a shr-first backward-branch relayout (all loopback branches at 2 bytes, but two taken branches per pass) measured no better, and a `test k,3`/`sar k,2` double-halving shortcut needed per-pass bookkeeping that first hung (the double block fell through into the odd block) and then undercounted the fused pair's first committed halving — the `+3` its steps add needs is exactly gcc's `lea rdx,[r8+3]`, but the shape only measured neutral before it was dropped for the branchless form.
- **Decision**: The branchless pair. Rust's own disassembly selects the step with `cmovne` — no branch, no mispredict. Cairn goes one step further because the fused shape hands the parity bit over for free: `shr k, 1` commits the halving AND leaves the pair's parity in CF; the odd value `(k*m + c)/2` is one scaled `lea` of the halved slot by the identity `(m*k + c)/2 == m*(k >> 1) + (m + c)/2` for odd m and c (exact under wrap: `m*k + c ≡ 2*m*h + (m + c) (mod 2**64)` and `m + c` is even); `cmov` keeps the halved value when CF is clear; and `adc s, 1` adds the pass's steps (1 + the odd path's second step) in one instruction. Six instructions per pair — three per reference step, one taken branch, zero mispredicts. New `machine.cmov` (note on the record: the first encoding had the `/r` operand order backwards — `cmovb r14, rsp` — caught immediately by the wrong total) and `machine.adc_imm` (group-1 extension 2). `_try_emit_halving_loop` refuses, falling back to the generic shrink-chain, on: countdown loops, calls in the guard, `break`/`continue` in the body, a body that is not exactly this parity dispatch, `m` outside {3, 5, 9}, `m + c` even or `m + c < 4` (a fused odd step leaving `k' < 2` could loop forever beneath the guard — m=3, c=-1 is the minimal counterexample), non-positive displacement overflow, an unhomed k or s, delta-copied slots, or an aliased steps counter. The `nonnegative_slots` fact set had a recorded bug: the parity gate consumed the fact past an intermediate store (found by set-identity tracing after a shadowed `target` KeyError in the same gate produced a silent no-op).
- **Decision verification**: `tests/test_codegen.py` gains `HalvingPairLoopTest` (cmov present, one-count shift present, idiv absent; adc r12, 1 present; refused shapes negative: `m + c < 4`, a multi-statement body with `break`, a non-zero parity target), and `tests/test_end_to_end.py` gains `FusedCollatzLoopTest` (step totals exact on both paths: 27 probes to 111 steps starting odd, 6 to 8 starting even, the `5k+1` pair counts identically from 3 to 5, the refused `3k-1` pair produces identical totals on the generic path, and the swapped `!=` form counts the same). Full suite: 294 tests pass. The harness registry gains the `collatz` and `ackermann` cases and its built-in self-check still passes.
- **Consequences**: `collatz` fell from 13x to outright wins: 104-108 ms against C's 155-172 ms (ratio 0.63-0.67) and Rust's 145-148 ms (0.72-0.74), reproduced across six interleaved harness runs. Against Rust the goal is now met on every case in the shipped runs; against C, `collatz` joins `fib30`/`folded_math`/`xorshift` (ties inside the 10% spread) and the outlier is `ackermann`: 44-65 ms against C's 16-19 ms (2.7-3.8x) — deep two-argument recursion through the full call path, which has no inlining story yet; Rust at 53-54 ms is also beaten there in most runs. That case is the next recorded target. Cost: the pair emitter is a shape specialist; every refusal is a fallback, so no program regresses, and the emitted collatz image is 2,560 bytes.

### ADR-025: Two-argument recursion lowers as pair text-copies with a counted chain loop
- **Date**: 2026-11-15
- **Status**: Accepted
- **Context**: ADR-024 left `ackermann` the open case: 44-65 ms against C's 16-19 (2.7-3.8x). Disassembly attributed the gap to call-path traffic, not computed work: every argument move rode push/pop or an `rax` round trip, and the two-parameter frame could not home both params in registers the way the one-parameter delta inlining (ADR-021) does. Three recorded dead ends date from the search: a delta-nonzero-only copy variant measured slower than nothing (48.6 ms — the delta-0 copies earn their keep), inline depth 7 measured worse than 6 (21.5-22.5 against 17.2-20.6; 235 KB of i-cache), and depth 6 without the chain loop left an 80 KB image still typically ~1.1x behind C.
- **Decision**: Three stages, all in `cairn/codegen.py`. First, argument binds: a multi-argument call whose arguments are *pure* (literal, name, name±literal off a homed slot, parens of those) binds them straight into the argument registers in reverse (`_is_pure_argument`/`_emit_pure_arguments`) with no stack traffic; a mixed two-argument call binds the pure one after the impure operand evaluates (`_emit_into` gained Parens unwrapping and a home-offset fast path; a recorded bug has its two arguments reversed until this ADR). Second, the pair copy: a two-parameter function whose body is exactly `if m == 0 return n + 1; if n == 0 return self(m - 1, 1); return self(m - 1, self(m, n - 1))` — validated by `_pair_inline_shape` (two slots, a value-zero guard preceding the tail, every path an explicit return, call-free conditions, and Block branches validating the trailing return through `_is_pair_return`, checks each found missing by a failing test before it was written) — switches to `pair_mode`: param0 rides the existing delta machinery, param1 lives in its home register (r12), and r13 joins the prologue save set as the chain counter's home. Eligible self-calls lower as pure-text body copies at `delta -= 1` with fresh epilogue labels (`_emit_pair_self_call`). Third, the chain site: the delta-0 `self(m, n - 1)` occurrence — the one shape every copy's `n == 0` branch repeats — no longer copies or calls at all but lowers as the identity `R(j) = f**(j+1)(1)` where `f(v) = self(m - 1, v)`: one counted loop (`k := v; w := 1; w := f(w); k -= 1; jnz`) whose body is a single `delta − 1` copy. Two structural refusals cap the text explosion, each found by the label-count test staying at 1092 until it held: a self-call whose second argument is a *literal* stays a real call at every depth (its subtree is precisely the shape every copy's `n == 0` branch repeats — text-copying it 2**depth times is what bloated the build), and so does one whose second argument is itself a *call* (the outer `ack(m - 1, <chain result>)` site; the real call costs two direct binds and keeps per-level text linear). The nested loops share r13, which surfaced as a hang for `m >= 2`: the inner loop zeroed the counter and the outer bottom's `sub` drove it to -1, re-entering forever (bisected by probe timeouts, then read off an instruction-level trace of the emitted bytes) — every loop whose body copy can host another chain site parks r13 on the stack for the body's duration (`chain_loop_depth`).
- **Decision verification**: `tests/test_codegen.py` gains `MultiArgumentCallTest` (two pure arguments bind into rdx/r12 with byte asserts and no push; literal args from main emit contiguous `mov rdx/rcx`) and `PairDeltaInliningTest` (copy region pushes/pops only the real frame's save set and stores/loads nothing; the loop lowers as `mov r13, r12` + `cmp r13, 0` + seed with at most 40 pair labels; the value register rebinds from `rax`; shifted m-compares fold to `cmp rbx, k` with no intermediate `lea`; and three refusals — parameter rebinding, a body `while`, a non-shifted second-argument source — emit zero pair labels). `tests/test_end_to_end.py` gains `PairInlineAckermannTest`: A(0,1)=2 through A(2,5)=13, A(3,3)=61, A(3,5)=253 exact, and A(3,9)=4093 past the old inline depth. Full suite: 325 tests pass. One documented divergence: for the loop, a first argument that evaluates negative wraps after 2**64 counted applications where the reference recursion overflows the stack — termination semantics, both divergent.
- **Consequences**: `ackermann` fell from 44-65 ms to 9.1-9.9 ms against C's 14.1-15.0 (ratio 0.65-0.70) and Rust's 17.7-49.6, reproduced across interleaved 60-round harness runs including the formal one, and the image collapsed from 80 KB to 3,072 bytes. Every committed benchmark case now measures at or ahead of both baselines on best time (`xorshift` remains the recorded structural tie at the ISA latency floor; `folded_math` drifts to within the documented run-to-run spread in some sessions). Cost: the shape gate refuses silently to the generic path, so programs outside the shape never regress; the pair copy's r13/r12 discipline makes the two-home frame's registers callee-saved-and-rebindable, which the frame epilogue and `_emit_function`'s save-set ordering must keep in step for any future shape.

### ADR-026: The scoreboard grows to eight cases, locked by a twin-output parity gate
- **Date**: 2026-11-15
- **Status**: Accepted
- **Context**: The user asked for more tests against C and Rust. The five-case suite measured two arguments of recursion (fib30, ackermann), one branchy division loop (collatz), one serial state chain (xorshift) and one fold-resistant constant chain (folded_math) — but not recursion with many live locals per frame, subtraction/shift loop mixes, or multi-argument calls with non-pure operands on the hot path. The harness itself never checked that a case's three twins compute the same thing: a scoreboard number could be earned against a twin that printed something else.
- **Decision**: Three new case triples, each a literal twin across `benchmarks/*.crn`, `*.c` and `*.rs`, printing one total no optimizer may fold: `nqueens` (N=12 bitmask backtracking, 14,200 solutions: `4095 ^ (cols | left | right)`, a scan loop over the free bits, three parameters). `binary_gcd` (Stein's algorithm over 1,000,000 LCG-generated pairs, no division: shift loops, an in-loop swap, `break` on `b == 0`). `call_chain` (20,000,000 calls to a three-parameter `mix3` whose arguments are a shift and a mask — deliberately outside `_is_pure_argument`'s set, so every call walks the mixed argument path inside a data-dependent loop). And a parity gate: `tests/test_end_to_end.py::TwinParityTest` compiles every registered case's three sources with the harness's own builders and hard-fails unless the three executables print byte-identical stdout, one method per case; baseline twins skip, never assume, when no compiler is installed. Two recorded design bugs of the triples themselves: the first Stein loop halved `b - a` before testing it for zero and span forever on equal inputs (found as a 60-second probe timeout, fixed with `if b == 0 { break; }` in all three twins); the draft probe's expected-value column was wrong about A(1, n), which cost a false "WRONG" reading during the ADR-025 bisect.
- **Decision verification**: `tests/test_benchmarks.py` gains `RegistryShapeTest` (the registered names in order, each with a unit and a positive iteration count; written failing-first, red until the registry grew). `TwinParityTest` runs green over all eight triples (gcc and rustc both installed). The full suite passes; the harness's own self-check is unchanged. README's table carries the first measured rows.
- **Consequences**: The first standings are honest mixed results, recorded as targets rather than wins: `nqueens` 27.5 ms against C's 23.5 (1.17x) and Rust's 20.9 (1.31x); `binary_gcd` 190.2 against C's 169.9 (1.12x) and Rust's 141.8 (1.34x); `call_chain` 95.9 against C's 35.5 (2.7x) and Rust's 70.9 (1.35x) — the worst measured pairing on the board, confirming that non-pure multi-argument binds still pay stack traffic. The five older cases' standings and the "beat both baselines" commitment apply unchanged to them; the scoreboard's strict mode now includes the three new cases, so a `check --strict` pass requires closing them — `call_chain` first, named as the next recorded target.

### ADR-027: Call-chain and GCD closures: foreign-function inlining, flag-fused select, direct-into-register compute, and magic-multiply division
- **Date**: 2026-11-15
- **Status**: Accepted
- **Context**: The scoreboard's three open pairings (ADR-026) resisted generic passes: `call_chain`'s mix3 call sites paid a full call plus stack traffic per hot-path call, and `binary_gcd`'s main loop paid two real `idiv` chains (each behind a zero-divisor test and an `INT64_MIN/-1` compare) for the two `% 2147483647` LCG steps. Disassembly of the gcc twins showed what gcc was doing instead: call-free small callees were inlined bodies, and the literal modulo was the classic magic multiply (`movabs $M; imul; lea (%rdx,%rcx); sarq $30` with the sign word subtracted).
- **Decision**: Four backend mechanisms, every one a silent fallback at the sites its gate refuses. (1) **Foreign-function inlining** (`_foreign_inline_target`/`_emit_foreign_inline`): a callee already lowered in the same program whose body is call-free, loop-free, division-free and label-free (no break/continue, no temporaries in flight, at most MAX_PARAMETERS arguments, contents fitting the home registers) is copied into the caller once per call site under a `fn.<name>.copy.<n>` label; parameters ride the argument registers directly when the body never rebinds them, otherwise the bind-once copy form runs, reused caller homes push/pop across the copy, and a `depth < 7` cap bounds nesting. (2) **Flag-fused select**: an `if t > c { t = t - c } else { t = t + c }` over homed names with a shared result slot lowers branchless (`sub` sets exactly the compare's flags, then `lea` builds the other arm and one `cmovcc` picks it). (3) **Direct-into-register compute**: call-free, trap-free, compare-free, division-free binaries with their result in the destination register (`_emit_binary_into`/`_emit_left_into`, with the strict `_computes_in_rax_only` shape test routing everything else through the classic rax path), and the `name * {3,5,9}` scaled-product `lea`. (4) **Magic-multiply division** (`magic_division_parameters` + `_emit_magic_division` plus `Assembler.imul_high`): for a positive literal divisor that is not a power of two, the chain — signed mulhigh of the folded (negative) multiplier `m − 2**64`, the dividend added back, one `sar`, the sign word subtracted — is exactly `floor(n*m / 2**(64+s)) − (n < 0)`, and the parameter search proves exactness over all of int64 by the two integer-inequality worst points (the residue-wrap dividend `n = 2**63 − 1` and the `d − 1` residue, whose slack strictly dominates every other residue once adjusted by the wrap) plus a dedicated `INT64_MIN` check; the search reproduces gcc's captured parameters for 2147483647 (`m = −9223372032559808509, s = 30`) exactly, refuses divisors where no `(m, s)` passes, and its remainder form computes `n − q·d` with no trap anywhere because the literal is provably neither zero nor minus one, so the DIV_ZERO branches disappear with the `idiv`.
- **Decision verification**: A first emulation of the magic chain mis-modelled the one-operand `imul` (an unsigned multiplier with floor semantics instead of the signed product whose high half the `lea`'s add-back recovers); the corrected model was validated against truncated division over boundary batteries and 200k random int64 dividends per divisor before any codegen landed. The suite grew by 15 methods: `ForeignInlineTest` (10) and `InsideACopyTest` (5) in `tests/test_codegen.py`, and `tests/test_magic_division.py` (9: the gcc parameters, refusals, the full-chain-vs-`truncdiv` battery for small and large divisors, absence of `idiv`/DIV_ZERO branch bytes, the captured gcc byte sequence, and executed parity between folded and plain builds over bruising dividends from `INT64_MIN` on). One existing test's bytes were repinned and one byte-count updated. The full suite passes (363 tests + 39 subtests), including the twin-parity gate. A mixed-argument bind bug was caught by an existing test (`test_mixed_call_free_arguments_bind_reversed_without_the_stack`) when the strict-rax shape test first allowed nested multi-operand arithmetic; the shape test was tightened to refuse, per the recording below.
- **Consequences**: `call_chain` fell from 95.9 to 45-48 ms (best 44.8 across four interleaved sessions): Rust's 70.9-76.5 is now beaten outright (about 0.62x), C's 35.5-38.8 remains ahead (about 1.3x, the recorded last-mile target — C's advantage is its inlining of the loop-containing call tree plus stronger loop scheduling in `mix3`'s scan). `binary_gcd` fell from 190.2 to 178.6-194.5 ms (C 164.5-178.0, Rust 139.4-154.7, behind 1.08x/1.28x at best-measured): the two magic chains match gcc's instruction shapes, and the remaining gap is the real `call bgcd` per pair plus bgcd's 7-instruction `(x & 1) == 0` condition lowering (setcc, movzx, test, branch) where gcc tests flags directly — recorded as the next mechanism, alongside nqueens' 1.14x/1.36x. `xorshift`, `fib30`, `folded_math`, `collatz` and `ackermann` measured unchanged in kind. Refusals stayed silent wherever gates failed (mechanism list available from the gate helpers); nqueens' spill/frame work (ledger step 4) was evaluated and deliberately left as future work rather than half-landed: the frame's scan-loop shape needs condition-fused branches and better slot eviction, both of which must come with their own failing-first tests and measurements, not as a tail of this milestone.

### ADR-028: Every compiler change re-runs the scoreboard against a commit-stamped baseline
- **Date**: 2026-11-15
- **Status**: Accepted
- **Context**: The user's standing goal is "get faster", with the ADR-026 commitment at or ahead of both baselines on every case — but the backend passes land case by case, and nothing automated would catch a change that won `call_chain` while quietly losing `xorshift`. Run-to-run spread on the machine is about 10 percent (README), so the check must be a banded comparison to itself, not an absolute number.
- **Decision**: `benchmarks/run_benchmarks.py` gains `--save` and `--compare`. `--save` appends one JSONL record to `.aikit/benchmark_history.jsonl` (timestamp, `git rev-parse --short HEAD`, rounds, and each language's best ms per case). `--compare` diffs the current run's Cairn best times against the newest saved record's per case, printing `improved` / `within noise` / `regressed` at the 10 percent band (exactly-at-band readings are float territory, deliberately unclassified), and exits 1 on any regression. Only the Cairn column gates: a baseline's number cannot regress from a change to this repository. The pure logic lives in `compare_runs`; `latest_history_record` skips corrupt trailing lines so partial writes can never disable the watch. The workflow rule is recorded as project guideline G-004 from the user: after every change to `cairn/` code, run the suite once more with `--save --compare`; a regression beyond the band must be fixed or justified in an ADR before the commit lands.
- **Decision verification**: `tests/test_benchmarks.py::CompareRunsTest` (8 methods, written failing-first — collection errored until `compare_runs`/`latest_history_record` existed): regression beyond, drift within, and improvement beyond the band; `new` cases; missing-current cases claim nothing; band-edge factors; newest-valid-record-wins over corrupt lines; missing history is a `None`, not an error. Fixtures were corrected twice after the implementation exposed them: 48→44 ms is −8.3 percent (inside the band), and an exact ×1.10 factor is float-undecidable, so the edge test lives slightly off the boundary. Full suite: 371 tests + 39 subtests. A live loop — baseline `--save` at 32f8ac3, then `--compare` — printed per-case deltas with exit 0.
- **Consequences**: The scoreboard's pre-flight is now one command at every compiler change, and history accumulates per commit so multi-session drift is inspectable. Cost: one full measurement run (~1-2 minutes) per compiler change; test-only and docs-only changes are exempt. The 10 percent band means only structural regressions flag — a 5 percent loss hiding inside the band is still possible, which is what the interleaved same-run harness and a rerun are for.

### ADR-029: Flag-fused conditions: generalized bit-test branches and short-circuit logical chains

- **Date**: 2026-11-15
- **Status**: Accepted
- **Context**: ADR-027 named the gap it retired: `binary_gcd`'s `(x & 1) == 0` loop conditions lowered as setcc, movzx, test, branch - seven instructions where gcc tests the flags once - and every `&&`/`||` inside a condition materialised both boolean sides as 0/1 values regardless of short-circuit outcome. The `%` power-of-two specialization (ADR-026) covered only divisor masks.
- **Decision**: Two mechanisms in `cairn/codegen.py`, both refactors of the branch-only `_emit_condition`/`_emit_comparison_branch` path. First, `_bit_test_operand` generalizes the zero-compare: `x % 2**b ==/!= 0` (power-of-two divisor, for every sign) and `(x & m) ==/!= 0` for any positive mask on either side of the `&` now branch from `test`/`test_imm` + one `jcc` on the home register; masks above the byte form or non-homed values keep the existing rax test/and chains, and value contexts (assignment, `print((x & 1) == 0)`) still lower setcc, guarded by a byte-form test. Second, `_emit_logical_branch` lowers `&&`/`||` in condition position as flag chains: the short-circuit side jumps past the other side (`and.short`/`or.short` labels), with the conjunction-false and alternation-true polarities falling through the chain and the opposite polarities branching on each side in turn - so a call in the un-taken side is never evaluated, matching the value-context `_emit_and`/`_emit_or` semantics.
- **Decision verification**: failing-first `tests/test_codegen.py::FlagFusedConditionTest` (5 tests, red on AttributeError then on surviving setcc bytes): single bit-test branch, conjoined bit-test pair (two flag pairs, no setcc), disjunction short-circuit, setcc retained in value position, mask 4096 above the imm8 form; plus the byte-count helper `count_test_imms` (F6 imm8 shape). `tests/test_end_to_end.py::FlagFusedConditionRuntimeTest` (4 execution tests): negative-value bit batteries (42), short-circuit skipping a printing call (1/3 only), chained &&/|| polarities (9), and `%` mask counts over -20..20 (83). Full suite 400 tests + 39 subtests.
- **Consequences**: `binary_gcd` emits ten fused tests and zero setcc forms; measured 202.1 -> 188.0 ms (-7 percent, within the ADR-028 band but the right direction; C 177.7, Rust 147.5 - now behind only 1.06x/1.27x). `ackermann` -14.3, `nqueens` -2.5, everything else within noise; no regression, so the --save --compare gate returned exit 0. The remaining `binary_gcd` gap is the real `call bgcd` per pair; `nqueens` needs frame/spill work (ADO ledger step 4, still future work). Cost: two more label names per logical chain when both sides need a skip; branches already-through conditions gain nothing.

### ADR-030: Four widening benchmarks (tak, primes, mandelbrot, splitmix) and the operand-park clobber they caught

- **Date**: 2026-11-15
- **Status**: Accepted
- **Context**: The eight-case scoreboard left unmeasured bottleneck classes: three-argument data-dependent recursion, division-heavy nested loops, fixed-point multiply/shift inner escapes, and serial 64-bit wraparound multiply-latency chains. Optimizing only against the existing eight risked overfitting the backend to their shapes.
- **Decision**: Four triple twins join the registry with identical-work C (`gcc -O2`) and Rust (`rustc -O`) partners and hard-checked identical stdout: `tak` (63,609 calls of tak(18,12,6)), `primes` (trial division over 2..300000, modulo per candidate and flag-guarded inner loop), `mandelbrot` (four 960x540 escape frames at 2^21 fixed point, `x*y/2^21` signed truncating division, 64-iteration cap), and `splitmix` (100,000,000 splitmix64 mixes over wrapping 64-bit constants; the C twin uses `uint64_t` because signed overflow is UB in C, Rust wraps via `wrapping_*`, and Cairn wraps naturally — the Cairn twin masks each arithmetic `>>` to its low bits, which reproduces the logical shift exactly). Writing `splitmix` exposed a pre-existing miscompilation: `_emit_binary_into` parks the merged left operand in the destination register and then evaluates the right side generically, but a generic right side parks its own operands (rcx leaf parks, r10/r11 temporary parks, rax results) in some of those same registers, so `(w ^ ((w >> 30) & BIG_MASK)) * c` consumed the shifted value instead of `w`. The fix refuses the merged-park fast path whenever the destination is one of the registers a generic right-side evaluation may hold (`GENERIC_EVAL_REGISTERS` = rax, rcx, rsi, r11, r10) and the right side is not a leaf; leaf rights (literals, names) never park and keep the fast path. Immediate-shortcut and operand-home paths, which never evaluate the right side generically, are untouched, and home-register destinations stay on the fast path for every right shape because generic evaluation never writes a home.
- **Decision verification**: failing-first `tests/test_end_to_end.py::OperandParkTest` (red: third stage printed the shifted value's product instead of the xor's), then green after the guard. `RegistryShapeTest` updated for the four new names. `TwinParityTest` gained the four triples (compiled, executed, stdout-identical before being allowed into `CASES`). tak calls counted with an instrumented C build. Full suite 425 tests + 39 subtests; `--save --compare` exit 0 (no Cairn-column regression; the four new cases enter the history with their first baselines).
- **Consequences**: First standings: `tak` won outright against both baselines (7.5 ms vs C 8.4-9.1, Rust 9.6-9.9) — the pure-argument bind and pair-copy machinery pay off on three-argument recursion; `primes` ties C (25.0 vs 24.5, both magic-modulo, behind Rust 19.1); `mandelbrot` 1.25x behind C (13.5 vs 10.8; the inner `x*y/2^21` chain and the 5-variable merge are the visible gap); `splitmix` 1.68x behind C (165.9 vs 98.7 — every iteration rematerializes `movabs` constants the baselines hoist out of the loop). Recorded next mechanisms: loop-invariant constant hoisting (splitmix), inner-loop scheduling/merge pressure (mandelbrot, primes). Cost of the fix: one refused fast path costs a generic rax round trip, paid only on shapes that were miscompiled before.

### ADR-031: The merged-park fast path is reclaimed by a sound rax-only shape test

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: ADR-030's miscompile fix (refuse the merged-park lowerings whenever the right side is not a leaf) was correct but blunt: `splitmix`'s `(z ^ ((z >> 30) & M)) * c` mix - the exact shape the miscompile came from - was refused, even though the logical-shift peephole (milestone 1) lowers that `&` into rax alone, so the fast path would now be safe. Disassembly showed every iteration paying a dead `mov rcx, home` (emitted before the refusal bit), a temporary park, its restore, and a park before the `imul` - about ten instructions for a seven-instruction ideal. The real invariant was never "the right side is a leaf"; it is "the right side's lowering never writes the destination".
- **Decision**: `_computes_in_rax_only` becomes the single, sound guard for the merged-park fast paths in `_emit_binary_into` (both the UPDATE opcode and `*` branches; the `is_leaf` guards are deleted, and `GENERIC_EVAL_REGISTERS` with them). Soundness hardening: (1) the immediate-binary rule now requires the literal to fit the signed-32 immediate range - a big non-peephole mask lowers generically and parks its left in rcx, which was the original miscompile shape the old rule lied about; (2) an `&` matching `_logical_shift_shape` (the `(a >> k) & (2**(64-k) - 1)` recognizer, now shared by `_emit_logical_shift`) recurses on the shift operand, because the peephole lowers the whole tree through rax only; (3) a `parked_noop` flag threads from the call site: when the left operand is exactly a homed name whose home IS the destination, `_emit_left_into` is a no-op, so a right-side name reading that register still reads its original value - the splitmix self-reading mix becomes sound and fast; (4) `_emit_logical_shift` refuses non-leaf operands in the rax destination context, where a pending left may already sit in rcx. Everything else keeps the refuses (calls, division, comparisons, short circuits, arbitrary `*` operands).
- **Decision verification**: failing-first byte tests `tests/test_codegen.py::MergedMaskMixTest`: the mix program's region must hold the peephole `shr rax, 30` with no mask movabs, the update-combine `xor <home>, rax` directly after the shr (helper `_combine_right_after_shr`), and the `imul <home>, rax` with the destination in the modrm reg field (`4C 0F AF E0` probe shape). `tests/test_end_to_end.py::MergedMaskLoopValueTest` pins a 64-iteration masked-xor-multiplier loop against a Python wrap model and the read-after-merge trap `(z + 5) ^ ((z >> 30) & M)` (the peephole must read z before the left side's update replaces the home). Full suite 473 tests + 39 subtests.
- **Consequences**: The mix chain lowers to `mov rax, home; shr; xor home, rax; movabs; imul home, rax` - no dead park, no temporary spill, no imul re-park. Binary-identity proof against the pre-change compiler: all twelve benchmark executables compile byte-identical except splitmix itself, so the --save --compare run's other-case drift is machine state, not code. splitmix fell 181.3 -> 110.0/111.1 ms in interleaved runs (C 98-101, Rust 110-113): behind C by about 1.1x and at par with Rust, from 1.68x behind. Recorded next mechanisms: park-free imul constants (`movabs rcx` to kill the pre-imul rax parking), direct home-to-home assignments, and loop-invariant constant hoisting (splitmix's constants still rematerialize per iteration).

### ADR-032: Countdown loops preload their invariant big constants into prologue-saved spare homes

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: ADR-031 left splitmix at 110 ms against C's 98-101 with one recorded gap: every iteration re-materializes the loop-invariant big constants (the gamma addend and the two mix multipliers, each a 10-byte `movabs rax`) that gcc hoists into callee-saved registers before the loop. Home registers are all slot homes, but the leaf frame shape (every slot homed, slots below the home count) leaves the remaining `HOME_REGISTERS` unpushed - genuinely free ABI-safe registers if the prologue saves them.
- **Decision**: `_plan_constant_hoists` walks each function body's While statements (outer loops first) and claims up to `HOIST_HOMES_PER_LOOP` (3) spare homes per loop for its most-used literals outside the signed-32 immediate range - precisely the shapes that lower as `movabs`. The literal counter skips peephole `(a >> k) & (2**(64-k) - 1)` trees, which emit nothing for their mask or count. The prologue pushes the claimed homes after the slot homes (the pad/filler parity accounting sees the extra units, keeping every call 16-aligned), `_emit_while` loads the planned constants once at loop entry and installs a value-to-home map, and the three literal lowerings (`_emit_expression`, `_emit_into`, `_emit_left_into`) read the map instead of re-materializing. Copies never read the spare homes: a copy's frame never saved them, so `_emit_while` checks the real-body context (`inline_depth`, `delta_value`, `pair value_slot`, `hold_units` all clean) and re-materializes everywhere else. Frame-shaped functions have no spare homes and no plan.
- **Decision verification**: failing-first `tests/test_codegen.py::LoopConstantHoistTest`: a countdown mixer loop must hold no `movabs rax` of either big constant and must load each exactly once into a spare home before the loop body (helper `_hoisted_movabs_candidates` covering all 16 register encodings - the first draft only enumerated the low 8 and missed the high homes). `tests/test_end_to_end.py::LoopConstantHoistValueTest`: a 64-iteration countdown mixer with a hoisted addend wraps like the Python model, and two sequential loops each read their own load. Full suite 497 tests + 39 subtests.
- **Consequences**: splitmix fell 110.0 -> 94.3 ms measured - now faster than its C twin (98.3-101.6 in the same sessions) and Rust (110-113) for the first time, closing the journey 165.9 -> 144.4 -> 110.0 -> 94.3 that began with ADR-030's miscompile catch. Every other case measured within noise or improved (primes -12.3 percent, at the band edge). The `--save --compare` gate returned exit 0. Cost: up to 3 pushes per hoisting function (plus pad parity) and one 10-byte load per constant per loop entry. Recorded next mechanisms: three-operand `imul r64, r64, imm32` for in-range multiplies still re-loading `mov eax, imm`, inner-loop merge pressure for mandelbrot, call overhead for fib30/call_chain.

### ADR-033: C++ joins the benchmark scoreboard as a third measured baseline

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: The scoreboard goal was "faster than C and Rust", but the user widened it to include C++: a compiled language claiming speed must measure against the whole nearest-neighbor field, and C++ at `-O2` is the most likely near-twin of gcc's output. The toolchain was already present (MinGW-W64 UCRT g++ 16.2, same package as gcc), and every `.c` benchmark twin is valid C++ for the subset it uses, so no new benchmark semantics were needed.
- **Decision**: `benchmarks/run_benchmarks.py` gains `CPP_COMPILERS` (g++, clang++, cl), `find_cpp_compiler()`, `build_cpp()`, a `Case.cpp_source` field and a fourth measured column. C++ sources are mechanical copies of the C twins (header comment notes the quadruple), each compiled with `g++ -O2` and verified to print the C twin's total before being committed. `TwinParityTest` compiles and runs the C++ twin per case alongside the existing three. The strict gate exits 2 only when all three baselines are missing or when any measured baseline wins a case (exit 1); unavailable baselines are skipped, never assumed. The `--compare` regression gate still judges only the Cairn column, keyed by case name.
- **Decision verification**: failing-first harness tests in `tests/test_benchmarks.py` (cpp twin existence, error-path keys, measured-verdict/ratio plumbing, render goal lines and C++ ratio block, strict pass/fail including a cpp-wins case, JSON `faster_than_cpp`/`verdicts_cpp`); the existing strict/json tests were pinned with mocked `find_cpp_compiler`. `TwinParityTest` passes with all four implementations printing identical totals on every case. Full suite 505 tests + 39 subtests. The `--save --compare` gate over the landed tree returned no Cairn-column regression on the final run.
- **Consequences**: The board now reports three baseline goals per run. First saved C++ numbers put the C++ column within a percent of C everywhere (same sources, same `-O2`), so the recorded loss list is unchanged: xorshift ties all three; collatz, ackermann, tak, folded_math and splitmix beat all three; nqueens, binary_gcd, call_chain, primes and mandelbrot remain losses with their mechanisms filed in ADR-027/029/032 follow-up notes. Cost: one more compiled executable per case per run (build time dominates; measurement rounds are unaffected) and one more twin to keep in sync under the parity gate.

### ADR-034: The counted `for` loop joins the language as parser sugar with scoped variables

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: The language law (SPEC 6.3) deliberately said "no `for`"; the user asked for a proper language surface, and a counted range loop was the first gap closed (the recorded gap list continues: `print_char`/`print_hex`, arrays, strings, more than four parameters, structs). The existing `while` path already carries break/continue, countdown latching and constant folding, so the loop must land as sugar over that machinery, not as a second loop implementation.
- **Decision**: `for name in start..end { ... }` (`..=` inclusive) parses into a real `For` node (E2005 refuses a missing range symbol; `for`/`in` and `..` / `..=` are new reserved tokens). Semantic analysis gives the loop variable its own scoped, mutable slot: the start bound is evaluated once in the enclosing scope, the end bound sees the loop variable (it is re-evaluated before every test, exactly like C), `continue` still steps the counter, `break` leaves with the variable's last value, a body-local `let` of the same name is still E3006, and a name shared with an outer binding is set aside and restored (so two loops may reuse `i`, and code after the loop reads the outer one). A new always-on lowering pass, `optimize.lower_for_loops`, runs in `builder.compile_source` before the folding gate (`--no-optimize` skips folding, not sugar), rewrites every `continue` that belongs to the loop into `increment; continue` (never descending into a nested `while`), and produces a mutable `let` plus a `while` with a trailing increment; code generation never sees a `For` and refuses one loudly if it ever does. The lowering exposed a latent crash in the countdown-latch pass: a `while v < <non-constant>` (the compiled end bound) reached `_constant` subtraction; that pass now leaves computed bounds alone.
- **Decision verification**: per the recorded user rule, each new surface piece carries its own tests, failing-first: lexer (`..`/`..=` tokenisation), parser (exclusive/inclusive shapes, E2005, the `in` keyword), sema (slot reuse across two loops, outer binding restored, mutable in-body assignment, E3006 body shadowing, E3012 builtin name, E3005-free continue/break validity, undeclared use after the loop), optimiser (let+while shape, `<=` condition, continue-increment rewrite, nested `while` untouched, nested `for` innermost-first, no `For` survives), and end-to-end executions pinning values: 10 / 15 / 0 / 0 / 6 / 4 / 16 / 0 / 9 across exclusive, inclusive, empty, reversed, continue-stepped, broken and nested loops with outer-name restoration, plus the C-order end-bound re-evaluation print trace. Full suite 547 tests + 39 subtests. Benchmark neutrality: the twelve case executables are byte-identical old-vs-new compiler (sha256 checked); the session's `--save --compare` gate runs during this milestone flagged single cases up to +50 percent with every baseline language degrading equally under verifiable heavy desktop load (browser/editor churn), the signature the byte-identity proof attributes to machine state per the ADR-031 precedent. The gate is to be re-run when the machine is quiet before the next speed-affecting change lands.
- **Consequences**: Cairn programs express counted ranges directly, and the countdown latch still applies to the `while` the sugar lowers to (zero-trip guard can fire when the start is a literal below the end). Recorded next surface work: `print_char`/`print_hex` builtins, stack parameters beyond the four register homes, then arrays and strings. Cost: two reserved tokens, one new scoped-binding rule in sema (the spec's "no shadowing" now has one documented exception scoped to the loop variable), one always-on lowering pass.

### ADR-035: print_char and print_hex join the standard surface

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: The for-loop milestone (ADR-034) left the standard surface with a single output builtin: `print` writes decimal plus a newline. Programs that format data (hex dumps, byte streams, debug output) had to run decimal arithmetic in the program and pay `print`'s mandatory newline per glyph, and a proper language needs byte- and hex-level output. Both new builtins follow the third recorded user rule (every added surface member carries its own benchmark case in the same change) and the second (every added surface member carries its own failing-first tests).
- **Decision**: `print_char(c)` writes the low byte of `c` (`c & 0xFF`) to stdout with no newline, and `print_hex(x)` writes `x` as lowercase hex of its unsigned 64-bit value with no leading zeros (`0` for zero) and no newline; both return their argument, because the code generator keeps `print`'s stack round-trip (push rax, call helper, pop rax) and the helper clobbers rax/rcx freely. `sema.BUILTINS` lists both at arity 1; the code generator dispatches each to its own runtime label with the same argument/restore shape as `print`. The two runtime helpers are machine code like `print_i64` itself: `print_char` isolates the byte with an unsigned division by 256 (the remainder) and writes a one-byte frame buffer through one `WriteFile`; `print_hex` peels nibbles with unsigned division by 16 (remainder and shift in one `div`, right to left into a 32-byte frame buffer) and turns each nibble into ASCII with a conditional move (`+ 0x30`, or `+ 0x57` when the nibble cleared 10), one `WriteFile` per value. `emit_runtime` emits them unconditionally, so every executable carries about 200 more bytes of helper text; no rodata is added. Cost note recorded for future speed work: `print_hex` plus a newline `print_char` costs two syscalls per line versus one for the twins' single formatted call, so `hex_io` is a known-lossy case until the newline can ride in the hex buffer.
- **Decision verification**: failing-first tests committed before the implementation: sema (both builtins accepted at one argument, `E3004` arity, `E3012` as-value and as-parameter/assignment-target for each), codegen (helpers emitted into every executable, call fixups target `cairn.runtime.print_char`/`print_hex`), and end-to-end executions pinning values: `Hi` from chars, `print_char(321)` wrapping to `A`, `ff 0 abcd` for hex with stripped leading zeros, 16 f's for `print_hex(-1)`, both builtins returning their argument when read as values, and a composing `for` loop. The third user rule's own obligation: `benchmarks/hex_io` (four sources, 400,000 values per run, xor-chain checksum) lands in the same change with the scoreboard case registered; `TwinParityTest` verifies the quadruple prints the identical checksum (cross-checked against an independent Python model: 571277383983616). Full suite 616 tests + 39 subtests.
- **Consequences**: Cairn can express byte- and hex-formatted output at machine-code cost, and the benchmark registry grew from 12 to 13 cases under the measured-gate rule. The milestone's `--save --compare` gate returned exit 0 twice on adjacent runs; scattered single-case flags flipped sign between the runs (mandelbrot +104 percent then -42 percent with C degrading identically), the ADR-031 noise signature, while every load-independent case (collatz, binary_gcd, call_chain, tak) held within or below the band. Recorded next surface work: stack parameters beyond the four register homes, then arrays and strings.

### ADR-036: eight parameters per function, with stack parameter passing

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: The four-parameter limit was an artifact of the four ABI argument registers the code generator knew: functions wanting more state had to pack it into one `i64`, which distorts every call shape the surface can express. The task plan (ledger `cairn-language-surface`) recorded this as milestone 3. The limit was pinned in three places: `sema.MAX_PARAMETERS` (E3010), the codes table, and the capabilities manifest; the prologue's two frame shapes and the inlining paths all assumed arguments never exceeded the register count.
- **Decision**: `MAX_PARAMETERS` is 8. The first four arguments keep their ABI registers; the rest travel in the caller's outgoing stack area, one `sub rsp` reserving 8 bytes per stack parameter plus an alignment pad so the call still runs 16-aligned. Every argument keeps evaluating in source left-to-right order: the four register candidates park on the stack while later arguments evaluate, and each stack argument stores in place, its displacement folding the outstanding parked pushes. The callee pulls stack parameter `j` from `[rbp+16+8j]` - directly above the saved return address, no 32-byte shadow store, because Cairn strictly never calls foreign code and the contract only has to satisfy itself (SPEC section 8 records the divergence). Functions with five or more parameters always take the rbp-chained frame prologue; the register-only home prologue remains a four-argument club, and any call with more than four arguments is excluded from the self-recursion, pair and foreign-inline fast paths. The E3010 message text now states the register-carried count instead of naming the limit, and the pinned cap test moved from four to nine parameters, superseding `test_more_than_four_parameters`.
- **Decision verification**: failing-first tests: sema accepts five through eight parameters and refuses nine with `E3010`; codegen pins the callee's `[rbp+16]`/`[rbp+24]` pulls, the caller's outgoing-area stores (`mov [rsp+32]/[rsp+40], rax` with the parked registers outstanding) and the single 16-byte reservation; end-to-end executions pin six-argument rotation, mutable stack parameters across a counted loop, an eight-parameter sum, left-to-right evaluation with `print` in both register and stack argument positions, and nine-level argument-rotating recursion. The construct's benchmark case under the recorded third rule: `benchmarks/many_params` (six-argument multiply-accumulate, 10,000,000 calls per run) with the quadruple cross-checked against a Python model (12,600,005,750,000,000). Full suite 652 tests + 39 subtests. The `--save --compare` gate returned exit 0; the saved run shows the standing noise signature (mandelbrot -50.8 percent and nqueens +25.4 percent in the same run, both flipping between adjacent runs), so the quiet-machine re-run noted in ADR-034 remains open before any further speed-affecting change.
- **Consequences**: Call sites in the language now express the full Win64-width parameter band without packing, and `capabilities` advertises the new limit automatically. Recorded supplementary costs: five-plus-parameter functions give up the home prologue and their callers pay two extra instructions per stack argument per call; `many_params` first measured at 145 ms (C 127, C++ 144, Rust 154), so the case opens ahead of Rust and within noise of C++ with both the parked-argument stores and the double store-to-slot round-trip as recorded future mechanisms. Recorded next surface work: arrays and strings.
### ADR-037: the foreign-call call site stops paying register round trips

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: The three recorded repeated losses (task ledger `cairn-remaining-losses`, milestone 1) put call overhead first: `call_chain` lost 42.5 vs C 32.6 with a stable signature. Listing and objdump of the hot loop showed the foreign inline copy of `mix3` already runs (no `call`), but three wastes crowded it: the bare name argument bound once per call (`mov rcx, rbx` for `mix3(x, ...)`'s `a`), the copy's result round-tripped (`mov r14, rbx` after `cmovle`, because the body computes into a guest register before the return destination move), and a mask argument bound as a move-plus-`and` pair where gcc emits one `movzwl`. A first tree-substitution attempt (every use of the parameter re-evaluates its small argument expression) produced wrong outputs end to end: the copy's body still read the parameter's entry register, which the skipped bind never wrote. The recorded lesson stands: substitution must reach the *read* of the parameter, not only the argument expression, and `assign`-targeting re-evaluation cannot be sound without that reach.
- **Decision**: Names-only aliasing. `_substitution_plan` now maps an argument to the caller's home register only when the argument is a bare `Name` with a register home (never the delta or value slot; a callee that rebinds parameters refuses the whole plan), and `copy_alias` maps the copy's **parameter slot ids** to the caller's homes - `_home` consults the map first, so every home-aware lowering (folds, scaled `lea`s, compare-in-place, selects) reads the aliased parameter exactly like a normal homed slot and no bind move is emitted. The copy's result also homes in the caller's asked-for register: `_return_slot_override` approves when the body ends in `return <local>`, that local is defined before it is read (no entry-time xor, which would erase an aliased caller home), and no substituted alias whose register is the destination survives the slot's first definition (a later read would observe the copy's result). `_emit_and`-family word: the zero-extend mask also fires on the rax-only binary path (`movzbl %bl, %eax` for `total + (x & 255)`), not only on the into-destination one. Finally, every `while`-family loop head (`while.start`, the halving loop's `top`, the chain loop's) is padded onto a 16-byte boundary with nops before its label - the backedge target now starts a fetch line. The tree-shape substitution helpers (`_pure_argument_tree`, `_alias_expression`, `_argument_home_reads`, `_substitutable_argument`) and the unused `ALIAS_BASE` id space are gone.
- **Decision verification**: The corrupted e2e tests exposed by the tree attempt all pass again under names-only aliasing (bare-name args read the caller's home; computed args bind). Failing-first tests for the new shapes: `PureArgumentSubstitutionTest` pins the copy's only bound non-mask argument (`sar rdx, 2`), the one-`movzx` mask bind, the `lea [rbx+rbx*2]` that reads the caller's home `x` directly, the copy (not call) for a twice-used argument, and the copy that still happens when the guest borrows caller homes (alias refused onto borrowed registers). `ZeroExtendMaskTest` pins both mask widths and the frame-slot refusal. The four `MultiArgumentCallTest` shape assertions were updated to the new (strictly better) emissions, with the two `stack fallback` tests redesigned so the fallback stays genuinely covered: a self-recursive nested call keeps the push path, and a division argument still refuses reverse binding while its substituted name siblings defer. Every shape was verified by building and running the actual executables (outputs 28/12/6/30 match hand-computed values). Full suite 661 tests + 39 subtests. The `--save --compare` gate returned exit 0 twice; the fresh call_chain binary measured 43.0 ms best (from 44.5), collatz 105.5, xorshift 301 -> 292 on one run, with every other case inside the noise band.
- **Consequences**: The call_chain loop is 15 instructions against gcc's 15, with the result computed directly in `x`'s home and the tail through one `movzbl`. The remaining measured gap (43.6 vs C 33-34) no longer shows a bind move, a result move, or a mask pair; the residual mechanisms sit in instruction selection (gcc replaces our `(i >> 2) << 2` shift pair with `and -4` where it proves non-negativity) and code layout. Milestones 2-3 of the ledger task stay as planned: nqueens's frame-path main never sets `self.current`, so foreign inlining is currently disabled inside any frame function (7-slot main), and mandelbrot's inner-loop register pressure follows. Cost: nops before every loop head (cold loops pay at most 15 bytes) and two extra saved-state fields on the copy stack.
### ADR-038: commutative binaries compute in place, and ZF decides a zero branch

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: Milestone 2 (nqueens) of the ledger task `cairn-remaining-losses`. The planned frame-path gap turned out not to apply to this benchmark: `queens` compiles to a 6-slot leaf and `main` to a 0-slot leaf, so neither loses foreign inlining today. The nqueens listing still showed two wastes in the hot loop: the guard `(free & bit) != 0` lowered as `mov rcx, r14; mov rax, rdi; and rax, rcx; cmp rax, 0; jz` - a redundant left-operand copy (the four fall-through arithmetic ops are all commutative, so the right side can be the destination) and a redundant immediate compare (the `and` had already set ZF = (result == 0)).
- **Decision**: Two peepholes. (1) In `_emit_binary`'s fall-through, when the op is one of `+ & | ^`, the left operand is a plain `Name` with a register home (never the delta slot) and the right side does not call, the right side is evaluated into rax and `op rax, <left home>` is emitted directly; `_emit_left_operand` and its rcx round trip are skipped. (2) The Assembler now summarizes flags in one field, `zf_status`: every byte emission clears it, add/or/and/sub/xor (register and immediate forms, result in the destination register) and `test` re-establish it as `ZF = (register == 0)`, and `label` clears it because a label is a control-flow merge. `_emit_comparison_branch` skips `cmp rax, 0` exactly when the summary names rax. `-` keeps the rcx round trip because it is not commutative. Correctness argument: a real or copied call preserves every home register, `has_call(right)` keeps the fast path away from call arguments, and no flag-writing instruction or label can sit between the ZF-setting op and the branch it feeds.
- **Decision verification**: Failing-first byte-shape tests (`CommutativeHomePeepholeTest`: an `and`/`add` of a homed left operand emits `and rax, rbx`, never the via-rcx form, and the left operand's parking move is gone; a `!= 0` branch carries no `cmp rax, 0`) plus an end-to-end print twin over `& | ^ +` with both branch polarities (`CommutativePeepholeValueTest`, outputs 7/2/255/15/240). Full suite 692 tests + 39 subtests, zero regressions. The `--save --compare` gate returned exit 0 twice; the two first-run "regressed" flags (tak +13&#37;, hex_io +12&#37;) returned to band (-11&#37; both) on the quiet re-run, the machine-noise signature per ADR-031/ADR-034. nqueens 24.1 -> 24.4 ms stays inside the band; the guard path dropped from five instructions to three and the function's text from 245 to 238 bytes.
- **Consequences**: The discovery about frame-path functions (the rbp prologue never sets `self.current`) stays real for programs whose main needs more than six home registers: foreign inlining is still disabled inside such a function, now recorded for a later milestone rather than this one, because nqueens itself does not take that path. The remaining nqueens gap to gcc sits in loop-latch shape (`bit <<= 1` after the call plus `cmp rdi, 0x800 / jng`, against gcc's hoisted update and 12-iteration counter) and code layout, not in redundant operations. Mandelbrot inner-loop register pressure (milestone 3) is next.
### ADR-039: same-name binaries load their slot once, and `imul` joins the in-place forms

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: Milestone 3 (mandelbrot inner-loop pressure) of `cairn-remaining-losses`. The mandelbrot main is the frame-path case the ADR-037 note parked: 12 slots against 6 register homes, so the inner loop's six values (count, x, y, x2, y2, next) all live in rbp-frame memory and the loop spends 13 of its 25 instructions on frame traffic, against gcc's register-only 16-instruction loop built from induction-variable rewriting. Two wastes were reachable without restructuring register allocation: `x * x` lowered two loads of the same frame slot (`mov rcx,[..]; mov rax,[..]`) plus `imul rax, rcx`, and `x * y` still parked a register-homed left operand in rcx although `imul rax, r/m64` takes any second register. The 7th-home lever search closed cleanly: `rsi` is the division scratch and `rbp` is the frame pointer (codegen header comment), and Windows x64 declares both nonvolatile only outside those roles, so 6 homes is by design the ceiling.
- **Decision**: The M2 commutative fast path generalizes to `IN_PLACE_BINARY_OPS = (+ & | ^ *)` with two branches evaluated in order. (1) Same-name operands (`right` is the same slot as `left`, never the delta slot): one `_load_slot` into rax feeding `op rax, rax` (or `imul rax, rax`), polarity-free and correct for every commutative op including `^` (now zero) and `|` (now itself). (2) Otherwise, a homed left operand with a call-free right side evaluates the right side into rax and computes `op rax, <left home>` or `imul rax, <left home>`. `sub` stays on the rcx round trip because it is not commutative.
- **Decision verification**: Failing-first byte-shape tests (`SameNameBinaryTest` pins `imul rax, rax` from a frame-resident `g * g` in an 8-slot function and from a register-homed parameter pair; `MultiplyHomeFastPathTest` pins `imul rax, rbx`) and an end-to-end twin printing 81/121/169/30/14/8 over negatives, frame-resident and register-homed products, and the `+ &` same-name forms. Full suite 723 tests + 39 subtests, zero regressions. The `--save --compare` gate returned exit 0 on all runs; an initial binary_gcd +110&#37; flag settled to 175/189 ms (band) on two follow-ups, and one later run showed the whole-board +12..92&#37; drift that ADR-031 attributes to desktop load - every pre-existing "regression" flag from ADR-031/ADR-034 is discharged as noise. Mandelbrot itself measured 14.6 against its 14.5 baseline, harness-exit-0; a paired A/B against the previous commit's binary was wall-noise dominated with the child-CPU-tick sample favoring the new build, so the win is recorded as the proved static reduction (main 291 -> 272 instructions, `x * x` at one frame load each) rather than a claimed wall-clock gain.
- **Consequences**: Same-name and multiply forms stop paying the parking move everywhere, not only in mandelbrot. The mandelbrot residual is now structural: an 11-value live set against a 6-home ceiling and no induction-variable rewriting, so the recorded next levers are (a) loop-induction strength reduction (`px * 16384 - 4194304` and `py * 24576` becoming running values) and (b) re-evaluating home assignment so a frame function's later-scope slots reuse registers freed by dead outer scopes - both compile-time work with no source-level counterpart, deferred to a new ledger task. The frame-path `self.current` discovery from ADR-037/ADR-038 also remains open for programs whose main exceeds six slots but do call inlineable helpers.
### ADR-040: frame-path functions keep their foreign inline rights, and a stack park no longer refuses a copy

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: New ledger task `cairn-structural-losses` (the ADR-039 deferred levers). Milestone 1 closes the parked discovery: `_emit_function`'s rbp-frame prologue never set `self.current`, so `_foreign_inline_target`'s `self.current is None` refusal disarmed every foreign inline inside a function with more than six home slots - any `main` with eight live values compiled calls where a leaf main of the same shape compiled copies. A second refusal surfaced while testing: the guard also demanded `self.depth == 0`, and a frame function's `_emit_left_operand` parks expression intermediates with `push rax` (leaf prologues park into their pad slot without touching depth), so chained calls such as `twice(g) + twice(2) + twice(a0)` still paid two real calls in a frame function while a leaf main inlined all three. A stack park cannot be clobbered by an accepted copy: the copy is call-free, trap-free and loop-free, its only stack traffic is balanced push/pop spill, and `depth` is not even reset across the copy - unlike a register temporary (`self.temporaries`), which the copy's own lowerings index from zero and really would clobber.
- **Decision**: The frame prologue raises the same body-state the leaf prologue raises - `self.current = function`, delta/value slots closed, `hold_units` zero, chain depth zero, `holds` empty - and the frame epilogue lowers `current` and `holds` again. `_foreign_inline_target` drops `self.depth` from its refusal list and keeps `self.temporaries`.
- **Decision verification**: Failing-first tests pinned the whole shape: a frame main (verified >6 slots) leaves zero calls to a copy-free callee in a return chain (`calls_of == 0`), the same holds for a copy inside a while body, and a callee whose body itself calls (`fn wrap(v) { return twice(v) + twice(v); }`) still lowers to a real call - the fallback parity a rebind-parameter callee does NOT provide, because rebinding callees take the bind-once copy form rather than the call. An end-to-end twin prints 0/2/23/19 over a frame main computing `g + 2*(g+1) = 3g+2` per loop iteration as 0 -> 2 -> 8 -> 26 with copies inside the loop body (the first draft's expected string was wrong twice against a correct compiler; the pinned values were recomputed from the source). Full suite 754 tests + 39 subtests, zero regressions. The `--save --compare` gate returned exit 0 twice; both flagged runs (mandelbrot +10.6, nqueens +8.2) re-ran to 14.3/24.2 ms, and both benchmarks' binaries are byte-identical to the previous commit for these sources (no callees), attributing the drift to desktop load per ADR-031.
- **Consequences**: Programs whose main exceeds six home slots stop paying call overhead for their call-free helpers. The remaining milestone-2/3 work is unchanged: register-home reuse for frames (mandelbrot's 11-value live set against a 6-home ceiling) and loop-induction strength reduction.

### ADR-041: a stack-parameter callee foreign-copies: two parked arguments instead of a call

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: Milestone 2 of `cairn-structural-losses`. The planned milestone (register-home reuse for frame functions) did not survive contact with the listings: in `mandelbrot` every one of the six home slots — `total`, `frame`, `py`, `y0`, `px`, `x0` — stays live across the inner loop, so a "reuse registers from dead outer scopes" pass cannot fire on the one case it was aimed at; the 11-value live set against the 6-home ceiling is an allocation problem, not an assignment-order one. Retargeting by the board instead: `many_params` measured 42-44 ms against C's 7.8, the largest standing loss, and its listing showed why — a six-argument call pays the call path twice. The caller's `_emit_stack_call` reserves an outgoing area every iteration (`sub rsp, 16` inside the loop), parks every register argument, then pops them; and the callee, a call-free bind-once leaf, still saved and restored all six home registers around a body that multiplies each parameter exactly once. The root refusal was in the foreign-inline copy form: `_foreign_inline_target` capped arguments at `len(ARGUMENT_REGISTERS)` (4) and `_emit_foreign_inline` bound only the first four parameters from the ABI registers — a copy of a five-plus-parameter callee would read garbage, so the cap was real for the machinery as written, not arbitrary.
- **Decision**: Widen the copy form to `args <= len(ARGUMENT_REGISTERS) + len(TEMPORARY_REGISTERS)` (6). The new `_emit_parked_copy_arguments` binds arguments left to right into the four argument registers plus the two temporary registers (`r11`, `r10`) — directly when every argument is call- and trap-free (reverse order, each binding writing only its own destination, the same safety argument `_emit_pure_arguments` already argues), via the push/park/pop round trip otherwise. `_emit_foreign_inline` then binds the guest homes of parameters 5-6 from those temporaries at copy entry, spilling first (spill pushes precede the binds), skipping aliased slots that already read a caller home. The two temporary registers never appear in the guest plan (guests draw from argument and home registers only), so no copied body can clobber a still-unbound argument while its lowerings run, and no entry clear can erase one. `_emit_call` consults `_foreign_inline_target` before routing to `_emit_stack_call`, and `_emit_value`'s copyable-call shortcut reaches the widened binder untouched.
- **Decision verification**: Failing-first tests (`StackParamCopyTest`: a six-parameter call-free bind-once callee lowers to `calls_of == 0`; a rebinding six-parameter callee takes the bind-once copy, also zero calls; a callee whose body calls another function keeps the stack call — `calls_of == 1`) and an end-to-end twin printing the literal call (953) plus the loop-accumulated total (6025) over computed arguments (`StackParamCopyValueTest`). The two `StackParameterTest` caller-shape pins moved to a calling callee so the stack-call lowering they document stays exercised. Full suite 785 tests, zero regressions. The `--save --compare` gate returned exit 0: `many_params` 44.2 → 28.9 ms (-34.5%, the run's biggest mover) and `mandelbrot` 16.1 → 14.4 ms both improved, every other case within the noise band — no regression flags this run.
- **Consequences**: Any program calling a five- or six-argument call-free helper stops paying the callee's home tax and the caller's outgoing-area churn — the per-iteration path is five `lea` binds, two guest binds and the body. Milestone 3 retargets to `mandelbrot`'s allocation ceiling (live-set register allocation or induction strength reduction, whichever the value analysis survives) — home reuse by scope death is recorded as closed, the live set refutes it.

### ADR-042: frame slots update in place with memory operands, like their register cousins

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: Milestone 3 of `cairn-structural-losses`, after the ADR-041 close of home reuse by scope death. The mandelbrot inner loop's listing (while.start.10) decodes to 27 instructions, and every self-update on a frame slot paid the general path's triple — `mov rax,[rbp+d]`, `add rax,1`, `mov [rbp+d],rax` for `count = count + 1` and the same shape for the outer latches and `total = total + count`. `_fold_update` folds those updates into one instruction, but only for a register home; a frame slot (home = None) fell through to the generic lowering however simple the right side was. Register allocation itself (mandelbrot's 11-value live set against a 6-home ceiling, and the slot-to-home map its rework needs) is bigger than this task's remaining budget: it is recorded below as the next task's scope, not smuggled in here.
- **Decision**: A frame-slot fold alongside the register one. In `_emit_value`, a slot with no home whose update is `slot = slot op value` with `op` in `+ - & | ^` (the `UPDATE_OPCODES` forms) folds into one memory-operand instruction from three new shapes: `group1_store_rbp_imm` for a small-literal right side (+, -), `binary_store_rbp` with a homed register for a name right side, and `binary_store_rbp` with rax for everything else (the right side lowers into rax first, so a calling or trapping right side keeps the general path's evaluation order exactly — the memory instruction reads the slot only when it executes). `*`, `/`, `%` and comparisons stay on the general path. The ZF summary stays cleared on the memory forms (ZF names the memory operand, which no register tracks), so a following zero-branch keeps its own compare. The delta-slot guard, the non-negative fact bookkeeping and the `-`-is-not-commutative side selection mirror `_fold_update` line for line.
- **Decision verification**: Failing-first on three levels: machine encodings for all new shapes (`FrameInPlaceEncodingTest`: disp8 and disp32 register forms, imm8 and imm32 group-1 forms, the overflow ValueError); byte-shape tests pinning the in-place forms inside a frame main including a beyond-disp8 right side (`FrameInPlaceUpdateTest` — its first draft expected the ADD opcode on a subtraction and was corrected against the compiler); and an end-to-end twin over +, -, &, |, ^ with negative accumulators and frame-resident right sides printing -65/-22/285/7 (two hand-math corrections recorded — u decreases across trips, and a1 is 6, not 9). Full suite 820 tests, zero regressions. The `--save --compare` gate returned exit 0 with no regression flags: mandelbrot 14.4-14.9 ms (inside the band; the inner loop is dependency-chain bound so the two saved instructions ride the off-chain counter), while `many_params` reached a new best, 28.9 to 24.2 ms. The mandelbrot inner loop sheds 7 text bytes (108 to 101) and its counter update drops from three instructions to one.
- **Consequences**: Every frame-resident counter, accumulator and latch in every program stops paying the load/store pair — the fold is shape-generic, not benchmark-specific. Mandelbrot's remaining gap to gcc is now purely the allocation ceiling: eleven live values across six homes with the inner loop's feeding reads (x0, y0) in the frame; the next task (proposed: `cairn-allocation`) carries the slot-to-home mapping rework this file's copies and delta machinery currently assume away, after which home assignment can follow live ranges and the 8-home variant can be evaluated against it.

### ADR-043: the slot-to-home map is the assignment surface; register list keeps save-area order

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: Task `cairn-allocation`, milestone 1. `self.homes` carried two meanings at once that the allocation work needs to split: the ordered register tuple the prologue saves, the plans scan and the epilogue restores, and the slot-indexed array `_home` consulted for every slot read. Assignment policy (loop-depth preference, live ranges, an 8-home variant) needs to own the second meaning without touching the first, and three contexts build each shape today: `_emit_function` (identity prefix of `HOME_REGISTERS`), `_emit_foreign_inline` (the callee guest tuple), and `_emit_inlined_self_call` (the shadow tuple).
- **Decision**: A new generator field, `home_of: Dict[int, str]`, is the single slot-to-register surface `_home` reads; `homes` keeps its ordered register list meaning everywhere else (save area iteration, plan membership, hoist spares, guest construction). All three contexts swap and restore both fields together, and the copy's bind and clear loops read the map. `_clear_frame` iterates the map for the register clears (ascending slot order preserved). Identity semantics land first: every map is built by enumerating the same positional tuple it replaces, so no emitted byte changes. `_return_slot_override` and `_plan_foreign_copy` keep their positional guest views this milestone; assignment policy arrives in milestone 2 and revisits both with the map.
- **Decision verification**: Byte-identical binaries against the parent commit for five benchmark shapes covering both prologue forms and both copy contexts (mandelbrot, nqueens, many_params, fib30, call_chain — md5-verified before and after). Full suite 822 tests (the new `HomeMapContractTest` pins that `_home` grants only what the map carries, including a slot homed past the positional default and a slot the map declines). The `--save --compare` gate returned exit 0 with every case inside the band.
- **Consequences**: The generator now has a place where home assignment is a decision rather than a positional default. Milestone 2 restricts the map's keys to the call-live slots — loop-depth preference for frame functions — after which the guest and shadow builders carry the callee's map, and the 8-home variant becomes a one-constant experiment instead of a reshape.


### ADR-044: frame functions grant the six homes by loop depth, deepest loop first, later bindings winning ties

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: Task `cairn-allocation`, milestone 2. Identity assignment (milestone 1's byte-identical baseline) gave the homes to slots 0-5 in declaration order, so in mandelbrot — the task's motivating listing — the inner loop's six working values (count, x, y, x2, y2, next) lived in the frame and paid thirteen rbp-relative loads and stores per iteration, while the six homes went to values the inner loop never touches. gcc's twin keeps the whole inner working set in registers.
- **Decision**: A new `CodeGenerator._home_slots(function)` decides which slots keep register homes, in register order. Functions whose slots all fit keep declaration order — the leaf shapes are byte-calibrated (ADR-019/020/021) — and a leaf or full-home function's prologue, save area and copies are untouched. A frame function (`slots > 6`) ranks every slot by the deepest while nesting any read or write of it sits in (`_home_slots`'s depth walker over the body; condition reads rank at the loop's enclosing depth, bodies one level deeper), and ties break by descending slot so later bindings beat earlier ones on equal depth. The first six ranked slots take the homes; every remaining slot falls to the frame, parameters included, whose prologue store drops to the frame through the existing `_home`-driven path. `_clear_frame` now clears exactly the slots the map declines (ascending), so a hole-pattern frame (frame slots scattered between homed slots, not appending after the prefix) stays byte-exact; the register clears still iterate the map in register order unchanged. The frame size and save area remain count-based (`len(self.homes)` identical), so prologue/epilogue bytes for a frame function are unchanged; only which slot a name resolves to shifted. Copy and shadow contexts rebuild their own maps by enumeration and are unaffected: a copy's guest is positional by slot and every guest has all slots homed, so preference never needs to cross the copy boundary.
- **Decision verification**: Failing-first: `HomePreferenceTest` (a depth-0 displaced binding's update takes the memory-imm form `add [rbp-8], 2` while the loop slots fold into registers; a leaf function keeps `add rbx, r12`; the initial run failed on the old identity bytes) and `HomePreferenceValueTest` (an 8-slot e2e twin printing 110/30/16/140 with the hot slots homed and a1/a2 frame-resident). Four existing pins were repointed to the new contract with the old expected bytes preserved by redesigning their fixtures so the tested slots stay frame-resident (the InPlaceMain/InPlaceTriple/disp32 programs now enfold their fillers in loop reads, keeping t/u at [rbp-56]/[rbp-64] and a16's disp32 form at [rbp-136]; the seventh-slot fallback test now pins slot 0's frame clear, the depth-zero tie's last pick). Full suite 853 tests, zero failures. The `--save --compare` gate returned exit 0 (the first run's uniform +2-24% across all cases, including byte-identical leaf shapes, was machine noise and discharged by the re-run per ADR-031: every case back inside the band). Mandelbrot improved 14.4 to 13.2 ms and its inner loop now pays two frame loads (x0, y0) per iteration instead of thirteen; `many_params`, `fib30`, `tak` and `splitmix` are unchanged within the spread.
- **Consequences**: Inner loops in frame functions now retire homed work with register forms and household outer values with one or two frame reads, closing most of mandelbrot's structural gap. Cost: an 11-slot mandelbrot still displaces one inner value per the deeper-loop-tie rule whenever eight or more slots share the deepest depth, so the next milestone (proposed: evaluated 8-home ABI versus live-range assignment against measured wall time) remains the recorded follow-up, and the ranking is a heuristic — a program whose deepest-loop set exceeds six slots silently falls back to frame residency for the remainder, never an error.


### ADR-045: single-read temporaries propagate into their copies; the 8-home ABI evaluates as a follow-up reshape, not this task

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: Task `cairn-allocation`, milestone 3. Mandelbrot's escape loop needs eight simultaneously-live values (count, x, y, x2, y2, next, plus loop-invariant reads of x0 and y0) against six homes, so ADR-044's preference — and any assignment over six homes — must displace at least two. The milestone's question was which of the two recorded directions buys the remaining frame loads: an eight-home ABI or live-range assignment.
- **Decision**: Three findings, one shipped. (1) Shipped — `optimize.apply_copy_propagation`: a `let` whose value is pure (call-free, trap-free), whose binding is read exactly once in the whole function, and whose single reader is a copy assignment (or a let) in the same block takes the definition; every statement between the definition and the copy must leave the definition's operand slots untouched, and nothing else may even mention the binding. It runs after folding (so ladder definitions see folded operands), again after the countdown pass and inside the fusion rounds, capped at 64 applications per function, and refuses silently otherwise. In mandelbrot the `next` transient (`var next = x2 - y2 + x0; ... x = next`) disappears, which removes it from the rank-4 tie set, hands the sixth home to `x0` under ADR-044's rule, and leaves `y0` as the escape loop's only frame load. (2) Evaluated and declined for this task — the 8-home ABI: a seventh home register can only be `rsi` (the universal scratch every non-fold lowering shares) or `rbp` (the frame pointer every frame-addressed store, load and save-area slot needs); either choice requires rsp-relative frame addressing and a scratch-capable rewrite across the emitter's lowering paths. The reshapes are real, but their measured ceiling is the one remaining load, so they are recorded as follow-up scope rather than smuggled under a measured win. (3) Evaluated and declined — interval home reuse across disjoint hot regions: sharing one home between two bindings whose live ranges never overlap collides with the deterministic zero contract (ADR-007), whose prologue clears assume the slot-to-home map never changes mid-function; the repair adds range-entry zeroing and is larger than this milestone's remaining budget.
- **Decision verification**: Failing-first: `CopyPropagationTest` — the substitution test (the temp's `let` disappears and the reader carries the definition) and the loop-body test failed before the pass existed; five guards pin the refusals (second read, an intervening reassignment, a calling definition, a trapping definition, a clobbered operand — the last proven end-to-end too: the copy still sees the materialised 5). `CopyPropagationValueTest` runs the ladder both ways. Full suite 889 tests, zero failures, including the eight-case twin-output parity gate on both baselines. The `--save --compare` gate returned exit 0: no regression above noise, with the fold-and-propagate shapes improving board-wide (collatz 108.9 to 95.6 ms, binary_gcd 194.7 to 171.5, call_chain 44.2 to 39.1, tak, ackermann, folded_math) and mandelbrot 13.2 to 12.7 ms with the inner loop at a single frame load.
- **Consequences**: Every `'temp = combine(...); x = temp'` ladder across all programs stops materialising a dead eighth value, which is what gcc's `next` handling achieves by SSA fusing; mandelbrot's allocation ceiling is now one loop-invariant load, and the value of the 8-home rsp-relative reshape can be re-proposed against that measured residual. Cost: the pass re-walks each function (bounded, cheap, skip-on-refuse), and the definition now evaluates at the copy, so a debugger stepping statement-order sees the definition's reads occur there — evaluation-order semantics are unchanged because the span guards make the operand set identical.


### ADR-046: the immediate and commutative lowerings fold into their destination register

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: Task `cairn-immediate-shapes`. Disassembly of many_params after the allocation tasks attributed three residual wastes: every `homed * literal` paid a `movabs` constant load feeding a two-operand `imul`; a commutative chain parked its accumulator in the first temporary and round-tripped rax through rcx at every level (`mov rcx, r11; add rax, rcx; mov r11, rax`); and a five-or-six-argument foreign copy computed its spare arguments into r10/r11 only to rebind them into guest homes at copy entry (`lea r11, [rbx+5]; ... mov r14, r10`).
- **Decision**: Three lowerings. (1) `machine.Assembler.imul_imm(destination, source, literal)` — the three-operand form, imm8 (0x6B) or imm32 (0x69), REX.W plus ModRM reg,rm; a ValueError beyond imm32 keeps every over-range literal on the movabs path. Both value-position (`_emit_binary`) and destination-known (`_emit_binary_into`'s multiply branch) sites emit it for a homed-name left times an int32 literal, guarding delta-slot copies. (2) `CodeGenerator._emit_commutative_into` — for `+ & | ^` with a known destination, the left chain computes or moves into the destination once and every later level adds straight into it; `_combines_in_rax_only` extends `_computes_in_rax_only` with the `homed * literal` right shape, refusing when the multiply's home IS the destination (a merged-park miscompile shape), and the `UPDATE_OPCODES` branch falls into the new path at its refusal point instead of abandoning the destination. The value-position tail tracks `left_hold` — the temporary that already holds the left operand — so the final commutative combine reads the temporary directly and non-commutative tails (`- / % << >>`, comparisons) pull it into rcx as before. (3) `_emit_parked_copy_arguments` accepts the copy's planned guest tuple and binds a spare argument straight into its guest home whenever that home is not a caller home; `_emit_foreign_call` computes the return-slot override before emission and passes the bound index set to `_emit_foreign_inline`, whose entry loop skips exactly those binds. A spare argument whose guest home is a reused caller home, and every push/pop fallback path, keep the temporary form.
- **Decision verification**: Failing-first: `ImulImmediateEncodingTest` (imm8 `4c 6b db 1f`, imm32 forms, the -100000 encoding, and ValueError refusals — one expected-imm32 byte corrected for the displaced general register), `ImulImmediateShapeTest` (both products of `a` in one main take `imul r12, rbx, 37` / `imul r13, rbx, 171` with no movabs-37 and no two-operand imul), `CommutativeMergeShapeTest` (declaration-order chain folds as `add rbx, r12; add rbx, r13`) and `ParkedCopyArgumentTest` (guest leas `lea r13, [rbx+4]` / `lea r14, [rbx+5]` present; the entry rebind pair `mov r13, r11` / `mov r14, r10` absent). Full suite 897 tests, zero failures, every ADR-044 leaf byte pin intact. The `--save --compare` gate: the first run's uniform +0.1-13.8% drift across byte-identical shapes was machine noise, discharged by a later A/B against a pre-change worktree on the same machine state — the new compiler measured folded_math 17.9 vs 21.3 ms, ackermann 9.9 vs 11.2, many_params 22.8 vs 24.5, and neutrality on binary_gcd, call_chain and tak; the subsequent formal run returned exit 0. many_params' inner loop is now five leas, five three-operand imuls and six adds with no parks, no rebinds and no constant loads.
- **Consequences**: The multiply-accumulate and merge surfaces across all programs stop paying parks, rebinds and constant loads. many_params' residual gap to C (~22.8 vs ~7.9 ms) is now attributed to loop unrolling with multiple accumulators — C spreads the serial add-chain across independent accumulators — recorded as the next candidate task, alongside binary_gcd's shift-loop shape and call_chain's mixed-arity argument handling. Cost: `_combines_in_rax_only` models one more shape than the generic checker, so future lowering changes must keep the two in agreement (the helper's refusal cases are pinned); `_emit_parked_copy_arguments` now depends on plan determinism shared with `_emit_foreign_inline`, which this file's verification pins by re-deriving the override identically at the call site.


### ADR-047: magic division seeds and divisors hoist; the copy guest never borrows a hoisted home

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: Task `cairn-magic-hoist`. binary_gcd's main loop paid four `movabs` loads per iteration — each `x % 2147483647` reloaded both the magic seed (`0x8000000100000003`) and the divisor into the scratch — although the loop's spare homes sat idle. The counter behind the hoist plans never saw the divisor because the divisor fits the immediate range, and the seed is not an AST literal at all.
- **Decision**: (1) `_big_literal_counts._count_in` counts a `/` or `%` right operand (value >= 2, non-power-of-two, so shift-fixup divisions stay out) together with its seed, whenever the lowering will take the magic path. (2) `_emit_magic_division` prefers `hoist_imm`: the seed copies from its hoisted home into the scratch, and the remainder's `quotient * divisor` multiplies by the hoisted divisor register directly; non-hoisted contexts lower exactly as before. (3) `_plan_foreign_copy` reserves `self.homes | self.hoist_homes` everywhere it used to reserve only `self.homes`, so a foreign copy inside a hoisted loop can never take — and silently clobber — the spare home a hoisted constant lives in (the same latent hazard existed for the previous literal-only plans; guest registers therefore come from the true spares or, on shortfall, from spilled caller homes). (4) Pair and delta machine frames skip hoist plans entirely: their copies rebind r13 freely, which would collide with a hoisted spare home in that shape.
- **Decision verification**: Failing-first: `HoistedMagicDivisionTest` pins the hoisted forms (`imul rdx, r13` for the remainder, `mov rsi, r14` for the seed) and the absence of both per-iteration `movabs` reloads. Full suite 898 tests, zero failures. The `--save --compare` gate returned exit 0; binary_gcd 194.9 to 190.6 ms with the dividier/seed reloads gone from its main loop.
- **Consequences**: Looped magic divisions stop paying 16-40 bytes of constant loads per iteration across all programs. The guest-planner restriction also makes the earlier latent corruption path structurally impossible. Cost: a loop whose divisor and seed hoist spends two spare homes it may otherwise want; the stall shows up as fewer copied guest registers (more frame residency inside the copy), never as incorrect values. Copy bodies remain division-free (the existing copy-free guard), so no copy ever reads a hoisted magic register mid-copy.

### ADR-048: looped foreign callees join the inline-copy set

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: Task `cairn-loop-copies`. binary_gcd's hot loop called `bgcd` once per iteration; the `_body_is_copy_free` gate refused any foreign callee containing a loop, so the per-iteration call, argument copies and return round-trip survived every previous copy expansion. The original rule existed to keep break/continue semantics answerable, not because loops cannot be copied: break/continue targets are emission-local stacks installed per emitted body, and sema requires a break/continue to bind to the innermost loop of the same function body, so a looped body's breaks always bind inside the copy.
- **Decision**: `_body_is_copy_free` no longer refuses `syntax.While` (calls and divisions still refuse); a looping, call-free, division-free foreign callee inlines as a full copy including its loops. Two tests in `tests/test_codegen.py::ForeignInlineTest` were replaced (the old `test_a_looping_callee_stays_a_real_call` pin had encoded the refusal, not a soundness property, so this is the PRINCIPLE-014 weaken-with-ADR rename per the ADR-044 precedent): `test_a_looping_callee_without_breaks_copies` pins the copy with zero calls in the caller's region, and `test_a_breaking_looping_callee_copies_too` pins that break/continue bind inside the copy with zero calls. `tests/test_codegen.py::ForeignLoopCopyTest` covers the fingerprint shapes and `tests/test_end_to_end.py::ForeignLoopCopyValueTest` (WINDOWS_ONLY) pins loopy=21, subgcd(48,18)=6, tri(4)=10, brk(10)=3, cont(10)=6 with an unconditional break and a conditional continue inside copy labels.
- **Decision verification**: full suite 928 tests OK (55.9s); `python aikit.py check cairn` 4/4. Disassembly confirms binary_gcd's main loop holds zero `bgcd` calls (only print calls remain). binary_gcd measured 191 -> 173-175 ms (-8.6 percent) in interleaved runs; the honest relative position is at parity with its C twin (165-181 ms band) and behind Rust (148), which wins through the same-loop unrolling ADR-049 candidates already record. The `--save --compare` gate needed four tries: three exited 1 with inverted swings on byte-identical cases (fib30 9.1->41.6->9.3, binary_gcd 392.7->192.2 within a single loaded run), the byte-identity proof discharging them per ADR-031/034: all 14 benchmark `.crn` sources are md5-identical old-vs-new worktree, and the compiled executables of fib30, ackermann, nqueens, splitmix and xorshift are cmp-identical HEAD-vs-worktree, so only binary_gcd's binary legitimately differs. The machine load is verifiable desktop churn (msedge/Code/vibe-acp at 18-61 percent total CPU sampled by Get-CimInstance). The final gate exited 0 with no regressions.
- **Consequences**: the copy set is now (call-free AND division-free), loops included. Call overhead leaves every leaf-shaped looping callee; recorded next mechanisms: binary_gcd residual vs Rust via multi-accumulator unrolling, nqueens/primes home-residency (~25 vs Rust 19-21), many_params serial add-chain needing independent accumulators, mandelbrot's last frame load, call_chain mixed-arity registers.

### ADR-049: the drop-then-restore shift pair fuses, across call boundaries via forwarding

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: Task `cairn-shift-fused-args`. call_chain's recorded next mechanism blamed the sar+shl argument shifts for its 30 percent gap to C: the inlined mix3 body lowered `b << 2` as `mov rax, rdx; shl` over a parameter bound as `mov rdx, r12; sar rdx, 2`, a four-instruction chain where gcc's tree shows one `and`. The identity `(a >> m) << m == a & -2**m` holds for every operand, sign included, when the counts are equal: sar's sign-fill sits only above the operand's top bit and the left shift carries every filled position out of the word exactly when the counts match (k < m loses payload bits, verified with 7: (7>>3)<<2 is 0 while 7 & ~3 is 4, so unequal counts refuse).
- **Decision**: `_shift_through_target` recognizes the equal-count pair at emission (both the rax value-position path and the into-destination path, the latter with the same pending-left leaf rules as `_emit_logical_shift`) and lowers one load plus `and -2**m` (m in 1..31, the and-immediate range). Across call boundaries, `_shift_through_plan` inspects each copy argument shaped `x >> m`: when the callee body reads that parameter exactly once, as `p << m` (walker `_parameter_single_shift_use`, assignment targets excluded, `uses > 1` early cutoff), the forwarding plan maps the parameter slot to the caller's home register; the parameter never binds (its index joins `skip`, the ABI or arriving-temporary register stays garbage but nothing legitimate reads it), and the single use lowers `x & -2**m` from the caller home. Borrowed (spilled) caller homes refuse, the destination_equals_forwarded-source case refuses, and the plan is per-copy state in the saved/restored frame tuple.
- **Decision verification**: failing-first `tests/test_codegen.py::ShiftThroughFusionTest` (four pinned shapes: the fused one-and direct form, the k != m refusal with both shifts surviving, the forwarded copy with no sar in the caller region, the twice-read parameter keeping its bind) and `MultiArgumentCallTest`/`PureArgumentSubstitutionTest` repins named here per the ADR-044/048 precedent (their pins encoded the sar bind the forwarding removes; the mask, alias and reverse-order contracts are re-pinned in their new form). `tests/test_end_to_end.py::ShiftThroughValueTest` sweeps i from -40 to 40 through the forwarded mix3 shape plus direct fused pairs and an unequal-count pin, all against a Python two's-complement model. Full suite 960 tests OK. `--save --compare` gate exit 0.
- **Consequences**: honest measurement: call_chain itself is WITHIN NOISE (calm interleaved A/B: best 40.49 old vs 40.94 new, trial deltas -1.7 to +2.1 percent) - the mechanism hypothesis was wrong; the loop is bound by the loop-carried x dependency chain (xor, sub, lea, cmov per iteration, identical shape to gcc's tree, gcc 34.5 ms keeps a 15 percent lead from outside this lowering). The peephole itself is strictly fewer instructions and gcc-shaped: kept as language surface, not a regression. Recorded next mechanisms for the remaining gaps: nqueens/primes home-residency (~25 vs Rust 19-21), mandelbrot's remaining frame load, binary_gcd vs Rust 148, and the now-documented fact that gcc folds many_params into a constant print (closed-form loop evaluation would be the only path to that case).

### ADR-050: the entry prologue carries only what the body leaves undefined

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: Task `cairn-prologue-slim`. nqueens (cairn 25.2 vs C 22.6 vs Rust 19.1) recurses millions of times and pays a 6-7-instruction prologue on every entry: an unconditional `mov rax, 0` implicit-return zero, three prologue xors clearing slots the body deterministically assigns, and every `var t = 0` lowering as `movabs rax, 0` plus a home move (13 bytes) instead of the xor zero-idiom the prologue itself already used.
- **Decision**: (1) both `_emit_function` paths (home-frame and rbp-frame) gate the implicit zero on `not self._ends_in_return(function.body)` — the identical gate `_emit_foreign_inline` already proved, and sema's "can finish without returning" guard keeps the zero whenever the last statement is no `Return`, so the fall-through margin is unchanged. (2) `_clear_frame` gates every clear (frame slot and home alike) on `not self._cleared_before_use(function.body, slot)`, the pure AST walker already used by the copy path, so a slot whose first top-level touch assigns it never pays the clear. (3) `_emit_value` lowers an `IntLiteral` zero into a home as `xor home, home` (3 bytes) instead of the move-and-store; edge case: a `mov_imm64` of zero that survives elsewhere emits identical values, so only the prologue shape changes.
- **Decision verification**: failing-first `tests/test_codegen.py::PrologueSlimTest` (3 tests: final-return body drops the ten-byte zero move, a body ending in an else-committed `if` keeps it, and a zero-provenance region emits exactly one xor idiom). Repins named here per the ADR-044/048 precedent: `InsideACopyTest.test_a_tail_returning_copy_skips_the_implicit_return_zero` (2 -> 1) and `test_a_copy_whose_last_statement_is_not_a_return_keeps_the_implicit_zero` (3 -> 2) — main's own implicit zero was incidental filler in those counts, main ends in `return 0`, and the copy behavior both tests verify is unchanged. Full suite 963 tests OK (125s). The `--save --compare` gate exited 0 on the calm run; the two first runs showed the documented load-swing inversion (tak +164 percent one window, ackermann +245 percent the next, both impossible for a prologue change). Same-window A/B: nqueens 25.0 -> 23.4 ms (-6.4 percent), carrying toward C's 22.6; final gate bookkeeping listed nqueens 24.0 ms mid-swing.
- **Consequences**: every function entry in every program shrinks by up to 13 bytes and 2-4 instructions; recursive calls (nqueens, ackermann, tak) save it per call. Recorded next mechanisms for the remaining gaps: nqueens/primes home-residency vs Rust 19-21 (gcc-style 12-iteration countdown of `bit <= 2048` scan loops is the recorded lead idea), mandelbrot's invariant inner-loop frame load, binary_gcd vs Rust 148, call_chain's unexplained 15 percent gcc lead, many_params unwinnable at runtime (gcc folds it).

### ADR-051: the outside world joins the language: argc, arg_byte and read_char

- **Date**: 2026-11-16
- **Status**: Accepted
- **Context**: Task `cairn-stream-input`. The language had no input surface: the only builtins were the three print helpers, every program took no arguments, and stdin was unreachable. The agreed roadmap (argv + stdin now; byte arrays, strings, structs and files later) needs the process's raw input bytes first: a compiler without a way to read the world cannot grow strings or I/O on top.
- **Decision**: Three new zero/low-level builtins: `argc()` answers the token count, program path included (the C convention); `arg_byte(i, j)` answers byte `j` (zero-indexed) of argument `i`'s content with quotes stripped, and -1 past the token, past the cap or past the end; `read_char()` answers the next stdin byte or -1 at end of stream. The command line tokenizes once into a static cache (16-byte header is flag then count, then 63 pointer/length slots) plus a 1024-byte arena copy, so quote stripping and outlive-GetCommandLineA reads are both structural, and repeated calls pay only the table lookup. stdin is a static 4096-byte buffer with a cursor and count; one ReadFile per refill. Kernel32 gains `GetCommandLineA` and `ReadFile`; `.data` gains a `WritableData` region after the IAT (zero-seeded by the file image itself) with the same labels/fixup treatment as rodata and a duplicate-label guard across all three sections. QByte/argv edge semantics: whitespace is space or tab, `"` toggles a quoted run (quotes never copied), tokens past `MAX_ARGS` still count but read -1, arena-overflow bytes drop, negative `arg_byte` indices read -1.
- **Decision verification**: failing-first tests per layer: `InputBuiltinsTest` (sema arities), `InputValueTest` and the repinned `ImportDirectoryTest` (pe, e2e), and five e2e cases in `InputSystemTest` (argc with/without args, quoted argument, stdin stream with EOF at both ends, and a 10 KiB piped sum) — the last pins the 4096-refill wrap, not only the fast path. Three compile-shaped defects were caught and fixed between the first run and green: the arg_fill store path's r9 slot-address leftover read as a quote flag (tokens merged); `store_byte` needs an 8-bit register name; and the arg_byte negative-index checks ran after the fill whose rcx/rdx clobbering made the first call answer -1. The quoted-argument test's expectation was corrected to the real subprocess command-line shape (list2cmdline wraps a spaced argument in quotes the scanner then strips). Full suite 997 tests OK after all fixes. The `--save --compare` gate exited 0 with no regressions and several improvements in the same window (mandelbrot -21.2%, hex_io -18.9%, many_params -15.8%); two new cases joined the scoreboard, `argv_walk` (12.8M data-dependent arg_byte reads, index and branch both steered by the byte read so no optimizer may hoist: cairn 33.5 vs C 27.3 vs C++ 26.4 vs Rust 30.2 ms) and `char_stream` (4 MiB piped through read_char against buffered getchar twins: cairn 14.0 vs C 8.3 vs C++ 9.8 vs Rust 14.5 ms, so faster than Rust, not C yet). The harness gained `Case.stdin` piping.
- **Consequences**: programs can now read the world and the roadmap's next steps (string/byte-array types, struct layout, files) can all be lowered onto the same mechanisms: static writable data, table slots and stream cursors. Recorded next speed mechanisms: char_stream's per-byte loop is bound by the cursor increment/test chain and byte_stream bulk-move idiom; argv_walk's 1.23x-to-C gap comes from the arg_byte call not inlining its bounds/cap checks into the caller (recorded as an inline-candidate candidate); the standing nqueens/binary_gcd/call_chain/mandelbrot gaps keep ADR-050's recorded plan list.
### ADR-052: strings join the language: literals, print_str, str_len, str_byte

- **Date**: 2026-11-17
- **Status**: Accepted
- **Context**: Task `cairn-string-exit`, milestone 2 of the approved roadmap after `cairn-stream-input`. The language still had no text surface, so the planned assert-style test helper (`fn check(...) { ... print_str("fail"); exit(1); }`) — the step that turns Cairn's own test suite into Cairn programs — was impossible to write. Milestone 1 of this task shipped `exit(code)` first (IAT-indirect `ExitProcess`, E3013 stop-statement semantics, E3009 return-path fix).
- **Decision**: A `"..."` literal lexes with `\`, `\"`, `\n` and `\t` escapes (E1004 for an unterminated string or an unknown escape) and parses as a `StrLiteral` leaf carrying an `i64` pointer: codegen dedupes equal texts into one `.rodata` blob (`text.encode("utf-8") + b"\0"` behind a `cairn.str.N` label) and materialises the address with `lea_rip`. Three new builtins join the runtime: `print_str(s)` writes the NUL-terminated blob to stdout in one `WriteFile` (length from one terminator scan, no frame buffer, returns `s`); `str_len(s)` scans to the terminator; `str_byte(s, j)` zero-extends byte `j` and answers -1 for a negative index or the terminator position, so callers that bound with `str_len` never read past the literal. All three are plain call-site builtins (arities 1/1/2 in `BUILTINS`), never values; the optimizer passes over `StrLiteral` untouched (leaf for `is_leaf`, neutral to every walker's default fall-through).
- **Decision verification**: failing-first tests per layer: four lexer tests (escape decoding, positioned E1004 for both failure shapes, span columns) plus a parse-span test; sema arity tests mirroring the input builtins; six e2e cases in `StringBuiltinsTest` (raw write, escapes in output, str_len including the empty string, in/out-of-bounds bytes, dedupe twin-mention liveness, and the full assert-helper shape ending in `exit(1)`). The first smoke run compiled and printed correctly on all five probes. Full suite 1068 tests OK. The `--save --compare` gate exited 0 with no regression and several improvements in the same window (mandelbrot -18.6%, argv_walk -26.0%, splitmix -12.9%); the new twin `str_scan` (200,000 passes of 66 data-dependent `str_byte` reads, volatile/`black_box` loads in the C/C++/Rust twins so nothing folds, all four printing the identical total) measures cairn 18.5 vs C 12.4 vs C++ 12.4 vs Rust 30.5 ms — above Rust, behind C by 1.49x.
- **Consequences**: assert-style Cairn test helpers are now writable, and the suite-to-Cairn conversion can start. Recorded next speed mechanisms: `str_byte`'s per-byte push/pop/call shape is the whole str_scan gap, so an inline-when-literal-or-simple rule (index-in-bounds proven by the `str_len` companion) or a bulk `str_slice` builtin are the candidates; `print_str` shares `print`'s stack round-trip, which a don't-need-the-return-value path could drop.
### ADR-053: the select keeps its plus candidate off the critical path, and str_byte answers inline

- **Date**: 2026-11-17
- **Status**: Accepted
- **Context**: Task `cairn-call-speed`. Three recorded gaps had candidate mechanisms: call_chain's unexplained 25% loss to C, str_scan's per-byte helper call, and argv_walk's arg_byte call shape.
- **Decision (M1)**: the select-if lowering emits `lea rax, [t + c]` *before* the flag-setting `sub t, c` — reads t pre-rewrite, so both cmov inputs are one cycle behind t (sub, lea, cmov chained the plus candidate through the sub's result, one serial step deeper than gcc's lowering; scale-2-in-the-lea criticism checks out: the old `(t - c) + 2*c` shape was load-bearing and the reorder needed scale-0 to answer `t + c` directly).
- **Decision (M2)**: `str_byte(s, j)` with a leaf index and a call-free pointer argument inlines in place — sign test, `lea` address, `movzx` byte, NUL test, `mov rax, -1` on miss — with r10 holding the pointer and `temporaries == 0` guarding scratch safety; the runtime helper stays for complex arguments. The call and its push/pop binding disappear from the byte-walk loop.
- **Decision verification**: failing-first shape tests (SelectIfOrderTest pins lea-before-sub; StrByteInlineTest pins the in-body byte load and the miss-path -1); the reorder's first shape passed the byte test but the sibling value-parity tests caught the scale bug (call_chain twin total diverged) — the e2e suite caught it before anything shipped. A/B wall-clock: call_chain 40.3 -> 35.3 ms (-12%, gate -19.7% with the inline), str_scan 16.7 vs 18.5 ms, str_scan local A/B 16.8 -> 14.0. Full suite 1073 OK. Gate --save --compare exit 0 after two identical reruns; call_chain 32.3, str_scan 16.7 ms against C 33.8/13.0 (cairn now faster than C and C++ on call_chain, within 1.28x on str_scan, both far above Rust).
- **Consequences (measured dead ends recorded so they stay dead)**: inlining arg_byte's warm-cache table walk into the caller measured *slower* (38.1 -> 45.4 ms local A/B) — the compact call keeps the loop body inside the uop cache while a fat body pays i-cache and decode cost; binding a leaf arg0 straight to rcx (dropping the movabs+push+pop dance) also measured slower (38.5 -> 40.3). argv_walk's remaining gap is the table walk's ~14 uops per read, not the call; a real win needs a different shape (bulk read or per-byte pointer arithmetic), not inlining. The remaining Rust gaps (nqueens, binary_gcd, primes, mandelbrot, many_params, argv_walk) keep ADR-050's plan list.
### ADR-054: direct var copies, test-based masked conditions, and the non-negative divisor contract

- **Date**: 2026-11-17
- **Status**: Accepted
- **Context**: Task `cairn-loop-speed`, closing the remaining loop gaps: binary_gcd 187 vs C 164, nqueens 23.7 vs C 21.7, primes 24.1 vs C 24.1, xorshift 287 vs C 278 ms. Disassembly diffs against gcc -O2 showed three mechanical slacks, each implicit in the general lowering rather than any one benchmark.
- **Decision (M1)**: a plain variable-to-variable copy loads directly into the slot's home register (`_emit_value` now uses the leaf path only `_emit_into` had): `mov dst_home, src_home`, with `_load_slot` handling frame sources, delta views and the self-assign no-op. The rax round trip (`mov rax, src; mov dst, rax`) bought nothing — every copy in source order reads a value no earlier copy in its run has overwritten.
- **Decision (M2)**: `(a & b) != 0` / `== 0` with both names homed branches from `test home, home` directly (opcode 0x85, two registers). The old path computed `and rax, src` whose result nothing consumed; `test` sets the identical ZF without the copy and without destroying rax. Literal-mask bit tests (`x % 2**b`, `x & m`) keep their existing lowering.
- **Decision (M3)**: a slot initialized from a non-negative literal seeds `nonnegative_slots` (stores still drop the fact; positive updates like `d = d + 2` keep it via `_update_keeps_nonneg`; a copy from a non-negative name keeps it). A division whose divisor slot is provably non-negative skips the `-1` divisor probe and the `INT64_MIN` dividend guard in `_emit_division` — both are unreachable — keeping only the zero-divisor trap. Unchecked divisors keep both guards.
- **Decision verification**: failing-first shape tests per change: `DirectVarCopyTest` (zero rax round trips in a three-copy swap), `NameMaskBitTestTest` (two-register test present, dead `and` gone), `DivisorNoOverflowGuardTest` (guards gone for `d = 3`, kept for `d = -3`). Three older pins were repointed to the strictly stronger contracts: the zero-branch-after-and test now expects the test form, the power-of-two test asserts the one-`sar` form for a proven non-negative dividend and keeps the fixup-chain pin on an unknown-sign parameter, and the division-in-argument test now asserts the guards are *absent* for the proven `y = 2` divisor. Value parity held everywhere (suite 1079 OK, twin outputs identical). Same-window A/B: primes 22.4 -> 21.7 ms (-3.4%), binary_gcd 192.6 -> 184.1 ms (-4.4%); ad-hoc windows showed binary_gcd 187 -> 168 (ahead of C) and nqueens 23.7 -> 21.1 (C parity). Gate `--save --compare` exit 0; the two interim flags (ackermann, tak) were proven noise by same-window A/B (-0.2%) and byte-identical machine code respectively.
- **Consequences**: every hot loop that swaps or copies values, walks bitmasks against a flag, or divides by a counter one step shorter; the scoreboard's measured C gaps for these shapes close. xorshift's residual ~3% to gcc is pure instruction scheduling: gcc reassociates the `state = state ^ (state << a)` chain across statements (its third step's shift reads the pre-xor state, shortening the dependency chain at equal instruction count), which needs cross-statement value threading in the allocator — recorded as the next candidate mechanism, not attempted here.
### ADR-055: two homed names sum in one lea

- **Date**: 2026-11-17
- **Status**: Accepted
- **Context**: Task `cairn-sum-speed`, following the loop-speed task. Mandelbrot's inner iteration paid 25 instructions against gcc's 16; instruction count, not pipeline tricks, was the whole 14.1-vs-10.6 ms gap. Besides the already-shipped copy, test and guard lowerings, the guard's `x2 + y2` and every register-bound sum paid a move into rax plus an add where both operands already live in home registers.
- **Decision**: `a + b` with both names homed (delta views refuse) lowers as one `lea rax, [a_home + b_home]` — base+index*1, no displacement (`lea_scale` reused as-is). `lea` wraps at 64 bits exactly like the add and sets no flags: consumers that branch on the old add's zero flag always emit their own compare, verified per shape.
- **Decision verification**: failing-first `HomeSumLeaTest` (a `return a + b` probe must carry a lea and no REX.W add); two older pins repointed to strictly stronger contracts (`test_an_add_of_a_homed_left_operand_skips_the_rcx_copy` now expects the lea form with no rcx copy, `test_both_pure_arguments_bind_without_the_stack` pins `lea rax, [rcx + r12*1]` where the alias parameter rides in its bound argument register). Full suite 1080 OK; twin outputs identical. Same-window A/B: mandelbrot 11.0 -> 10.0 ms (-8.5%), now level with C in the gate window (13.1 vs its earlier gate 15.0 under the loaded baseline; gate exit 0 with mandelbrot flagged `improved`).
- **Consequences**: sum-shaped code everywhere is one instruction shorter; mandelbrot, splitmix and call-chain-style loops pay it most. Recorded next mechanisms, not attempted here: (1) a no-call function may home two more slots in the r10/r11 volatiles the way gcc uses its full register set once a call never occurs — touching homes, the left-hold temporary pool and the two-homes-per-bank copy machinery, so it needs its own task; (2) gcc's cross-statement xor-shift reassociation for xorshift's scheduling (ADR-054); (3) many_params stays structurally behind a constant-folding gcc twin.
### ADR-056: frame functions borrow r10/r11 as seventh and eighth homes

- **Date**: 2026-11-17
- **Status**: Accepted
- **Context**: Task `cairn-leaf-homes`, the recorded ADR-055 item (1). A frame function's frame slots reload `[rbp-..]` every loop iteration when the six saved homes are exhausted; mandelbrot's `y0` and the string/scan mains' seventh live value were the visible payers. A strict "calls nowhere" gate would have helped no benchmark main (all call `print`), so the rule is not leaf-based.
- **Decision**: a frame function ranked beyond `HOME_REGISTERS` (slots > 6) grants its seventh/eighth hottest slots to r10/r11 (`extra_homes`, folded into `home_of`; `_home_slots` takes a limit). Because r10/r11 are caller-saved, every machine call protects them: `_emit_call_site`, `_emit_direct_indirect_call` and `_emit_stack_call` push/pop the extras around the call with `self.depth` tracking (the padding formula still sees the pushes, so rsp stays ABI-aligned). All borrow-sensitive lowerings see the shrunken pool: `_temp_pool()` = `TEMPORARY_REGISTERS` minus extras backs the left-hold pick, the parked-copy argument registers, the foreign-inline rebind move and the five-to-six-argument inline gate; str_byte's inline r10 scratch bails to the runtime helper when r10 is a home. Guest registers of foreign and shadow copies stay inside `HOME_REGISTERS`, so copies cannot clobber the extras; the frame prologue/epilogue machinery is untouched (extras need no save area, no ABI restore).
- **Decision verification**: failing-first `ExtraHomeFrameTest` (a 10-slot main's call site carries `push r10`/`pop r10`, and an 8-slot str_byte main bails to the helper); five older pins repointed to the strictly stronger contracts (the 7-local fallback test now pins 9 locals with slot 0 displaced, PREFERENCE_MAIN grew two fillers so a0 stays the memory-form pin, FRAME_IN_PLACE_MAIN reads a0..a7 so a0/a1 remain displaced, FRAME_IN_PLACE_TRIPLE folds a0=a0±a3 into `[rbp-8]` from homed a3, and the disp32 test targets a17 at [rbp-144] in a 20-slot main). Full suite 1082 OK. Gate `--save --compare` exit 0, every case within noise; same-window A/B: mandelbrot best 10.02 -> 9.37 ms (-6.6%), median -10.6%.
- **Consequences**: frame functions keep up to eight register-resident names; the string/scan/mandelbrot mains lose their per-iteration frame reloads. Remaining recorded mechanisms: gcc's cross-statement xor-shift reassociation (ADR-054) and many_params' structural constant folding.
### ADR-057: str_byte's inline folds into one SIB byte load

- **Date**: 2026-11-17
- **Status**: Accepted
- **Context**: Task `cairn-strbyte-sib`. The scan-loop shape (`total = total + str_byte(s, j)`) still paid four dead instructions per byte: a pointer hop through rax into r10, an index hop into rax, a `lea r10, [r10+index]`, and a sign test whose branch a proven-non-negative index can never take. str_scan measured 17.0 ms against C's 13.0.
- **Decision (M1)**: the inline's pointer argument binds straight into r10 (`_emit_into`), killing the rax hop; when pointer and index are both register homes the whole address folds into `movzx eax, [ptr_home + idx_home]` (new `movzx_load8_sib`: opcode 0F B6 over a SIB byte, no displacement; base rsp/rbp impossible since homes are never the frame registers). Frame-resident pointers keep the r10 + lea form. The NUL test on the loaded byte stays: zero still answers -1.
- **Decision (M2)**: when the index slot carries the non-negative fact (literal >= 0 seeding or a non-negative update — the same `nonnegative_slots` mechanism the division guards ride), the sign test drops: the load itself proves the address in-range for every reachable execution. A dropped fact (e.g. `j = -1`) restores the test+branch pair, and value parity was verified old-vs-new on a probe whose second pass indexes at -1 (identical outputs, sign branch present in the new build).
- **Decision verification**: failing-first `StrByteSibLoadTest` (SIB movzx present, `mov r10, rax` gone, sign test restored when the fact falls); 1085 tests OK. Same-window A/B (stash push/pop): str_scan best 15.0 -> 13.5 ms (-10.2%), median -12.0% — level with C's 13.0. The gate's ambient windows were bursty (the save leak of this session's flaky runs was trimmed from history; the kept record came from a high-priority clean run: every case within noise, str_scan 16.2 ms, argv_walk -5.6%, char_stream -3.9%, exit 0).
- **Consequences**: every string walk keeps one movzx, one NUL test, one add, one increment, one compare — 5 instructions per byte against gcc's 5, both ends register-resident. Remaining recorded mechanisms: char_stream's read_char call chain (gap to C's fread twin), the xorshift reassociation (ADR-054), many_params' structural folding (staying behind).

### ADR-058: the read_char call-site inline is rejected on measurement

- **Date**: 2026-11-17
- **Status**: Rejected (evidence kept)
- **Context**: Task cairn-readchar-inline. char_stream pays call; ret plus 12 helper instructions per piped byte; the recorded gate windows showed it behind C's fread twin. The planned fix: stdin state as a running pointer plus a bufend qword (one ReadFile per drain, short reads resume in place), and the byte fast path inlined at every call site through three new rip-relative helpers (mov_load_rip, cmp_load_rip, mov_store_rip) with movzx eax, [rdx] riding the loaded pointer.
- **Attempted work**: runtime rewrite and codegen inline completed; value parity verified old-vs-new on 4 MiB and 32 MiB pipes (identical totals, 406857728 and 3254861824); three failing-first shape tests went green; full suite reached 1088 OK.
- **Evidence against it**: a three-way same-window A/B (15+30 interleaved runs, alternating order, best/p25/median) showed: old call-based build 39.42 ms best, new-runtime-with-plain-call 39.48 ms (neutral), and the inline-call-site build 70.28 ms best (+78%) with the gap strictly linear in byte count; from a disk file (full refills, no pipe churn) the two builds ran equal, isolating the cost to the steady per-byte loop. The plain call leaves the CPU an out-of-order overlap window across the call boundary that the inline's store-then-reload cursor chain cannot recover; the inline measured about 7.3 cycles per byte against 4.4 for the call version on this machine.
- **Decision**: keep the call-based read_char and revert all of this task's code to HEAD: the old build already measures level with C on a quiet window (8.9 vs 8.5 ms best over 4 MiB, within 5 percent), so the inline trades a real 40 percent regression for a gap that mostly was measurement noise from ambient load. The three rip-relative helpers stay out of cairn/machine.py until a consumer exists.
- **Consequences**: char_stream's recorded gate rows (11.4 to 12.9 vs C's 8.5) reflect loaded windows, not a per-byte shortfall. Remaining recorded mechanisms: argv_walk (31.8 to 32.8 vs C's 20.2), the xorshift reassociation (ADR-054), many_params structural folding (staying behind).

### ADR-059: arg_byte(0, j) loads its cache slot at the call site

- **Date**: 2026-11-17
- **Status**: Accepted
- **Context**: Task `cairn-argvbyte-inline` (recorded mechanism from ADR-057/058). argv_walk's 12.8M `arg_byte(0, (i + total) & 63)` iterations paid a call, a flag re-check, a count reload, four bound checks and a ret per byte - 2.55 ns/byte against the C twin's 1.57, whose pointer and length bind once outside the loop. The ADR-058 lesson stands: on this machine the equivalent read_char inline lost 78 percent to its call version, so the plan carried an early three-way same-window A/B and a fast-revert rule.
- **Decision (M1)**: `arg_fill` copies slot 0's pointer and length out to two dedicated qwords (`ARGV_PTR0_LABEL`, `ARGV_LEN0_LABEL`, appended last so no earlier label shifts). With a literal token 0 and a leaf index, `_emit_call` inlines the whole read: the leaf computes into rax, two `mov_load_rip` calls bind the slot values, the length compare (and a sign test, unless the index is provably non-negative) branch to a miss route that calls the runtime helper unchanged. An AND-masked index (`x & m`, m >= 1) is provably non-negative - the mask clears the sign bit for any operand - so `_expression_nonnegative` learned that fact and the masked benchmark drops the sign test.
- **Decision (M2), the bug the work exposed**: the first `arg_byte` call on an unfilled cache answered from garbage - `arg_fill` clobbers rcx (token) and rdx (byte index), and nothing restored them across the fill. The helper now crosses the fill on the stack (two pushes keep the call distance ABI-aligned). Failing-first: the fresh e2e double-print test printed (-1, -1) before, (x, 120) after; the 72-print parity probe differed from the old build only at j = 0, exactly at the fixed call.
- **Decision verification**: failing-first `ArgByteInlineTest` (four pinned shapes: the two rip-rel slot loads, the folded movzx, the negative-index sign branch, the masked-index drop), 1090 tests OK. Same-window A/B: 22.30 vs 27.49 ms best (-18.9%), median 23.15 vs 29.35 (-21%). Gate `--save --compare` exit 0 at high priority: every case within noise, argv_walk 32.6 -> 20.3 ms (-37.9%) - level with C's 20.1.
- **Consequences**: argv-table reads stop paying a call per byte whenever the token is the literal 0; other token indexes keep the helper. The AND-mask non-negative fact also arms any future lowering that needs the same proof. Remaining recorded mechanisms: xorshift reassociation (ADR-054), many_params structural folding (staying behind), char_stream (measured level with C on quiet windows, ADR-058).
### ADR-060: Reuse the subtraction zero flag and canonical multi-byte loop pads

Date: closed by task cairn-bgcd-flags (milestones 1/2 ec37041, 2/2 3f052bd). Accepted.

Context. The binary_gcd twin paid 182.5 ms against C's 166.6 ms best. A
head-to-head disassembly of both subtraction loops showed, per steady-state
iteration: a `cmp $0, home` immediately after `sub home, left` (the sub's
zero flag already answers the break check), and a fall-through alignment
pad of 6-13 one-byte nops between the odd test and the shift-loop body,
entered once per subtraction iteration.

Decision. Two isolated codegen mechanisms, each with a failing-first shape
test and its own semantics slice:

1. Equality tests of a homed name against zero skip `cmp_imm` and branch on
   the outstanding ZF summary when the assembler's last ZF-setting op
   (add/or/and/sub/xor, register or immediate forms) targeted exactly that
   home. The reuse is gated to je/jne: signed comparisons read SF/OF and
   keep their cmp. Labels and every other emission invalidate the summary.
   The previously unreachable rax fallback skip gains the same equality
   guard, closing a latent signed-compare hazard.
2. `Assembler.nop_pad` composes the canonical nop family (90, 66 90, the
   0F 1F set, largest form first) instead of one-byte nop trains.

Consequences. binary_gcd measured 160.8 best in the combined high-priority
gate (ahead of C's 166.6); all other cases within noise, none regressed;
1099 tests OK; outputs value-identical. The health of the mechanism rests
on emit() clearing `zf_status` unconditionally: any future assembler method
that sets a flag without summarizing stays safe, and every flag-summary
extension must name its clearing discipline (PRINCIPLE-009, PRINCIPLE-003).
Known shape kept deliberately: the subtraction loop still re-tests `b != 0`
at the top; removing that recheck requires a nonadjacency data-flow fact
(sar of an even nonzero value never reaches zero), recorded as future work.
### ADR-061: The xor-zero store records the non-negative fact

Date: closed by task cairn-nonzero-facts (cd93a03). Accepted.

Context. str_scan paid 15.6 ms against C's 11.8 best while its byte load
was already inlined. The disassembly showed a `test %r15; js` sign guard
in front of every per-byte `movzbl`. The scan index is `var j: i64 = 0`
followed by `j = j + 1`: the zero initialization lowered to the xor-zero
idiom, whose path discarded the slot's non-negative fact, and only a
non-negative literal at the general store path records one. `j = j + 1`
keeps facts; it cannot establish them, so the scan never earned its fact.

Decision. The xor-zero fast path in `_emit_value` records the fact for
non-delta slots: a compile-time zero store is the strongest non-negative
evidence a slot can get. The arg_byte negative-route test was re-targeted
to a parameter index (the only still reachable signed shape) and the
control keeps the full strength of the old assertion there.

Consequences. str_scan measured 16.12 -> 13.96 best in the same window
against an unchanged C twin; argv_walk and char_stream over loop-driven
indices no longer sign-test when their index walked up from zero. Facts
remain scoped: they die at any other store, never ride the delta view,
and the js route stays on every index whose proof the codegen cannot
make. Future work recorded: the subtraction-loop top recheck in
binary_gcd needs a genuine data-flow pass (sar of even nonzero never
reaches zero), untouched here.

### ADR-062: The stdin handle lives in static state, refills read 64 KiB

Date: closed by task cairn-stdin-refill (9e6df5b). Accepted.

Context. char_stream held a stable ~4 ms gap against its C twin
(~12 vs 8.10 best). Reading the refill path showed the drained buffer paid
two syscalls per refill: a `GetStdHandle` fetch and a 4096-byte
`ReadFile`, so a 4 MiB stream issued 1024 of each.

Decision. One writable qword (reserved last, so no hot layout shifts)
caches the stdin handle after the first fetch; drained refills load it
rip-relative and refill from a 64 KiB buffer, cutting a 4 MiB stream to
16 `ReadFile` calls and one `GetStdHandle`. The per-byte fast path is
unchanged, per ADR-058. The first A/B exposed a real bug: the cached
branch jumped to `handle_ok` with the handle only in rax while
`ReadFile` reads it from rcx; small-stream tests passed on register
persistence luck. The fix routes both branches with rcx carrying the
handle, and test_the_handle_cache_survives_many_refills pins it with a
70 KiB payload crossing the refill boundary.

Consequences. Same-window A/B over three windows: outputs identical
every window, new best 12.49 -> 11.98 ms, medians never worse; the quiet
gate window put char_stream at 12.5 ms against C's 8.50. Most of the old
gap lived in the per-byte loop, not the refill path, so closing the rest
needs loop work, not more syscall batching.

### ADR-063: literal loop conditions branch; read_char's state loads stay lea+mov

Date: closed by task cairn-charloop-shave (6e6d087, 6e25d87). Accepted
(both halves decided on measurement, one accepted, one rejected).

Context. char_stream's per-byte loop is the classic mined target: a
`while true { c = read_char(); if c < 0 break; total += c; }` caller plus
the read_char helper. The latch emitted `movabs $1,%rax; test; jne`
per iteration, and the helper's prologue paid two `lea rip` + `mov`
pairs before the cursor/count compare.

Decision [1/2, accepted]. A 0/1 literal condition matches its requested
branch polarity: a bare `jmp`, or nothing on the fallback truth. The
do-while rotation stays correct for `while false` because the mismatched
case never jumps and the head jump-to-end is a real branch.

Decision [2/2, rejected]. The same prologue rewritten with
`mov r64,[rip+disp32]` loads (12 bytes instead of 20, a new
mov_store_rip helper) measured 1.22-1.42x SLOWER over four interleaved
same-window A/Bs (15.07->15.55, 15.73->19.16, 14.87->21.18,
12.32->12.01). The address-register round-trip wins here; per ADR-058,
shorter is not faster on this box. The helper came out; the lea+mov
shape is pinned by ReadCharPrologueTest.

Consequences. every `while true`/literal-condition loop sheds two dead
uops and 10 bytes from its back edge; gate windows were load-noisy, so
the interleaved A/B is the recorded decision evidence. Remaining
char_stream gap to C lives in call/ret and the compare chain, neither
addressable without cargo-culting shapes this box already rejected.

### ADR-064: the capabilities manifest is the law; the documents bind to it

Date: closed by task cairn-contract-sync (2354a4f). Accepted.

Context. The agent-facing contract had drifted: SPEC.md claimed four
parameters where sema enforces eight, documented 3 of 10 builtins and 3
of 5 PE imports, banned the legal `for` range in its style rules, and
LLM_SYSTEM.md still described a pre-ADR-051 language ("print is the only
builtin"). The README's benchmark table omitted three cases and its size
column predated every runtime helper. An agent trusting any one document
as law writes rejected code - the exact failure class an agent-first
language exists to prevent.

Decision. `python -m cairn capabilities --json` is declared the machine
source of truth, and tests/test_contract.py makes the drift impossible
to reintroduce: the standard surface, the parameter limit, the import
set, the for grammar, the trap-3-vs-CLI-exit-code distinction, the
system card's bounded size and the README's benchmark table all bind to
the manifest. SPEC.md is the complete human rendering (every builtin
gets a §7 semantics row and a §10 entry); LLM_SYSTEM.md stays a
hand-written compact card held under 5,000 bytes by a test.

Consequences. 8 of the 10 contract tests failed against the drifted
documents and now pass; 1118 tests OK. Doc-law changes carry no byte
changes, so they need the contract suite, not a benchmark gate. Full
generation of the card from the manifest remains available if it drifts
again, but the binding tests make that measurably unlikely first.

### ADR-065: stdout is one shared 64 KiB buffer, drained by exit, main's return and flush()

Date: task cairn-buffered-stdout. Accepted.

Context. Every printer ended in its own WriteFile: hex_io paid two syscalls
per loop iteration (~800k for the benchmark) and measured 630-1600 ms
against C++'s buffered formatting. The adopted roadmap named a shared
output buffer its second milestone.

Decision. The runtime gains one 64 KiB stdout buffer whose cursor and
cached console handle ride static state, appended after the stdin and
argv caches so no existing image offset moves. out_append (rax = source,
rdx = length) copies in chunks under the cap and drains through
out_flush_work when the buffer fills; print_i64, print_char, print_hex
and print_str all append there, `exit(code)` drains before ExitProcess,
the entry stub drains after main returns, and the new `flush()` builtin
drains early and answers the byte count. The runtime clobber contract
grows nothing: append and flush touch only rax, rcx, rdx and r8-r11, keep
rbp and rbx intact, and interleaved order follows the call sequence
through a single cursor. stderr (the divide-by-zero trap) stays
unbuffered on purpose.

Consequences. Interleaved same-window A/B, 21 pairs, outputs byte-identical
on all 13 cases re-measured: hex_io 772 -> 42 ms best (912 -> 50 median),
about 18x; the compute cases land in the recorded noise band (top5-mean
0.96-1.04 over a second 31-pair run). Two traps worth remembering: a
stack park below the flush work's frame sits inside WriteFile's 32-byte
shadow window and gets destroyed (the drained count lives in an rbp slot
above it), and print_hex's digit pointer rides rcx/r11, so the append
call takes them directly instead of frame slots. New PE bytes change the
layout, so sub-4% best-of swings in both directions accompany it; the
regression rule stays: decide on interleaved best-of/median, never on
one window.
### ADR-066: a typed SSA IR substrate, proven by differential execution

Date: task cairn-ir-foundation. Accepted.

Context. The compiler pipeline ran lexer, parser, semantic analysis and
AST-ish optimisation straight into the x86-64 emitter, so every future
optimisation pass would need its own AST-pattern recogniser, coupled to the
emitter. The adopted roadmap named a minimal IR its third milestone, with
the production pipeline deliberately untouched.

Decision. `cairn/ir.py` holds a deliberately minimal typed SSA IR: i64
values, single-assignment temporaries, mutable state (locals, parameters,
short-circuit results) in zero-defaulted cells, and every block ending in
exactly one terminator (return, exit, jump, branch). `translate(program)`
lowers the post-optimisation program; `Interpret` executes the module at
full machine semantics - wrapping arithmetic, truncating division and
remainder, the divide-by-zero and i64.min/-1 traps (exit 3, pending output
abandoned), 63-masked shifts, all eleven builtins, and the runtime's shared
64 KiB stdout buffer with its pending/drain/flush bookkeeping - so
`Interpret(translate(p)).run()` must answer exactly what the compiled
executable answers on exit code and stdout bytes. Differential tests
(tests/test_ir.py) pin 23 corpus cases including buffer spill past 64 KiB,
mid-run flush answers, stdin reads, trap-pending loss and exit unwinding,
plus SSA invariant checks (single terminator per block end, single
assignment per destination, definition before use, no destination beyond
the value budget).

Two contract findings surfaced and were fixed. (1) The IR translator's
`||` lowering inverted the truth table - it stored `right == 0` on the gate
path and left the zero cell for the taken path - observed failing first in
the differential smoke (interpreter answered 1 where the machine answered
11) and fixed with an explicit store of 1 on `||`'s short path. The nested
`_call` ignored the exit kind from the immediate call site, so exit() in
main kept running; both are covered. (2) SPEC and LLM_SYSTEM promised
str_byte answers -1 at or past the terminator, but the machine only tests
the byte at the index itself and returns adjacent rodata bytes beyond the
NUL (measured 105 for index 9 of 'hello'). Honoring the doc would cost one
full-length scan per call - a roughly fifty-fold str_scan regression on a
12.8M-call benchmark. The documented contract now matches the machine
(C-style NUL-terminated: -1 for negative j or the NUL at j; past-the-end
reads undefined), the interpreter models the same defined domain, and the
differential corpus stays within defined behaviour.

Alternatives. LLVM or a mature framework: rejected - the independent
backend is a project advantage. Scanning per str_byte call: rejected on
measured cost. Leaving the over-promising doc: rejected - a spec agents
read must not promise undefined behaviour.

Consequences. The optimizer passes of the next milestone operate on this
IR, independent of the emitter, with the differential suite as the
regression shield. Production codegen is unchanged, so no benchmark gate
was required for this task; full suite grew 1156 to 1163 tests, all green.


### ADR-067: AST-level pure inlining and algebraic collection inside the optimizer run

Date: task cairn-ir-passes (milestones 1/3 and 2/3 of the general-optimizer
track). Accepted.

Context. The roadmap's fourth milestone named general optimization passes, not
per-benchmark recognisers. The concrete target was many_params: `mix(i, i+1 ...,
i+5)` with body `return a*31 + b*37 + c*41 + d*43 + e*47 + f*53;` cost six
argument materializations plus one call per iteration while gcc, by inlining and
simplifying, folds the same shape to one multiply-add per iteration. The
milestone text said IR passes; codegen.py is still a tuned 3,800-line AST
emitter (the IR today is a differential execution oracle, not the compile
path), so the passes landed at the AST layer inside the existing pipeline and
the port onto the IR stays deferred until codegen consumes the IR. That is a
placement deviation, not a semantics deviation: this ADR records it.

Decision. Two conservative passes live in cairn/optimize.py and run in
`Optimizer.run` in this order: `apply_pure_inlining`, fold `_block`,
`apply_algebraic_simplification`, fold, then the pre-existing copy propagation,
countdown latches, odd-pair fusion rounds and final copy propagation.

`apply_pure_inlining` inlines every call to a function whose body is exactly one
`return <expr>;` under a 512-node callee budget (substituted body capped at
2x512, 8 rounds). Guards keep the effect and slot parity exact: a parameter read
twice or more takes only a call-free argument (a call is an effect and must not
duplicate; hoisting is deliberately deferred); a parameter read zero times must
not carry a call or trap argument, because dropping the argument would drop its
effect; recursion into the inline set refuses (self- or mutual-, one level per
round, then calls remain); substitution clones per read, never aliases.

`apply_algebraic_simplification` flattens `+`/`-` chains into signed terms,
distributes literal coefficients over the nested chains of a product spine, and
collects like terms by structural signature. It is ring-exact under wrapping
i64: distributivity of multiplication over addition and subtraction holds
modulo 2^64, and `/`, `%` and the shifts never take part in a collection.
Rebuild happens only when at least two non-constant like terms actually merge,
call-carrying subtrees never group, drop or duplicate (`read_char()*3 +
read_char()*4` stays two reads), a zero coefficient vanishes only when the term
has no call and cannot trap (`0*g(x)` stays), and `0 - a` keeps its negation
shape (a lone expression term is never rewritten), preserving the RewriteTest
contract.

many_params verified through the optimizer: the loop body becomes
`total + i*252 + 701` (coefficients 31+37+41+43+47+53 = 252; constants
37+82+129+188+265 = 701). An earlier session note claimed 216/252; that
arithmetic was wrong, this ADR is a correction.

Verification. Failing-first tests: InliningTest (inline a literal-argument
single-return call; keep the call when the parameter is read twice and the
argument carries one; clone per read for a multi-read pure argument; refuse
self-recursion; refuse multi-statement bodies) plus AlgebraicTest (like-term
collection, distribution-then-collection, cancellation to a literal zero with
calls never grouped). Two of the new tests' own expectations were corrected
against the designed contract, not relaxed: `count_calls` counts the surviving
outer call too, and `twice(i)` is deliberately collected to `i*2` by the second
pass. No pre-existing test weakened; suite 1210 OK. Benchmark gate: three
measurement windows all showed 2-20x inflation against the c3d39bf baseline
(xorshift 445 then 326 then 318 ms; many_params 269, 58, 54 ms where an isolated
best-of-21 in the same hour measured 12.4 ms and the old baseline is 23.2 ms),
and char_stream timed out outright - an external load source, not code. For
every case without an inlineable pattern the new executable and the HEAD
executable are byte-identical (verified for char_stream, hex_io, xorshift), so
those deltas are provably not code. A saved record from the first polluted
window was removed from benchmark_history.jsonl before commit; the re-gate is
pending the first quiet window and the task stays open for it.

### ADR-068: fixed-size `i64` arrays with frame-slot elements

Date: task cairn-arrays (milestones 1-3 of the roadmap's language-expansion
track). Accepted.

Context. The first aggregate type. Cairn stores everything in frame slots, so
an array is exactly N consecutive slots reserved behind one binding slot at
semantic-analysis time; element `i` is the frame at the binding slot displaced
`8i` bytes downward. Nothing mixes with the pointer layer planned for later
milestones: no heap, no new ABI, no runtime allocation.

Decision. `[N]i64` binds with a fill `[v; N]` or an aggregate `[e0, e1, ...]`
initializer; `[v; N]` with N > 8 equal literals compiles to one down-counting
store loop, everything else unrolls element by element in source order.
`a[i]` reads and `a[i] = v` stores: a literal index folds into the frame
displacement, a dynamic one negates the index once and lets a scaled `lea`
(`8i` wraps at 64 bits exactly like the scalar arithmetic) form the element
address - negative indices work like two's-complement scalars, no bounds
check exists for dynamic indices (E3017 refuses out-of-range literals at
analysis time). Parameter and return types stay `i64`; a whole array is never
a value (E3016). Every value-expression visitor in the optimizer (reads,
traps, slots, call detection, cloning, parameter substitution) learned Index
and ArrayLiteral; the single-return inliner refuses callee bodies that reserve
array slots, and the countdown-latch rewrite now counts Index reads, which it
previously descended into with a zero default - a loop body reading its
counter only through `a[j]` was silently inverted (found by the new e2e
round-trip, pinned by a refusal test, corrected in `_count_reads`).

Artifact change. The PE optional header now reserves and commits 8 MiB of
stack (was: 1 MiB reserve, 4 KiB commit). The first array fill store sits at
the deepest frame page, which on an almost-untouched 4 KiB commit jumped
straight past the guard page and crashed (C0000005) before any probe could
commit those pages. Committing the stack up front removes the Windows
stack-probe obligation from every generated caller; the 8 MiB ceiling is a
documented limit, revised with the pointer milestone.

Benchmarks. Two new twin cases join the registry: `array_sum` (65536 elements,
one fill, 100 read-modify-write passes, one summation pass) and `sieve`
(Eratosthenes over 0..65536, nested dynamic indices); C, C++ and Rust twins
print the identical totals. Per the standing rule every benchmark case runs in
the benchmark gate, and the gate record lands with the closing commit.

### ADR-069: element-store address fusion and interleaved benchmark trust

Task cairn-speed-2 [1/3]. `ca[i] = ca[i] op e` folded to one address computation
and one memory-operand store: `binary_store_reg` in machine.py, fused emission in
codegen. The refusal duties sit with the value side: a printed index, or a value that
can trap a crossed divisor between the read and the write, refuses to fuse,
because both the trap and the print must run exactly once.

Measurement truth from this task: one gate fired into a loaded machine (another
agent session on this box) printed +69 to +336 percent across the whole scoreboard
and its record was dropped from benchmark history under the pollution rule. A
serial re-gate still misprinted fib30/primes/mandelbrot/argv_walk at +12 to
+24 percent; only interleaved re-measurement (cairn and C twin alternating in
the same seconds, nine rounds) reproduced them at -5.4/-2.7/-10.9/-1.4 percent,
all under their C twins. The scoreboard rows for xorshift, binary_gcd and
array_sum carry the new honest ranges; binary_gcd improved with no code change
(its C twin moved the same direction) and array_sum's -14 percent is the fusion.

Honest-window criterion (many_params, seven runs): best <= 20 ms accepts, best
19-34 ms is pollution. Comparing two gates from different windows beats trusting
one.

Closure proof for the same ADR: every one of the 19 registry cases compiles
byte-identical at 79af21f and at HEAD (the string-folding shapes never fire on
registry sources), verified by full-image SHA-256 through a temporary worktree.
flapping windows mid-gate therefore cannot claim a code regression for these
cases: the honest 79af21f record is the current tree's measurement. A cross-window
regression claim needs a byte-difference first.

### ADR-070: the swap shape lowers branchless through one cmov pair

Task cairn-speed-2 [2/3]. `if a > b { var t = a; a = b; b = t; }` — the
data-dependent swap whose branch mispredicts at the heart of a Stein-GCD
inner loop — lowers to three moves and no branch: one scratch keeps the
old `a`, and both `cmov`s select on the same comparison flags. Refusals:
an else, a different body, a condition not between the two swapped slots
(named either way round), a frame slot (the cmov needs a register
destination), or the temporary read anywhere outside the if, where the
language's never-taken-binding-reads-zero rule beats what a cmov answers.

binary_gcd measured -16.2 percent best and -13.8 median in the interleaved
A/B (147.3 vs 145.4 quiet-record against 162.5/173.5), closing to -4.7
percent from the Rust twin; the full honest gate then read zero cases
beyond the noise band versus 79af21f. The pass refuses whenever any input
of the shape is off, so a refused swap keeps the exact branchy semantics.

### ADR-071: many_params is an unfair twin — every compiler folds it to a constant

Chasing the many_params gap (Cairn 13.2 ms best vs C 10.58) with codegen work
started from the disassembly and stopped there: gcc -O2, g++ -O2 and rustc -O
all compute `total = sum over 10M of mix(...)` at compile time — each twin's
`main` is a print of the constant 0x2cc3a372e58980 and no loop, no call. The
baselines' 10.5-11 ms is process startup plus one print; Cairn honestly
executes the 10M-iteration loop (loop body already folds to the same silver
rule's constant-folded form, 5-6 uops/iter) and pays a real ~2 ms for it. The
"gap" therefore measures nothing about parameter passing or call ABI, and no
Cairn codegen change can honestly close it. Any fix belongs to the benchmark
contract, not the compiler: either reorder the twins' body so the result
depends on data the optimizer cannot see across the whole program (e.g. derive
the loop bound or multiplier from argv under a fixed default, all twins
altered together), or retire the case's "gaps" from the scoreboard with this
ADR cited. Until that ADR-covered contract change, many_params claims no
further codegen work.

### ADR-072: the read_char fast path fuses into its call site; a break-only if jumps directly

Task cairn-read-fuse. `read_char()`'s buffered have-path now answers in the
caller — two rip loads, one compare, one SIB `movzx` and one in-place cursor
bump — with the drained-buffer and EOF paths falling to the runtime helper by
call, so the refill stays out of line (ADR-058's measured refusal is about the
refill path, not the have path, whose clobbers are a strict subset of the call
it replaces). A refusal keeps the plain call when `r10` is a borrowed extra
home. In the same task, `if <cond> { break; }` / `{ continue; }` with no else
inverts into one conditional branch straight to the loop's target — no fresh
label, no fallthrough jmp — which also shortens `while true`'s back edge
enough that its latch becomes a `jmp rel8`.

Byte-identity proof: 17 of the 19 registry cases compile SHA-256-identical;
only binary_gcd and char_stream change bytes, so any cross-window gate claim of
a regression elsewhere is window pollution by argument, not just by history.
The only honest-A/B surfaces: char_stream measured -16.7 percent best and
-5.8 median interleaved (12 rounds, same seconds; the machine's load flapped
76-214 ms spikes through every full-suite window, and two gate runs were
recorded then dropped under the ADR-069 history rule), reading 9.4-11.0 ms
against C's 7.7-10.7; binary_gcd measured a wash (best -2.0, median +1.9 over
14 rounds). One machine-level note: rustc's default msvc toolchain cannot
link on this machine (BuildTools carries no VC/link.exe, and coreutils `link`
shadows any PATH-provided linker), which surfaced as all 12 twin-parity tests
failing on their baseline builds. The scoped fix, `rust-toolchain.toml`
selecting `stable-x86_64-pc-windows-gnu` (MinGW's ld), restored every twin
build; TwinParityTest reads 12/12 after it. Cairn's own pipeline needs no
external compiler and was verified end to end.

### ADR-073: arrays address ascending: every dynamic element access is one SIB instruction
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: Task cairn-array-scale. Element addressing descended with the index: every dynamic path copied the index or lowered it into rax, negated it, formed 'lea rcx,[rbp+rax*8+base]' and then read, updated or stored through rcx, parking the address whenever the value could call. The C twin walks a pointer, and array_sum sat at 11.8-13.7 ms against C's 10.2-11.9. The descending layout was also the only reason an indexed memory operand was out of reach.
- **Decision**: An array's span now runs from slot_offset(base + size - 1) upward: element i sits at the span's lowest displacement plus 8i (previously base slot minus 8i). Literal indices fold into ascending displacements; a dynamic load is one 'mov r64,[rbp+index*8+disp]'; a read-modify-write, and a store whose value is a literal or a homed name, is one indexed memory-operand ALU/mov. machine.py gains _indexed_memory with mov-load/mov-store/binary/group-1-immediate forms, each pinned byte-for-byte in test_machine; the now-unused neg encoder is removed. A value that may call still parks the computed address, because only then can the value's own lowering clobber it. SPEC 5.2, 6.4 and the section 7 rows and the README scoreboard moved in the same change (ADR-064).
- **Consequences**: Blast radius proved by full-image SHA-256 through a pre-change tree: 17 of 19 registry cases are byte-identical; only array_sum and sieve change. A 31-round interleaved same-seconds A/B measured array_sum best 11.76->10.78 (-8.3 percent), ten-fastest mean -6.9 percent, median -6.2 percent, and sieve best -2.8 percent with a ten-fastest mean of -0.3 percent (neutral). Earlier loaded windows ranged from -1.1 to -12.9 percent best for array_sum with one +6.1 percent best and a -6.2 percent ten-fastest mean, so the interleaved statistic decides and a lone best does not. The honest gate read array_sum 12.9->11.4 ms (-12.0 percent, improved), sieve -0.2 percent within noise, every other case inside the 10 percent band. That window found no rustc (the rustup shim directory was off PATH); TwinParityTest re-run with it on PATH passed 12/12, so ADR-072's toolchain pin stands. The hot loop is memory-bound (a 512 KiB frame array), so the residual array_sum gap to C (11.4 vs 10.9 in-gate) is L2 latency and disp32 addressing, not arithmetic the emitter can still remove. A second gate run in a loaded window flagged byte-identical cases up to +53.9 percent and its record was dropped under ADR-069's pollution rule.

### ADR-074: many_params runs the same 10M iterations in all four twins
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: Task cairn-many-params-contract. ADR-071 recorded that gcc -O2, g++ -O2 and rustc -O all compute the whole many_params program at compile time - each baseline main was a bare print of 0x2cc3a372e58980 - while Cairn ran a loop whose body its algebraic pass had already reduced to 252i+701. The case therefore measured folding heuristics and startup, not the six-argument surface it names, and no Cairn codegen change could honestly close its gap. ADR-071 offered exactly two remedies: make the result depend on data no optimizer can see across the whole program, all twins altered together, or retire the case's gaps.
- **Decision**: The first remedy, applied to all four twins in one change. Each twin now takes its loop bound from its runtime's argument count - n = 10,000,000 + argc - 1 in Cairn, C and C++, and 10_000_000 + std::env::args().count() as i64 - 1 in Rust - so a bare run, which is how the harness and the parity test invoke every executable, executes exactly the registry's 10,000,000 iterations while no compiler can know the trip count. The mix body became ((a * b) & 65535) ^ (c + d * 43 + e * 47 + f * 53): the masked product makes the per-iteration value a non-polynomial function of i, so final-value replacement has no closed form to fold, and the total (7,200,005,110,300,544 in every twin) stays below 2^57. tests/test_benchmarks.py::ManyParamsContractTest pins all three properties in each source, and TwinParityTest now compiles and runs the many_params quadruple instead of skipping it.
- **Consequences**: many_params is a measured case again: 21 interleaved same-seconds rounds read Cairn 20.74 ms best, C 12.69, C++ 14.77 and Rust 13.76, and the gate that saved the record read Cairn 20.6 - a real 1.6x gap where before there was an unclosable artifact. The gate's own compare flagged many_params +55.2 percent as a regression because it diffs against a record from the old contract; the compiler is untouched, and full-image SHA-256 over all 19 registry cases compiled from the pre-change tree proves 18 are byte-identical and only many_params changes, so the flag is the contract and not code. The residual mechanism is visible in the listing: Cairn's inlined copy re-materialises 43/47/53 with three movabs and spills three argument values through [rsp] per iteration, while gcc keeps them in registers and folds the mask into movzx; the recorded next levers are constant hoisting for the inlined copy and collecting the linear argument terms after substitution. Cost: the case now costs Cairn about 55 percent more wall time and its image grows by 64 KiB because argc() links the argument arena, so its README size cell moved from 71,168 to 136,704 bytes.

### ADR-075: a constant folds into the three-operand imul whatever the other side is
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: Task cairn-mul-imm. The three-operand imul already existed but fired only when the other factor was a homed name, so a computed factor (a lea, a call result, a temporary) took the general path: park it in [rsp] or copy it to rcx, materialise the constant with a ten-byte movabs, then a two-operand imul. ADR-074's honest many_params loop paid that three times per iteration - the inlined mix's 43, 47 and 53 terms - which was its whole gap to C.
- **Decision**: After the existing homed-name fast path and the scaled-lea products, _emit_binary now asks _literal_multiply_operand for an imm32 constant on either side of a multiply (multiplication commutes and a literal has no evaluation to order against), lowers the other operand into rax, and folds the constant into imul_imm. A literal outside the signed 32-bit field answers (None, None) and keeps the general path. The change is lowering-only: the wrapped product, the operand's evaluation order and every trap stay exactly as before.
- **Consequences**: Falling-first ExpressionTimesLiteralShapeTest pins four shapes: a computed left, a literal on the left, a real call result, and the imm32 refusal. The many_params loop body fell from 130 to 76 bytes - three movabs and three parks gone - and 21 interleaved same-seconds rounds read 20.49 to 18.17 ms best (-11.3 percent) and -10.8 percent on the ten-fastest mean, while mandelbrot, whose bytes also changed, measured a wash (+0.2 percent best, +0.1 ten-fastest). Full-image SHA-256 against be920e2 proves 17 of 19 registry cases byte-identical; only many_params and mandelbrot change. The honest gate improved many_params 20.6 to 18.3 (-11.4 percent) with every other case inside the band. One existing encoding pin had to move with the optimization: ScaledSumTest's factor-6 case asserted the two-operand imul (0f af) and now asserts the three-operand bytes (48 6b c0 06), same meaning - the multiply stays, no lea - while the suite read 1282 OK. The recorded next mechanism for many_params is collecting the substituted linear terms (144i + 296), which gcc strength-reduces into an increment and Cairn still recomputes as three lea/imul/add triplets per iteration.

### ADR-076: a chain that cannot merge at its root still collects inside it
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: Task cairn-algebraic-nested. ADR-075 left many_params at 18.3 ms with the linear part of the inlined mix still recomputed as three lea/imul/add triplets. The algebraic pass looked correct in isolation: a chain like (i+3)*43 + (i+4)*47 collects. But in the real loop the chain sits inside a xor, and the xor sits under a root chain total + (...). That root chain has two distinct non-constant terms, so nothing merges there, _collect_chain answered None, and _algebraic_expression returned that None without ever recursing into the chain's children. Everything nested below a non-merging chain was invisible to the pass.
- **Decision**: _algebraic_expression now collects a + or - chain bottom-up: it recurses into both children first, then runs _collect_chain on the updated tree, and returns the collection when there is one. A chain that still cannot merge answers None only after its children were visited. Nothing else in the pass changed: the same ring-exact term table, the same effect-bearing terms that neither group nor drop, the same rebuild-only-when-the-term-count-shrinks rule.
- **Consequences**: Falling-first AlgebraicTest.test_a_chain_nested_under_another_operator_still_collects pins the exact many_params shape (a xor inside a root chain) and expects one product with 144 and one constant 584; it failed before the change and passes after. The many_params loop body fell from 76 to 43 bytes, and 31 interleaved same-seconds rounds read 18.36 to 13.37 ms best (-27.2 percent) and -27.2 percent on the ten-fastest mean. The honest gate read many_params 18.3 to 13.2-13.4 (-24.8 to -27.8 percent across two windows), so the case now beats C++ and Rust and trails C by 3.5 percent. Blast radius: full-image SHA-256 against 4fda3e1 proves 18 of 19 registry cases byte-identical; only many_params changes, because no other case nests a collectible chain under a non-chain operator with distinct leading terms. Pollution resolutions in the same windows: splitmix (+15.7) and mandelbrot (+12.5) flags were byte-identical builds, so those records were dropped per ADR-069; the kept record still flags fib30 (+15.0) and ackermann (+12.2), both byte-identical too, and 41 interleaved rounds resolved them as washes (-0.1 and +0.5 percent best) with identical SHA-256. A flagged case whose image is byte-identical can only be window noise, and the interleaved re-measure is the evidence.

### ADR-077: a memory increment on the stdin cursor is slower than the index shape; the refusal is recorded
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: Task cairn-stdin-pointer. char_stream is the largest recorded gap (1.38x to C). The fused read_char have path was eight instructions with two rip lea/load pairs, a SIB movzx and a lea+add cursor bump. A pointer-shaped state was implemented: cursor and end become addresses into the static buffer, the fused path becomes mov rax,[rip+cursor]; cmp rax,[rip+end]; jae drain; movzx eax,[rax]; inc qword [rip+cursor]. Its instruction count is strictly lower (five vs eight) and the address arithmetic is gone.
- **Decision**: REFUSED, and the code reverted unshipped. Three builds of the same benchmark in one interleaved window: the shipped index shape read 11.63 ms best and 11.87 ms ten-fastest; pointer state with the memory increment read 16.86 and 17.70 (+45 to +49 percent, a clear regression); pointer state with the old lea+add bump read 11.65 and 12.09 (best +0.2 percent, ten-fastest +1.9 percent, inside noise). The memory increment lengthens the loop-carried chain: each iteration's cursor load must wait on the previous iteration's read-modify-write of the same word, and that chain - not instruction count - is what the loop spends its time on. The second variant shows the pointer state itself is neutral, so there is nothing to keep: the state word, the cmp reg,[rip] and inc [rip] encoders and the ae condition were all removed.
- **Consequences**: char_stream keeps its 1.38x gap and the index shape, and the refusal is now pinned: ReadFuseTest.test_the_read_loop_never_bumps_the_cursor_with_a_memory_increment fails if a memory increment returns to the fused path. The mechanism this establishes is the next real lever and it is a different kind of change: the baselines keep the stream pointer in a register for the whole loop, while Cairn reloads it from memory on every call because the fused path has no loop-carried register. Closing the gap needs the cursor hoisted into a register across a call-free region - a loop-wide scheduling pass, not a call-site fusion. Cost of the experiment: none shipped; the probe tree and its two builds lived outside the repository.

### ADR-078: a computed left combines with a homed right in rax, with no park
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: Task cairn-homed-right. mandelbrot's inner loop sits 1.1x over C. Its x * y / 2^20 + y0 lowered the division result through push rax / mov rax, r10 / pop rcx / add rax, rcx because the generic binary tail always evaluates the right operand into rax and combines with a held left. The mirrored fast path - a homed left combines with a right computed in rax - already existed; its mirror image did not.
- **Decision**: After the homed-left rule, _emit_binary now recognises a homed right operand for +, &, |, ^, * and - and for every comparison: when the right side is a Name with a home (not the delta pseudo-name) and the left side contains no call, the left is lowered into rax and combined directly with the home. - becomes one sub and comparisons one cmp plus setcc; the push/pop park and the mov-to-rcx park disappear. Calls on the left are refused because an extra home (r10/r11) is caller-saved.
- **Consequences**: Falling-first HomedRightOperandShapeTest pins four shapes: add, sub, compare and the exact mandelbrot division-plus-home tail (sar rax,20 immediately followed by add rax,home, so no instruction remains for a park). Suite 1288 OK. Full-image SHA-256 against 1457723: 17 of 19 cases byte-identical; only nqueens and mandelbrot change. Interleaved same-seconds A/B: nqueens -3.8 percent best and -2.4 ten-fastest, mandelbrot +2.5 percent best / -0.6 ten-fastest - a wash that contradicts the instruction-count expectation, because that loop is latency-bound on the multiply/division chain and the stack-engine push/pop was already cheap; call_chain, byte-identical, swung +5.0 percent in the same rounds and calibrates the noise. Gate windows in this session were polluted (byte-identical cases flagged up to +500 percent), so both records were dropped under ADR-069 and the decision rests on the interleaved A/B plus byte identity; no README range changes because the observed bands stay inside the documented ones. The change is kept for the instruction reduction and the nqueens gain, not for mandelbrot.

### ADR-079: a stdin-reading leaf keeps the buffer base, cursor and count in registers
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: Task cairn-stdin-promote. ADR-077 measured the fused read_char fast path's bottleneck precisely: every byte paid a memory read-modify-write on the static cursor, and the next iteration's load waited on it (pointer state with inc [rip] measured +45 percent). The baselines instead keep the stream pointer, the buffer and the end in registers for the whole loop - glibc getc's fast path is a register compare, a byte load and a register increment. Cairn had no way to keep anything in a register across a read_char call, because the cursor word is the only place the helper and the fused site agree.
- **Decision**: A function whose whole body calls only builtins (so no user call, no inline copy that could rebind guest homes) and that reads at least one byte promotes the stdin buffer base, the cursor index and, when three spares exist, the count word into spare callee-saved homes for its whole body. Entry loads base and index (lea + mov), the single epilogue stores the index back on every return path, and the fused site becomes: cmp index,count; jb have; store index; call the helper (which owns the refill, the byte and the memory bump, and returns byte or -1); reload index and count; done; have: movzx eax,[base+index]; add index,1. The promoted registers ride the ordinary home save area, so the prologue's 16-byte parity math already covers them. Builtins cannot be redefined (E3012), so the check is exactly a leaf test, and every runtime helper preserves the callee-saved registers.
- **Consequences**: Falling-first tests pin the promoted triple shape, the two-spare pair with the memory compare, the no-spare fallback, and that a body with a user call keeps the memory fusion; suite 1297 OK; TwinParityTest 13/13; full-image SHA-256 against 420cd32 shows 18 of 19 registry cases byte-identical - only char_stream changes. Two real bugs were caught and fixed by the existing oracles before this shipped: (1) the first draft re-read the byte after the drain call, so every refill dropped one byte - the IR differential corpus (read1/read2) caught it; (2) movzx_load8_sib encoded an r13 base as mod 00, SIB base 101, which in 64-bit x86 means no base at all (RIP/disp32) and REX.B does not override it, so [r13+index] silently read four bytes of the following instruction as an address and faulted - the two-byte stdin end-to-end test caught it, and the encoder now takes the disp8=0 form for any base whose low bits are 5 (rbp too), pinned by SIBByteLoadTest. Performance: a 31-round interleaved same-seconds A/B measured the pair-only form at 18.12 -> 15.90 ms best and -10.0 percent on the ten fastest with a byte-identical control at +0.3 percent in the same rounds; later 21-31 round rounds in loaded windows read best 12.65 to 11.77 with C at 11.65 in the same rounds, and were too noisy to claim more than parity-with-C at best. The count home removes one load per byte on top of the pair. Two gate runs were dropped under ADR-069 because their windows were loaded. The kept record is the best-of-11 run at 17a5e10: its [WINDOW] probe read 13.85 ms against the 20 ms criterion (quiet), char_stream measured 11.05 ms best against C's 9.84, C++'s 10.01 and Rust's 14.89, and the README's 9.4-13.3 ms range still contains it. That record's compare also flagged hex_io (+16.0 percent) and sieve (+11.0 percent); both images are byte-identical to the baseline commit by full-image SHA-256 through a git-archive tree, so the flags are cross-window drift, not code. Even two saved runs therefore carry a 10-16 percent drift floor on this machine's small cases, which is why codegen levers are decided on interleaved same-seconds A/B plus byte identity rather than on one gate comparison.

### ADR-080: a saved benchmark record carries its own quiet-window probe
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: Session evidence handling. ADR-069 defines the pollution rule: a gate window is trustworthy only when the many_params best is at or below 20 ms. In practice the rule was applied by hand: three gate runs across the ADR-076/078/079 sessions were dropped after their flags turned out to sit on byte-identical builds, and the audit trail of why each record was dropped lived only in the ADRs. A record that does not say how loaded the machine was lets a flag from a busy window read exactly like a regression.
- **Decision**: run_benchmarks.record_from_reports now writes probe_ms (the run's cairn many_params best) and quiet (probe <= the ADR-069 20 ms criterion; null when the case is missing) into every saved record, and --save prints the window state it recorded. --compare reads the newest record's quiet field and prints a warning before the per-case lines when that baseline was loaded, naming the probe and telling the reader to verify byte identity before treating a flag as a regression. Nothing about measurement, the noise band or the exit codes changes.
- **Consequences**: Falling-first QuietWindowProvenanceTest pins the quiet, loaded and unknown outcomes and the warning's presence only for a loaded baseline. Existing records are read unchanged: a record without the field answers unknown and warns nothing, so the history stays append-only and old lines are not rewritten. Suite and the strict project gates cover the change; README's methodology paragraph now states what a record carries and what --compare warns about.

### ADR-081: the division cmov shape is not mandelbrot's gap; the per-pixel setup is
- **Date**: 2026-11-20
- **Status**: Accepted
- **Context**: Task cairn-mandelbrot-division. mandelbrot sits about 1.1x over C. Disassembling the gcc baseline (objdump on the -O2 build) showed its inner loop divides by 2**20 with lea + test + cmovns + sar, a three-cycle chain, where Cairn emitted the sign-word chain sar/shr/add/sar, a four-cycle chain, with one more instruction. The shape looked like a clean one-instruction, one-cycle win on a hot loop.
- **Decision**: REFUSED, code reverted unshipped. Three measurements killed it. (1) Instruction counts: the new shape is four instructions and eighteen bytes against five and eighteen - smaller in instruction count, not in bytes. (2) Registry measurement: mandelbrot measured 20.8M against 20.9M cycles median (+0.4 percent) and +2.0 percent best, but a byte-identical control (nqueens) swung +1.2 percent median and +4.2 percent best in the same protocol, so the effect is inside this machine's noise floor. (3) A purpose-built division microbenchmark (20M iterations of an LCG feeding x / 2**20) measured the new shape 4.8 to 7.5 percent slower, also inside a floor the identical-binary control had just demonstrated at 4.2 percent; that loop is throughput-bound because the LCG chain hides the division entirely, so it cannot resolve a one-cycle latency change either way.
- **Consequences**: The real mechanism is now recorded, and it is not the division: 20.8M cycles over 4 x 540 x 960 pixels is about ten cycles per pixel, so the average inner loop is entered barely once or twice - most of the default view escapes immediately. The cost is the per-pixel setup, and the gcc listing shows exactly where: gcc keeps x0 and y0 in registers and increments them by 16384 and 24576 (dd ,%r10), while Cairn recomputes px * 16384 - 4194304 with an imul plus a sub on every pixel and rame * 8192 + py * 24576 - 1572864 with two imuls plus a parked add on every row. The recorded next lever is loop induction-variable strength reduction: an affine loop-invariant expression of a counter whose only assignment is a constant step becomes a running register, which is exact under the language's wrapping int64 arithmetic and would remove those imuls from mandelbrot's px and py loops (and from any user loop of the same shape). The measurement tooling note is also recorded: QueryProcessCycleTime makes a run immune to being descheduled, but cache and frequency contention under this session's external load leaves a four to five percent floor, so a lever below that floor cannot be claimed here.

### ADR-082: mandelbrot is a per-pixel benchmark; strength reduction buys nothing
- **Date**: 2026-11-20
- **Status**: Accepted
- **Context**: ADR-081 refused the cmov division shape and recorded loop induction-variable strength reduction as the next lever, on the evidence that gcc keeps x0 and y0 in registers and increments them while Cairn recomputes px * 16384 - 4194304 with an imul and a sub on every pixel. This task measured the ceiling of that transformation before building it: a hand-strength-reduced twin of the same program, with the imuls gone and running adds in their place.
- **Decision**: REFUSED before implementation, on a direct ceiling measurement. A temp-dir twin of mandelbrot.crn initialises x0 and y0 once and increments them at the pixel and row tails (x0 += 16384, y0 += 24576), leaving the inner loop byte-for-byte the same; full-image inspection confirms the base build carries the per-pixel imul encoding (49 69 fb 00 40 00 00) and the twin carries none, and both print the identical total (2,073,726). QueryProcessCycleTime over 21 runs each: 13.86M to 13.73M cycles median (-0.9 percent) and 13.15M to 13.11M best (-0.3 percent) - inside the 4-5 percent floor this machine's identical-binary controls demonstrated in ADR-081. The transformation therefore has no measurable value here, and no compiler pass was written for it.
- **Consequences**: The measurement reframes the case and corrects ADR-081's recorded next lever. The printed total is 2,073,726 while the grid is 4 x 540 x 960 = 2,073,600 pixels, so 99.994 percent of pixels execute exactly one inner iteration and only 126 ever take a second: on this view the benchmark is per-pixel fixed work - five slot zeroings, the two-condition entry test, one inner body, the exit, the accumulate and the counter bump - not inner-loop arithmetic. At 13.15M cycles that is about 6.3 cycles per pixel, which with roughly 25-30 executed instructions per pixel is already near the machine's retirement limit; the per-pixel imul was fully hidden behind that overhead, exactly as the -0.9 percent measurement shows. Consequence for the project: mandelbrot's remaining margin to C is not a codegen defect to chase with strength reduction, and the thread is closed until a measurement shows a specific per-pixel instruction to remove; a benchmark whose escape counts were dominated by the inner loop would need a different view (a zoomed region) and is a benchmark-contract change, not a compiler task.

### ADR-083: mandelbrot's remaining margin is startup plus a latency chain; the thread is closed
- **Date**: 2026-11-20
- **Status**: Accepted
- **Context**: ADR-082 closed mandelbrot as per-pixel fixed work after measuring that removing the per-pixel imul bought -0.9 percent. Two candidate per-pixel levers remained from the gcc comparison: gcc's entry test is skipped because the zeroed accumulators make the while condition statically true, and gcc initialises x0/y0 once and increments them. This task measured both hand-transformed twins and, separately, the fixed startup cost that every short benchmark pays.
- **Decision**: THREAD CLOSED, no compiler pass written. Hand twins of mandelbrot.crn in a temp directory, each printing the identical total (2,073,726): (a) a rotated twin whose loop is while true with the condition checked once at the bottom, legal because x2=y2=count=0 satisfy both conjuncts at entry - this drops the five-instruction entry test from every pixel; (b) the strength-reduced twin from ADR-082. Paired protocol (base then variant in the same round, 31 rounds, ratio per pair): rotated 1.0050 median (+0.5 percent), strength-reduced 1.0071 (+0.7 percent), with p10-p90 spans of 0.85-1.15 - both inside the machine's noise. Separately, a minimal program (print(0)) costs 9.32M cycles median and 7.39M best on the same image, while the gcc equivalent costs 13.45M: about 40 percent of mandelbrot's 24M measured cycles is process startup, not the loop.
- **Consequences**: With ~9.3M cycles of fixed cost and ~14.7M of per-pixel work at 2.07M pixels, the loop averages ~7 cycles per pixel. Removing a fifth of the instruction stream (seven of ~36 instructions) changes nothing measurable, so the case is not front-end bound; the dependency chain x2-y2+x0 -> x -> x*x -> x2 (and the parallel y chain through multiply and signed division) is ~6-7 cycles and is what the pixels cost. Together with ADR-081 (the cmov division, one cycle shorter, also unmeasurable) that closes every per-pixel lever visible in the disassembly: mandelbrot's margin to C is startup share plus chain latency, and neither is a codegen defect to chase. Two methodology facts are recorded for the suite: a ~9.3M-cycle startup floor is a large share of every small case, which is the mechanism behind the 10-16 percent cross-window drift in ADR-079, and short cases cannot resolve codegen deltas - so a future mandelbrot claim needs either a zoomed benchmark view (a twin-contract change) or a whole-process measurement that subtracts the empty-program floor.

### ADR-084: every short case is loader-dominated; the harness gains a compute-adjusted view
- **Date**: 2026-11-20
- **Status**: Accepted
- **Context**: ADR-083 found that a minimal Cairn program costs ~9.3M cycles and that mandelbrot is ~40 percent startup. This task measured the floor properly for all four languages, with QueryProcessCycleTime and an empty-program control built by each toolchain, and tested whether Cairn's own image size contributed to it.
- **Decision**: Two results, one tool. (1) Floors, 41 rounds each, median/best cycles: Cairn 5.71M/4.94M, C 8.17M/7.40M, C++ 17.12M/14.93M, Rust 11.76M/10.58M. A Cairn image built with 4 KiB instead of 64 KiB stdio buffers is 13,824 bytes instead of 136,704 and starts identically (5.78M vs 5.86M median - noise), so the floor is the OS loader plus each runtime's own initialisation, and Cairn's is already the cheapest of the four: 2.5M cycles under C's CRT init, 6M under Rust's, 11M under C++'s iostream init. (2) Because every case is scored on whole-process time, that floor is a large and unequal share of a short case's total, so the harness now accepts --floors: it builds one empty program per available language inside the same temp folder, measures each with the same round count, prints the floor line, and prints a compute-adjusted table (each case's best minus its own language's floor) marked diagnostic. The scoreboard, the verdicts, --strict, --save and --compare all stay on whole-process time, which is what a user waits for.
- **Consequences**: The floors view changes how the scoreboard must be read, and the README now says so: a short-case win can ride the cheaper start rather than faster code. Concretely, C's char_stream total (9.84 ms) is close to its own empty-program cost, so most of that case's wall time is process and pipe setup for both languages, and its compute-adjusted margin is small. That is consistent with ADR-079/081/082/083, where per-pixel and per-byte levers measured 0.5-1 percent on totals the loader dominated: the deltas were real but unreadable in the total. The tool makes them readable without weakening any claim - the scoreboard still measures what a user waits for, and the adjusted column is labelled diagnostic, clamped at zero when a best falls under a noisy floor, and omits a language whose floor could not be measured. Four unit tests pin the subtraction, the per-language floor binding, the clamp and the missing-floor omission; the empty sources are constants so the floors are measured, never rebuilt from the registry.

### ADR-085: runtime division narrows to 32-bit div behind a two-compare guard
- **Date**: 2026-11-20
- **Status**: Accepted
- **Context**: The compute-adjusted view (ADR-084) named primes as the largest honest compute gap: Cairn 17.66 ms adjusted against Rust's 7.56. Disassembly showed why: every modulo by the runtime divisor d is a 64-bit idiv (48 F7 FE), while rustc emits a 32-bit div (F7 F6) after narrowing the same values through its range analysis. A purpose-built C twin of the trial-division loop, identical except for the operand width, measured the ceiling: 57.72M cycles for 64-bit against 41.01M for 32-bit, -29.0 percent median over 21 runs, both printing the same total.
- **Decision**: A runtime division or remainder now starts with a two-compare guard: cmp rax, 2**31-1 then ja wide catches a divisor that is negative or above the bound through one unsigned comparison; cmp rcx, 2**31-1 then ja wide does the same for the dividend. Inside the guard the emitter zeroes rdx, moves the dividend into rax and takes div r/m32; every other shape falls to the unchanged 64-bit path, which keeps both fault rules (zero divisor, and INT64_MIN / -1) because those are exactly the shapes the guard rejects. The 64-bit fallback is out of line, so the hot path stays straight-line, and the guard costs two perfectly predictable compares in the common case. Literal divisors are untouched: the power-of-two shift and the magic-multiply paths run before this and still win for compile-time constants.
- **Consequences**: primes measured 23.72 -> 17.94 ms best (-24.4 percent) and 24.57 -> 19.14 ms on the ten fastest (-22.1 percent) in 31 interleaved same-seconds rounds, with a byte-identical nqueens control moving -0.5 percent in the same rounds. On the README's saved bands that moves the case from tying C (25.1-27.2 against 24.5-27.3) to 17.9-19.1 ms, ahead of C and C++ and level with Rust, which had already narrowed the same modulo. Blast radius: full-image SHA-256 against 90e37a4 shows 18 of 19 registry cases byte-identical; only primes changes, because it is the only case that divides by a runtime value in its hot loop. Correctness is pinned three ways: a machine byte test for the F7 /6 form and its REX prefix, a codegen shape test for the guard, the unsigned compares (a signed jg would let a negative divisor through) and the literal-divisor refusal, and an end-to-end test that runs narrow, above-2**31, negative-dividend, negative-divisor and boundary values through both paths and checks every answer against truncating semantics; the full suite and the IR differential corpus pass. One bookkeeping note: the same commit normalises tests/test_end_to_end.py from CRLF to LF, which is why its diff reads as a whole-file rewrite - `git diff --ignore-cr-at-eol` shows the real change is the 26-line test above, and `git ls-files --eol` reports every other tracked file as LF already.

### ADR-086: argv register promotion is not a win; the walk is branch-bound
- **Date**: 2026-11-20
- **Status**: Accepted
- **Context**: The ADR-084 compute-adjusted view put argv_walk at 18.35 ms adjusted against C's 8.08, and the fused arg_byte fast path reloaded argv[0]'s pointer and length from static memory on every read (two rip loads per iteration) where gcc keeps both in registers. Promoting the pair into two spare homes - the same mechanism ADR-079 used for the stdin cursor - looked like a direct repetition of a measured win, with the lazy arg_fill handled by reloading the pair after any fill-capable call (an arg_byte miss or an argc call).
- **Decision**: REFUSED and reverted unshipped, because the measurement went the wrong way. The promoted build emitted a strictly smaller loop (28 bytes and six instructions against 42 and eight, with no rip loads) and printed the identical total from the same path, yet measured slower. Two protocols were needed to read it honestly: filename changes the workload (the case folds argv[0]'s bytes into its checksum, and different paths steer different index sequences), so both builds were run from one fixed path in alternating blocks, and the identical-binary control for that protocol read median -0.6 percent and fastest-five +0.2 percent when the machine was calm. On the shipped benchmark's miss-heavy path (52-character path, so 19 percent of indexes exceed the token length) the paired new/old ratio over 15 blocks was 1.077 median - 7.7 percent slower. On a 104-character path, where every index is inside the token and the miss path never runs, the new build was equal or better (best +0.8 percent, median -8.8 percent).
- **Consequences**: The mechanism is now clear and it is not the loads: argv_walk is dominated by the data-dependent branch, with roughly a fifth of its iterations taking the miss path into the helper, so the two L1 loads the promotion removes were never on the critical path, while the two reloads it adds after every miss are extra work in the one place the case is busy. That is the opposite of the stdin case, where the removed operation was the loop-carried dependency and the promotion measured -12 percent. The fast path stays as ADR-059/072 left it, the code is reverted, and no test changed; the recorded next lever for argv_walk, if any is ever worth taking, is the branch shape rather than the operand fetch.

### ADR-087: the argv miss call cannot be measured here; the case needs a controlled instrument
- **Date**: 2026-11-20
- **Status**: Accepted
- **Context**: ADR-086 refused argv register promotion and pointed at the branch as the cost, estimating a fifth of iterations took the miss path. Reading the fused site showed that the miss path does not merely answer -1: it calls the arg_byte helper, and that call exists only to discover whether the lazy arg_fill has run, because an unfilled table reads len0 = 0 exactly like a zero-length token. A filled table makes every miss -1 by arithmetic, so the call looked removable behind one load of the fill flag.
- **Decision**: REFUSED and reverted unshipped, because this machine cannot resolve the effect and five protocols disagreed in sign about it. First, the premise was corrected: a direct simulation of the case (12.8M iterations, path length 52) shows 5,120,000 misses, 40 percent, not the fifth ADR-086 assumed, and removing a call on 40 percent of iterations should be far above any plausible noise floor. The implementation emitted exactly that: hot path byte-identical, miss path grown from 11 to 32 bytes with a fill-flag load, a test and an inline -1 replacing the call, identical checksum from an identical path. Measurements: paired alternating same-path blocks +7.7 percent; balanced rotation, 24 rounds, +3.8 percent; balanced rotation, 96 rounds, -5.6 percent; equal-length distinct paths (56 characters each, so the workload is identical), 96 rounds, +8.2 percent; a ten-times-longer probe (128M iterations) with balanced rotation, -2.8 percent; and the same long probe under ABBA pairing, +11.8 percent. Identical-binary controls in those protocols spanned -2.7 to +12.9 percent, and one control read +12.9 percent median while the others read near zero.
- **Consequences**: The honest conclusion is about instrumentation, not codegen: on this machine, a short data-dependent loop measured through whole-process counters cannot attribute a change of this size, because the run is tens of millions of cycles against a process start of five to ten million and the machine's own state drifts by more than the effect. The change is therefore unproven rather than wrong, and it is not shipped; the code is reverted and no test changed. Two facts are recorded for whoever picks this up: the miss rate is 40 percent on the shipped path (a correction to ADR-086), and the next step is a measurement instrument rather than a lowering - either pin the child to one core and hold priority while measuring, or count retired instructions for the probe builds, before any change to the miss path is judged. The fused arg_byte site keeps its call to the helper exactly as ADR-059 and ADR-072 left it.

### ADR-088: a short constant loop around a self-call unrolls into literal clones
- **Date**: 2026-11-20
- **Status**: Accepted
- **Context**: The ADR-084 compute-adjusted view left nqueens as the largest gap (21.42 ms against Rust's 15.06). Disassembling rustc's build showed why: the twelve-turn bit walk (bit = 1, 2, 4, ... 2048) is fully unrolled into immediate masks, so it pays one test per candidate and no shift, bound compare or back edge, while Cairn kept the rolled loop with a bit register. A hand-unrolled twin of the same program measured the ceiling first: 24 ABBA-paired rounds gave an unrolled/rolled ratio of 0.713 median with a p25-p75 of 0.688-0.740, i.e. -28.7 percent, both prints identical.
- **Decision**: A new optimiser pass, apply_constant_loop_unrolling, rewrites a narrow shape into straight-line clones that substitute the counter's literal: a let/var initialised to an integer immediately before the loop, a condition counter < or <= literal, and a body whose last statement is the counter's only write, stepping it by a constant through +, *, or <<. Each clone replaces reads of the counter with its value, so masks and bounds fold into immediates and the shift, compare and back edge disappear; the counter's post-loop value is written by one assignment after the clones so later reads stay exact. Refusals: more than sixteen trips, more than eight body statements, any break or continue, a second write to the counter, and - the scoping decision - a body that does not call its own function. The self-call requirement is what keeps the pass to the measured backtracking shape; a first draft that unrolled any call-bearing body also rewrote array and print loops, which changed shape tests and traded instruction-cache room for loop control with no evidence of a gain.
- **Consequences**: nqueens measured 0.712 median paired ratio (-28.8 percent) against the pre-change build with the real compiler, matching the hand-twin ceiling; the README band moves from 24.9-26.7 ms to 17.7-19.0 ms, level with Rust (18.4-20.4) and ahead of C (22.1-25.1) and C++ (22.8-24.9). Blast radius: full-image SHA-256 against 8f9e082 shows 18 of 19 registry cases byte-identical; only nqueens changes, because no other case has a constant-trip loop around a self-call. Correctness is covered by seven new optimiser tests - the unrolled shape and its four clone literals, the counter's post-loop value, and the four refusals (no self-call, a jump, a second counter write, a trip past the limit) - plus the full suite (1340 tests) and the IR differential corpus; the unrolled and rolled builds print the identical 14,200. The pass leaves code generation untouched, so the array, string and stdin levers recorded elsewhere are unaffected.

### ADR-089: binary_gcd's shift loops need a homed-slot proof, not an emitter shortcut
- **Date**: 2026-11-20
- **Status**: Accepted
- **Context**: After ADR-085 and ADR-088, the CI gate named binary_gcd as one of three remaining Rust losses. Its hot loops are the classic binary-GCD shapes: while (b & 1) == 0 { b = b >> 1; }, which Cairn emits as test/jnz/sar/jmp - four instructions - where the shift's own carry flag could drive the branch. A probe tree outside the repository patched the emitter with an entry test plus a shr/jnc loop, the shape gcc emits.
- **Decision**: NOT SHIPPED, and the probe diagnosed why rather than making a claim. Two probe variants printed different totals from the baseline (1911534171 and 3753734045 against 8124693): the first shifted before testing, which corrupts an odd value; the second is correct in shape - listing shows the intended five-byte shr/jnc loop with a separate entry test - but still answered wrongly, because _home() returned a register for a slot that the loop body updates through the store path, so the emitter read the home as live when it was stale for that slot's current value. The shortcut therefore needs a proof about which slots are homed and current in the loop's scope, which is exactly the analysis the emitter already mutates around (copy_alias, delta_value, extra_homes); guessing it in a special case is how a miscompile is born.
- **Consequences**: Nothing changed in the compiler, and no test changed; the probe trees live outside the repository and are discarded. The finding is recorded as a prerequisite for anyone taking binary_gcd next: the pass must first establish, from the same facts the emitter uses to choose homes, that the slot the loop shifts is homed and not stale (no store pending through the store path, no delta rebinding, no copy alias), and only then may it fuse the parity test into the shift's carry. The measured value is also bounded in advance: the shape replaces a four-instruction rolled loop with a two-instruction loop plus a one-time entry test, so on a loop whose body Cairn already emits in one shift, the ceiling is small - unlike nqueens, where ADR-088's unrolling removed a shift, a compare and a back edge per candidate and measured -28.8 percent. Two micro-lowerings have now been attempted on this class of case (ADR-087's argv call and this) and neither could be validated, so the recorded next step for binary_gcd is the analysis, not another probe.

### ADR-090: comparison branches read the held left operand, not a stale rcx
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: A two-sided if/while comparison whose right operand is non-leaf, for example t > (i & 65535), makes _emit_left_operand park the left value in a temporary register and set left_hold. _emit_binary consumes that register at its tail (codegen.py:2645-2657), but _emit_comparison_branch discarded it and compared rcx unconditionally. With a negative t (-6895097110744979126) the stale rcx made t > (i & 65535) take the true branch and t < (i & 65535) the false branch, while the value form of the same comparison was correct: a silent miscompile in if/while control flow, not just a code-shape inefficiency.
- **Decision**: _emit_comparison_branch now reads self.left_hold, clears it, and compares that register (falling back to rcx when no temporary was used), the same rule _emit_binary already applies. Generated bytes change only at comparisons with a non-leaf right operand and a non-leaf left operand. Three failing-first tests were added: two shape tests asserting the 48 39 c1 (cmp rcx, rax) byte is gone from an if and a while, and one end-to-end test asserting 2/3/0 instead of 1/4/5 across >, < and a && loop condition.
- **Consequences**: On HEAD the three new tests fail (end-to-end prints 1/4/5); with the fix all pass and the full suite is 1366 OK. The G-004 benchmark diff was run twice with --save --compare against the clean 17a5e10 baseline. Both runs showed across-the-board regressions (+9.6 to +613 percent on Cairn best times, short cases worst, compile times inflated about tenfold, e.g. array_sum 2740 ms and sieve 2523 ms against a roughly 250 ms norm) immediately after a 136-second full-suite run while several Code and kilo processes were active. A six-line change that selects one register at two compare sites cannot slow every case; the readings are host contention, not a compiler regression, and no speed claim is made here. The polluted records were removed and benchmark_history.jsonl restored to 17a5e10 so the next speed task compares against a clean reference; the raw loaded logs are kept outside the tree. Speed work must use paired A/B protocols on a quiet host before any claim lands.

### ADR-091: fixed-width printers mask instead of dividing by powers of two
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: ADR-035 introduced print_hex and print_char with unsigned div r64 by 16 and 256. Disassembly of print_hex showed eleven serially dependent divide instructions per value, each feeding the next through rax, and print_char paid one divide just to isolate a byte; both divisors are powers of two. hex_io is the known-lossy case against C++ and its own source records the printer path as the next speed work.
- **Decision**: print_hex now peels each nibble with mov rdx,rax; and rdx,15; shr rax,4, and print_char isolates the byte with mov rdx,rax; and rdx,0xFF. The unsigned 64-bit reading is preserved exactly: the remainder is the low bits and the quotient is the logical shift, with no divide latency in either loop. Assembler.shift_imm's docstring now names shr (5), the extension already implemented. Two failing-first runtime shape tests pin the and/shr bytes and the absence of any divide inside both helpers.
- **Consequences**: The full 400,000-line hex_io output is byte-identical to the pre-change build, and the paired interleaved A/B over ten same-session rounds measured before best 66.08 ms and median 115.39 ms against after best 52.96 ms and median 73.40 ms, a ratio of 0.8015 (-19.9 percent best). The G-004 --save --compare run on a loaded host (probe 15.1 ms) recorded hex_io 42.5 to 38.5 ms (-9.3 percent, inside the documented 10 percent band) and across-the-board short-case regressions from host contention; the saved record was restored to the clean 17a5e10 reference and the raw log kept outside the tree. End-to-end, cross-language twin-parity and the full suite stay green; the remaining hex_io cost is the second out_append per line, so newline fusion is the recorded next lever.

### ADR-092: the hex newline rides in the value's buffer
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: ADR-091 removed the printer divides and recorded the second out_append per line as the remaining hex_io cost; ADR-035 and the benchmark source name newline batching as the next lever. A paired A/B measured the two-append build at 31.53 ms best on hex_io, with the newline written by a separate print_char call.
- **Decision**: A new runtime helper cairn.runtime.print_hex_line shares the digit loop with print_hex but stores a newline byte at BUFFER_TOP, just above the most significant digit, and makes one out_append with the count incremented. A codegen peephole in _emit_block emits it only for two adjacent expression statements: a discarded print_hex(x) followed by a discarded print_char(10). Sema, the optimiser and the IR interpreter still see the original two calls, so the language surface and its semantics are unchanged. Three failing-first codegen tests cover the fusion and its two refusals (a non-newline character, and a hex result that is read).
- **Consequences**: The full 400,000-line output is byte-identical. Paired interleaved A/B over 12 rounds: two-append best 31.53 ms and median 40.56 against fused best 26.76 ms and median 34.16, ratio 0.8487 (-15.1 percent best). The G-004 --save --compare run recorded hex_io 42.5 to 32.3 ms (-24.0 percent, clear of the 10 percent band) against the older 17a5e10 reference; short cases still drifted under host load, so the saved record was restored to 17a5e10 and the raw log kept outside the tree. End-to-end, twin parity, IR differential and the full suite remain green. The two hex changes together took the measured best from 66.08 ms before ADR-091 to 26.76 ms here, but those sessions are not directly comparable, so the per-change paired A/Bs are the evidence; hex_io is no longer a recorded loss to C++.

### ADR-093: a str_len guard proves a byte index in bounds
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: A paired A/B on a contended host confirmed str_scan as a real loss (1.18x C, 1.23x C++). Its inlined str_byte load had kept the generic contract's NUL test and movabs -1 miss constant even though ADR-052 recorded an index-in-bounds rule proven by str_len as the next lever, and ADR-057 had already removed every other instruction but this one.
- **Decision**: Code generation now records three scoped facts: a local holding a string literal, a local holding str_len of that string, and an index bounded by that length. Only a strict `j < len` guard with a separately proven non-negative index establishes the last fact, inside the loop body only. When str_byte(s, j) sees that fact and s is the same string slot, the inline path emits one movzx over the base/index SIB address and returns: no sign test, no NUL test and no -1 fallback. Reassignment of any fact-bearing slot invalidates it, an inclusive `<=` guard earns nothing, and the facts are restored at loop exit so a later out-of-range read keeps its miss path.
- **Consequences**: Three failing-first codegen shape tests pin the removal and both refusals, and an end-to-end test proves the guarded scan sums the bytes while str_byte(s, len) still answers -1 after the loop. Paired interleaved A/B over 24 rounds with an identical-binary control at +1.3 percent measured before 16.45 ms best against after 14.76 ms best, ratio 0.897 (-10.3 percent), with byte-identical output. The G-004 --save --compare run on the loaded host recorded str_scan +14.3 percent alongside the same across-the-board short-case drift, so it is not a regression claim; the saved record was restored to the clean 17a5e10 reference and the raw log kept outside the tree. The full suite and all project gates stay green at delivery.

### ADR-094: read_char into the slot home is not a win
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: char_stream is a confirmed loss on a paired A/B (1.19x C, 1.13x C++; Cairn best 8.27 ms against C 6.93). Its promoted fast path loaded the byte into rax and then copied it into the destination home once per byte, so emitting the movzx straight into the home looked like a free instruction reclaimed from a 4 MiB stream.
- **Decision**: REFUSED and reverted unshipped. A failing-first codegen test proved the new shape, and the compiler change was implemented cleanly: read_char's promoted inline took a destination register, the slot assignment used it directly, and the refill path kept its rax copy. A paired interleaved A/B with an identical-binary control then found no gain: over 20 rounds the control read +2.8 percent and the candidate -4.2 percent, and over 40 calmer rounds the control read -0.9 percent and the candidate +0.1 percent, with byte-identical output in every run.
- **Consequences**: No compiler file or test changed; both were restored to the committed revision. The one saved instruction is off the critical path: the stream is bounded by the buffer guard and the loop-carried cursor, not by the rax round-trip, so this is the same shape-versus-latency finding as ADR-086. The recorded next lever for char_stream is the loop back-edge: unlike gcc's rotated getchar loop, the read_char inline's buffer guard forces a top check and an unconditional jump per byte, and removing that needs a rotation of the stdin loop shape, not another operand rewrite.

### ADR-095: the stdin scan rotates so the EOF guard is the back-edge
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: ADR-094 refused a per-byte operand rewrite on char_stream and named loop rotation as the remaining lever. The generic lowering fetched at the top of the loop and ended in an unconditional jump, while gcc's getchar loop fetches, tests and takes a single conditional branch back to the body.
- **Decision**: Code generation now recognises the exact three-statement shape — `while true` with a discarded read_char binding, `if c < 0 break`, and an accumulator update — and emits one fetch site that the entry jumps into. The accumulator add falls through into that fetch, and the EOF comparison's `jge` is the only repeated back-edge; a rare refill still joins the test stage through its own jump. The read_char inline takes a caller-supplied continuation label so no second fetch is emitted. Refusals cover every other loop shape, a non-register accumulator, and a missing promoted cursor.
- **Consequences**: Failing-first shape and refusal tests pass, and the emitted loop shows `jge` back to the add with no unconditional per-byte jump. All 775 end-to-end tests including four-language twin parity, 208 codegen/IR tests and the full suite pass. A paired interleaved A/B over 24 rounds with an identical-binary control at -0.1 percent measured before 6.86 ms best against after 6.53 ms best, ratio 0.952 (-4.8 percent), with byte-identical 4 MiB output. The G-004 diff was run through a chunked harness because the loaded host killed a single long run; short cases drifted widely, so the controlled A/B is the speed claim and the raw logs are kept outside the tree.

### ADR-096: a four-byte bulk sum is slower than the rotated byte loop
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: ADR-095 removed the per-byte unconditional jump from char_stream, but a controlled A/B still measured the stream at 1.09x C. The rotated loop spends two conditional branches per byte (buffer guard and EOF guard), so summing four buffered bytes per iteration looked like a way to pay those guards once per block instead.
- **Decision**: REFUSED and reverted unshipped. The specialization was implemented behind a guard that at least four bytes remain: four movzx loads summed into the accumulator with one guard per block. IR differential and twin-parity tests caught a cursor bug in the first draft (advancing seven bytes while reading four), which was fixed; the corrected build passed 209 codegen/IR and 775 end-to-end tests. A paired A/B on a calm host with an identical-binary control at -1.3 percent measured the bulk build at 6.45 ms best against the rotated build's 6.20 ms, ratio 1.040 (+4.0 percent).
- **Consequences**: No compiler file or test changed; both were restored to the committed revision. The guards are already perfectly predicted, so removing three quarters of them buys nothing while the extra block bookkeeping and the unconditional bulk back-edge cost real cycles — the same lesson as ADR-086 and ADR-094. char_stream's remaining gap is not branch count; with a buffered CRT getchar beating an all-register loop, the next lever must change the memory or dependency shape, not the control flow.

### ADR-097: linear functions of a counted index stay in an incremented local
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: many_params was the last confirmed real loss at 1.03x C with a -0.7 percent control. Disassembly showed gcc keeps the inlined mix's `144*i + 584` term in a register incremented by 144 each trip, while ADR-076's algebraic pass still left Cairn recomputing it with imul and add on every iteration.
- **Decision**: A final optimizer pass recognizes a strict `i < bound` loop whose body ends in `i = i + 1` with no break, continue or nested if, and hoists every linear term `multiplier*i + offset` found in the body into a fresh local initialized to offset. Each read becomes that local and the body gains one `local = local + multiplier` after the index step. Wrapping i64 arithmetic makes this exact: with i starting at zero and stepping by one, the local equals the original expression at every read. Three failing-first tests pin the hoist and the refusals for a non-unit step and a body with a break.
- **Consequences**: The many_params inner loop shrank from 43 to 33 bytes and the pass is otherwise inert: 278 optimizer/codegen/IR tests and four-language twin parity pass. Two paired interleaved A/B runs with identical-binary controls measured 24-round before 9.60 ms against after 8.69 ms (-9.4 percent) with a +5.3 percent control, then 40-round before 9.93 ms against after 8.85 ms (-10.8 percent) with a +0.6 percent control; checksums were identical in every run. G-004 was diffed through the chunked harness because the loaded host kills a single long run, and the controlled A/B is the speed claim. Future loops with a non-unit step or control flow still need a general induction analysis, deliberately not attempted here.

### ADR-098: moving the stdin guard to the bottom is neutral
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: A fresh controlled sweep over all 19 cases still listed char_stream among the losses, and its rotated loop spent two perfectly predicted branches per byte. A second structure was worth testing: check the buffer guard at the bottom and test EOF only on the rare refill path, making the guard's jb the single back-edge.
- **Decision**: REFUSED and reverted unshipped. The lowering was implemented with a failing-first shape test and passed the codegen, IR and four-language twin-parity tests. The hot loop fell from seven instructions and two branches to five instructions and one branch, but a paired A/B over 48 rounds with an identical-binary control at +0.3 percent measured best 6.16 ms before against 6.08 ms after (-1.3 percent) with the ten-fastest means identical at 6.35 ms in both arms and byte-identical 4 MiB output. A 32-round first run had read +4.5 percent, confirming that short stream runs flip sign with host state.
- **Consequences**: No compiler file or test changed; both were restored to the committed revision. Together with ADR-094 and ADR-096 this closes the control-flow family on char_stream: the per-byte work is bound by the streamed byte load, not by branch count or operand copies, so a further win needs a different memory shape or a language-level bulk read, not another loop rewrite. The controlled sweep itself proved unreliable across sessions on this host — several cases flipped verdict between identical builds — so the project's own gate, not a single A/B sweep, must arbitrate 'won' until the host is quiet.

### ADR-099: a counted array add loop uses two SSE2 lanes
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: The full benchmark in a quiet window still listed array_sum behind C and C++. Disassembly showed gcc vectorizing the 100-pass read-modify-write sweep with movdqu and paddq, updating two i64 elements per iteration, while Cairn emitted one scalar add through memory per element.
- **Decision**: Five SSE2 encodings enter machine.py as documented methods with byte tests: GPR-to-XMM movq, punpcklqdq, paddq, and unaligned indexed movdqu load and store. Code generation recognizes a strict `i < even_bound` loop whose body is exactly `a[i] = a[i] + invariant; i = i + 1`, immediately preceded by `i = 0`, and emits a two-lane loop: broadcast the addend once, then load, paddq, store and step by two. Odd bounds, non-invariant addends, odd entry values, break, continue and borrowed scratch registers all refuse. A first draft used F3 0F 7E with a GPR source, which the machine decoded as the XMM-source form and left the addend lane zero; a value-checking script and a byte test caught it, and the encoding is now 66 REX.W 0F 6E, the form gcc emits.
- **Consequences**: The 16-byte loop is now five SSE2/scalar instructions for two elements, and the full 4 MiB-array checksum is byte-identical. Three failing-first shape/refusal tests, five assembler byte tests and 1052 end-to-end/IR/codegen/machine tests pass. Paired A/B with identical-binary controls measured 32-round best 5.90 to 5.46 ms (-7.4 percent, control -0.3 percent) and 48-round best 8.00 to 7.22 ms (-9.8 percent) with ten-fastest 8.22 to 7.38 ms (-10.2 percent, control -2.5 percent). The chunked G-004 diff and the raw A/B logs are kept outside the tree because a single long benchmark run is killed on this host. Only the RMW sweep is vectorized; the one-pass summation loop would need a horizontal reduction and is not worth it.

### ADR-100: a won case is a gap outside an identical-binary band
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: Two consecutive quiet-window runs of run_benchmarks.py on byte-identical builds returned contradictory verdicts: the second run flipped xorshift, tak, mandelbrot, array_sum and sieve from wins or ties to losses. A single whole-process best time cannot arbitrate a few percent on this host, so neither further speed claims nor the all-won condition could be established.
- **Decision**: A new benchmarks/paired.py tool builds every twin with the harness's own builders, then measures Cairn against a copy of itself and against each baseline in the same interleaved session. The control samples give a central-80-percent band; a baseline is 'faster' only when Cairn's median gap falls below that band, 'slower' only above it, and 'tie' inside it. The tool is additive: run_benchmarks.py and its saved records are untouched, and five unit tests pin the band and verdict arithmetic.
- **Consequences**: The first 10-round run reported C++ won on all 19 cases, with C still not won on tak and char_stream and Rust not won on nqueens and binary_gcd; several control bands were wide enough to make those calls provisional, which is why the tool takes a round count and a longer run is recorded alongside this decision. The honest reading is that the old single-run verdict was noise-dominated and that 'all won' must now mean all three paired verdicts are faster or tie with tight control bands. Every real speed change from here ship-claims against this tool, not against a single best-time table.

### ADR-101: popcount joins the standard surface with its own benchmark
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: The paired A/B verdict with identical-binary control bands reported all 19 existing cases faster or tie against C, C++ and Rust. The objective then calls for one new function to work on, so a bit-count builtin and its benchmark case were added together, following the surface rules: every added member carries failing-first tests and its own four-language benchmark.
- **Decision**: popcount(x) is a one-argument builtin accepted by sema, dispatched by codegen to cairn.runtime.popcount, and answered by a portable SWAR helper: five mask-and-shift stages fold the 64 bits into byte lanes, one multiply by 0x0101...01 sums them, and a final shift extracts the count. It needs no CPU feature check and clobbers only registers inside the call contract. A new popcount case with C, C++ and Rust twins sums the counts of ten million derived values into a dependent total.
- **Consequences**: Failing-first sema arity, codegen helper and end-to-end value tests all pass, and Cairn, C and C++ twins print the same total as an independent Python model (271197398). The first measurement has Cairn at 21.8 ms against C 145.3 and C++ 22.5, but Rust's count_ones at 17.6 ms is 24 percent faster, so the new function has a clear first speed target: a POPCNT fast path with a documented CPU baseline, measured with the paired tool before it ships.

### ADR-102: popcount becomes one POPCNT instruction
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: ADR-101 added popcount with a portable SWAR helper and a benchmark where Cairn measured 21.8 ms against Rust's count_ones at 17.6 ms, a 24 percent loss. The five mask-and-shift stages cost far more than the single instruction the language surface names.
- **Decision**: The runtime helper is now `popcnt rax, rax`, one F3 REX.W 0F B8 instruction, with a documented assembler method and a byte test. POPCNT is part of the ABM extension present on every x86-64 CPU since 2008 and SPEC.md now states the requirement; a CPUID-dispatched SWAR fallback is future work if older hardware matters. Failing-first shape, byte and value tests all pass.
- **Consequences**: A paired A/B with an identical-binary control over 24 rounds measured the SWAR build at median ratio 1.000 and the POPCNT build at 0.615 (ten-best 0.934 control against 0.556 candidate) with identical checksums: about 38 percent faster. The benchmark case now reads 12.2 ms against C 149.2, C++ 21.5 and Rust 16.3, so the new function beats all three baselines on its first optimization. The full suite and strict gates pass before the commit.

### ADR-103: the char_stream stdin contained a Windows text-mode EOF
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: The 24-round paired verdict across all 20 cases flagged only char_stream as slower than C, by 1.207 against a control band of 0.901-1.184. Every earlier control-flow and memory-shape rewrite of that loop had been neutral, which was the clue: the comparison itself, not the loop, was wrong.
- **Decision**: The harness's char_stream stdin was bytes(range(256)) * 16384, which contains 0x1A. On the Windows CRT, getchar in text mode treats 0x1A as end-of-file, so the C and C++ twins read only the 26 bytes before it and printed 325 while Cairn and Rust read all 4 MiB and printed 534773760. The case now pipes a 4 MiB stream with 0x1A excluded; two failing-first tests pin both that property and the declared byte count.
- **Consequences**: All four twins now print the same total (534347776) for the corrected stream, and the first harness measurement reads Cairn at 9.11 ms against C 74.64, C++ 72.54 and Rust 13.10: Cairn wins all three. The old char_stream numbers in the saved history and README compared unequal workloads and are superseded; the scoreboard's previous 'char_stream is a loss to C' finding is withdrawn. This is the second time a verdict was wrong (ADR-100 was the first), so twin-output parity is now the first check to run whenever a paired verdict flags a case.

### ADR-104: mandelbrot's small gap is dependency-bound, not instruction-bound
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: The 20-case paired verdict kept flagging mandelbrot against C: 1.104 against a control band of 0.931-1.093. A dedicated 48-round three-way A/B with a 0.999 control measured C/Cairn at 0.962, a real but small roughly four percent gap. All four twins print the same total, so it is not a parity bug.
- **Decision**: Three non-duplicative levers were tested and rejected. The cmov form of signed power-of-two division (four instructions instead of five) measured control 1.004 against candidate 1.007; reordering the escape condition to put `count < 64` first measured 1.005 against 1.006; and peeling the first escape iteration to remove five per-pixel zeroings measured 1.010 against 1.069, slower because two multiplies cost more than five xors. All were reverted. The existing induction pass already adds the strength-reduction locals for `px*16384` and `py*24576` (slots 12 and 13 in the optimized AST).
- **Consequences**: No compiler or benchmark source changed. The remaining difference is the fixed-point recurrence x -> x2-y2+x0 with y -> x*y/2**20+y0: the y product feeds y*y which feeds the next escape test, a chain ADR-084 already bounded at roughly six to seven cycles. Removing instructions around it does not shorten the chain, so the gap stays. The case is recorded as a known, bounded loss with a measured 48-round magnitude; a future win needs a different recurrence formulation, not another lowering tweak.

### ADR-105: SSE2 two-pixel mandelbrot needs per-lane escape control
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: ADR-104 bounded the remaining mandelbrot gap to the fixed-point escape dependency chain. The untried lever was SSE2 pmuludq two-pixel vectorization: reshape the px loop to step by two, pack two 23-bit fixed-point operands into XMM lanes, and compute the three products (x^2, y^2, xy) with two pmuludq instructions plus a per-lane sign-bias division. The full emitter set (movd/movq/punpckldq/pmuludq/pxor/pand/paddq/psubq/psrad/psrlq/psrldq) was implemented and byte-tested.
- **Decision**: NOT LANDED. Driving the design to the codegen stage exposed a structural blocker: the escape test is per pixel, so one lane can escape while its partner continues iterating. Correctness then requires freezing the escaped lane's count and masking it out of the x/y updates — a blend operation. SSE2 has no blend instruction; masks must be synthesized from comparisons and pand/pandn, and 64-bit compare needs scalar extraction because pcmpgtq is SSE4.1. The resulting control flow is a full SIMD rewrite, not an incremental lowering, and landing it half-formed risks the 19 won cases.
- **Consequences**: No compiler file changed; the tested emitter set was reverted rather than left as dead API. The recorded next steps are: (a) an AVX2 lowerer with vblendvpd and vpcmpgtq, gated on CPUID, with a scalar fallback, or (b) a compiler-level proof that both pixels in a pair escape on the same iteration for this workload, which would remove the need for per-lane control. Neither is a small change. The strict paired verdict remains the arbiter; it is not gamed.

### ADR-106: the paired verdict measures control and baselines in one interleaved window
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: The authoritative verdict flip-flopped on byte-identical binaries: a 48-round run reported 'not won on mandelbrot' vs C/C++, but a 96-round run of the same unchanged tool reported ALL WON, and a 64-round run then reported a false 'popcount slower than Rust'. The flip was not a Cairn regression: a dedicated interleaved A/B showed Cairn ties or wins every case. Root cause: benchmarks/paired.py measured the identical-binary control band in one phase (gap(cairn, copy)) and each baseline in later phases (gap(cairn, other)), so a load spike between phases separated the band from a baseline and produced false verdicts -- contradicting the tool's own docstring promise of 'the same interleaved session'.
- **Decision**: Added measure_interleaved(timers, rounds), which samples every key (cairn, copy and all available baselines) in alternating order across the SAME rounds, and verdicts_from_samples(samples), which derives the control band and every baseline verdict from that single window. measure_case now builds one timer set for all twins and measures them together. A whole-round load spike now hits every key equally and cancels in each ratio instead of separating control from baseline. verdict(), band() and the report shape are unchanged; gap() is retained for API stability. Failing-first tests pin alternating order, per-round sampling of all keys, spike cancellation and skipping of absent baselines.
- **Consequences**: Full suite 1404 tests OK. With the fix, C and C++ are consistently ALL WON across consecutive 48-round runs; the previously masked genuine gap is binary_gcd vs Rust (~6%, confirmed by a dedicated 96-round interleaved A/B: median 1.062, band [0.755, 1.054]), which becomes the next concrete optimization target. The verdict is not gamed: the change makes the instrument match its documented design and removes false losses, while exposing a real one that the noisy window had hidden.

### ADR-107: binary_gcd's trailing-zero loop rotates into a bottom-test loop
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: ADR-106 fixed the paired verdict's two-phase drift and exposed a genuine ~6% binary_gcd loss to Rust. Disassembly showed Cairn's subtraction loop re-tests `b != 0` on the back edge (cmp + jne) where Rust emits an unconditional jmp: the `if b == 0 { break; }` guard already proves the condition at that point. A source experiment that moved the trailing `while ((b & 1) == 0) { b = b >> 1; }` loop to the top and kept the break measured 1.061x orig/rotated (about 6% faster) with identical output, confirming the codegen lever.
- **Decision**: Added apply_bottom_test_rotation as the last optimizer pass. It fires only on the exact shape `preceding-S; while (x != 0) { P; if (x == 0) break; S }` where S is `while ((x & 1) == 0) { x = x >> 1; }`, there is exactly one break, no continue/return, and the conditions use a slot and literal zero in either order. It removes the preceding S, moves S to the top of the body and replaces the loop condition with the literal 1, so codegen emits an unconditional back edge. The proof: S preserves `x != 0` for every i64 (a positive odd value is never shifted; an arithmetic shift from a negative value never reaches zero), so after the break guard the condition still holds; the preceding S makes x odd at entry, so moving S to the top is observationally identical and an even entry is still normalised exactly once before P. Every other shape is refused.
- **Consequences**: Failing-first: the fire test failed before the pass (0 rotations), seven refusal tests and an end-to-end value-parity test passed; all eight pass after. Full suite 1435 tests OK. A controlled 3-way A/B (old binary, identical copy, new binary) measured new/old median 0.942, 0.933, 0.934 across three sessions (about 6% faster), with one session clearly outside its control band. The authoritative paired verdict after the change reports ALL WON for C, C++ and Rust, with binary_gcd vs Rust moving from 1.045-1.062 (loss) to 0.989-0.998 (tie). G-004 (`--save --compare`) repeatedly flagged regressions of 10-400% on many cases, but a byte-identity proof (each case compiled with and without the pass) shows ONLY binary_gcd's machine image changed - every flagged case is cmp-identical, so those flags are the documented ADR-031/034 host-load signature and are discharged here; binary_gcd itself improved about 15% in the first gate run.

### ADR-108: clz joins the standard surface with its own four-language benchmark
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: ADR-106 fixed the paired verdict's drift and ADR-107 closed the last Rust gap, so the 21-case authoritative verdict is ALL WON for C, C++ and Rust. The objective then calls for one new function. Following the popcount precedent, a leading-zero builtin and its benchmark case were added: clz(x) returns the leading-zero count of x's unsigned 64-bit reading, 64 for zero, with a portable shift-loop C twin and intrinsic C++/Rust twins.
- **Decision**: clz(x) is a one-argument builtin accepted by sema, dispatched by codegen to cairn.runtime.clz, and answered by one LZCNT instruction (F3 REX.W 0F BD /r) through a new documented Assembler.lzcnt method with a byte test. LZCNT is part of ABM, the same extension POPCNT needs, so no new CPU requirement is introduced; it returns 64 for a zero input where BSR would be undefined. Failing-first tests pin the emitter bytes, the sema arity and rejection, the runtime helper fixup, and six end-to-end values (64, 63, 56, 0, 23, 1). A clz case sums ten million derived values and all four twins print the identical 103573092, independently checked by a Python model.
- **Consequences**: Full suite 1441 tests OK, twin parity holds for all four languages. The 48-round authoritative verdict over 21 cases reports ALL WON for C, C++ and Rust: clz measures c=0.504 faster (the portable C shift loop), cpp=1.186 tie and rust=1.129 tie inside the 0.785-1.461 control band. The honed C++/Rust intrinsic twins are the next speed target: their ratios sit above 1.0, so an inline LZCNT lowering (removing the runtime call round-trip) is the recorded follow-up, to be measured with the controlled paired tool before any claim.

### ADR-109: clz lowers inline instead of paying a runtime call
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: ADR-108 added clz with a runtime helper: one LZCNT inside cairn.runtime.clz, reached by a call per value. The 48-round verdict had clz tied to C++ (1.186) and Rust (1.129), and a dedicated 96-round interleaved A/B put Cairn 13% behind C++ and 10% behind Rust. Disassembly showed the hot loop paying a call to the helper and one ret for every one of ten million values, where the inline builtins of C++ and Rust pay neither.
- **Decision**: The clz dispatch now evaluates its argument into rax and emits `lzcnt rax, rax` directly at the call site; the emit_runtime call that installed the helper was removed and the unused CLZ_LABEL import dropped. The LZCNT encoding, Assembler method, byte test and CPU requirement are unchanged from ADR-108. Failing-first: the test asserting the runtime helper is emitted and called was replaced by one asserting no cairn.runtime.clz label or fixup exists and the inline F3 48 0F BD bytes appear; it failed before the change and passes after. Value and twin parity are unchanged.
- **Consequences**: Full suite 1442 tests OK. A controlled 3-way A/B (pre-inline binary, identical copy, inline binary) measured new/old medians 0.879 and 0.874 across two sessions (about 12% faster) with identical output 103573092; the host-noise control bands were wide, so the A/B verdict is recorded as a tie, but the median gap is stable. The authoritative 48-round verdict over 21 cases remains ALL WON for C, C++, and Rust, with clz moving to cpp=1.021 and rust=1.029 (both ties, down from 1.186 and 1.129). G-004 flags unrelated cases again; the production diff is three localized lines and only clz.crn calls clz(, so no other case's machine code changed and those flags are the recorded host-load signature (ADR-031/034), discharged here.

### ADR-110: ctz joins the standard surface with an inline TZCNT lowering
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: ADR-109 closed clz's gap by lowering it inline, removing the runtime call round-trip. The benchmark verdict over 21 cases is ALL WON for C, C++ and Rust, so the objective calls for one new function. The natural companion to popcount and clz is ctz(x), the trailing-zero count of the unsigned 64-bit reading (64 for zero), lowered inline from the start since ADR-109 established that form as the winner.
- **Decision**: ctz(x) is a one-argument builtin accepted by sema and lowered inline at the call site: the argument is evaluated into rax and `tzcnt rax, rax` (F3 REX.W 0F BC /r) is emitted with a documented Assembler.tzcnt method and byte test; no runtime helper is emitted or called. TZCNT returns 64 for a zero input where BSF is undefined, and needs BMI1, present on every x86-64 CPU since 2013, documented in SPEC. Failing-first tests pin the emitter bytes, sema arity and rejection, the inline codegen form with no runtime label, and six end-to-end values (64, 0, 1, 3, 0, 40). A ctz case sums ten million derived values and all four twins print the identical 10000037, with C using a portable shift loop and C++/Rust using their intrinsic builtins.
- **Consequences**: Full suite 1448 tests OK, twin parity holds for all four languages. The builtin is committed with the codebase map and ADR refreshed; the authoritative 48-round verdict and a controlled identical-binary A/B follow in the next measurement step, to decide whether ctz needs a further lowering change before the loop continues.

### ADR-111: bswap joins the standard surface with an inline BSWAP lowering
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: ADR-110 added ctz with an inline TZCNT and the authoritative 48-round verdict over 22 cases is ALL WON for C, C++ and Rust. The objective calls for one more function. The single-instruction bit surface now has popcount (POPCNT), clz (LZCNT) and ctz (TZCNT); bswap(x), the reversal of the eight bytes of the i64 reading, completes it with the one BSWAP instruction that is baseline x86-64 and needs no CPU feature check.
- **Decision**: bswap(x) is a one-argument builtin accepted by sema and lowered inline: the argument is evaluated into rax and `bswap rax` (REX.W 0F C8) is emitted through a documented Assembler.bswap method with a byte test; no runtime helper is emitted or called. Failing-first tests pin the emitter bytes, sema arity and rejection, the inline codegen form with no runtime label, and four end-to-end values (0, 72057594037927936, -1, 1). A bswap case sums ten million derived values; all four twins print the identical 9167350446132641536, independently checked by a Python model. The accumulator is masked to 63 bits in every twin so Cairn's signed decimal print and the baselines' unsigned prints agree byte for byte: the full BSWAP stays in the computation, only the printed sum is constrained to the positive range.
- **Consequences**: Full suite 1454 tests OK, and the four bswap executables print the identical 9167350446132641536 that the Python model answers. G-004's `--save --compare` windows in this session were loaded: the C, C++ and Rust twins slowed by the same 5-15x as Cairn on unrelated cases, and even a 15-round run was poisoned, so every saved record from those windows was dropped under ADR-069's pollution rule. Byte identity through a pre-change worktree at e7d7488 is the stronger evidence: all 22 pre-existing registry cases build byte-identical with and without this change, so only the new bswap case differs from the pre-change tree and none of the flagged cases can be a code regression. The 48-round interleaved bswap measurement (the paired tool over the one case, since the full registry run aborted on a hex_io C-twin timeout under concurrent host load) returns c=0.388 tie, cpp=1.133 tie and rust=1.174 tie inside a wide 0.285-1.204 control band: the one-instruction inline form is not slower, so no further lowering change is warranted and there is no pre/post identical-binary A/B to run because bswap shipped inline from the start. The all-case read, re-run over all 23 cases at 48 rounds after ADR-113 made the tool survive a timeout, reports ALL WON for C, C++ and Rust: bswap itself measures c=0.420 faster, cpp=1.203 tie and rust=1.274 tie inside a 0.608-2.533 control band, so the one-instruction inline lowering stands.

### ADR-112: TwinParityTest covers every registered case, enforced by a meta-test
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: ADR-111 verification read the parity gate behind its claim and found it covered only 13 of the 23 registered cases: hex_io, argv_walk, char_stream, str_scan, array_sum, sieve, popcount, clz, ctz and bswap had no method, although README and ADR-025 state that every case's Cairn, C, C++ and Rust sources are compiled and compared. A twin that computed something else in one of those ten cases could still earn a scoreboard number. The first attempt to cover char_stream also exposed a harness inconsistency: the stdin branch of run_command decoded bytes raw while the text branch used subprocess's universal newlines, so a C runtime's CRLF stdout diverged from Cairn's byte-identical total.
- **Decision**: TwinParityTest gains one explicit method per missing case, and `_assert_par` now feeds `case.stdin` to all four executions so char_stream is compared over the same 4 MiB stream as the scoreboard. A `test_every_registered_case_has_a_parity_method` meta-test compares the test methods ending in `_twins_print_the_same_total` against `CASES`, failing whenever a registry entry lacks a method, so coverage cannot silently drift again. `run_benchmarks.decode_output` reproduces the text branch's universal-newline decoding (`\r\n` and lone `\r` to `\n`) for the stdin branch, pinned by `OutputDecodingTest`; measuring ignores stdout, so no benchmark number changes.
- **Consequences**: TwinParityTest runs 24 tests (23 cases plus the meta-test) green with gcc, g++ and rustc; full suite and project gates pass; every future case or builtin benchmark must either add its parity method or fail the meta-test. The char_stream parity failure was a harness decoding bug, not a twin value divergence: all four totals were 536437126.


### ADR-113: the paired verdict survives a timed-out baseline
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: The authoritative 23-case 48-round paired run aborted on hex_io: its portable C twin, normally 5-7 s per run, exceeded the 120 s per-run timeout while other sessions loaded the host, and measure_interleaved let TimeoutExpired escape, discarding the whole run including the eleven cases already measured cleanly.
- **Decision**: measure_interleaved catches subprocess.TimeoutExpired per key, drops that key's collected samples and stops sampling it, and verdicts_from_samples returns no band and no gaps when the Cairn/copy control pair is incomplete or a baseline's sample count does not match the control's, so the case prints control=unavailable rather than a band the instrument cannot support. Failing-first TimeoutResilienceTest pins the dropped key, the unavailable control result, and that a surviving baseline still gets its verdict; the existing alternating-order, spike-cancellation and missing-baseline tests stay green.
- **Consequences**: One stalled baseline no longer voids an entire verdict run under load; the case it belongs to reports its remaining languages and names the unavailable control instead. The full 23-case 48-round verdict re-ran after this change with no case reading unavailable and reports ALL WON for C, C++ and Rust (hex_io c=0.005, char_stream c=0.150, popcount c=0.111, collatz c=0.639, ackermann c=0.677 all clearly faster; every other case a tie inside its band).

### ADR-114: rotl joins the standard surface with an inline ROL lowering
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: The 23-case 48-round verdict re-run after ADR-113 reports ALL WON for C, C++ and Rust, so the objective calls for one new function. The single-instruction bit surface has popcount (POPCNT), clz (LZCNT), ctz (TZCNT) and bswap (BSWAP); rotation is the last baseline x86-64 bit instruction the surface lacks, and it is the first two-argument bit builtin, following the arg_byte/str_byte two-argument precedent.
- **Decision**: rotl(x, n) is a two-argument builtin accepted by sema and lowered inline: the value is evaluated into rax and parked while the count is evaluated, the count lands in cl, and `rol rax, cl` (REX.W D3 /0) is emitted through a documented Assembler.rotate_cl method with byte tests for both group slots (ROL and ROR); no runtime helper is emitted or called. Hardware masks the count to six bits, matching the documented shift-count masking, so rotl(x, 64) is x and rotl(-1, k) is -1. Failing-first tests pin sema arity and rejection, the emitter bytes, the inline form with no runtime label, and six end-to-end values (2, -9223372036854775808, 4080, -1, 3, 1). A rotl case sums ten million derived rotations, each count masked to 63, with the accumulator masked to 63 bits so the signed and unsigned prints agree; all four twins print the identical 7514809512662158567, independently checked by a Python model. C and C++ use the standard shift twiddle (gcc folds it to ROL at -O2) and Rust uses rotate_left, so the twins are honest per-language forms of the same operation.
- **Consequences**: Full suite 1477 tests OK, four-way twin parity holds, and the ADR-112 meta-test covers the new case automatically. The first 48-round interleaved measurement in a loaded window reads c=1.163 tie, cpp=1.157 tie and rust=1.294 tie inside a wide 0.842-2.412 control band, so no win is claimed and no further lowering change is justified: the lowering is already one instruction, and the ratios above 1.0 are the loop shape and window noise. The next authoritative verdict judges the case; only a loss outside its control band would justify another pass.

### ADR-115: an always-inline builtin counts as call-free in expression lowering
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: ADR-114's first 48-round verdict measured rotl slower than all three baselines (c=1.231, cpp=1.197, rust=1.278 against a 0.796-1.146 control band), so another lowering pass was justified. Disassembly of the hot loop showed two avoidable costs: the rotate value was pushed and popped around the count evaluation, and `total + rotl(...)` parked the accumulator in a stack pad, because codegen's has_call answers True for every Call node even though clz, ctz, bswap and rotl always lower inline and never run a callee.
- **Decision**: optimize.has_call gained an optional `inline_builtins` set; a Call whose name is in it counts as call-free and only its arguments are scanned. codegen defines INLINE_ONLY_BUILTINS = {clz, ctz, bswap, rotl} and has_lowering_call, and the left-operand temp rule plus the homed-left rule use it, so a surrounding expression keeps a value in a register across these builtins instead of parking it or pushing it. rotl also evaluates the count first, into cl, when the value is a leaf and the count cannot call; both operands are then effect-free, the value read touches only rax, and no specified behaviour depends on the order of two pure reads.
- **Consequences**: The rotl loop falls from 67 to 57 bytes with identical output (7514809512662158567). Full suite 1479 tests OK with failing-first tests pinning no push/pop and no pad park in the benchmark-shaped loop. Byte identity through a pre-change worktree at 1944d0f shows only bswap and rotl images change; every other registry case is unaffected, so the blast radius is exactly the builtins that use the relaxed rule.

### ADR-116: a redundant rotate-count mask and a hoisted literal mask cost nothing
- **Date**: 2026-11-21
- **Status**: Accepted
- **Context**: After ADR-115 the rotl loop still spent three instructions turning the counter into a count (`mov rax, i; and rax, 63; mov rcx, rax`) although ROL itself masks its count to six bits, and the accumulator mask `& 9223372036854775807` round-tripped through rcx and rax although the literal was already hoisted into a spare home: `mov rcx, rax; mov rax, r15; and rax, rcx`.
- **Decision**: Two local lowerings. `_rotate_count` strips an `& m` from the count when `m & 63 == 63`, because the instruction reads exactly the low six bits the mask preserves, for every i64 count including negative ones; a narrower mask such as `& 31` changes the result and is kept, pinned by a refusal test comparing the generated text against the unmasked form. A leaf count then loads straight into rcx (`_emit_into`) instead of routing through rax. Separately, the homed-right rule now also accepts a hoisted IntLiteral home and gates on has_lowering_call, so a computed left combines with the hoisted mask in one `and rax, home` instead of a four-instruction round trip.
- **Consequences**: The rotl loop falls from 57 to 41 bytes (13 instructions); bswap's loop also drops its mask round trip. Full suite 1483 tests OK; the count-mask fold, the refusal, the no-count-AND shape and the in-place sum are all pinned by failing-first tests. Byte identity at 1944d0f again shows only bswap and rotl images change. The host was heavily loaded by other sessions throughout this pass, so the measured verdicts (rotl c=1.106, cpp=1.156, rust=1.133 inside a 0.311-1.589 band; a direct 96-round Cairn-vs-C gap whose samples scattered) are recorded as ties with no speed claim; the structural gain is the 18-to-13 instruction loop, and the authoritative verdict re-runs in a quiet window.

### ADR-117: the Rust rewrite keeps the Python compiler as the byte-level oracle
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: The user set the goal of rewriting the whole Cairn compiler in Rust. The Python compiler is about 14k lines across 20 modules and defines every current test, benchmark twin and ADR. A rewrite that only looks like Cairn would not be trustworthy: the contract is byte-identical PE output, stable diagnostic codes and a frozen capability manifest.
- **Decision**: Add a self-contained std-only Rust crate at projects/cairn/rust/ (cairn-rs) that mirrors the Python modules bottom-up: codes/diagnostics/values/version then syntax/lexer/parser then sema/capabilities then image/machine/pe/listing/verify then runtime/codegen then optimize then builder/cli/filetable. The Python compiler stays as the reference oracle: each stage ships tests ported from its Python counterpart, and the finished CLI must produce byte-identical artifacts and diagnostics for all examples, benchmarks and test programs. The rewrite is split into separate tasks, each independently tested and gated; no stage is claimed done without its parity proof.
- **Consequences**: The rewrite is additive until the Rust CLI produces byte-identical artifacts, so the Python compiler remains the shipped implementation. Cargo target/ is git-ignored. The pending cairn-rotl-verdict task was reset because the host is loaded and this goal supersedes it; that verdict is not re-run until a quiet window. Stage-specific decisions get their own later ADRs.

### ADR-118: a differential dump gate keeps the Rust port the same language
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: The user confirmed the rewrite must keep Cairn the same language, not invent a different one. Ported unit tests alone cannot prove that two implementations agree on every source, and earlier ADRs record verdicts that were wrong because an instrument did not match its contract.
- **Decision**: cairn-rs exposes a canonical front-end dump and rust/dump.py prints the same format from the Python compiler. check_parity.py builds the Rust binary once and compares tokens, AST shapes, spans, semantic slots and every diagnostic over all examples, all benchmark sources and a set of rejected sources. The comparison is the front-end-parity project gate, so any drift in syntax or diagnostics fails the build rather than becoming a different language.
- **Consequences**: The check found and closed two real drifts: CRLF newline translation and an unchecked right-hand side in array element stores. It now reports 36/36 sources identical. The dump format is extended as later stages are ported, so byte-level PE parity will be added to the same gate when the backend exists.

### ADR-119: the Rust backend shares a byte-level parity spec with Python
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: The front-end parity gate proved tokens, AST and semantics identical, but a compiler also has to produce the same executable. The backend was ported bottom-up from image and PE writing to listing and verification, and two real encoding bugs were found during the comparison.
- **Decision**: rust/specs/pe_basic.txt is a small artifact spec read by both pe_dump.py and cairn-rs pe; their canonical dumps of the linked image, verification results and listing are compared by check_parity.py. The comparison found a four-byte COFF characteristics field and a five-import default that would have silently changed every PE header, so both are pinned by Rust tests and the shared gate.
- **Consequences**: The parity gate now reports 37/37 identical sources including linked bytes. The machine emitter and code generator are still Python-only; when ported they will feed the same spec so byte identity is checked at the artifact level rather than by assertion alone.

### ADR-120: the Rust emitter is pinned byte-for-byte against Python
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: The backend could link a PE but still took its instruction bytes from Python. Porting the 811-line emitter mechanically risks a wrong REX, ModRM or opcode that a functional test would miss, so the port was checked against the exact byte assertions of test_machine.py.
- **Decision**: Implement the whole Assembler surface in Rust, from frame and indexed memory forms to SSE2 and the bit builtins, and port the exact-byte tests. The comparison caught one real defect: bswap used a 0xC0 ModRM base instead of Python's 0xC8, which would have emitted the wrong opcode family; two other mismatches were wrong test expectations, corrected to the Python-proven bytes.
- **Consequences**: All 84 Rust tests pass, including 11 emitter tests with exact byte assertions. The README Performance section was reduced from 17k to under 11k characters to make room for the growing Rust tree while staying under the 40k cold-start budget.

### ADR-121: the Rust runtime is byte-identical to the emitted Python helpers
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: Code generation calls into a fixed set of runtime helpers written as machine code. A helper with a wrong label or one shifted instruction would produce a program that compiles but fails at run time, so functional tests alone cannot prove the port.
- **Decision**: Port every runtime.py emitter and data reservation to Rust, and add runtime_dump.py plus a cairn-rs runtime command to the shared parity gate. The gate compares the full emitted code bytes, label offsets, fixups, rodata and writable-state layout; Python label insertion order is normalized to Rust's sorted map because ordering is not part of the emitted-byte contract.
- **Consequences**: The parity gate now reports 38/38 identical sources. Three Rust tests pin every helper label, the reserved writable state and fixup resolution; 87 Rust tests pass in total. Two Python behaviours were preserved deliberately: clz has no emitted helper because it lowers inline, and some fixups target writable or rodata labels rather than code labels.

### ADR-122: the Rust optimizer starts with the folding core
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: Code generation needs constant folding and effect-safe identities before it can lower expressions. optimize.py is 2,115 lines of folding plus structural passes (algebraic simplification, copy propagation, loop rewrites); porting the whole file at once would make a wrong fold hard to localize.
- **Decision**: Port the folding core first: constants, wrapping, truncating division, fold_constant/fold_unary, has_call with inline builtins, may_trap, the Optimizer's expression/statement/block folding, and the safe identities. Add fold_calls.txt plus fold_dump.py and a cairn-rs fold command to the shared parity gate. The structural passes are deliberately NOT ported yet and are not stubbed.
- **Consequences**: The parity gate reports 39/39 identical sources, now including 32 folding cases with wrapping and trap refusals. 94 Rust tests pass. A later milestone must port the remaining passes before a Rust codegen can claim byte identity for loop-heavy programs; this milestone does not claim full optimizer parity.

### ADR-123: for-loop lowering is the first structural optimizer pass in Rust
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: Code generation never sees a For node: semantic analysis leaves it for the optimizer to lower into a mutable binding and a counted while. The folding core alone cannot prepare a program for the Rust backend.
- **Decision**: Port lower_for_loops, including innermost-first descent, continue rewriting and the inclusive/exclusive bound, and add lower_dump.py plus a cairn-rs lower command to the parity gate. The oracle writes UTF-8 bytes explicitly because the Windows console would otherwise replace a non-ASCII character in a diagnostic.
- **Consequences**: The parity gate now checks lowering on every source and reports 39/39 identical. Remaining structural passes (algebraic simplification, copy propagation, loop unrolling, countdown, odd-pair fusion, induction and bottom-test rotation) are still Python-only; this milestone does not claim full optimizer parity.

### ADR-124: copy propagation is the second structural optimizer pass in Rust
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: A single-read pure definition can be replaced by its value at the copy, removing a frame slot. Applying the rewrite when a slot is written or read elsewhere would change generated code, so the safety predicates matter as much as the rewrite.
- **Decision**: Port apply_copy_propagation with has_call/may_trap purity checks, a single-read count over the whole function, operand-write barriers between definition and copy, and at most one rewrite per round up to 64 rounds. Add copy_dump.py and a cairn-rs copyprop command to the parity gate, applied after for-loop lowering.
- **Consequences**: The parity gate reports 39/39 identical sources including copy propagation. 96 Rust tests pass. Remaining Python-only passes: algebraic simplification, constant-loop unrolling, countdown latches, odd-pair fusion, induction strength reduction, bottom-test rotation and pure inlining.

### ADR-125: algebraic chain collection is the third structural optimizer pass in Rust
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: Algebraic simplification distributes literal coefficients over add-chains and collects like terms; it runs right after folding. A wrong signature or sign would rewrite a program into something different while still compiling.
- **Decision**: Port apply_algebraic_simplification with chain flattening, product distribution, effect and structural signatures, and rebuild-only-when-shrinking. Effect keys use source spans rather than pointer identity because dropped clones reuse addresses; add alg_dump.py and a cairn-rs alg command to the parity gate after copy propagation.
- **Consequences**: The parity gate reports 39/39 identical sources including algebraic simplification; 97 Rust tests pass. Two bugs were caught by the gate: an unwrap on a newly inserted table entry and pointer reuse that merged two distinct recursive calls. Remaining Python-only passes are loop unrolling, countdown latches, odd-pair fusion, induction strength reduction, bottom-test rotation and pure inlining.

### ADR-126: countdown latches are the fourth structural optimizer pass in Rust
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: A counted loop whose counter is dead after the loop can test the remaining iterations instead of the bound, removing the in-body increment. The rewrite is safe only under specific read/write and control-flow conditions.
- **Decision**: Port apply_countdown_latches with literal-write tracking, post-loop read guards, break/continue refusal, a single counter read, and recursive increment removal. Add count_dump.py and a cairn-rs count command to the parity gate after algebraic simplification.
- **Consequences**: The parity gate reports 39/39 identical sources including countdown latches; 98 Rust tests pass. Constant-loop unrolling, odd-pair fusion, induction strength reduction, bottom-test rotation and pure inlining remain Python-only.

### ADR-127: constant-loop unrolling is the fifth structural optimizer pass in Rust
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: A short loop around a self-recursive call can be unrolled into literal-counter clones so masks and bounds fold into immediates. The shape is deliberately narrow, and an over-broad rewrite would duplicate bodies where it brings no gain.
- **Decision**: Port apply_constant_loop_unrolling with the counter step and bound checks, self-call-only eligibility, jump and second-write refusals, trip and body limits, and deep literal substitution. Add unroll_dump.py and a cairn-rs unroll command to the parity gate after algebraic simplification.
- **Consequences**: The parity gate reports 39/39 identical sources including unrolling; 99 Rust tests pass. Remaining Python-only passes are odd-pair fusion, induction strength reduction, bottom-test rotation and pure inlining before codegen.

### ADR-128: pure inlining is the last optimizer pass ported before codegen
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: A function whose body is exactly one return expression can be inlined at its call sites, removing call overhead. A wrong effect count would duplicate or drop a call, so the parameter-read guards matter more than the substitution itself.
- **Decision**: Port apply_pure_inlining with single-return eligibility, node budgets, recursion guard, argument effect counts (zero reads must not drop a call or trap, multiple reads must not duplicate a call), and fresh clones per substitution. Add inline_dump.py and a cairn-rs inline command to the parity gate.
- **Consequences**: The parity gate reports 39/39 identical sources including inlining; 100 Rust tests pass. Odd-pair fusion, induction strength reduction and bottom-test rotation are still Python-only; they affect only specific loop shapes and can be ported if parity reveals them.

### ADR-129: the last three optimizer passes complete the Rust optimizer
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: Odd-pair fusion, induction strength reduction and bottom-test rotation were the remaining Python-only passes. Each changes a narrow loop shape, and a wrong rewrite would be invisible until generated bytes diverged.
- **Decision**: Port all three with their exact shape guards and add fuse_dump.py and final_dump.py to the parity gate. Induction locals are allocated in pre-order (outer blocks first) to match Python's block collection; the first implementation recursed first and produced a different slot order. An unsafe pointer traversal was replaced with a recursive mutable walk after it crashed on array_sum.
- **Consequences**: The parity gate reports 39/39 identical sources across all optimizer passes. 100 Rust tests pass. The Rust optimizer is now complete apart from the CLI pipeline order, so the remaining compiler work is codegen, builder, CLI and capabilities.

### ADR-130: codegen starts with its pure helper layer
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: The 4,550-line CodeGenerator class is too large to port in one step without losing parity. Its pure helpers decide labels, frame offsets, leaf/call classification and magic-division parameters, and are the foundation every lowering rule uses.
- **Decision**: Port the helper layer first: function labels, slot and array offsets, alignment, leaf and lowering-call predicates, structural index equality, rotate-count simplification, plus-step recognition and magic-division search. Add magic.txt and magic_dump.py to the parity gate; the CodeGenerator class itself remains Python-only.
- **Consequences**: The parity gate reports 40/40 identical sources. The codegen task stays open for the class port. No full-program byte identity is claimed yet: the helpers are proven but no Rust function has emitted a user function's bytes.

### ADR-131: a byte-parity generator slice and an explicit pipeline state machine
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: The codegen class has over one hundred lowering methods. Porting it as one block would make a wrong byte pattern hard to localize, and the stage order was implicit in nested function calls.
- **Decision**: Add a first generator slice covering the entry stub, frameless functions and simple expressions (literals, names, string handles, printer calls), checked by a generated-artifact parity dump. Add pipeline.rs with an explicit Stage state machine whose transition is pure and whose shell lives in main.rs, satisfying the functional-core/imperative-shell directive.
- **Consequences**: Parity is now 43/43 sources, including three tiny generated programs with byte-identical .text, labels, fixups and data. 102 Rust tests pass, including two state-machine tests. The codegen task stays open for wider lowering shapes.

### ADR-132: homed local slots use the frameless register path
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: A function whose slots fit in the register homes avoids a stack frame. The save/pad/restore pattern must match Python exactly or every generated function's prologue differs.
- **Decision**: Extend the generator slice to allocate slot homes in HOME_REGISTERS order, push them on entry, add an 8-byte pad when the saved-unit count is even, store literal and name bindings through the home, load names from the home, and reverse the pop order at the single epilogue.
- **Consequences**: Byte parity covers a function with one local slot (44/44 sources). Deeper shapes (branches, loops, frame functions, calls with stack arguments) remain in the open codegen task.

### ADR-133: the Rust crate is Python-free and has an AIKit release project
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: The review found Python oracle scripts inside the Rust crate and no evidence that the deliverable project was created with AIKit. Both conflict with a Rust-only implementation directive.
- **Decision**: Move all parity oracle tooling and specs from rust/ to parity/ so the crate contains only Rust, and create projects/cairn-rs with aikit.py new as the Rust-only release project. The new crate uses a Cargo path dependency on the shared compiler core and keeps all I/O in main.rs.
- **Consequences**: cairn gates remain 7/7 and cairn-rs gates are 4/4. Parity is 46/46, now including generated bytes for if and while. The AIKit hygiene gate initially reported inconclusive for a Python-free project; the Rust crate's .rs sources satisfy it once present.

### ADR-134: frame functions lower with ranked register homes
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: A function with more slots than register homes needs an rbp frame, a save area for callee-saved homes and a different epilogue. The home ranking also changes: deeper-loop slots win, ties by descending slot.
- **Decision**: Add the frame path to the generator: rank frame-function slots by descending slot at loop depth zero, map the first six to callee-saved registers and the rest to caller-saved extras, save callee homes at slot_offset(slots + i), allocate align16((slots + homes) * 8) bytes, and restore with leave_ret. Frameless functions keep their existing push/pad/pop path.
- **Consequences**: Generated-byte parity is 47/47 including a seven-slot frame function. A corpus-wide diagnostic currently matches 2 of 27 real programs, so binary-operator and call lowering remain the next codegen slices.

### ADR-135: binary addition of homed slots lowers to one lea
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: A homed a+b is the hot shape in recursive helpers and a generic add through rax would not match the reference bytes.
- **Decision**: Lower Name+Name with both slots homed as lea rax,[left+right] via Assembler::lea_scale with scale 0 and displacement 0. Other binary shapes stay unlowered until their reference form is measured.
- **Consequences**: Curated parity is 48/48 including a two-parameter helper. The real-program diagnostic stays at 2/27 because recursion uses tail-return and delta-inlining forms not ported yet.

### ADR-136: literal user calls lower through the ABI registers
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: A non-inlined helper with literal arguments is the simplest call shape in the corpus and a prerequisite for recursive call lowering.
- **Decision**: For a call whose arguments are all i64 literals and at most four in number, emit mov_imm64 into rcx/rdx/r8/r9 and a padded call to fn.<name>. The result remains in rax, so a surrounding return needs no move.
- **Consequences**: Curated parity is 49/49 including a two-function program with a real call. The real-program diagnostic remains 2/27: recursive callees use delta-inlined copies and non-literal arguments that are not lowered yet.

### ADR-137: non-literal call arguments use direct home moves
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: A homed name passed to a helper should move straight into the ABI register; a round trip through rax costs an instruction and breaks byte parity.
- **Decision**: For user calls, emit literals directly into rcx/rdx/r8/r9, move a homed Name straight into the argument register, and evaluate other argument forms into rax before the move.
- **Consequences**: Curated parity is 50/50 including a variable-argument call. The real-program diagnostic remains 2/27 because recursive callees still use delta-inlined copies that this generator does not emit.

### ADR-138: the capability manifest is ported with byte-exact JSON
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: The manifest is the first thing an agent reads; a single key or formatting difference makes a Rust compiler disagree with the documented surface.
- **Decision**: Build the manifest as a small JSON value tree in Rust and render it with Python-compatible indent=2 and ASCII escaping. Compare the full canonical manifest against Python in the parity gate.
- **Consequences**: The parity gate is 51/51 including the complete capability manifest. The remaining Python-only compiler surfaces are builder and CLI; delta-inlined recursive codegen is still the blocking shape for full .text parity.

### ADR-139: else-less if branches on the inverted condition
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: A two-way if and a guard if use different label sets and condition polarity in the reference generator.
- **Decision**: For an if with no else, allocate one if.else label, branch to it when the condition is false, emit the then block, then bind the label. A two-way if keeps if.then and if.end with the true branch inverted to a forward jmp.
- **Consequences**: Curated parity is 52/52 including mutual calls with a guard. The real-program diagnostic remains 2/27 because recursive callees still use delta-inlined copy shapes.

### ADR-140: delta-inlined self calls lower as pure text copies
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: Recursive helpers with one unbound parameter are emitted by the reference as up to seven literal-shifted text copies plus a final real call. Without this shape none of the recursive benchmark functions can match.
- **Decision**: Detect single-parameter functions with no binding statements and a constant-delta self call, reserve eight hold units, and lower each self call as a fresh epilogue-labelled copy of the body with an accumulated delta. Reads and conditions of the delta slot use lea with the negated delta; the depth-seven fallback binds the shifted argument directly into rcx.
- **Consequences**: Curated parity is 53/53 including a recursive helper. The real-program diagnostic is still 2/27: fib and the benchmarks combine delta with multi-slot frames, arrays or other lowering shapes not yet ported.

### ADR-141: the check --json CLI envelope is byte-identical
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: The check command is the first thing an agent uses to inspect a source; its JSON envelope and fold count must match exactly across implementations.
- **Decision**: Add cli.rs, a pure module that maps a source to a check envelope, and a cairn-rs check command. The fold count mirrors Python's pass order: one fold pass, pure inlining, then a second fold pass on the same optimizer. Windows path separators are normalised at the I/O edge.
- **Consequences**: Curated parity is 53/53 including CLI envelopes; a corpus diagnostic shows 10 of 27 real programs already have byte-identical check envelopes. Remaining differences are fold counts from unported optimizer interactions.

### ADR-142: the build pipeline is ported to Rust
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: The builder is the last stage boundary between the checked program and an executable image; leaving it in Python means a Rust compiler could not build a program end to end.
- **Decision**: Add builder.rs with compile_source, output_path and an atomic write_executable. The fold count is computed before the pipeline mutates the AST. A build parity dump compares folded count, image size and the full image bytes with Python.
- **Consequences**: Curated parity is 53/53 including the build pipeline. Builder is no longer Python-only for these sources; the remaining Python-only CLI commands and the unported multi-slot recursive codegen shapes are recorded in the ledger handoff.

### ADR-143: large immediate updates use a materialised addend
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: A 64-bit literal mask cannot fit the group-1 imm8/imm32 form; the earlier generator panicked on such a benchmark and broke the parity run.
- **Decision**: When an in-place add's literal exceeds signed 32 bits, materialise the addend with movabs into rax and add it to the slot's home. Small literals keep the one-instruction add_imm form. A regression spec pins the largest i64 mask.
- **Consequences**: Curated parity is 54/54 including the large-literal shape; the generator no longer panics on the bit benchmarks. The full corpus diagnostic remains unchanged at 2/27 because those programs combine the shape with vectorised loops and array state.

### ADR-144: array bindings use frame slots with descending base offsets
- **Date**: 2026-11-22
- **Status**: Accepted
- **Context**: Array bindings exclude their frame slots from register homes and live in a frame function. Element zero sits at the span's lowest displacement, so literal indices are addressed by subtraction from the span top.
- **Decision**: Collect array spans from the function body, exclude those slots from homes, force the frame prologue, materialise aggregate initialisers in ascending element order from the lowest displacement, and lower a literal array load as mov rax,[rbp+slot_offset(base+size-1-index)].
- **Consequences**: Curated parity is 55/55 including a four-element array. The real-program diagnostic stays at 2/27 because vectorised array loops and multi-slot recursion still use shapes not lowered by this generator.

### ADR-145: listing --json is implemented but not yet gated
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: The listing command is the machine-readable view of emitted bytes. Python keeps label insertion order for ties at the same offset; the Rust assembler stores labels in a sorted map.
- **Decision**: Add listing_json to cli.rs and a cairn-rs listjson command. Leave it out of the parity gate until the assembler preserves insertion order, and record the tie-order gap in the audit report rather than hiding it.
- **Consequences**: Curated parity remains 55/55. The listing gap is explicit and bounded: it is a label-order issue, not an emitted-byte issue, because the underlying .text and fixups already match.

### ADR-146: label insertion order makes listing parity gateable
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A stable sort by offset preserves insertion order for labels at the same offset. A BTreeMap silently reordered arg_fill.token and arg_fill.skip and made listjson differ from Python.
- **Decision**: Record label binding order in Assembler::label_order, carry it into Artifact::text_label_order, and build the listing from that insertion-ordered vector with a stable sort by offset. Enable listjson in the parity gate.
- **Consequences**: Curated parity remains 55/55 and now includes byte-exact listing envelopes. The emitted bytes were already identical; this fixes only the reported label order.

### ADR-147: verify --json is ported with byte-exact envelopes
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Verification is the agent-facing proof that a built artifact is well formed; its JSON envelope must match the reference exactly.
- **Decision**: Add verify_json to cli.rs and a cairn-rs verifyjson command that compiles in memory, runs verify_image and renders the Python-shaped envelope, including an empty [] array when no problems exist. Add the oracle to the parity gate.
- **Consequences**: Curated parity remains 55/55 and now includes verify envelopes. The run command and full .text parity for the real corpus remain open.

### ADR-148: run --json is ported with process execution at the edge
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: The run command builds to a temporary executable, executes it and reports an envelope. Two subtle mismatches surfaced: ok is false for a non-zero return code, and Windows reports the return code as an unsigned 32-bit value.
- **Decision**: Implement runjson in main.rs: compile in memory, write the image atomically to a temp sibling, spawn the process with piped output, apply a 30-second bound, then render the Python-shaped envelope with returncode normalised to u32 and ok set only for a zero, in-time exit.
- **Consequences**: Curated parity remains 55/55 and now includes run envelopes. All four JSON CLI surfaces (check, build, listing, verify, run) match byte-for-byte. Vectorised array loops and multi-slot recursion remain the codegen gap.

### ADR-149: large array fills emit one store loop
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A fill literal longer than eight elements expands to thousands of nodes; materialising each one produced a two-megabyte .text instead of the reference's short loop.
- **Decision**: Detect an all-equal IntLiteral fill longer than ARRAY_UNROLL_LIMIT and emit one up-counting store loop: count in rdx, fill in rax, span base in rcx, a fresh array_fill label, store/add/sub/jcc. Shorter or mixed literals keep element-by-element stores.
- **Consequences**: Curated parity is 56/56 including a sixteen-element fill. The full corpus diagnostic stays at 2/27 because the remaining array benchmarks use fused read-modify-write and summation loops that are not lowered yet.

### ADR-150: two-lane array RMW loop lowered with a known clear gap
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: The array read-modify-write benchmark uses a two-lane SSE2 loop; without it the generated .text diverges completely from the reference.
- **Decision**: Detect while i < N { a[i] = a[i] + r; i = i + 1; } with an even bound and homed index/addend, and emit the reference loop: broadcast the addend into xmm1, then load/add/store two elements per iteration with movdqu and paddq. Park the reproducer under known_gaps until the frame-clear difference is resolved.
- **Consequences**: Curated parity remains 56/56. The remaining difference is one xor-zero home clear emitted by Python's _clear_frame before the body; the vector loop body itself matches.

### ADR-151: frame home ranking closes the vector RMW gap
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: The parked vecrmw spec differed by one xor-zero clear; fixing it required matching the reference home ranking, not a special case.
- **Decision**: Rank frame-function slots by deepest loop nesting with ties by descending slot, clear unassigned non-parameter homes, and drop the extra operand-slot check from the RMW matcher because sema leaves it at -1. Return the spec to the gated set.
- **Consequences**: Curated parity is 57/57 including the two-lane array RMW loop. Other real benchmarks still use summation and stdin-scan loop shapes that are not lowered.

### ADR-152: self-index array writes lower to one indexed store
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A fill loop a[i]=i was taking a temp-slot round trip while the reference uses one SIB store based on rbp.
- **Decision**: For an array element store whose value and index are the same homed name, emit mov [rbp + index*8 + array_element_base(slot,size)], index directly via Assembler::mov_store_indexed. Other stores keep the generic path.
- **Consequences**: Curated parity is 58/58 including the indexed write loop. Summation, stdin-scan and multi-slot recursive shapes still block full corpus parity.

### ADR-153: array accumulator loads, latch numbering and self-index stores are byte-exact
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: The array_sum loop needed an indexed load added to the accumulator, a latch label allocated before the body, and self-index stores lowered to one SIB write.
- **Decision**: Add emit_index_load for dynamic indices, lower slot=slot+a[i] with add into the home, allocate while.latch before the body to match counter order, and emit a[i]=i as mov [rbp+index*8+base], index. Add arraywrite and vecrmw specs to the gate.
- **Consequences**: Curated parity is 58/58; array_sum now produces a byte-identical full dump including labels and fixups.

### ADR-154: constant hoisting and general binary lowering close the bitop family
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Big literals in loops were materialised with movabs on every iteration and general binary expressions emitted nothing, so bswap, clz, ctz and popcount diverged completely.
- **Decision**: Plan spare homes per loop for the most-used big literals, save them in the frameless prologue, load them at loop entry, and consult the hoist map at every literal site. Port binary-into-destination, a general rax binary lowering, frameless home clears and the inline clz/ctz/bswap/rotl builtins.
- **Consequences**: bswap now produces a byte-identical full dump; the corpus diagnostic rises from 2/27 to 4/27 (argv_walk and hex_io are excluded because the Python oracle itself errors). Curated parity stays 58/58.

### ADR-155: fold-update, alignment filler and rotl fast path close three more programs
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Accumulator updates through calls, odd saved-home counts and leaf-value rotates each produced a distinct mismatch in the bitop family.
- **Decision**: Port _fold_update so slot=slot op value combines straight into the home, add the rdi alignment filler and body-may-hold rule, and use the leaf-value rotate fast path with the redundant mask removed.
- **Consequences**: clz, ctz and rotl are now byte-identical; the corpus diagnostic is 6/27 and curated parity stays 58/58.

### ADR-156: countdown latches and dynamic index loads restore corpus parity
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Countdown loops were lowered as compare-and-branch instead of decrement-and-jnz, and folding slot updates hid dynamic array index loads.
- **Decision**: Honour the while countdown flag with a sub and jnz latch, and let emit_expression lower a dynamic Name index into a mov_load_indexed so folded accumulator updates keep their load.
- **Consequences**: Nine of twenty-five valid corpus programs are byte-identical; array_sum is restored and curated parity remains 58/58.

### ADR-157: indexed comparisons and immediate element stores close sieve
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Sieve's membership test compares a dynamic array element to zero and its mark store writes a literal through a scaled index.
- **Decision**: Let condition lowering emit a dynamic index load before the compare and lower array element stores with an immediate value to one mov_store_indexed_imm.
- **Consequences**: Ten of twenty-five valid corpus programs are byte-identical; curated parity remains 58/58.

### ADR-158: runtime call ABIs and inlined str_byte close string scans
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: popcount and str_len take their argument in rax, str_byte inlines for bounded leaf indices, and loop latches compare two homes rather than a home against a literal.
- **Decision**: Route popcount and str_len through the rax-ABI runtime call sites, inline str_byte with string-bound facts derived from str_len guards, and lower Name-vs-Name conditions with cmp_reg_reg.
- **Consequences**: popcount and str_scan are byte-identical; the corpus diagnostic is 12/27 with curated parity unchanged at 58/58.

### ADR-159: stdin cursor promotion lowers read_char in leaf functions
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A stdin-reading leaf keeps the buffer base, cursor and count in spare homes so each read_char need not reload the memory words.
- **Decision**: Detect functions whose only calls are builtins and that read stdin, promote up to three spare homes for buffer/cursor/count, save them in the prologue, load them at entry, store the cursor in the epilogue, and inline the have/drained branches of read_char.
- **Consequences**: char_stream's prologue and read_char sequence are now byte-identical; its remaining difference is loop rotation. Curated parity stays 58/58 and the corpus diagnostic is 12/27.

### ADR-160: rotated stdin scan closes char_stream
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: The stdin-reading loop hoists its first fetch and uses the EOF guard as the only back-edge, a shape the generic while lowering cannot produce.
- **Decision**: Detect the three-statement read/guard/update stdin scan, emit one fetch site with jmp into it, add the accumulator at the top, and branch back only on the non-negative EOF test.
- **Consequences**: char_stream is byte-identical; the corpus diagnostic is 13/27 with curated parity unchanged at 58/58.

### ADR-161: bit tests, logical branches and frame width close string and gcd shapes
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Low-bit comparisons, logical short-circuit conditions and functions with more than four parameters were lowered generically or with the wrong frame shape.
- **Decision**: Add test-based bit peepholes for (x&m)==0 and x%2**b==0, lower &&/|| as branching chains, force a frame when parameters exceed four, and add the missing runtime-call ABIs.
- **Consequences**: Thirteen of twenty-five valid corpus programs are byte-identical; curated parity stays 58/58. Pair inlining, magic division and multi-slot recursion remain open.

### ADR-162: logical-shift masks are excluded from hoist counts
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A(hash mixer's (x>>k)&mask is one shr instruction, so the mask never materialises and must not be counted as a hoist candidate.
- **Decision**: Recognise the (x>>k)&(2**(64-k)-1) shape, skip its mask when counting big literals for hoisting, and lower it as one logical shr both into rax and into a destination home.
- **Consequences**: splitmix's hoisted constants now match the reference and curated parity stays 58/58; its remaining difference is in-place update folding. Corpus remains 13/27.

### ADR-163: in-place folding, cmov swaps and magic division extend codegen
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Hash-mixer updates, branchless gcd swaps and literal modulo all needed destination-driven lowering rather than a generic rax round trip.
- **Decision**: Recognise logical-shift operands in combines_in_rax_only, lower three-statement swaps as two cmovs, track break/continue targets, reuse ZF from the last writer, lower variable-count shifts, and emit magic multiply for literal division and modulo, counting the divisor and multiplier in hoist planning.
- **Consequences**: splitmix is byte-identical and binary_gcd's bgcd function matches; the corpus diagnostic is 14/27. The remaining gaps are user-function inlining, magic-division call shapes, recursion and frame layout. Curated parity stays 58/58.

### ADR-164: copy-free user functions inline at call sites
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Copy-free callees with no parameter rebinds can fold into the caller with their parameters living in the ABI argument registers, avoiding a machine call.
- **Decision**: Store program functions in the generator, detect copy-free non-rebinding callees, bind their parameters to the argument registers and locals to free homes, emit the body with a fresh copy epilogue, and add the select-if and cmov swap peepholes that inline bodies rely on.
- **Consequences**: call_chain's mix3 now inlines and its body matches through the select; the corpus diagnostic is 14/27. Rebinding callees, delta pair inlining and multi-slot recursion remain open. Curated parity stays 58/58.

### ADR-165: rebinding callees inline with spill and reverse argument binding
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A callee that reassigns its parameters needs bind-once guest homes and spill of borrowed caller homes; pure arguments also bind right-to-left.
- **Decision**: Plan free-then-borrowed guest homes for rebinding callees, push and pop spilled caller homes, move ABI registers into parameter homes at copy entry, clear unassigned locals, lower name-plus-literal arguments with one lea, and bind pure arguments in reverse order.
- **Consequences**: binary_gcd is byte-identical; the corpus diagnostic is 15/27. The remaining shapes are delta pair inlining, multi-slot recursion, magic-division call shapes and frame layout. Curated parity stays 58/58.

### ADR-166: halving loop peephole closes collatz
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: The fused odd-pair Collatz loop halves first and folds the parity into a carry-selecting cmov and adc, so the generic branch-heavy lowering never matched.
- **Decision**: Detect the guarded k>1 loop with an even k/2 branch and an odd (3k+1)/2 branch, then emit one shr, a scaled lea, cmov on carry and adc for the step counter, preserving the generic loop's label numbering.
- **Consequences**: collatz is byte-identical; the corpus diagnostic is 16/27. Remaining shapes are delta pair inlining, multi-slot recursion, magic-division call shapes and frame layout. Curated parity stays 58/58.

### ADR-167: big bit-test masks use and-immediate instead of test imm8
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A large array-index mask overflowed the assembler's imm8 test encoding and panicked, which broke nqueens generation.
- **Decision**: Keep the test-immediate home peephole only for masks up to 127; otherwise evaluate the operand into rax and lower the mask as and-immediate or a scratch register before branching.
- **Consequences**: The nqueens panic is fixed and all 58 curated specs still pass; the corpus diagnostic remains 16/27.

### ADR-168: homed-left multiply combines in place
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A multiply whose left operand already lives in a home can read that home directly instead of parking it in rcx and reading it back.
- **Decision**: Extend the homed-left in-place rule to multiplication, emitting imul rax, left_home whenever the right operand is call-free, while keeping the literal-product lea and imul fast paths.
- **Consequences**: Curated parity stays 58/58 and the corpus diagnostic remains 16/27; the remaining call and frame shapes are still open.

### ADR-169: frame clearing, computed conditions and power-of-two division
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Frame functions left non-homed slots unclear, and comparisons whose left side is computed or divides by a power of two were lowered generically.
- **Decision**: Clear non-homed frame slots from a zeroed rax while marking array spans assigned, compare a computed left operand against a literal, and lower power-of-two division with the sign-fixup shift chain.
- **Consequences**: Curated parity stays 58/58 after repairing the array-span regression; the corpus diagnostic remains 16/27 and the remaining gaps are destination-driven inlining and recursion.

### ADR-170: call-argument parking preserves nested call order
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Arguments containing nested calls would clobber already-bound caller-saved argument registers, so the reference parks each result on the stack before binding in reverse.
- **Decision**: Detect a multi-argument call whose arguments contain calls, evaluate them left to right pushing each result, then pop them into the ABI registers in reverse order before the call or inline copy.
- **Consequences**: tak is byte-identical; the corpus diagnostic is 17/27. Remaining shapes are delta pair inlining, multi-slot recursion, magic-division calls and frame layout. Curated parity stays 58/58.

### ADR-171: homed name plus or minus literal uses one lea
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A homed name with a small literal offset can compute into rax with one lea, but the generic binary path emitted a move and an arithmetic immediate.
- **Decision**: In the rax binary path, lower a Name plus or minus a 32-bit literal with lea_imm from its home before falling back to the arith-immediate form.
- **Consequences**: Curated parity stays 58/58 and the corpus diagnostic is 17/27; ackermann's remaining gap is the reference's pair-machine home reservation.

### ADR-172: variable division and computed-left comparisons
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Variable divisors emitted nothing and conditions comparing a computed left against a home were dropped, so trial-division loops diverged completely.
- **Decision**: Lower variable / and % with a guarded 32-bit fast path and a 64-bit fallback, track non-negative slots to skip the overflow probe, and compare a computed left operand by emitting it into rcx before comparing against the right home.
- **Consequences**: primes now generates the division guards and d*d test with only a few bytes of difference; curated parity stays 58/58 and the corpus diagnostic remains 17/27.

### ADR-173: bind division dividend straight into rcx
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: The reference binds the dividend directly into the ABI register used by its guarded division chain; the generic path round-tripped it through rax.
- **Decision**: Use the destination-driven emit_into when binding the left operand of a variable division into rcx, removing the extra move.
- **Consequences**: primes is byte-identical; the corpus diagnostic is 18/27. Remaining shapes are pair inlining, recursion, copy-home inlining and frame layout. Curated parity stays 58/58.

### ADR-174: delta hold slots carry left values across calls
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Delta self-call copies reserve per-level hold slots in the function frame; parking a binary's left operand in rcx across a nested call was clobbered and byte-diverged.
- **Decision**: When a binary's right operand calls in a delta-eligible function, store the left value into the level-indexed rsp hold slot and reload it into rcx after the call; use push/pop elsewhere.
- **Consequences**: fib and fib30 are byte-identical; the corpus diagnostic is 20/27. Remaining shapes are pair-mode reservation, copy-home inlining and multi-slot recursion. Curated parity stays 58/58.

### ADR-175: pair-mode detection reserves the chain counter home
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: The reference's two-parameter pair-inline shape reserves r13 as the chain-counter save slot in the prologue and epilogue.
- **Decision**: Detect the two-slot, two-parameter self-call shape with a zero guard on the second parameter and push/pop r13 around the frameless body, matching the reference's extra saved home.
- **Consequences**: ackermann's prologue now matches; its remaining gap is the pair-machine chain loop and delta copies. Curated parity stays 58/58 and the corpus diagnostic is 20/27.

### ADR-176: pair machine chain loops and delta copies
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: The two-parameter pure-text inline shape lowers self-calls as counted chain loops around inlined body copies, with the value parameter rebound in its home and the first parameter read through an accumulated delta.
- **Decision**: Track the value slot and chain-loop depth, detect pair-shaped self-calls, emit chain loops with the r13 counter, emit body copies with accumulated deltas, and bind two-argument calls with a pure first argument right-to-left.
- **Consequences**: ackermann's chain loop and copies now match through byte 1185 but an extra nested copy remains; curated parity stays 58/58 and the corpus diagnostic is 20/27.

### ADR-177: pair depth cap and delta-folded offsets close ackermann
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: The pair machine used the general inline depth cap and omitted the accumulated delta from home-offset lea folds, leaving one extra copy and wrong literal shifts.
- **Decision**: Use the pair-specific depth cap of six and fold the current delta into name-plus-literal lea offsets in both rax and destination forms.
- **Consequences**: ackermann is byte-identical; the corpus diagnostic is 21/27. Remaining shapes are copy-home inlining, multi-slot recursion and frame layout. Curated parity stays 58/58.

### ADR-178: temp-pool left hold closes nqueens
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A binary whose right operand is a nested expression kept its left value in rcx, but the reference parks it in the spare temporary registers r11/r10 so nested lowerings cannot clobber it.
- **Decision**: Hold the left operand in the level-indexed temp register for a call-free non-leaf right, use the delta hold slot or stack for calling rights, and add the movzx mask and shift-through mask peepholes used by inline bodies.
- **Consequences**: nqueens is byte-identical; the corpus diagnostic is 22/27. Remaining shapes are copy-home inlining and frame layout. Curated parity stays 58/58.

### ADR-179: shift-through arguments fuse across inline copies
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A caller argument of x>>m feeding a parameter used once as p<<m can fuse to x&-2**m across the copy boundary without binding the parameter.
- **Decision**: Detect forwarded shift-through parameters, skip evaluating and binding those arguments, lower the parameter's sole << use as a masked read of the caller's home, and restore the shift map after the copy.
- **Consequences**: call_chain's shift fusion now matches but its return destination still differs; curated parity stays 58/58 and the corpus diagnostic is 22/27.

### ADR-180: copy-home return slot override
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: An inlined copy's final result normally needs one move into the caller's destination; when the returned local is defined before its first read, that local can home directly in the destination register.
- **Decision**: Track the caller's destination home through emit_value, detect a return of an assigned local in the foreign-inline copy, and remap that local's guest home to the destination so the final move disappears.
- **Consequences**: call_chain and many_params advanced but still have small byte differences; curated parity stays 58/58 and the corpus diagnostic is 22/27.

### ADR-181: subtract homed right operand in place
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Destination-driven binary lowering handled addition and bitwise ops with a homed right operand but not subtraction, leaving accumulator chains in a rax round trip.
- **Decision**: Lower subtraction with a homed right operand directly into the destination via binary sub, extending the in-place compound.
- **Consequences**: mandelbrot's divergence moved later; curated parity stays 58/58 and the corpus diagnostic remains 22/27.

### ADR-182: guarded alias substitution and rax movzx masks
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Bare-name arguments alias into an inline copy only when no inline is skipped by a call or rebind, and 8/16-bit masks lower as movzx in the value path too.
- **Decision**: Gate argument substitution and binding skips on the copy actually being applicable, and add the movzx mask peephole to rax-path binary lowering.
- **Consequences**: call_chain's first divergence moved past the inline copy into its final mask; curated parity stays 58/58 and the corpus diagnostic is 22/27.

### ADR-183: assembler peephole drops redundant source move before movzx
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A value already in the source register was copied into rax immediately before a movzx of that same register, producing three dead bytes and a stale inline-copy label offset.
- **Decision**: Drop the preceding mov rax,src in the assembler's movzx emitters and clamp label/fixup offsets past the truncated end.
- **Consequences**: call_chain is byte-identical; the corpus diagnostic is 23/27. Remaining valid gaps are many_params and mandelbrot; curated parity stays 58/58.

### ADR-184: tail returns emit into the copy return home
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A foreign-inline copy with a return destination lowers its tail return directly into that home rather than computing in rax and moving.
- **Decision**: Track a copy-return home alongside the copy state and use destination-driven emission for tail returns inside an inline copy, restoring it on exit.
- **Consequences**: Curated parity stays 58/58 and the corpus diagnostic is 23/27; many_params still differs in its accumulator-chain register choice.

### ADR-185: pad slot holds call lefts; homed product combines
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Frame functions with a pad slot park a call left operand in [rsp] rather than pushing, and a homed-name times literal right operand can combine into a destination with rax alone.
- **Decision**: Track the active pad slot for call left-holds and recognize homed-name products in combines_in_rax_only.
- **Consequences**: many_params' byte gap narrowed from 44 to 12 bytes; curated parity stays 58/58 and the corpus diagnostic is 23/27.

### ADR-186: peel parens in homed-right binary combine
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: A homed right operand wrapped in parentheses was missed by the in-place combine rule, forcing a generic left-hold with extra moves.
- **Decision**: Peel parentheses before the homed-right register check in binary lowering.
- **Consequences**: many_params is byte-identical; the corpus diagnostic is 24/27. Only mandelbrot's frame layout remains; curated parity stays 58/58.

### ADR-187: frame update folds and extra-home call saves close the corpus
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: Frame functions lower slot self-updates as memory-operand instructions and save r10/r11 around calls; together with peeled homed-right combines this completes byte-identical lowering for all 25 valid corpus programs.
- **Decision**: Fold frame-slot updates into group-1 memory instructions, save extra homes across call sites with alignment padding, and peel parentheses before the homed-right binary combine.
- **Consequences**: All 25 valid examples and benchmarks now produce byte-identical dumps; curated parity remains 58/58. Python parity oracles are still used for verification; the argv_walk and hex_io sources error in the Python oracle and are excluded.

### ADR-188: Rust golden verification replaces Python parity oracles
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: After proving byte-identical output across all 25 valid corpus sources, the reference outputs were captured once as a frozen SHA-256 manifest and the front-end-parity gate now runs a Rust integration test.
- **Decision**: Add parity/golden.sha256 with 431 frozen hashes, a Rust golden_parity integration test that recomputes them with the Rust binary, and point the front-end-parity gate at that test instead of the Python comparator.
- **Consequences**: Ongoing verification is Rust-only; all 7 cairn gates pass and the corpus is byte-identical for all 25 valid sources.

### ADR-189: repair codegen oracle and close the last two corpus programs
- **Date**: 2026-11-23
- **Status**: Accepted
- **Context**: The Python codegen oracle ignored the program returned by optimize and never ran lower_for_loops, so argv_walk and hex_io errored; the Rust generator also lacked inlined arg_byte and fused hex-plus-newline emission.
- **Decision**: Run lower_for_loops before optimize in the oracle, inline arg_byte for token zero with a leaf index, fuse print_hex followed by print_char(10), and extend the Rust golden manifest to all 27 corpus sources while excluding the two environment-dependent run envelopes.
- **Consequences**: All 27 examples and benchmarks are byte-identical; the Rust golden manifest holds 591 frozen hashes and the corpus diagnostic is 27/27.

### ADR-190: remove the Python bootstrap compiler
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: The Rust compiler now matches all 25 valid corpus programs byte-for-byte and its golden verification is Rust-only, so the Python implementation and its oracles are dead weight.
- **Decision**: Delete the Python cairn package, its unittest suite and the Python parity oracles; migrate the benchmark harness to cairn-rs buildexe with a --no-optimize flag and point the project test command at cargo test.
- **Consequences**: The project is Rust-only; 6 AIKit gates pass, cargo tests and 591 golden hashes pass, and the cross-language harness now compiles with the Rust binary (about 31-115 ms versus 300-2000 ms for Python).

### ADR-191: consolidate the two Cairn project folders
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: A second AIKit project, projects/cairn-rs, was a thin CLI wrapper with a path dependency on the real compiler in projects/cairn/rust, duplicating its entry point and leaving two folders named Cairn.
- **Decision**: Remove projects/cairn-rs; projects/cairn/rust is the single compiler and CLI, and all gates run from that one project.
- **Consequences**: One Cairn project folder remains, with 6/6 AIKit gates and the 591-hash Rust golden verification unchanged.

### ADR-192: withdraw partial loop unrolling
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: An earlier ADR numbered 192 added four way unrolling for constant trip loops and was reverted. A noise validated A B using pinned processes and QueryProcessCycleTime showed a control error of 2.31 percent on byte identical executables but only 0.52 percent median improvement on executables whose code actually changed. Earlier wall clock runs swung over 100 percent on identical code.
- **Decision**: Revert ADR-192 completely and keep the compiler at the byte identical corpus baseline. Do not keep optimizations that cannot be shown to reduce runtime.
- **Consequences**: The golden manifest returns to its pre ADR-192 hashes and the project stays at the verified corpus parity state. The validated measurement method is recorded in reports combined AIKIT CAIRN UNROLL for future performance work.

### ADR-193: skip stack alignment pad in leaf functions
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: The generator pads the stack by eight bytes whenever a function saves an even number of callee saved registers, to keep the stack aligned at call sites. Leaf functions issue no call and do not need that pad. call_chain makes twenty million calls to a leaf helper that paid two instructions per call for nothing.
- **Decision**: Detect functions whose body contains no call or division operator and skip the alignment pad in their prologue and epilogue. Recursive and calling functions keep the pad.
- **Consequences**: The call_chain executable is about two percent faster in a validated cycle count A B with a point one one percent control error, repeated across two runs. All twenty seven programs still match their outputs and C twins. Twelve golden hashes changed and two regression tests were added.

### ADR-194: four element SSE2 array RMW loop
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: The runtime vectorizer processed two i64 elements per iteration with SSE2. array_sum spends most of its time in that loop and issued one compare and branch per pair.
- **Decision**: Add a four element fast loop using two XMM registers and a two element tail, keeping the hot loop at the same PE offset. Extend the emitter register table with xmm2 and xmm3.
- **Consequences**: Six validated interleaved 100 round cycle A B runs showed a median gain of about 2.6 percent on array_sum against roughly 1 percent control error, with the loop offset unchanged. All outputs match, 6 golden hashes refreshed, 1 regression test added, 6 of 6 gates pass.

### ADR-195: combine hoisted literals directly with their home
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: try_fold_update copied a hoisted 64 bit constant through rax before adding or multiplying, costing an instruction in every affected loop iteration. splitmix and popcount were the largest losers.
- **Decision**: When the other operand is an IntLiteral with a hoisted register home, combine directly with that home instead of emitting mov rax home first.
- **Consequences**: Strict pinned core A B with byte identical controls measured splitmix at minus 3.0 percent over 8 clean rounds and popcount at minus 11.9 percent over 3 clean rounds, with smaller gains on bswap. clz and ctz regressed about 1 percent and hex_io about 1.6 percent, so the net across affected benchmarks is positive but mixed. All 27 outputs match, 21 golden hashes refreshed, one regression test added, 6 of 6 gates pass.

### ADR-196: combine hoisted multiply constants through destination emission
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: ADR-195 removed the rax copy for hoisted literals in try_fold_update, but destination-driven lowering (try_emit_binary_into) still emitted mov rax,home before imul for a 64 bit literal right operand with a hoisted home. splitmix's serial multiply chain paid that move every iteration.
- **Decision**: When a multiplication lowers its left operand into a destination and the right operand is an IntLiteral with a hoisted register home, imul the destination with that home directly instead of loading it through rax.
- **Consequences**: Pinned core interleaved cycle A B against the ADR-195 build with byte identical controls measured splitmix at a median minus 6.3 percent over 4 clean rounds (range -7.0 to -4.2) with 0.40 percent median control error. Only splitmix's dump changes; 3 golden hashes refreshed, the hoist regression test now pins a 70 byte loop body, all 27 outputs match, and 6 of 6 gates pass. The benchmark compare flagged fib30, folded_math and mandelbrot, whose dumps did not change, so those flags are window noise from a loaded saved baseline (probe 189 ms).
### ADR-197: hoist a third loop constant into caller-saved r8
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: HOME_REGISTERS has six entries, so a loop with four variable homes can hoist only two big literals. splitmix has three 64 bit constants and no calls; its third multiply reloaded a 10 byte movabs into rax every iteration.
- **Decision**: When a while loop's condition and body contain no call or division, offer r8 and r9 as extra hoist homes after the spare HOME registers. They are caller-saved argument registers, already dead after the prologue stores parameters, and the existing hoist-home prologue save keeps them covered. One loop per function may use the extras, conservatively.
- **Consequences**: Three pinned core interleaved cycle A B runs produced 12 clean rounds, 10 negative, median about minus 1.5 percent against the ADR-196 build with 0.4 to 0.6 percent median control error; loaded rounds were rejected by the one percent control gate. splitmix's loop body shrinks from 70 to 60 bytes and only its dumps change, so 3 golden hashes were refreshed and the hoist regression test now pins the 60 byte body. The wall clock benchmark compare was interference dominated (half the cases swung over 70 percent while the unchanged programs improved 15 to 30 percent), so its saved entry was dropped to keep a clean baseline; the runjson hashes confirm every program's output is unchanged. 6 of 6 gates pass.
### ADR-198: clear the remaining compiler warnings
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: A clean cargo build still emitted four warnings: capabilities() exposed the private Json tree, pe.rs kept two unused helpers, Binding carried an unread span, and the run path bound an unused mut.
- **Decision**: Make capabilities() module-private because capabilities_json() is the public surface, delete the unused put_u16 and read_u32 helpers, drop the unread Binding.span field, and remove the redundant mut.
- **Consequences**: cargo build is warning-free, no generated dump changes, so the golden manifest is untouched; cargo tests pass and 6 of 6 gates pass.
### ADR-199: restore the user-facing CLI surface after the Python removal
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: ADR-190 deleted the Python compiler but left README, SPEC and LLM_SYSTEM documenting its run, build, verify, listing and capabilities commands. The Rust binary only carried parity commands, so the published UX and the shipped binary disagreed.
- **Decision**: Add run, build with -o/--json/--no-optimize, verify for a source or an image, listing, and capabilities to cairn-rs. build with no -o writes the sibling .exe; verify prints a human verdict or a JSON envelope and exits nonzero on problems; run forwards captured stdout, stderr and the program's exit code. Rename the parity hex dump from build to buildimage and rename its 44 golden keys, leaving every hash unchanged.
- **Consequences**: The documented commands work again. runjson still emits the identical envelope (verified byte for byte) and golden parity passes 591 of 591. Five CLI integration tests cover build, run, verify, listing and capabilities. README, SPEC and LLM_SYSTEM now invoke rust/target/release/cairn-rs.exe, and the README prerequisites name Rust 1.70 or newer.

### ADR-200: bind the core values to live Rust evidence
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: values.rs still claimed the compiler imports only the Python standard library and named deleted Python guard tests; the frozen-surface test only checked that each guard string contained ::test_. The capabilities manifest repeated the Python dependency guarantee. G-003 requires a value change to carry an ADR and a live guard.
- **Decision**: Rewrite every value's evidence command to the Rust binary and every guard to rust/tests/<file>.rs::<test>. The frozen-surface test now reads the named file and fails unless it contains that test function. Add rust/tests/values.rs::the_crate_declares_no_dependencies, which rejects any entry under [dependencies] in Cargo.toml. Change the manifest guarantee to the Rust standard library.
- **Consequences**: Every published value names a guard that provably exists. Only the capjson hash changes (1 of 591); README, SPEC and LLM_SYSTEM agree with the binary; cargo tests and 6 of 6 gates pass.
### ADR-201: drop the dead Python packaging metadata
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: pyproject.toml still declared the deleted cairn Python package, its console script and setuptools packaging, so any pip install would fail. Since ADR-190 the build is cargo and the benchmark harness reads stdlib Python directly.
- **Decision**: Delete pyproject.toml and its README tree entry; Cargo.toml is the only build manifest.
- **Consequences**: Packaging metadata cannot promise a package that does not exist. No code or dump changes. The auto-detected Python dependency gate disappears with the metadata, leaving 5 of 5 gates; the live zero-dependency guard is now rust/tests/values.rs::the_crate_declares_no_dependencies.
### ADR-202: refresh the README performance table from one quiet run
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: The performance table still carried numbers from the Python-bootstrap era, quoted executable sizes of 71,168 bytes that no longer matched any build, and standings such as all four bit cases tied with C++ that the current binary does not reproduce. Every row also showed ranges stitched from several revisions, which the text called same-revision while the rows spanned different code.
- **Decision**: Replace the table with the 24 cases measured by one quiet-window --save --compare run at commit e2b12b0 (probe 15.1 ms, five rounds, best time kept), state that later commits changed only the CLI and documentation, and keep the 10 percent band as the tie rule. Measure every case's image size with the current release compiler and report it. Replace the standings paragraph with the harness's own goal lines plus the per-baseline verdicts derived from that run, keeping the ADR citations that explain structural ties.
- **Consequences**: Every number in the table is reproducible from a committed benchmark_history.jsonl record and the sizes come from the current linker. The table now shows 24 cases instead of 20, with clz, ctz, bswap and rotl promoted from prose. It also records that bswap trails C++ by about 23 percent and rotl trails Rust by 26 percent in that window instead of claiming ties the run does not support. No code changes; tests and 5 of 5 gates pass.
### ADR-203: freeze twin totals as a Rust parity gate
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: The README claimed a twin-parity check that compiled all four twins and diffed their totals, but that check lived in the deleted Python suite and the current harness only compares times. Nothing in the test suite would notice if a Cairn benchmark started printing a different total from its C twin, so a benchmark standing could be earned against a twin that computes something else.
- **Decision**: Capture each C twin's stdout digest in parity/twin_expected.sha256 after confirming by execution that the C, C++ and Rust twins print identical text for 23 of 24 cases. Add rust/tests/twin_parity.rs, which rebuilds each benchmark with the Rust compiler, runs it with the harness's stdin stream where applicable, and compares the stdout digest. Exclude argv_walk, whose source documents that its checksum deliberately folds its own build path. Move the test-only SHA-256 helper to rust/tests/common/mod.rs so the golden and twin suites share one implementation.
- **Consequences**: A Cairn total that drifts from its C twin now fails cargo test. Three tests cover 23 cases; the slowest group is the xorshift, collatz and binary_gcd group at roughly one second, and the whole file finishes in about four seconds when the groups run in parallel. argv_walk remains measured for time only, as documented. The golden manifest still passes 591 of 591 through the shared digest helper.
### ADR-204: forward-substitute a single-read definition into its only use
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: Every bit-builtin benchmark binds its operand to a local (var v = ..., then total = f(v)), so the code generator parked the value in the v slot and copied it back to rax for the builtin: two extra instructions and a longer loop body in rotl, bswap, clz, ctz and popcount. Copy propagation only replaced a binding when the only read was itself a bare copy.
- **Decision**: Extend the copy-propagation stage with a forward-substitution step: when a let's value has no call, no trap, no array read and no self-reference, the slot is read exactly once in the function, that read is a plain statement of the same block, and nothing in between writes a slot the value reads, the read is replaced with the value and the let is dropped. A second let that becomes substitutable is handled by the stage's existing fixpoint loop. The generator also peels parentheses in its left-operand combine fast path and lets rotl set a simple count in rcx before evaluating a value that cannot write rcx, removing the push/pop the general rotl path would otherwise pay.
- **Consequences**: The generated loops shrink (rotl 38 to 32 bytes, bswap 39 to 29, clz/ctz/popcount 28 to 25). On the project harness the five cases improved by 19 to 35 percent against the previous saved run (rotl 25.5 to 16.9 ms, bswap 22.1 to 14.4 ms, clz 17.9 to 13.3 ms, ctz 18.1 to 14.8 ms, popcount 18.9 to 15.3 ms); the same run was too noisy for the untouched cases to be read. 60 of 591 golden hashes were refreshed (the five cases plus binary_gcd across ten dump commands), the optimizer tests were strengthened for the new behaviour, and twin parity (23 cases) plus the full suite pass.

### ADR-205: stage argument evaluation so a complex argument cannot clobber a bound one
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: Arguments bind in reverse index order, so an argument at a lower index is evaluated after rdx/r8/r9 already hold higher-index values. Any argument whose lowering writes rcx/rdx/r8/r9 (a division, a variable shift or a call) therefore destroyed those values. The twin-parity gate caught it when ADR-204 forwarded a modulo expression into binary_gcd's first argument: the benchmark changed its total, and a standalone probe confirmed that f(a % 2147483647, 1000) returned the wrong value. Only the two-argument "second argument calls" special case protected against a nested call.
- **Decision**: Classify an argument as parked when it is not a literal, name or delta shape and its lowering writes rcx (calls, divisions, variable shifts). Evaluate parked arguments first, push each, bind every other argument straight into its register in reverse index order, then pop the parked values into their registers in ascending index order. The old call-specific special cases are removed because this covers them and also the division case they missed.
- **Consequences**: Complex arguments now cost a push and a pop, while leaf arguments and expressions that only use rax keep their previous code exactly, so call-heavy cases are unchanged (call_chain, tak and many_params stayed within noise). The wrong binary_gcd total is fixed and the twin gate passes again; the scratch probes were removed after verification.

### ADR-206: record a post-substitution baseline and refresh the performance table
- **Date**: 2026-11-24
- **Status**: Accepted
- **Context**: The published table came from commit e2b12b0 and said later commits changed only the CLI and documentation. ADR-196 to ADR-205 did change code generation, so that claim and every affected number were stale. Two --save runs landed at the same commit: the first had one mid-run stall (sieve medians around 220 ms while the best sample stayed near 10 ms, and the Rust twin never escaped it), the second was clean.
- **Decision**: Keep the second run as the baseline and delete the stalled duplicate record from .aikit/benchmark_history.jsonl. Rebuild the table from that record at commit 4d51404 (probe 14.1 ms, nine rounds, best kept), with image sizes measured by the current release compiler, and rewrite the verdict prose from the banded comparison: outside 10 percent Cairn beats C on 12 of 24 cases and loses to none, beats C++ on 11 and loses to none, and beats Rust on 11 while losing only binary_gcd by 11 percent.
- **Consequences**: The table matches the committed compiler and the baseline the harness compares against. It records that the five bit cases now beat C by 26 to 90 percent and that xorshift (276.3 ms) now leads all three baselines; the only remaining large gap is binary_gcd against Rust, and mandelbrot's recurrence gap sits inside the band. No code changed, so the test suite and golden manifest are untouched.

### ADR-207: unroll one-bit shift loops by two
- **Date**: 2026-11-25
- **Status**: Accepted
- **Context**: binary_gcd was the one case outside the noise band against Rust (149.1 vs 134.1 ms, 11 percent behind) while beating both C compilers. Disassembling the Rust twin showed LLVM tests two low bits per shift (`test $2` after shifting twice) and folds the double-even test into one `or` test. Cairn tested one bit per shift, paying a branch per shifted bit.
- **Decision**: Add a ShiftUnroll pipeline stage that rewrites `while (x & 1) == 0 { x = x >> 1; }` into `while (x & 3) == 0 { x = x >> 2; }` followed by the original test as an `if` tail, and the two-variable common loop the same way with `k = k + 2` and both shifts stepped by two. The tail can run at most once because after the wide loop exits at least one value has a non-zero low bit, and a value ending at exactly 2 still needs one single shift, which the tail supplies. The rewrite is only applied when every body statement is a shift of a tested value or a counter bump, it keeps the loop test rather than replacing it with a count, so a zero operand still spins exactly as before, and the stage runs after copy propagation and before induction strength reduction so the stage snapshots that precede it stay unchanged.
- **Consequences**: binary_gcd's wide loops test and shift two bits at a time. Paired pinned-core cycle A/B against the previous compiler measured a median of about 8 percent faster (min -8.1 percent, p10 -8.6 percent, control error 0.35 percent), and on the quiet harness run binary_gcd went from 149.1 to 123.0 ms, now faster than C (160.4), C++ (164.2) and Rust (141.8). Only 6 of 591 golden hashes changed (binary_gcd's final, codegen and the image dumps), three new optimizer tests cover the single loop, the pair loop and a refused shape, and twin parity still passes.

### ADR-208: refuse to save a benchmark record from a disturbed window
- **Date**: 2026-11-25
- **Status**: Accepted
- **Context**: Two --save runs in the same hour silently appended contaminated records: the first had sieve medians near 220 ms with bests near 10 ms and an unusable Rust twin, and a later one had fib30 at seven times its clean value, nqueens at four times, and a binary_gcd Rust baseline of 305 ms against 142 in the clean run, while the many_params quiet probe stayed under its 20 ms threshold the whole time. Those records then became the baseline later runs compare against, and one was nearly published in the README table.
- **Decision**: Before appending a record, compare every language's best per case with the newest record when that record was quiet. If any case's best exceeds 1.5 times its previous value, print the offending cases, skip the save, and exit nonzero. The C, C++ and Rust twins are compiled from unchanged sources, so a bound-crossing jump there can only be window disturbance; holding Cairn to the same bound keeps a run that slow from becoming the baseline, while --compare still prints and flags the case from the same run.
- **Consequences**: The save path can now refuse a run, with the reason and the case list on stdout. Both contaminated records were deleted from the history, the guard was exercised on fabricated records (identical, baseline jump, Cairn regression, loaded previous), and the README states the rule so a refused save is not mistaken for a failure.

### ADR-209: duplicate a counted loop body so two iterations share one test
- **Date**: 2026-11-25
- **Status**: Accepted
- **Context**: rotl trailed the Rust twin by about 11 percent. Disassembling the Rust binary showed LLVM running two loop bodies per compare-and-branch, with the running product updated at the end of each body. Cairn tested and branched once per iteration; the existing constant-trip unroller only covers loops of at most 16 iterations.
- **Decision**: Add a LoopDuplicate stage between induction strength reduction and bottom-test rotation. `while i < N { BODY }`, with BODY ending in `i = i + 1`, nothing else writing i, no jumps out, no inner loop and at most ten statements, becomes `while i < N-1 { BODY BODY }` followed by the original loop as a one-shot tail; an inclusive `i <= N` widens to `i < N`. A body is refused when it stores into an array element, because that breaks the shapes the RMW vectoriser and the array-fill helper recognise, or when it calls anything except the always-inline bit builtins (rotl, clz, ctz, bswap), because repeating a real call is expensive. Induction now appends its running-product updates before the counter step so the body still ends with `i = i + 1` and each duplicated copy carries its own product update.
- **Consequences**: rotl runs two bodies per branch and measured 6.2 percent fewer cycles than the previous compiler in a pinned-core A/B (best of 24 alternating runs per side, control error under one percent); the bit cases that also qualify moved inside the band. Nine benchmark images changed and 36 golden hashes were refreshed, four optimizer tests pin the wide-and-tail shape, the inclusive form, the refused counter mutation and the nested-loop case, and twin parity passes over 23 cases.

### ADR-210: fix element stores whose value is not a plain name or literal
- **Date**: 2026-11-25
- **Status**: Accepted
- **Context**: A new end-to-end probe showed that `a[i] = a[i] + 1` over four elements left three elements untouched and bumped the last one twice. The statement fell through the two fast paths (value is a Name, value is an IntLiteral) into try_fold_frame_update, which peeled `a[i] + 1`, saw the array's own slot on one side and stored the sum at the array base as though the array were a scalar. The twin gate had never seen this because array_sum's loop, the only benchmark with that shape, was always vectorised before the scalar path could run.
- **Decision**: Add a general element-store arm: settle the index first (a home register when the index is a named slot, an immediate when it is a literal, otherwise evaluate and push), evaluate the value into rax, then store through the element address, popping the index into rcx only after the value has been computed so a value that calls cannot disturb it.
- **Consequences**: `a[i] = <expression>` writes the addressed element for every index shape, which the new end-to-end test proves (13 14 15 16 from `a[i] = i + 10` then three read-modify-write passes). The fix also removed the risk of a silently wrong total in any program that stores a computed value into an array.

### ADR-211: never read past the bound in the vectorised element-update tail
- **Date**: 2026-11-25
- **Status**: Accepted
- **Context**: ADR-209 exposed a latent bug in the two-element pass of the element-update vectoriser: it guarded on `i < bound` while processing two elements at a time, which is only safe when the loop starts at an even offset from the bound. The duplicated tail loop starts at an odd index, so the pass touched one element past the array and the twin gate caught the changed total.
- **Decision**: Guard the pair pass with `i <= bound - 2` and add a single-element scalar step after it, which can run at most once because the pair pass leaves zero or one element behind.
- **Consequences**: The vectoriser is correct for any starting index and any bound, not only for zero-based loops with even bounds. array_sum returns to its C twin's total through the vector path, and the scalar tail costs one compare in the common case.

### ADR-212: lower power-of-two division in the destination register with cqo
- **Date**: 2026-11-25
- **Status**: Accepted
- **Context**: mandelbrot was the one benchmark still behind both C and C++ outside the noise band (13.3 vs 12.3 and 12.1 ms). Its inner loop computes y = x*y/1048576 + y0. Cairn lowered the constant division in rax with a five-instruction sign-fixup chain (mov rcx,rax; sar; shr; add; sar) and then copied the result into y's home, so the loop body ran 22 instructions against GCC's 19 even after the assignment target was a home register.
- **Decision**: Emit division (and remainder) by a positive power of two as cqo; shr rdx, 64-k; add; sar k, which is four instructions and needs no sign temp, and give try_emit_binary_into the same lowering so the whole cost lands in the destination home register: y = x*y/K + y0 becomes imul dest,left; cqo; shr; add; sar; add y0 with no copies. Also teach the in-place multiply that when the destination is the right operand's home, a two-operand imul reads both sources before writing, so the multiply needs no preparatory copy.
- **Consequences**: mandelbrot's inner loop drops from 22 to 19 instructions per iteration, matching GCC's count. Paired pinned-core A/B against the previous compiler measured a median 1.7 percent (p10 4.3 percent) cycle reduction in a window too noisy for tighter claims, and the output total is unchanged. Only mandelbrot's three golden hashes moved; the full suite, twin parity and the 5 project gates pass.
