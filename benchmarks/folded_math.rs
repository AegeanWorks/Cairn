// Rust twin of folded_math.c: one constant expression and one bit-mix per iteration.
fn main() {
    let mut i: i64 = 0;
    let mut acc: i64 = 0;
    while i < 20_000_000 {
        acc = acc + (2 * 3 + 4 * 5 - 6 / 2);
        acc = (acc ^ (1 << 4)) | 3;
        i = i + 1;
    }
    println!("{}", acc);
}
