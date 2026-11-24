# Cairn

<!-- aegean:media:start -->
<p align="center"><img src="assets/social-preview.svg" alt="Cairn social preview" width="100%"></p>
<p align="center"><a href="LICENSE"><img src="https://img.shields.io/badge/license-AL--1.1-2f9e44" alt="License"></a> <a href="https://github.com/AegeanWorks/AIKit"><img src="https://img.shields.io/badge/AIKit-integrated-6f42c1" alt="AIKit Integrated"></a></p>
<!-- aegean:media:end -->


An agent-first systems language with its own x86-64 backend. Cairn compiles straight to a
Windows PE executable: no C compiler, no assembler, no linker, no libc, no runtime library.

The design target is not "a language that looks friendly in a demo". It is a language an agent
can read, repair and predict without holding a language reference in context: one numeric type,
one statement form per idea, a flat scope, no implicit conversions, no undefined behaviour in
the integer semantics it does define, and diagnostics that carry stable codes and a fix.

## Core values, each with the command that proves it

Six values decide every trade-off in this repository. Each one is a record in `cairn/values.py`
with the command that demonstrates it and the test that fails when it stops being true, published
by `cairn capabilities --json` and asserted by `tests/test_values.py`.

| Value | What it means here | Prove it with |
|---|---|---|
| **No Dependency** | The Rust compiler uses only the standard library; a produced `.exe` needs 64-bit Windows and nothing else. | `cargo test --quiet --manifest-path rust/Cargo.toml` |
| **AI Compatible** | One JSON object describes the whole surface, and every failure is one object with a stable code. | `python -m cairn capabilities --json` |
| **Easily Understandable** | One type, one binding form per intent, one flat scope, no implicit conversion. | `python -m cairn capabilities` |
| **Almost Machine Language Like** | Source maps to emitted x86-64 bytes with no interpreter, GC or hidden runtime, readable label by label. | `python -m cairn listing examples/hello.crn` |
| **Verifiable** | An artifact is proved well formed from its bytes alone, and two builds are byte-identical. | `python -m cairn verify examples/hello.crn` |
| **Easily Problem Understood via Error Codes** | Every diagnostic code is registered, documented and published; a corrupt image names the invariant it broke. | `python -m cairn check broken.crn --json` |

## Specification

The language law is `SPEC.md` (§-numbered, with the §16 validity checklist an LLM can walk),
the compressed agent card is `LLM_SYSTEM.md`, and `examples/alloy_probe/` records the Alloy
backend probe (fit and misfit with equal weight). If this README and the machine sources
disagree, `python -m cairn capabilities --json` wins.

## ⚡ 30-Second Cold-Start Quick Reference

- **Runtime Prerequisites**: Python 3.10 or newer to run the compiler (developed on Python 3.14);
  no C toolchain, assembler, linker or third-party package is needed, standard library only.
  The produced `.exe` needs 64-bit Windows and nothing else.
- **Canonical Regression Shield Gate**:
  ```bash
  cargo test --quiet --manifest-path rust/Cargo.toml
  cargo test --quiet --manifest-path rust/Cargo.toml
  cargo test --quiet --manifest-path rust/Cargo.toml --test golden_parity
  ```
  (run from `projects/cairn`)
- **Compile and run something now** (under 10 seconds):
  ```bash
  python -m cairn run examples/hello.crn
  python -m cairn run examples/fib.crn
  python -m cairn build examples/hello.crn -o hello.exe
  python -m cairn verify examples/hello.crn
  python -m cairn listing examples/hello.crn
  python -m cairn capabilities --json
  python benchmarks/run_benchmarks.py
  ```

### 🛠️ AIKit Commands (run from the AIKit folder)
| Role | Command |
|---|---|
| Status, task and gates | `python aikit.py status cairn` |
| Phase 4: every gate | `python aikit.py check cairn` |
| Repair memory and map, then check | `python aikit.py fix cairn` |
| Resume an interrupted task | `python aikit.py resume cairn` |

