# Cairn

[![Built with AIKit](https://img.shields.io/badge/built%20with-AIKit-6f42c1)](https://github.com/AegeanWorks/AIKit)

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
| **No Dependency** | The compiler imports only the Python standard library; a produced `.exe` needs 64-bit Windows and nothing else. | `python -m unittest tests.test_capabilities` |
| **AI Compatible** | One JSON object describes the whole surface, and every failure is one object with a stable code. | `python -m cairn capabilities --json` |
| **Easily Understandable** | One type, one binding form per intent, one flat scope, no implicit conversion. | `python -m cairn capabilities` |
| **Almost Machine Language Like** | Source maps to emitted x86-64 bytes with no interpreter, GC or hidden runtime, readable label by label. | `python -m cairn listing examples/hello.crn` |
| **Verifiable** | An artifact is proved well formed from its bytes alone, and two builds are byte-identical. | `python -m cairn verify examples/hello.crn` |
| **Easily Problem Understood via Error Codes** | Every diagnostic code is registered, documented and published; a corrupt image names the invariant it broke. | `python -m cairn check broken.crn --json` |

## ⚡ 30-Second Cold-Start Quick Reference

- **Runtime Prerequisites**: Python 3.10 or newer to run the compiler (developed on Python 3.14);
  no C toolchain, assembler, linker or third-party package is needed, standard library only.
  The produced `.exe` needs 64-bit Windows and nothing else.
- **Canonical Regression Shield Gate**:
  ```bash
  python -m unittest discover
  ```
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
    var i: i64 = 0;
    while i < 10 {
        total = total + fib(i);
        i = i + 1;
    }
    print(total);
    return 0;
}
```

- **One type**: `i64`. Every value is a signed 64-bit integer; comparisons yield `0` or `1`.
- **One binding form per intent**: `let` is immutable, `var` is assignable. No shadowing.
- **One function form**: `fn name(a: i64, b: i64) -> i64 { ... }`, up to four parameters,
  which are passed in registers. `main` is the entry point, and its return value is the exit code.
- **Statements**: `let`, `var`, assignment, `if`/`else`, `while`, `return`, `break`, `continue`.
- **Operators**, C precedence: `|| && | ^ & == != < <= > >= << >> + - * / %`, unary `- ! ~`.
- **Builtin**: `print(x)` writes the decimal form and a newline, and returns `x`.
- **Comments**: `//` to the end of the line.

Defined semantics a C programmer would have to look up:

| Question | Cairn's answer |
|---|---|
| Integer overflow | Wraps two's complement; no undefined behaviour |
| Division by zero | Trap: message on stderr, exit code 3 |
| Division rounding | Truncates toward zero; `%` keeps the dividend's sign |
| Right shift | Arithmetic (`>>` keeps the sign) |
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
| E1003 | Integer literal does not fit in `i64` |
| E2001 | Grammar expected a specific token |
| E2002 | Unknown type |
| E2003 | Assignment target is not a name |
| E2004 | Expression statement with no effect |
| E3001 | Duplicate function |
| E3003 | Unknown function |
| E3004 | Wrong argument count |
| E3005 | Assignment to an immutable binding |
| E3006 | Duplicate binding or parameter |
| E3007 | Undeclared name |
| E3008 | `break`/`continue` outside a loop |
| E3009 | Function can finish without returning |
| E3010 | More than four parameters |
| E3011 | Missing or ill-formed `main` |
| E3012 | Builtin redefined or used as a value |

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
python -m unittest discover           # includes the stdlib-only import proof
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
   │  codegen.py      x86-64, values in rax, arguments in rcx/rdx/r8/r9
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
how many rewrites fired in the `folded` field.

Measured effect on the constant-heavy benchmark, same source, five runs each:

| Build | Best wall clock | Instructions per iteration |
|---|---|---|
| `--no-optimize` | 71.9 ms | one move and one operation per source operator |
| default | 34.7-35.5 ms | one move and one operation per source *statement* |

On code with nothing to fold the pass is neutral, and the loop benchmark below shows that: the
loop body is already two names and two additions, so there is no constant to remove.

## Performance, measured and honestly bounded

Cairn emits direct machine code with no interpreter loop, no bounds checks and no GC. On the
integer subset it implements, the generated code is one instruction per operation plus the loads
and stores its flat slots need. `benchmarks/run_benchmarks.py` reproduces every number below and
adds a C baseline whenever `gcc`, `clang` or `cl` is already on `PATH`; on a machine with none it
reports `C baseline: unavailable` rather than inventing a comparison.

Measured on the development machine (Windows x86-64, Python 3.14 bootstrap, `--rounds 5`):

| Case | Work | Best | Per unit | Size |
|---|---|---|---|---|
| `loop_sum` | 200,000,000 loop iterations | 170 ms | 0.85 ns | 2,048 bytes |
| `fib30` | 2,692,537 recursive calls | 12.7 ms | 4.7 ns | 2,560 bytes |
| `folded_math` | 20,000,000 iterations of constant arithmetic | 35.5 ms | 1.8 ns | 2,560 bytes |

Run-to-run spread on this machine is about 10 percent, so treat these as an order of magnitude
rather than three significant figures; the harness prints best, median and worst for that reason.

Compile time is about 150-250 ms per case, nearly all of it Python interpreter startup; the
produced executable is two to three kilobytes because there is no runtime to link.

What is *not* claimed: that Cairn is faster than C in general. Beating an optimising C compiler
on arbitrary code is a claim about optimisers and workloads, not about syntax, and this compiler
has one folding pass and a register fast path, not a modern optimiser. What Cairn does claim
today is a zero-cost execution model on its subset, sub-second compiles, deterministic output,
and a total dependency surface of one Python interpreter.

### Goal: faster than C, measured per case

The owner's target is that Cairn beats the C twin on every committed benchmark case. That is a
goal with a scoreboard, not a slogan the README asserts: `run_benchmarks.py` measures both
languages in the same run and derives one verdict per case.

```bash
python benchmarks/run_benchmarks.py           # table plus a "Goal: faster than C" line
python benchmarks/run_benchmarks.py --json    # goal.faster_than_c: true, false or null
python benchmarks/run_benchmarks.py --strict  # gate: 0 won, 1 slower, 2 no C compiler
```

A verdict is `faster`, `tie`, `slower` or `unavailable`. `unavailable` means no `gcc`, `clang` or
`cl` was found, and `--strict` then exits `2` rather than reporting a win it did not measure. The
development machine has no C compiler, so the goal is currently unmeasured there, and the three
cases cover integer loops, recursion and constant arithmetic only.

## What's Inside

```text
cairn/
├── .gitignore                <- transient AIKit state excluded
├── .aikit/                   <- project memory: map, ADRs, ledger, media
├── LICENSE                   <- Aegean License 1.1 (AL-1.1): free personal use
├── COMMERCIAL.md             <- commercial licensing terms
├── LICENSES/                 <- REUSE license texts
├── REUSE.toml                <- machine-readable SPDX metadata
├── README.md
├── pyproject.toml            <- package metadata and the `cairn` console script
├── cairn/                    <- the bootstrap compiler
│   ├── cli.py                <- build, check, run; exit codes and JSON diagnostics
│   ├── lexer.py              <- scanner and token model
│   ├── parser.py             <- recursive-descent parser
│   ├── syntax.py             <- the syntax tree
│   ├── sema.py               <- names, slots, arity, mutability, return paths
│   ├── optimize.py           <- folding, neutral operands, dead branches
│   ├── codegen.py            <- syntax tree to x86-64
│   ├── machine.py            <- x86-64 encodings and label fixups
│   ├── runtime.py            <- print and divide-by-zero helpers as machine code
│   ├── pe.py                 <- PE32+ writer and fixup resolution
│   ├── builder.py            <- the stages and the atomic file write
│   ├── capabilities.py       <- the accepted surface as one JSON object
│   ├── codes.py              <- the diagnostic code registry
│   ├── diagnostics.py        <- Diagnostic, Span, the JSON contract
│   ├── image.py              <- artifact and read-only data model
│   ├── listing.py            <- .text bytes grouped by label
│   ├── verify.py             <- structural checks over a built image
│   ├── values.py             <- the six core values with their proving commands
│   ├── version.py            <- the one version string
│   └── __main__.py           <- `python -m cairn`
├── benchmarks/               <- three cases, their C twins, and the harness
├── examples/                 <- hello, fibonacci, arithmetic
├── tests/                    <- lexer, parser, sema, optimiser, emitter, PE, CLI, harness, capabilities, verifier, values, end-to-end
└── assets/                   <- generated brand candidates
```

## 📜 License

Free for personal use under the Aegean License 1.1 (AL-1.1); see [LICENSE](LICENSE). Any commercial use needs the Aegean Commercial License 1.0 (ACL-1.0); see [COMMERCIAL.md](COMMERCIAL.md).
