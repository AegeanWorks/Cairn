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
2. One binding form per intent: `let` is immutable, `var` is assignable. No shadowing.
3. One scope: flat and function-wide. A name declared in any block is visible after it.
4. One function form: `fn name(a: i64, ...) -> i64`. At most four parameters.
5. One effect: `print(x)` writes the decimal form plus a newline and returns `x`.
6. No hidden behaviour: overflow wraps, division by zero traps, every frame slot reads as `0` on entry.
7. No toolchain: the compiler is Python standard library only; the artifact is a Windows PE32+ console executable needing nothing else.

## 2. Lexical grammar

```
comment      ::= "//" ~[\n]*
ident        ::= [A-Za-z_][A-Za-z0-9_]*        (ASCII only)
int          ::= [0-9]+                          (decimal only, must fit in i64)
keyword      ::= fn | let | var | if | else | while | return | break | continue | true | false
symbol       ::= "->" | "==" | "!=" | "<=" | ">=" | "&&" | "||" | "<<" | ">>"
               | "(" | ")" | "{" | "}" | "," | ":" | ";" | "=" | "+" | "-" | "*" | "/" | "%"
               | "<" | ">" | "!" | "&" | "|" | "^" | "~"
```

Whitespace separates tokens. Anything else is `E1001`. An integer above
`9223372036854775807` is `E1003`. `true` is `1`, `false` is `0`.

## 3. Compilation units

A program is one `.crn` text file holding one or more `fn` items. There is no
package manifest, no module path, no `use`, no import, no conditional
compilation. Two files are never joined: what the file declares is the program.

## 4. Types

Exactly one type name exists: `i64`. Any other type name is `E2002`.
There are no strings, floats, arrays, structs, classes, interfaces, pointers,
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
- At most four parameters (`E3010`); parameters are mutable slots.
- Redefining the builtin `print` is `E3012`. Duplicating a function is `E3001`.
- `fn main() -> i64` with no parameters is the entry point (`E3011` when missing or ill-formed). Its return value is the process exit code.

### 5.2 Bindings

```
let x: i64 = expr;
var y: i64 = expr;
```

- Every binding names its type (`i64`), has an initializer, and gets one frame slot.
- `let` cannot be assigned after init (`E3005`). `var` can.
- A name already declared in the function cannot be redeclared (`E3006`), including parameters. There is no shadowing.
- Reading an undeclared name is `E3007`. Using `print` as a value is `E3012`.

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

- A call names a declared `fn` or the builtin `print`.
- Arity must match exactly (`E3004`); unknown callees are `E3003`.
- Statement position accepts only an assignment or a call; any other bare expression is `E2004`. Only a name is assignable (`E2003`).

### 6.3 Control

```
if expr { } else if expr { } else { }
while expr { }
break;  continue;
return expr;
{ block }
```

No `for`, no `match`, no exceptions. `break`/`continue` outside `while` is
`E3008`. An `if` condition that is a comparison branches directly; `!cond`
flips the branch.

## 7. Defined semantics

| Question | Answer (no undefined behaviour here) |
|---|---|
| Overflow | Wraps two's complement |
| Division by zero | Trap: message on stderr, exit code 3 |
| Division rounding | Truncates toward zero; `%` keeps the dividend's sign |
| Right shift | Arithmetic (`>>` keeps the sign); shift counts mask to 63 in folding |
| Uninitialised reads | Impossible: initializers are mandatory and every frame slot is zeroed on entry |
| Branch never taken | A binding declared inside it reads as `0` |
| Undeclared read | Rejected before code generation (`E3007`) |

Folding (`optimize.py`) preserves these answers exactly: it never folds a zero
divisor or `i64.min / -1`, never drops a `print`, and `while 0` removal plus
dead-branch selection keep the zeroed-frame rule observable in both build modes.

## 8. Targets

One target only: Windows x86-64, PE32+ console, sections `.text` (code),
`.rdata` (import names and string constants), `.data` (import address table).
Imports are exactly `GetStdHandle`, `WriteFile`, `ExitProcess` from
`kernel32.dll`. There is no `#if target`, no `extern`, no triple flag.
Header timestamps are zero, so identical sources give identical bytes.

## 9. No dependency injection, no prelude

There is no container, no interface, no `use`, no standard library beyond
§10. A program that needs a service takes an `i64`, returns an `i64`, or
prints one. (Divergence from Alloy SPEC §§9–10, recorded deliberately.)

## 10. Standard surface (the whole of it)

- Types: `i64`. Literals: decimal integer; `true`/`false`.
- Statements: `let`, `var`, assignment, `if`/`else`, `while`, `return`, `break`, `continue`, block.
- Builtin: `print(x: i64) -> i64`.
- Comments: `//` to end of line.
- Limits: 4 parameters; `i64` range `-9223372036854775808..9223372036854775807`; ASCII identifiers, no shadowing.
- (Divergence from Alloy SPEC §§4, 6, 10: no strings, pointers, classes, interfaces, `Result`, `match`, closures, or IO interfaces. `print(i64)` replaces `Writer`/`write_str`.)

## 11. Programs and entry

A program is well formed when it declares `fn main() -> i64` with zero
parameters and every function can only exit via `return`. `main` returning `0`
means success; any other return is that exit code. (Cf. Alloy SPEC §11, whose
`function main(): i32` is the same idea with a different keyword and width.)

## 12. Build interface

```
python -m cairn build <file>.crn [-o out.exe] [--json] [--no-optimize]
python -m cairn check <file>.crn [--json] [--no-optimize]
python -m cairn run <file>.crn [--timeout S] [--no-optimize]
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
stmt         ::= let-stmt | var-stmt | if-stmt | while-stmt | return-stmt | break | continue | block | expr-stmt
let-stmt     ::= ("let" | "var") ident ":" "i64" "=" expr ";"
```

## 14. Style rules for generated code

An LLM writing Cairn MUST:

1. Use `fn`, never `function`/`func`/`def`.
2. Declare every binding with `: i64` and an initializer; end every function path with `return expr;`.
3. Call with exact arity; use `print(x)` only as a call, never as a value.
4. Keep one flat scope: never redeclare a name in the same function.
5. Put `main` as `fn main() -> i64` with no parameters.
6. Name files `*.crn`.
7. Never invent strings, imports, modules, generics, closures, classes, traits, lifetimes, `unsafe`, exceptions, or `for`.

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
- [ ] Every binding is `let name: i64 = expr;` or `var name: i64 = expr;`, never redeclared
- [ ] No type name besides `i64` appears
- [ ] Every call has exact arity; `print` is only ever called with one argument
- [ ] `fn main() -> i64` exists, takes no parameters, and every function ends in `return`
- [ ] No statement besides assignment or call sits bare; `break`/`continue` are inside `while`
- [ ] `python -m cairn check file.crn --json` reports `ok: true`
- [ ] `python -m cairn verify file.crn` reports verified (same ten `V####` rules as a built `.exe`)
- [ ] `python -m cairn run file.crn` prints the expected integers and forwards the expected exit code

Non-goals for 0.1 (explicit): floats, strings, arrays, structs, modules,
closures, generics, exceptions, threads, GC, pointers, interfaces, DI,
conditional compilation, foreign declarations, and any standard library
beyond `print`. Each is a new specification task with its own ADR, not an
interpretation of this one.
