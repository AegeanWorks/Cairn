//! The user-facing CLI surface: build, run, verify, listing and capabilities.

use std::path::PathBuf;

fn root() -> PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

fn binary() -> PathBuf {
    let base = root().join("rust").join("target").join("debug").join("cairn-rs");
    if base.is_file() {
        return base;
    }
    base.with_extension("exe")
}

fn hello() -> PathBuf {
    root().join("examples").join("hello.crn")
}

fn scratch(name: &str) -> PathBuf {
    let folder = std::env::temp_dir().join(format!("cairn-cli-{}-{}", std::process::id(), name));
    let _ = std::fs::remove_dir_all(&folder);
    std::fs::create_dir_all(&folder).unwrap();
    folder
}

#[test]
fn build_writes_a_runnable_executable() {
    let folder = scratch("build");
    let exe = folder.join("hello.exe");
    let status = std::process::Command::new(binary())
        .arg("build").arg(hello()).arg("-o").arg(&exe)
        .status().expect("build the Rust binary before running this test");
    assert!(status.success(), "build command failed");
    let image = std::fs::read(&exe).expect("build wrote no executable");
    assert_eq!(&image[..2], b"MZ", "not a PE image");
    let output = std::process::Command::new(&exe).output().unwrap();
    assert_eq!(String::from_utf8_lossy(&output.stdout), "42\n");
    assert!(output.status.success());
    let _ = std::fs::remove_dir_all(&folder);
}

#[test]
fn run_prints_the_program_output_and_forwards_the_exit_code() {
    let output = std::process::Command::new(binary())
        .arg("run").arg(hello())
        .output().expect("build the Rust binary before running this test");
    assert_eq!(String::from_utf8_lossy(&output.stdout), "42\n");
    assert!(output.status.success());
}

#[test]
fn verify_accepts_a_built_image() {
    let folder = scratch("verify");
    let exe = folder.join("hello.exe");
    assert!(std::process::Command::new(binary())
        .arg("build").arg(hello()).arg("-o").arg(&exe)
        .status().unwrap().success());
    let output = std::process::Command::new(binary())
        .arg("verify").arg(&exe)
        .output().unwrap();
    assert!(output.status.success());
    let text = String::from_utf8_lossy(&output.stdout);
    assert!(text.starts_with("verified "), "unexpected verdict: {}", text);
    let _ = std::fs::remove_dir_all(&folder);
}

#[test]
fn listing_names_the_entry_and_main_labels() {
    let output = std::process::Command::new(binary())
        .arg("listing").arg(hello())
        .output().expect("build the Rust binary before running this test");
    assert!(output.status.success());
    let text = String::from_utf8_lossy(&output.stdout);
    assert!(text.contains("<cairn.entry>"), "listing has no entry label");
    assert!(text.contains("<fn.main>"), "listing has no main label");
}

#[test]
fn capabilities_publishes_the_manifest() {
    let output = std::process::Command::new(binary())
        .arg("capabilities")
        .output().expect("build the Rust binary before running this test");
    assert!(output.status.success());
    let text = String::from_utf8_lossy(&output.stdout);
    assert!(text.contains("\"name\": \"cairn\""), "manifest has no name");
    assert!(text.contains("\"commands\""), "manifest has no command list");
}

/// Compile and run one inline program, returning its stdout.
fn run_source(name: &str, source: &str) -> String {
    let folder = scratch(name);
    let path = folder.join("program.crn");
    std::fs::write(&path, source).unwrap();
    let output = std::process::Command::new(binary())
        .arg("run").arg(&path)
        .output().expect("build the Rust binary before running this test");
    let _ = std::fs::remove_dir_all(&folder);
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    String::from_utf8_lossy(&output.stdout).into_owned()
}

#[test]
fn an_indexed_store_with_an_expression_writes_the_element() {
    let folder = scratch("indexed-store");
    let source = folder.join("store.crn");
    std::fs::write(&source, "\
fn main() -> i64 {
    var a: [4]i64 = [0; 4];
    var i: i64 = 0;
    while i < 4 { a[i] = i + 10; i = i + 1; }
    var r: i64 = 0;
    while r < 3 {
        i = 0;
        while i < 4 { a[i] = a[i] + 1; i = i + 1; }
        r = r + 1;
    }
    print(a[0]);
    print(a[1]);
    print(a[2]);
    print(a[3]);
    return 0;
}
").unwrap();
    let output = std::process::Command::new(binary())
        .arg("run").arg(&source)
        .output().expect("build the Rust binary before running this test");
    assert!(output.status.success(), "{}",
            String::from_utf8_lossy(&output.stderr));
    assert_eq!(String::from_utf8_lossy(&output.stdout), "13\n14\n15\n16\n");
    let _ = std::fs::remove_dir_all(&folder);
}
/// The rotate count's own home can be the destination; loading rcx first must
/// keep the count even though the destination is overwritten afterwards.
#[test]
fn rotl_keeps_the_count_when_it_lives_in_the_destination() {
    let text = run_source("rotl-alias", "\
fn main() -> i64 {
    var x: i64 = 6;
    var k: i64 = 3;
    var i: i64 = 0;
    while i < 4 {
        x = rotl(x, k);
        k = k + 1;
        i = i + 1;
    }
    print(x);
    return 0;
}
");
    // rotl(6,3)=48, rotl(48,4)=768, rotl(768,5)=24576, rotl(24576,6)=1572864.
    assert_eq!(text.trim(), "1572864", "rotl lost its count when the count shared the target");
}

