// A three-argument function on the hot path: every call walks the argument-passing path
// with non-pure operands (a shift and a mask), inside a data-dependent loop. Twin of
// call_chain.crn and call_chain.c; all three print the identical total.
fn mix3(a: i64, b: i64, c: i64) -> i64 {
    let mut t: i64 = (a * 3) ^ (b << 2);
    if t > c {
        t = t - c;
    } else {
        t = t + c;
    }
    t
}

fn main() {
    let mut x: i64 = 1;
    let mut i: i64 = 0;
    let mut total: i64 = 0;
    while i < 20_000_000 {
        x = mix3(x, i >> 2, i & 65535);
        total += x & 255;
        i = i + 1;
    }
    println!("{}", total);
}
