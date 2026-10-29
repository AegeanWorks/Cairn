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