/// popcount into its own home, then into a fresh home, must both count correctly.
#[test]
fn popcount_targets_its_home_and_its_own_value() {
    let text = run_source("popcount-home", "\
fn main() -> i64 {
    var a: i64 = 255;
    var b: i64 = 0;
    b = popcount(a);
    print(b);
    a = popcount(a);
    print(a);
    b = popcount(a);
    print(b);
    return 0;
}
");
    // 255 has eight bits, so both first counts are 8; the next popcount of 8 is 1.
    assert_eq!(text.trim(), "8\n8\n1", "popcount produced the wrong counts");
}

#[test]
fn a_loop_with_a_break_still_exits_at_the_right_iteration() {
    let text = run_source("break-guard", "\
fn main() -> i64 {
    var i: i64 = 0;
    var t: i64 = 0;
    while i < 10 {
        if i == 3 {
            break;
        }
        t = t + i;
        i = i + 1;
    }
    print(t);
    print(i);
    return 0;
}
");
    assert_eq!(text.trim(), "3\n3", "a duplicated body ignored the early exit");
}

/// `3 < x` puts the literal on the left, which x86 cannot compare directly; the
/// relation must be mirrored rather than read as `x < 3`.
#[test]
fn a_literal_on_the_left_keeps_its_relation() {
    let text = run_source("literal-left", "\
fn main() -> i64 {
    var x: i64 = 5;
    var n: i64 = 0 - 5;
    var t: i64 = 0;
    if 3 < x { t = t + 1; }
    if 3 < n { t = t + 10; }
    if 5 == x { t = t + 100; }
    if 0 <= x { t = t + 1000; }
    print(t);
    return 0;
}
");
    // 3 < 5 holds, 3 < -5 fails, 5 == 5 holds, 0 <= 5 holds.
    assert_eq!(text.trim(), "1101", "a literal-first relation was inverted");
}

/// An element compared against a named value has no register-register shortcut;
/// it must still emit a real comparison instead of falling through.
#[test]
fn an_element_compared_with_a_name_jumps_correctly() {
    let text = run_source("index-name", "\
fn main() -> i64 {
    var a: [3]i64 = [10, 20, 30];
    var x: i64 = 25;
    var t: i64 = 0;
    var i: i64 = 0;
    while i < 3 {
        if a[i] < x { t = t + i + 1; }
        i = i + 1;
    }
    print(t);
    return 0;
}
");
    // Only a[0]=10 and a[1]=20 are below 25, contributing 1 and 2.
    assert_eq!(text.trim(), "3", "an element-to-name compare did not jump");
}

/// An element of one array compared with an element of another exercises the
/// general two-expression fallback.
#[test]
fn two_element_expressions_compare_correctly() {
    let text = run_source("index-index", "\
fn main() -> i64 {
    var a: [3]i64 = [5, 10, 15];
    var b: [3]i64 = [8, 8, 8];
    var t: i64 = 0;
    var i: i64 = 0;
    while i < 3 {
        if a[i] < b[i] { t = t + 1; }
        if a[i] >= b[i] { t = t + 10; }
        i = i + 1;
    }
    print(t);
    return 0;
}
");
    // a[0]=5<a[0]... 5<8 holds (+1), 10<8 fails, 15<8 fails; 5>=8 fails,
    // 10>=8 holds (+10), 15>=8 holds (+10): total 21.
    assert_eq!(text.trim(), "21", "two element expressions did not compare correctly");
}

/// SPEC.md line 112: a comparison yields 0 or 1 and is usable as an integer.
/// Before this test the relation leaked its right operand into the assignment.
#[test]
fn a_comparison_used_as_a_value_yields_one_or_zero() {
    let text = run_source("compare-value", "\
fn main() -> i64 {
    var x: i64 = 5;
    var below: i64 = x < 6;
    var above: i64 = x > 6;
    var equal: i64 = x == 5;
    var different: i64 = x != 5;
    print(below);
    print(above);
    print(equal);
    print(different);
    print(x < 6);
    print((x < 6) + (x > 6));
    return 0;
}
");
    assert_eq!(text.trim(), "1\n0\n1\n0\n1\n1",
               "a comparison did not yield 0 or 1 as an integer");
}

/// SPEC.md line 113: `&&` and `||` short-circuit and yield 0 or 1.
#[test]
fn logical_operators_yield_one_or_zero_when_used_as_values() {
    let text = run_source("logic-value", "\
fn main() -> i64 {
    var x: i64 = 5;
    var n: i64 = 0 - 2;
    var both: i64 = x > 3 && x < 9;
    var partial: i64 = x > 3 && n > 9;
    var either: i64 = x > 30 || n < 9;
    var neither: i64 = x > 30 || n > 9;
    print(both);
    print(partial);
    print(either);
    print(neither);
    print(x > 30 || x > 3);
    return 0;
}
");
    assert_eq!(text.trim(), "1\n0\n1\n0\n1",
               "a logical operator did not yield 0 or 1 as a value");
}

/// Short-circuiting must not evaluate the right side once the left decides.
#[test]
fn logical_operators_short_circuit() {
    let text = run_source("logic-short", "\
fn noisy(v: i64) -> i64 {
    print(v);
    return v;
}
fn main() -> i64 {
    var t: i64 = 0;
    if 0 != 0 && noisy(7) != 0 { t = t + 1; }
    if 1 != 0 || noisy(9) != 0 { t = t + 10; }
    print(t);
    return 0;
}
");
    // Neither call may run: the first body is skipped and only the second runs.
    assert_eq!(text.trim(), "10", "a short-circuit evaluated the right side anyway");
}
