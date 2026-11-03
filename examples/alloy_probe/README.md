# Alloy probe: can Alloy-shaped programs target Cairn's backend?

One frozen Alloy program, one mechanical lowering, one worked verdict.
Equal weight to fit and misfit; a clean negative beats a forced positive.

## Files

- `alloy.toml` — package manifest (`bin`, `alloy_probe_min 0.1.0`), mirroring Alloy SPEC §3.
- `src/main.al` — the Alloy source: `function sum_to(n: i32): i32` plus `function main(): i32`, integer-only.
- `lowered.crn` — the same program lowered to Cairn by adapter (`function`→`fn`, `i32`→`i64`), plus `print(total)`.
- This file — the fit/misfit record and the §16 walk.

## Alloy source (frozen)

```
function sum_to(n: i32): i32 { var total / var i / while i <= n / return total; }
function main(): i32 { let total: i32 = sum_to(10); return total - 55; }
```

Full listing is `src/main.al`. Returns `0` when the sum is 55.

## Lowered Cairn (frozen)

Full listing is `lowered.crn`. Prints `55`, returns `0`.

Adapter (mechanical, three rules): `function`→`fn`, `(x: i32): i32`→`(x: i64) -> i64`,
`let/var x: i32`→`let/var x: i64`. Added `print(total)` — Cairn cannot return a
string, so observability needs the one builtin.

## Alloy §16 walk for `src/main.al`

- [x] Every function uses `function` and has a return type (except `drop`) — `function sum_to(n: i32): i32`, `function main(): i32` (SPEC §5).
- [x] Every interface method starts with `self` — vacuously true: no interfaces (SPEC §4.5 absent by design).
- [x] No borrow-checker syntax appears — none (SPEC §1: no borrow checker).
- [x] Pointer ops only use `*`, `*mut`, `*const`, `&`, `&mut`, `as` — vacuously true: no pointers (SPEC §4.2 unused).
- [x] `main` exists for bins and returns `i32` — `function main(): i32` (SPEC §11).
- [x] `use` paths resolve to public items — vacuously true: no `use`; only prelude `i32` (SPEC §3 prelude).
- [x] `#if target` blocks parse on all branches — vacuously true: no `#if` (SPEC §8 unused).
- [x] DI only through `Container.bind`/`get` — vacuously true: no DI (SPEC §9 unused).
- [x] Strings used as pointers stay reachable — vacuously true: no strings (SPEC §§4.1, 7, 10 unused).

Verdict on the Alloy side: valid Alloy v0.1 by §16. `alloy check: ok` by the
checklist (no backend exists to run it further — Alloy SPEC §12).

## Cairn §16 walk for `lowered.crn`

All nine Cairn SPEC §16 boxes tick; commands below are the evidence.

## Fit

| Alloy feature (section) | Cairn counterpart | Status |
|---|---|---|
| `function` + return type (SPEC §5) | `fn` + `-> i64` (Cairn SPEC §5.1) | fits-with-adapter (keyword + width rename) |
| `let`/`var` + initializer (SPEC §5) | `let`/`var` + `: i64` + initializer (Cairn SPEC §5.2) | fits-with-adapter (width only) |
| `while` / `if` / `return` / calls (SPEC §6) | same forms (Cairn SPEC §6.3) | fits as-is |
| Integer arithmetic, `<=`, `+` (SPEC §6) | same operators, same precedence (Cairn SPEC §6.1) | fits as-is |
| `function main(): i32` returns 0 (SPEC §11) | `fn main() -> i64` returns exit code (Cairn SPEC §11) | fits-with-adapter |
| `alloy check` mental typecheck (SPEC §12) | `cairn check --json` real check (Cairn SPEC §12) | fits (Cairn goes further: real backend) |

## Misfit (equal weight)

| Alloy feature (section) | Why it does not lower | Exhibit |
|---|---|---|
| Strings + `Writer`/`write_str` (SPEC §§4.1, 10, 15) | Cairn has no strings; `print` takes `i64` only | Canonical DI hello (`examples/hello` in Alloy) cannot lower without replacing IO |
| DI `Container.bind/get` + interfaces/vtable (SPEC §§4.5, 9) | No classes, interfaces, GC, or container in Cairn 0.1 | Probe avoids DI deliberately; any `di.get<Writer>()` line diverges |
| Pointers `*T`, `&`, `as`, `null` arithmetic (SPEC §§4.2, 7) | No pointer type; no address-of; no UB to waive | Any `*p` / `&x` line diverges |
| `match` on `Result` (SPEC §6.4) | No `match`, no `Result`, no enums with payloads | Any `match`/`Ok`/`Err` line diverges |
| `use`, modules, `alloy.toml` packages (SPEC §3) | One `.crn` file is the program; no manifest consumed | `alloy.toml` travels as documentation only |
| `#if target`, `extern`, triples (SPEC §§8, 12) | One target only: Windows x86-64 PE32+; three fixed imports | Any `#if`/`extern` line diverges |

## Verdict

**Fits-with-adapter for the integer/control subset; diverges for the language.**
An Alloy-shaped integer program (`function/let/var/while/if/return/arithmetic/main`)
lowers to Cairn with a three-rule mechanical adapter and runs through the real
backend (`check`/`run`/`listing`/`verify` below). The full Alloy language —
strings, `Writer`/DI, classes/interfaces, pointers, `match`/`Result`, modules,
`#if`/`extern` — has no Cairn counterpart in 0.1 and diverges without new
specification work. The canonical Alloy DI hello is the standing diverge
exhibit: it is valid Alloy and unlowerable to Cairn as written.

## Reproduce

From `projects/cairn`:

```bash
python -m cairn check examples/alloy_probe/lowered.crn --json
python -m cairn run examples/alloy_probe/lowered.crn
python -m cairn listing examples/alloy_probe/lowered.crn
python -m cairn verify examples/alloy_probe/lowered.crn
python -m cairn capabilities --json
python -m unittest discover
```

Expected: `check ok: true`, `run` prints `55` and exits `0`,
`listing` shows `<fn.main>` + `<fn.sum_to>`, `verify` verified,
`unittest` 133+ OK (no test enumerates `examples/`).