## The language in one screen

```cairn
fn fib(n: i64) -> i64 {
    if n < 2 { return n; }
    return fib(n - 1) + fib(n - 2);
}

fn main() -> i64 {
    var total: i64 = 0;
    for i in 0..10 {
        total = total + fib(i);
    }
    print(total);
    return 0;
}
```

- **One type**: `i64`. Every value is a signed 64-bit integer; comparisons yield `0` or `1`.
- **One binding form per intent**: `let` is immutable, `var` is assignable. No shadowing.
- **One function form**: `fn name(a: i64, b: i64) -> i64 { ... }`, up to eight parameters:
  four in registers, the rest through the caller's outgoing stack area. `main` is the entry point,
  and its return value is the exit code.
- **Statements**: `let`, `var`, assignment, `if`/`else`, `while`, `for i in a..b` (or `a..=b`;
  the loop variable is scoped to the loop and `continue` still steps it), `return`, `break`,
  `continue`.
- **Operators**, C precedence: `|| && | ^ & == != < <= > >= << >> + - * / %`, unary `- ! ~`.
- **Builtins**: `print(x)` writes the decimal form and a newline, and returns `x`;
  `print_char(c)` writes the low byte of `c` (no newline), `print_hex(x)` writes `x` in
  lowercase hex with no leading zeros (no newline); both return their argument.
  `popcount(x)` returns the number of set bits in `x`'s unsigned 64-bit reading.
  `clz(x)` returns the number of leading zeros in that reading (64 for zero).
  `ctz(x)` returns the number of trailing zeros in that reading (64 for zero).
  `bswap(x)` returns `x` with its eight bytes reversed.
  `rotl(x, n)` returns `x` rotated left by `n` (the count masks to 63).
  Input: `argc()` is the token count with the program path included (the C convention);
  `arg_byte(i, j)` is byte `j` (zero-indexed) of argument `i`'s content with quotes
  stripped, or -1 past the token; `read_char()` is the next stdin byte, or -1 at end
  of stream. All three return their answer as an `i64`.
  Strings: `"text"` is a NUL-terminated literal in `.rodata`; `print_str(s)` writes it
  raw (no newline) and returns `s`; `str_len(s)` is its byte count; `str_byte(s, j)` is
  byte `j`, or -1 for a negative or at-or-past-terminator index.
  Control: `exit(code)` ends the process with `code` at once (statement only, never a value).
- **Comments**: `//` to the end of the line.

Defined semantics a C programmer would have to look up:

| Question | Cairn's answer |
|---|---|
| Integer overflow | Wraps two's complement; no undefined behaviour |
| Division by zero | Trap: message on stderr, exit code 3 |
| Division rounding | Truncates toward zero; `%` keeps the dividend's sign |
| Right shift | Arithmetic (`>>` keeps the sign); shift counts mask to 63 |
| Comparison result | `0` or `1`, usable as a condition or an integer |
| Evaluation order | Left to right; `&&` and `||` short-circuit |
| Uninitialised values | Impossible: every `let`/`var` needs an initialiser, and every frame slot is zeroed on entry |
| A binding declared in a branch that never ran | Reads as `0`, deterministically, because of the zeroed frame |
| Reading an undeclared name | Rejected before code generation |

## Diagnostics for agents

Every failure is one object with a stable code, a span and a fix. `--json` is the contract:

```bash
python -m cairn check broken.crn --json
```

```json
{
  "ok": false,
  "path": "broken.crn",
  "error": "compile-error",
  "diagnostics": [
    {
      "code": "E3007",
      "severity": "error",
      "path": "broken.crn",
      "line": 1,
      "column": 26,
      "end_line": 1,
      "end_column": 33,
      "message": "'missing' is not declared",
      "fix": "Declare it with 'let name: i64 = value;' before this line."
    }
  ]
}
```

