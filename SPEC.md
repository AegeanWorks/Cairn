# Cairn Language Specification

Version: cairn/0.1 (compiler 0.1.0) — 2026-11-03
Status: language law for agents and the compiler. Read with `LLM_SYSTEM.md`.

Attribution: the document shape (numbered law, compressed system card,
validity checklist, canonical package) follows the practice of Alloy
(`https://github.com/pryzmatpl/Alloy` SPEC §§12, 14, 16). No Alloy
sentence is copied here. Alloy section numbers are cited only where a
deliberate divergence is recorded. Alloy has no license file at the
checked revision, so this file is original text under Cairn's own license.

Precedence: if this file and the machine sources disagree, the sources win
in this order: emitted bytes, `python -m cairn capabilities --json`,
`cairn/codes.py` plus `README.md`, then this file. Stage 4 of the adopting
task re-verifies every claim below against those sources.

## 1. Design axioms

1. One type: `i64`. Every value is a signed 64-bit integer; comparisons yield `0` or `1`.
   Arrays are homogeneous `i64` collections declared `[N]i64` (§5.2, §6.4).
2. One binding form per intent: `let` is immutable, `var` is assignable. No shadowing.
3. One scope: flat and function-wide. A name declared in any block is visible after it.
4. One function form: `fn name(a: i64, ...) -> i64`. At most eight parameters.
5. One effect family: the §10 builtins. Decimal, byte and hex printers write to stdout;
   `read_char` reads stdin, `argc`/`arg_byte` read the command line, `str_len`/`str_byte`
   read string literals, `print_str` writes a literal, `exit` stops the process. Each
   returns its argument (or the value it answered) unless it is `exit`, which returns
   to no one.
6. No hidden behaviour: overflow wraps, division by zero traps, every frame slot reads as `0` on entry.
7. No toolchain: the compiler is Python standard library only; the artifact is a Windows PE32+ console executable needing nothing else.

## 2. Lexical grammar

```
comment      ::= "//" ~[\n]*
ident        ::= [A-Za-z_][A-Za-z0-9_]*        (ASCII only)
int          ::= [0-9]+                          (decimal only, must fit in i64)
keyword      ::= fn | let | var | if | else | while | for | in | return | break | continue | true | false
symbol       ::= "..=" | ".." | "->" | "==" | "!=" | "<=" | ">=" | "&&" | "||" | "<<" | ">>"
               | "(" | ")" | "{" | "}" | "," | ":" | ";" | "=" | "+" | "-" | "*" | "/" | "%"
               | "<" | ">" | "!" | "&" | "|" | "^" | "~"
```

Whitespace separates tokens. Anything else is `E1001`. An integer above
`9223372036854775808` is `E1003`. `9223372036854775808` itself lexes so that
unary minus can spell `i64.min` (bare `-9223372036854775808` or parenthesised
`-(9223372036854775808)`), and is `E1003` anywhere else. `true` is `1`,
`false` is `0`.

## 3. Compilation units

A program is one `.crn` text file holding one or more `fn` items. There is no
package manifest, no module path, no `use`, no import, no conditional
compilation. Two files are never joined: what the file declares is the program.

## 4. Types

The scalar type is `i64`; arrays are `[N]i64`. Any other scalar type name is `E2002`.
There are no strings-as-values, floats, structs, classes, interfaces, pointers,
generics, function types, enums, or `Result`. Values that other languages put
in those types are packed into `i64` by the author until a later specification
adds them.

## 5. Declarations

### 5.1 Functions

```
fn name(a: i64, b: i64) -> i64 { block }
```

- The keyword is always `fn`.
- Every function declares `-> i64` and every path through its body ends in `return expr;`, else `E3009`.
- At most eight parameters (`E3010`); the first four travel in registers and the rest in the
  caller's outgoing stack area. Parameters are mutable slots.
- Redefining any §10 builtin is `E3012`. Duplicating a function is `E3001`.
- `fn main() -> i64` with no parameters is the entry point (`E3011` when missing or ill-formed). Its return value is the process exit code.

### 5.2 Bindings

```
let x: i64 = expr;
var y: i64 = expr;
var a: [N]i64 = [v; N];
var b: [N]i64 = [e0, e1, ...];
```

- Every binding names its scalar type (`i64`), has an initializer, and gets one frame slot.
- `let` cannot be assigned after init (`E3005`). `var` can.
- An array binding declares `[N]i64` with a literal initializer: `[v; N]` fills N
  elements with one literal, `[e0, e1, ...]` lists them in evaluation order every
  element once. N is 1..65536 (`E3014`); a fill or aggregate must match N exactly
  (`E3014`); an empty `[v; 0]` or literal is refused the same way. The elements
  occupy N consecutive frame slots; element 0 sits at the span's lowest address
  and element `i` eight bytes above it.
