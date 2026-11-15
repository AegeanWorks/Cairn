// Six-argument call throughput: each iteration passes four arguments in registers and two
// on the stack, multiplied and accumulated in a body no optimizer can fold. The total
// stays below 2^57, so no twin overflows.
// Twin of many_params.crn, many_params.c and many_params.cpp; all four print the
// identical total.
fn mix(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64) -> i64 {
    a * 31 + b * 37 + c * 41 + d * 43 + e * 47 + f * 53
}

fn main() {
    let mut i: i64 = 0;
    let mut total: i64 = 0;
    while i < 10_000_000 {
        total = total + mix(i, i + 1, i + 2, i + 3, i + 4, i + 5);
        i = i + 1;
    }
    println!("{}", total);
}
