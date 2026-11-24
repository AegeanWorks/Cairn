# Codebase Map & Symbol Catalog 🗺️

> **AUTO-GENERATED & VERIFIED BY `scripts/update_knowledge_base.py`**
> This document catalogues Python modules, classes, and exported functions. See Map Coverage for any stack it does not index.

---

## 📊 Codebase Metrics
- **Total Python Modules**: `3`
- **Total Classes**: `1`
- **Total Top-Level Functions**: `33`
- **Total Class Methods**: `0`
- **Total Source Lines**: `877`

## 🧭 Map Coverage
- **Indexed**: python (`3` module(s))
- **Not indexed by this map**: cpp (`48` file(s)), rust (`63` file(s))

> Only Python symbols are catalogued. A language above is reported as not indexed, never as verified by this map.

---

## 📦 Modules & Exported Symbols

### [`benchmarks/__init__.py`](benchmarks/__init__.py)
> *Benchmarks for Cairn: case sources and the harness that measures them.*

**Size**: `1` lines

---

### [`benchmarks/paired.py`](benchmarks/paired.py)
> *Deterministic paired A/B verdict for the benchmark registry.*

**Size**: `174` lines

**Functions:**
- `def timed(executable, stdin)` - *One wall-clock run; returns milliseconds and stdout for parity checks.*
- `def gap(left, right, rounds, stdin)` - *Median ratio left/right and its samples (lower = left faster).*
- `def band(samples)` - *The central 80 percent of an identical-binary control run.*
- `def verdict(gap_value, control_band)` - *Win only when the gap is clearly outside the control band.*
- `def measure_interleaved(timers, rounds)` - *Sample every timer in alternating order across the same rounds.*
- `def verdicts_from_samples(samples, baselines)` - *Derive the control band and baseline verdicts from interleaved samples.*
- `def measure_case(case, folder, rounds)` - *Build every twin for one case and measure all gaps in one interleaved window.*
- `def main(argv)`

---

### [`benchmarks/run_benchmarks.py`](benchmarks/run_benchmarks.py)
> *Benchmark harness: measure Cairn executables and, when one exists, C, C++ and Rust baselines.*

**Size**: `702` lines

**Classes:**
- `class Case` - *One benchmark: a Cairn source, equivalent C, C++ and Rust sources, and its unit.*

**Functions:**
- `def find_c_compiler()` - *The first C compiler already installed, or None when the machine has none.*
- `def find_rust_compiler()` - *The first Rust compiler already installed, or None when the machine has none.*
- `def find_cpp_compiler()` - *The first C++ compiler already installed, or None when the machine has none.*
- `def decode_output(raw)` - *Decode captured bytes with the universal newlines of a text-mode run.*
- `def run_command(argv, timeout, cwd, stdin)` - *Run one command with a hard limit, capturing text.*
- `def cairn_build_command(source, destination, optimise)` - *The exact Rust `cairn-rs buildexe` command for one case.*
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