| Code | Meaning |
|---|---|
| E1001 | Unexpected character |
| E1004 | Unterminated string literal or unknown escape sequence |
| E1003 | Integer literal does not fit in `i64` |
| E2001 | Grammar expected a specific token |
| E2002 | Unknown type |
| E2003 | Assignment target is not a name |
| E2004 | Expression statement with no effect |
| E2005 | For range missing `..` or `..=` |
| E3001 | Duplicate function |
| E3003 | Unknown function |
| E3004 | Wrong argument count |
| E3005 | Assignment to an immutable binding |
| E3006 | Duplicate binding or parameter |
| E3007 | Undeclared name |
| E3008 | `break`/`continue` outside a loop |
| E3009 | Function can finish without returning |
| E3010 | More than eight parameters |
| E3011 | Missing or ill-formed `main` |
| E3012 | Builtin redefined or used as a value |
| E3013 | `exit(...)` used inside an expression instead of as a statement |
| E3014 | Array declaration, size range, or initializer literal length mismatch |
| E3015 | Indexed a scalar instead of an array |
| E3016 | A whole array used as a value instead of an indexed element |
| E3017 | Static index literal outside `0 .. N-1`; dynamic bounds are undefined |

`E1002` and `E3002` are reserved for future stages and must not be reused.

A built executable is checked by the same contract. `verify` reads the bytes and never runs them:

| Code | Meaning |
|---|---|
| V0001 | DOS or PE signature is missing |
| V0002 | COFF header does not describe an amd64 image |
| V0003 | Optional header is not PE32+ console |
| V0004 | Section table is not `.text`, `.rdata`, `.data` |
| V0005 | Section alignment or raw pointer is wrong |
| V0006 | Entry point is outside `.text` |
| V0007 | Import directory is missing or outside `.rdata` |
| V0008 | Image size does not match the section layout |
| V0009 | A section has the wrong permissions |
| V0010 | The file is shorter than its section table claims |

Exit codes: `0` success, `1` the source did not compile or the program could not start, `2`
bad command line or unreadable input. `cairn run` forwards the program's own exit code.

## Machine-checkable surface

The core values are commands and tests, not prose an agent has to trust:

```bash
python -m cairn capabilities --json   # every accepted form, limit, exit code and diagnostic
python -m cairn verify build.exe      # structural proof of a built PE, nothing executed
python -m cairn verify source.crn     # the same checks on a source compiled in memory
python -m cairn listing source.crn    # exact .text bytes grouped by label
cargo test --quiet --manifest-path rust/Cargo.toml   # std-only Rust test suite
```

- `capabilities.py` builds its object from the same constants the parser, sema, optimiser and
  image use, so it cannot drift from the compiler without a test failing.
- `codes.py` is the diagnostic registry. A test asserts three directions: every code literal in
  the package is registered, every registered code is documented in this README, and every
  registered code is published by `capabilities`.
- `verify.py` is pure over bytes and reports `V####` codes; it writes nothing and executes
  nothing. Ten independent corruptions in `tests/test_verify.py` prove every code is reachable.
- `listing.py` prints the bytes the PE writer will place in `.text`, grouped by the label that
  owns them, so a build can be inspected without a debugger.
- `tests/test_capabilities.py` parses the compiler's imports with `ast` and fails if any module
  outside the Python standard library or `cairn` itself is referenced.

## How the compiler works

```text
source.crn
   │  lexer.py        tokens, one integer type, ASCII only
   │  parser.py       recursive descent, C operator precedence
   │  sema.py         names, slots, mutability, arity, return paths
   │  optimize.py     constant folding, neutral operands, dead branches
   │  codegen.py      x86-64, first six slots in callee-saved registers
   │  runtime.py      print and divide-by-zero trap as machine code
   │  pe.py           section layout, fixups, PE32+ headers, imports
   ▼
program.exe
```