- Arrays never narrow to scalars: a whole-array use as a value is `E3016`, and
  parameters and return types stay `i64` (`E2002` refuses `[N]i64` there).
- A name already declared in the function cannot be redeclared (`E3006`), including parameters. There is no shadowing.
- Reading an undeclared name is `E3007`. A builtin's bare name is never a value (`E3012`): every
  use is a call.

## 6. Expressions and statements

### 6.1 Operators (loosest to tightest)

```
||  &&  |  ^  &  == !=  < <= > >=  << >>  + -  * / %
unary - ! ~
```

Comparison yields `0` or `1` and is usable as an integer or a condition.
`&&` and `||` short-circuit and yield `0` or `1`. Evaluation is left to right;
a `print` inside an argument list runs in argument order.

### 6.2 Calls

```
name(arg, ...)
print(x)
```

- A call names a declared `fn` or one of the builtins (`§10`).
- Arity must match exactly (`E3004`); unknown callees are `E3003`.
- Statement position accepts only an assignment or a call; any other bare expression is `E2004`. Only a name is assignable (`E2003`).

### 6.3 Control

```
if expr { } else if expr { } else { }
while expr { }
for ident in expr ".." expr { }
for ident in expr "..=" expr { }
break;  continue;
return expr;
{ block }
```

No `match`, no exceptions. `break`/`continue` outside `while` or `for` is
`E3008`. An `if` condition that is a comparison branches directly; `!cond`
flips the branch.

A `for` declares one scoped, mutable loop variable: the start bound is
evaluated once in the enclosing scope, the end bound is re-evaluated before
every test like C, `..` excludes and `..=` includes it, `continue` still
steps the counter, and a name the loop variable shares with an existing
binding resumes that binding after the loop (`E3006` still refuses shadowing
inside the body). The loop lowers to `var` + `while` before optimisation;
code generation never sees a `for`.

### 6.4 Arrays

```
a[i]           read element i
a[i] = expr;   store into element i
```

- `a` is an array binding; indexing a scalar is `E3015`. Indices nest: `a[a[0]]` is legal;
  array literals may not nest inside one another, and an element is not assignable
  through anything but a plain `a[i] =` statement.
- A literal index outside `0 .. N-1` is `E3017` at analysis time. A dynamic index is
  scaled by 8 and added to the element span's lowest displacement, so element `i` sits
  at the same frame slot a static analysis would name; there is no runtime bounds check,
  and an out-of-range dynamic index addresses foreign frame slots or beyond.
- A static element store writes its frame slot directly. A dynamic one writes through
  the scaled address; when the value is a literal or a name already in a register, that
  write is one memory-operand instruction, and otherwise the element address is parked
  in a frame scratch slot so the value's own calls and register use cannot clobber it.
  Evaluation order is index, then value.

## 7. Defined semantics

| Question | Answer (no undefined behaviour here) |
|---|---|
| Overflow | Wraps two's complement |
| Division by zero | Trap: message on stderr, exit code 3 |
| Division rounding | Truncates toward zero; `%` keeps the dividend's sign |
| `print_char(c)` | Writes the low byte of `c` (`c & 0xFF`), no newline; returns `c` |
| `print_hex(x)` | Writes `x` as lowercase hex of its unsigned 64-bit value, no leading zeros, `0` for zero, no newline; returns `x` |
| `print_str(s)` | Writes the bytes of the string literal `s` with no newline; returns `s` |
| `argc()` | The command-line token count, the program path included |
| `arg_byte(i, j)` | Byte `j` (zero-indexed) of command-line token `i`, quotes stripped by tokenization; `-1` for a negative `j`, past the token's end, past `63` tokens, or before the first fill |
| `read_char()` | The next stdin byte, or `-1` at end of stream (a true end keeps answering `-1`) |
| `exit(code)` | Stops the machine at once with process exit code `code`; usable only as its own statement, exactly like a `return` (`E3013` anywhere else); nothing after it runs |
| `str_len(s)` | The byte count of the NUL-terminated literal `s`, terminator excluded |
| `str_byte(s, j)` | The byte at `j` into the NUL-terminated literal `s`, or `-1` for a negative `j` or when the byte at `j` is the NUL terminator; reading past the terminator is undefined |
| Output buffering | Every printer appends to one shared 64 KiB stdout buffer, drained at once when it fills, when `exit(code)` runs and when `main` returns; inter-source byte order is preserved either way |
| `flush()` | Writes out everything the shared buffer holds through one `WriteFile` and answers how many bytes went out; `0` on an empty buffer |
| `popcount(x)` | The number of set bits in `x`'s unsigned 64-bit reading, emitted as one POPCNT instruction; requires an x86-64 CPU with ABM/POPCNT (all such CPUs since 2008) |
| Right shift | Arithmetic (`>>` keeps the sign); shift counts mask to 63 in folding and hardware |
| Uninitialised reads | Impossible: initializers are mandatory and every frame slot is zeroed on entry |
| Branch never taken | A binding declared inside it reads as `0` |
| Undeclared read | Rejected before code generation (`E3007`) |
| Array element address | Element `i` sits at the element span's lowest displacement plus `8i`; static indices are bounds-checked at analysis time (`E3017`), dynamic indices are never re-checked at runtime |
| Array element store order | `index`, then `value`; a literal or register value writes in one indexed memory instruction, any other value's address is parked in the frame across its evaluation |

