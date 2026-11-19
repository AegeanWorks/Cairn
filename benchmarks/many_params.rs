// Six-argument call throughput: each iteration passes four arguments in registers and two
// on the stack. The loop bound comes from the process argument count so no compiler can
// fold the whole program into one print (ADR-071, ADR-074); run bare, the count is 1 and
// the bound is exactly 10,000,000. The masked product keeps the mix a live recurrence
// rather than a closed form, and the total stays below 2^57 so no twin overflows.
// Twin of many_params.crn, many_params.c and many_params.cpp; all four print the identical
// total.
fn mix(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64) -> i64 {
    ((a * b) & 65535) ^ (c + d * 43 + e * 47 + f * 53)
}

fn main() {
    let n: i64 = 10_000_000 + std::env::args().count() as i64 - 1;
    let mut i: i64 = 0;
    let mut total: i64 = 0;
    while i < n {
        total = total + mix(i, i + 1, i + 2, i + 3, i + 4, i + 5);
        i = i + 1;
    }
    println!("{}", total);
}