The backend is a byte emitter, not a wrapper: `machine.py` writes the encodings, `pe.py` writes
the image. Imports are limited to `GetStdHandle`, `WriteFile` and `ExitProcess`, resolved through
an import table the writer builds. Builds are deterministic: the timestamp fields are zero, so
compiling the same source twice produces identical bytes (tested).

## The optimiser

`optimize.py` runs after semantic analysis and before code generation. It is conservative by
design: a rewrite happens only when the result is byte-for-byte what the machine would compute.

| Rewrite | Condition |
|---|---|
| Fold `left op right` | Both sides are literals, and folding preserves i64 wrapping |
| Fold unary `- ~ !` | The operand is a literal |
| Fold division or remainder | Never for a zero divisor, and never for `i64.min / -1` (the trap stays) |
| `x + 0`, `x - 0`, `x \| 0`, `x ^ 0`, `x << 0`, `x >> 0`, `0 + x`, `0 \| x`, `0 ^ x` | Always: the operand is evaluated once either way |
| `x * 1`, `1 * x` | Always |
| `x * 0`, `x & 0` | Only when neither side calls a function, so no effect is dropped |
| `if <constant> { A } else { B }` | Replaced by the branch it always takes |
| `while 0 { ... }` | Removed: the body never runs |
| Statements after `return`/`break`/`continue` | Removed: they are unreachable |

`--no-optimize` skips the pass on `build`, `check` and `run`, which is how the differential test
in `tests/test_end_to_end.py` proves both code paths behave identically. `check --json` reports
how many rewrites fired in the `folded` field. The benchmark harness accepts the same flag, so the
effect is reproducible with `python benchmarks/run_benchmarks.py --no-optimize`.

Measured effect on the constant-heavy benchmark, same source, nine runs each:

| Build | Best wall clock | Instructions per iteration |
|---|---|---|
| `--no-optimize` | 65.1 ms | one move and one operation per source operator |
| default | 18.0 ms | one in-place update per source statement |

On code with nothing to fold the pass is neutral, and the `xorshift` case shows that: its loop
body is a serial chain of shifts and xors over a live value, so there is no constant to remove.

## The backend: slot homes, in-place updates, direct branches

`codegen.py` gives a function's first six slots — parameters first, then locals in declaration
order — a home in a callee-saved register (`rbx`, `r12`-`r15`, `rdi`) instead of a frame slot.
Every function saves the homes it uses on entry and restores them in a single epilogue that every
`return` jumps to, so a call cannot clobber the caller's locals. A function with more than six
slots keeps the stack path for the rest.

Three rewrites then remove the copies those homes would otherwise need:

| Pattern | Emitted |
|---|---|
| `x = x + e` (also `-`, `*`, `&`, `\|`, `^`) | evaluate `e`, then one arithmetic instruction into `x`'s register |
| `x = x + <literal>`, `x = x - <literal>` (32-bit immediate) | one group-1 immediate instruction |
| a comparison used as an `if`/`while` condition | `cmp` plus a conditional jump; no `setcc`, `movzx` and second `test` |
| a comparison against a 32-bit literal | `cmp r64, imm32` instead of materialising the literal in `rcx` |

| a dead-counter `while v < C` loop (ADR-023) | hoisted zero-trip guard, then one `sub` and one conditional jump at the loop's bottom — gcc's countdown latch shape |
| a frame slot's `x = x op e` with `op` in `+ - & \| ^` and `e` a small literal or register (ADR-042) | one memory-operand instruction into the slot; no load/store pair |

These are backend rewrites, not language changes. `--no-optimize` still skips only the folding
pass; the differential end-to-end test runs both build modes against the same expected output.

## Performance, measured and honestly bounded