Folding (`optimize.py`) preserves these answers exactly: it never folds a zero
divisor or `i64.min / -1`, never drops a `print`, and `while 0` removal plus
dead-branch selection keep the zeroed-frame rule observable in both build modes.

## 8. Targets

One target only: Windows x86-64, PE32+ console, sections `.text` (code),
`.rdata` (import names and string constants), `.data` (import address table).
Imports are exactly `GetStdHandle`, `WriteFile`, `ReadFile`, `GetCommandLineA`,
`ExitProcess` from `kernel32.dll`. There is no `#if target`, no `extern`, no triple flag.
Header timestamps are zero, so identical sources give identical bytes.
Calls use the Windows x64 register block (`rcx`, `rdx`, `r8`, `r9`) and a private
contract for the rest: stack parameter `j` rides at `[rbp+16+8j]` in the callee, in
the caller's padded outgoing area, without Microsoft's 32-byte shadow store. There is
no foreign code to call, so the contract only has to satisfy itself; a debugger sees
rbp-chained frames either way.

## 9. No dependency injection, no prelude

There is no container, no interface, no `use`, no standard library beyond
§10. A program that needs a service takes an `i64`, returns an `i64`, or
prints one. (Divergence from Alloy SPEC §§9–10, recorded deliberately.)

## 10. Standard surface (the whole of it)

- Types: `i64`. Literals: decimal integer; `true`/`false`; read-only string literals
  (`"bytes"`, double-quoted, NUL-terminated, strengthened to an `i64` handle).
- Statements: `let`, `var`, assignment, `if`/`else`, `while`, `for` (counted range), `return`, `break`, `continue`, block.
- Builtins (every name, arity and behaviour the manifest publishes):
  - `print(x: i64) -> i64` — decimal plus newline.
  - `print_char(c: i64) -> i64` — single byte, no newline.
  - `print_hex(x: i64) -> i64` — lowercase hex, no newline.
  - `print_str(s: i64) -> i64` — the literal's raw bytes, no newline.
  - `argc() -> i64` — command-line token count, program path included.
  - `arg_byte(i: i64, j: i64) -> i64` — byte `j` of token `i`, `-1` past any bound.
  - `read_char() -> i64` — next stdin byte, `-1` at end of stream.
  - `exit(code: i64)` — stops the process with process exit code `code`; statement-only (`E3013`).
  - `str_len(s: i64) -> i64` — NUL-terminated byte count.
  - `str_byte(s: i64, j: i64) -> i64` — byte `j`, `-1` at or past the terminator.
  - `flush() -> i64` - drains the shared stdout buffer with one `WriteFile`; answers the byte count.
- `popcount(x: i64) -> i64` - set-bit count of the unsigned 64-bit reading of `x`.
- Comments: `//` to end of line.
- Limits: 8 parameters (the first four in registers, the rest in the caller's outgoing
  stack area); `i64` range `-9223372036854775808..9223372036854775807`; ASCII identifiers,
  no shadowing.
- (Divergence from Alloy SPEC §§4, 6, 10: no floats, pointers, classes, interfaces,
  `Result`, `match`, closures, or IO interfaces beyond `§10`. `print(i64)` replaces
  `Writer`/`write_str`; string literals and the read builtins above are the whole
  standard library.)

## 11. Programs and entry

A program is well formed when it declares `fn main() -> i64` with zero
parameters and every function can only exit via `return`. `main` returning `0`
means success; any other return is that exit code. (Cf. Alloy SPEC §11, whose
`function main(): i32` is the same idea with a different keyword and width.)

## 12. Build interface

```
python -m cairn build <file>.crn [-o out.exe] [--json] [--no-optimize]
python -m cairn check <file>.crn [--json] [--no-optimize]
python -m cairn run <file>.crn [--timeout S] [--no-optimize] [--json]
python -m cairn verify <target.exe|file.crn> [--json]
python -m cairn listing <file>.crn [--json]
python -m cairn capabilities [--json]
```

