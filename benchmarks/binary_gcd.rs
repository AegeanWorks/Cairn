// Binary GCD (Stein's algorithm) over one million generated pairs: subtraction and shift
// loops with a swap, no division. Twin of binary_gcd.crn and binary_gcd.c; all three print
// the identical total.
fn bgcd(a: i64, b: i64) -> i64 {
    if a == 0 { return b; }
    if b == 0 { return a; }
    let mut a = a;
    let mut b = b;
    let mut k: i64 = 0;
    while (a & 1) == 0 && (b & 1) == 0 {
        a = a >> 1;
        b = b >> 1;
        k = k + 1;
    }
    while (a & 1) == 0 { a = a >> 1; }
    while (b & 1) == 0 { b = b >> 1; }
    while b != 0 {
        if a > b {
            std::mem::swap(&mut a, &mut b);
        }
        b = b - a;
        if b == 0 { break; }
        while (b & 1) == 0 { b = b >> 1; }
    }
    a << k
}

fn main() {
    let mut i: i64 = 1;
    let mut total: i64 = 0;
    while i <= 1_000_000 {
        let a: i64 = (i * 48271) % 2147483647;
        let mut b: i64 = ((i * 1103515245) % 2_147_483_647) >> 8;
        if b == 0 { b = 3; }
        total += bgcd(a, b + 1);
        i = i + 1;
    }
    println!("{}", total);
}