Cairn emits direct machine code with no interpreter loop, no bounds checks and no GC. On the
integer subset it implements, the generated code is one instruction per operation; the first six
slots stay in registers, and only the rest are loaded and stored. `benchmarks/run_benchmarks.py`
reproduces every number below and adds baseline columns whenever a compiler for them is already
on `PATH`: a C baseline for `gcc`, `clang` or `cl`, a C++ baseline for `g++`, `clang++` or `cl`
(compiling the same .c benchmark sources as C++), and a Rust baseline when `rustc` is; when
none is found the relevant line reports `baseline: unavailable` rather than inventing a
comparison.

Measured on the development machine (Windows x86-64, Python 3.14 bootstrap), against MinGW GCC
16.2.0 (WinLibs UCRT), MinGW G++ 16.2.0 (same WinLibs toolchain, `-O2`) and rustc 1.100.0
(pc-windows-gnu). Because independent runs drift by more than the
gaps being measured, each row shows the best times across that session's saved
`--save --compare` gate runs, which keeps every comparison same-revision while still reporting
a spread:

| Case | Work | Cairn best | C best | C++ best | Rust best | Cairn size |
|---|---|---|---|---|---|---|
| `xorshift` | 200,000,000 serial xorshift steps | 283-306 ms | 283-306 ms | 287-300 ms | 292-314 ms | 71,168 bytes |
| `fib30` | 2,692,537 recursive calls | 9.6-12.7 ms | 8.7-11.8 ms | 8.7-10.7 ms | 10.6-13.1 ms | 81,408 bytes |
| `folded_math` | 20,000,000 bit-mixed constant additions | 18.4-20.4 ms | 18.0-22.0 ms | 18.3-22.1 ms | 19.2-23.1 ms | 71,168 bytes |
| `collatz` | 1,000,000 probes of the `3k+1` map, 131,434,272 steps | 104-106 ms | 158-164 ms | 160-164 ms | 140-150 ms | 71,168 bytes |
| `ackermann` | `A(3,9)`, about 25 million calls | 10.2-13.9 ms | 15.2-19.4 ms | 15.4-18.0 ms | 18.4-64.5 ms | 71,680 bytes |
| `nqueens` | N=12 bitmask backtracking, 14,200 solutions | 17.7-19.0 ms (ADR-088 self-call unrolling) | 22.1-25.1 ms | 22.8-24.9 ms | 18.4-20.4 ms | 71,168 bytes |
| `binary_gcd` | 1,000,000 Stein-GCD pairs | 147-162 ms | 158-180 ms | 173-181 ms | 139-154 ms | 71,680 bytes |
| `call_chain` | 20,000,000 three-argument hot-path calls | 45-48 ms | 33-37 ms | 34-37 ms | 74-79 ms | 71,168 bytes |
| `tak` | Takeuchi `tak(18,12,6)`, 63,609 three-argument calls | 7.8-9.7 ms | 7.7-10.7 ms | 7.7-10.9 ms | 8.6-10.8 ms | 71,168 bytes |
| `primes` | trial division over 2..300000 | 17.9-19.1 ms (ADR-085 32-bit divide) | 24.5-27.3 ms | 24.1-26.3 ms | 18.9-23.2 ms | 71,168 bytes |
| `mandelbrot` | four 960x540 frames, 2^21 fixed point | 13.1-16.1 ms | 11.1-13.3 ms | 10.9-13.4 ms | 12.0-14.4 ms | 71,168 bytes |
| `splitmix` | 100,000,000 splitmix64 mixes, 64-bit wrap | 94.3-96.7 ms | 92.5-101.5 ms | 96.4-102.9 ms | 105.9-113.5 ms | 71,168 bytes |
| `hex_io` | 400,000 hex-format-then-print values | 612-645 ms | 4,764-5,095 ms | 397-467 ms | 677-723 ms | 71,168 bytes |
| `many_params` | 10,000,000 six-argument mix calls under an `argc`-derived bound (ADR-074, ADR-075, ADR-076) | 13.2-13.7 ms | 12.5-12.7 ms | 14.3-14.8 ms | 13.4-13.8 ms | 136,704 bytes |
| `argv_walk` | 12.8M data-dependent `arg_byte` reads steering the walk | 20.0-23.7 ms | 18.5-24.2 ms | 19.1-22.5 ms | 25.5-28.4 ms | 71,168 bytes |
| `char_stream` | 4 MiB piped through `read_char()`, summed (ADR-103 stream) | 9.1 ms | 74.6 ms | 72.5 ms | 13.1 ms | 136,704 bytes |
| `str_scan` | 200 passes x 66-byte NUL-terminated scan, 12.8M reads | 13.9-18.4 ms | 11.8-16.6 ms | 12.2-19.6 ms | 28.2-37.0 ms | 71,168 bytes |
| `array_sum` | 65536 elements: fill, 100 read-modify-write passes, one summation pass | 10.8-11.4 ms (ADR-073 SIB addressing) | 10.2-11.9 ms | 10.0-11.9 ms | 11.6-13.0 ms | 136,704 bytes |
| `sieve` | Eratosthenes sieve over 0..65536, 6,542 primes, nested `a[j]` stores | 9.3-9.4 ms (ADR-073 SIB addressing) | 9.3-10.0 ms | 10.1-10.9 ms | 11.2-11.8 ms | 136,704 bytes |
| `popcount` | 10,000,000 derived values, set-bit count summed | 12.2 ms (ADR-102 POPCNT) | 149.2 ms | 21.5 ms | 16.3 ms | 136,704 bytes |