- `build` writes a PE; `check` compiles in memory and writes nothing; `run` builds to a temp file, runs it, and forwards its exit code; `verify` decides the ten `V0001`–`V0010` artifact rules from bytes alone; `listing` prints `.text` bytes grouped by label; `capabilities` prints §10 plus codes, limits, and guarantees as JSON.
- Exit codes: `0` ok, `1` source did not compile or program failed to start, `2` usage or unreadable input. (Cf. Alloy SPEC §12, whose `alloy build/check/run` is emit-and-typecheck without a backend; Cairn's verbs execute a real backend.)

## 13. Concrete syntax summary

```
program      ::= { function-item }
function-item::= "fn" ident "(" [ params ] ")" "->" "i64" block
params       ::= param { "," param }
param        ::= ident ":" "i64"
block        ::= "{" { stmt } "}"
stmt         ::= let-stmt | if-stmt | while-stmt | for-stmt | return-stmt | break-stmt | continue-stmt | block | expr-stmt
let-stmt     ::= ("let" | "var") ident ":" "i64" "=" expr ";"
if-stmt      ::= "if" expr block [ "else" (if-stmt | block) ]
while-stmt   ::= "while" expr block
for-stmt     ::= "for" ident "in" expr ( ".." | "..=" ) expr block
return-stmt  ::= "return" expr ";"
break-stmt   ::= "break" ";"
continue-stmt ::= "continue" ";"
expr-stmt    ::= expr ";"
expr         ::= binary-expr
binary-expr  ::= unary-expr { binop unary-expr }
unary-expr   ::= ("-" | "!" | "~") unary-expr | primary
primary      ::= int | "true" | "false" | ident | call | "(" expr ")"
call         ::= ident "(" [ expr { "," expr } ] ")"
```

An `expr-stmt` is valid only when `expr` is an assignment to a name
(`E2003` otherwise) or a call, optionally parenthesised (`E2004` otherwise).
Operator precedence and associativity follow the tiers in §6.1; `-x`, `!x`
and `~x` bind tightest and nest right-associatively.

## 14. Style rules for generated code

An LLM writing Cairn MUST:

1. Use `fn`, never `function`/`func`/`def`.
2. Declare every binding with `: i64` and an initializer; end every function path with `return expr;`.
3. Call with exact arity; use `print(x)` only as a call, never as a value.
4. Keep one flat scope: never redeclare a name in the same function.
5. Put `main` as `fn main() -> i64` with no parameters.
6. Name files `*.crn`.
7. Never invent strings-in-variables, imports, modules, generics, closures, classes,
   traits, lifetimes, `unsafe`, or exceptions. §10 is the whole standard library; a
   builtin beyond it does not exist.

Illegal (do not emit):

```
function main(): i32 {}
fn main() {}
let x = 1;
x: i64 = 1;
for (i = 0;;) {}
match x { _ => {} }
use std.io.Writer;
extern "C" write(...);
#if target.os == "windows"
```

## 15. Reference hello (canonical)

```cairn
// The smallest complete Cairn program: one expression, one call, one exit code.
fn main() -> i64 {
    print(40 + 2);
    return 0;
}
```

This is `examples/hello.crn` verbatim. It prints `42` and exits `0`.
(Cf. Alloy SPEC §15: Alloy's hello prints a string through `Writer`/DI;
Cairn's hello prints an integer through the single builtin. The difference
is the backend probe's starting exhibit.)

## 16. Acceptance checklist

A source file is valid Cairn 0.1 if every box ticks:

- [ ] Every routine uses `fn` and declares `-> i64`
- [ ] Every binding is `let name: i64 = expr;`, `var name: i64 = expr;`, or an array form
      `var name: [N]i64 = [v; N];` / `var name: [N]i64 = [e0, e1, ...];`, never redeclared
- [ ] No scalar type name besides `i64` appears (`[N]i64` array annotations excepted)
- [ ] Every array index is a literal in `0 .. N-1`, or reads or overwrites no slot outside the array
- [ ] Every call has exact arity per §10 (`exit` sits only as its own statement)
- [ ] `fn main() -> i64` exists, takes no parameters, and every function ends in `return`
- [ ] No statement besides assignment or call sits bare; `break`/`continue` are inside a loop (a `while` or a `for`)
- [ ] `python -m cairn check file.crn --json` reports `ok: true`
- [ ] `python -m cairn verify file.crn` reports verified (same ten `V####` rules as a built `.exe`)
- [ ] `python -m cairn run file.crn` prints the expected integers and forwards the expected exit code

Non-goals for 0.1 (explicit): floats, structs, modules,
closures, generics, exceptions, threads, GC, pointers, interfaces, DI,
conditional compilation, foreign declarations, and any standard library
beyond §10's named builtins. Each is a new specification task with its own ADR, not an
interpretation of this one.