The size column is the executable's `.text` frame at the current revision: the runtime helpers (argv arena, 64 KiB stdin state, printers, divide trap) ride in every program, so most cases share the same 71,168-byte base. `array_sum` and sieve reserve 8 MiB of stack in the PE header (ADR-068), and `many_params` links the argument arena for its `argc`-derived bound (ADR-074), so their base carries the bigger frame.

`popcount`, `clz`, `ctz` and `bswap` are the single-instruction bit cases (ADR-101 to ADR-111): each lowers to one x86-64 instruction, and the authoritative 23-case verdict measures each faster than C and tied with C++ and Rust. `rotl` (one ROL, ADR-114) is the newest; ADR-115 and ADR-116 shrank its hot loop from 18 to 13 instructions (no push/pop around the count, no redundant `& 63`, the accumulator mask applied in place), and its measured verdicts read as ties against all three baselines.

Standings per case: `collatz`, `ackermann`, `tak`, `folded_math` and `splitmix` win outright;
`hex_io` wins after ADR-091/092; `xorshift` is a structural tie at the ISA latency floor (ADR-023).
The remaining cases are ties or small bounded gaps documented in the ADRs cited above; each is
argued from disassembly and paired A/B measurements, never from a single run. `mandelbrot` is a
known bounded loss to C (ADR-106) that needs a different recurrence, not another lowering tweak.

The twin-parity gate (`tests/test_end_to_end.py::TwinParityTest`) compiles all four twins for every
case and fails unless they print the same total, so a standing cannot be earned against a twin that
computes something else.

Run-to-run spread is about 10 percent: use `python benchmarks/run_benchmarks.py --save --compare`
to append one JSONL record (including the ADR-069 quiet-window probe) and diff against the newest
saved record; a Cairn regression beyond the band exits nonzero. Every case is scored on
whole-process time; `--floors` prints a compute-adjusted diagnostic.

Three user-recorded project rules: after every change to `cairn/` code, run the full benchmark
suite and compare; every new language feature ships its own tests in the same change; every new
feature ships a C/C++/Rust benchmark twin in the same change.

What is *not* claimed: that Cairn beats optimising compilers in general. It claims a zero-cost
execution model on its subset, sub-second compiles, deterministic output, and one Python
interpreter as its only dependency.

### Goal: faster than C, C++ and Rust, measured per case

The owner's target is to beat the C, Rust and C++ twin on every committed case. Verdicts are
measured by `run_benchmarks.py` in one run per baseline:

```bash
python benchmarks/run_benchmarks.py           # table plus a "Goal: faster than ..." line
python benchmarks/run_benchmarks.py --json    # goal.faster_than_c / _cpp / _rust
python benchmarks/run_benchmarks.py --strict  # 0 won, 1 slower/errored, 2 no baseline
```

A verdict is `faster`, `tie`, `slower`, `unavailable` or `error`; `--strict` exits `2` only when no
baseline can be measured. `benchmarks/paired.py` is the authoritative verdict: it measures all
languages in one interleaved window with an identical-binary control band, so a gap counts only
outside that band.

## What's Inside

```text
cairn/
├── .aikit/
│   ├── cache/
│   │   ├── hygiene.json
│   │   └── knowledge_ast.json
│   ├── benchmark_history.jsonl
│   ├── codebase_map.md
│   ├── decisions.md
│   ├── guidelines.md
│   ├── media.json
│   ├── project.json
│   ├── reforge.json
│   ├── reforge.json.bak
│   ├── task_ledger.json
│   ├── task_ledger.json.bak
│   ├── task_ledger_archive.jsonl
│   └── task_ledger_archive.jsonl.bak
├── LICENSES/
│   └── LicenseRef-AL-1.1.txt
├── assets/
│   ├── logo.svg
│   └── social-preview.svg
├── benchmarks/
│   ├── __init__.py
│   ├── ackermann.c
│   ├── ackermann.cpp
│   ├── ackermann.crn
│   ├── ackermann.rs
│   ├── argv_walk.c
│   ├── argv_walk.cpp
│   ├── argv_walk.crn
│   ├── argv_walk.rs
│   ├── array_sum.c
│   ├── array_sum.cpp
│   ├── array_sum.crn
│   ├── array_sum.rs
│   ├── binary_gcd.c
│   ├── binary_gcd.cpp
│   ├── binary_gcd.crn
│   ├── binary_gcd.rs
│   ├── bswap.c
│   ├── bswap.cpp
│   ├── bswap.crn
│   ├── bswap.rs
│   ├── call_chain.c
│   ├── call_chain.cpp
│   ├── call_chain.crn
│   ├── call_chain.rs
│   ├── char_stream.c
│   ├── char_stream.cpp
│   ├── char_stream.crn
│   ├── char_stream.rs
│   ├── clz.c
│   ├── clz.cpp
│   ├── clz.crn
│   ├── clz.rs
│   ├── collatz.c
│   ├── collatz.cpp
│   ├── collatz.crn
│   ├── collatz.rs
│   ├── ctz.c
│   ├── ctz.cpp
│   ├── ctz.crn
│   ├── ctz.rs
│   ├── fib30.c
│   ├── fib30.cpp
│   ├── fib30.crn
│   ├── fib30.rs
│   ├── folded_math.c
│   ├── folded_math.cpp
│   ├── folded_math.crn
│   ├── folded_math.rs
│   ├── hex_io.c
│   ├── hex_io.cpp
│   ├── hex_io.crn
│   ├── hex_io.rs
│   ├── mandelbrot.c
│   ├── mandelbrot.cpp
│   ├── mandelbrot.crn
│   ├── mandelbrot.rs
│   ├── many_params.c
│   ├── many_params.cpp
│   ├── many_params.crn
│   ├── many_params.rs
│   ├── nqueens.c
│   ├── nqueens.cpp
│   ├── nqueens.crn
│   ├── nqueens.rs
│   ├── paired.py
│   ├── popcount.c
│   ├── popcount.cpp
│   ├── popcount.crn
│   ├── popcount.rs
│   ├── primes.c
│   ├── primes.cpp
│   ├── primes.crn
│   ├── primes.rs
│   ├── rotl.c
│   ├── rotl.cpp
│   ├── rotl.crn
│   ├── rotl.rs
│   ├── run_benchmarks.py
│   ├── sieve.c
│   ├── sieve.cpp
│   ├── sieve.crn
│   ├── sieve.rs
│   ├── splitmix.c
│   ├── splitmix.cpp
│   ├── splitmix.crn
│   ├── splitmix.rs
│   ├── str_scan.c
│   ├── str_scan.cpp
│   ├── str_scan.crn
│   ├── str_scan.rs
│   ├── tak.c
│   ├── tak.cpp
│   ├── tak.crn
│   ├── tak.rs
│   ├── xorshift.c
│   ├── xorshift.cpp
│   ├── xorshift.crn
│   └── xorshift.rs
├── examples/
│   ├── alloy_probe/
│   │   ├── src/
│   │   │   └── main.al
│   │   ├── README.md
│   │   ├── alloy.toml
│   │   └── lowered.crn
│   ├── arithmetic.crn
│   ├── fib.crn
│   └── hello.crn
├── parity/
│   ├── specs/
│   │   ├── codegen/
│   │   │   ├── array.crn
│   │   │   ├── arrayfill.crn
│   │   │   ├── arraywrite.crn
│   │   │   ├── bigliteral.crn
│   │   │   ├── binary.crn
│   │   │   ├── callvar.crn
│   │   │   ├── delta.crn
│   │   │   ├── frame.crn
│   │   │   ├── if.crn
│   │   │   ├── mutual.crn
│   │   │   ├── simple_print.crn
│   │   │   ├── simple_return.crn
│   │   │   ├── simple_str.crn
│   │   │   ├── slot.crn
│   │   │   ├── usercall.crn
│   │   │   ├── vecrmw.crn
│   │   │   └── while.crn
│   │   ├── fold_calls.txt
│   │   ├── magic.txt
│   │   └── pe_basic.txt
│   └── golden.sha256
├── rust/
│   ├── src/
│   │   ├── builder.rs
│   │   ├── capabilities.rs
│   │   ├── cli.rs
│   │   ├── codegen.rs
│   │   ├── codes.rs
│   │   ├── diagnostics.rs
│   │   ├── dump.rs
│   │   ├── generator.rs
│   │   ├── image.rs
│   │   ├── lexer.rs
│   │   ├── lib.rs
│   │   ├── listing.rs
│   │   ├── machine.rs
│   │   ├── main.rs
│   │   ├── optimize.rs
│   │   ├── parser.rs
│   │   ├── pe.rs
│   │   ├── pipeline.rs
│   │   ├── runtime.rs
│   │   ├── sema.rs
│   │   ├── spec.rs
│   │   ├── syntax.rs
│   │   ├── values.rs
│   │   ├── verify.rs
│   │   └── version.rs
│   ├── tests/
│   │   ├── backend.rs
│   │   ├── front_end.rs
│   │   ├── golden_parity.rs
│   │   ├── hoist_direct.rs
│   │   ├── leaf_pad.rs
│   │   ├── machine.rs
│   │   ├── optimize.rs
│   │   ├── runtime.rs
│   │   ├── sema.rs
│   │   └── vec_wide.rs
│   ├── Cargo.lock
│   └── Cargo.toml
├── .gitignore
├── COMMERCIAL.md
├── LICENSE
├── LLM_SYSTEM.md
├── README.md
├── REUSE.toml
├── SPEC.md
├── char_stream.old.exe
├── hex_io.old.exe
├── pyproject.toml
├── rust-toolchain.toml
└── xorshift.old.exe
```

<!-- aegean:brand-assets:start -->
## 🎨 Brand Assets

> **AI-Generated Brand Artwork.** The banner and logo are **AI-generated brand assets**. They are illustrative and may not literally depict every feature or the full project scope.
<!-- aegean:brand-assets:end -->

## 📜 License

Free for personal use under the Aegean License 1.1 (AL-1.1); see [LICENSE](LICENSE). Any commercial use needs the Aegean Commercial License 1.0 (ACL-1.0); see [COMMERCIAL.md](COMMERCIAL.md).
