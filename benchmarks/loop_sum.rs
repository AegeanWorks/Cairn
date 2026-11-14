// Rust twin of loop_sum.c: sum 0..N, print, exit.
fn main() {
    let mut total: i64 = 0;
    let mut i: i64 = 0;
    while i < 200_000_000 {
        total = total + i;
        i = i + 1;
    }
    println!("{}", total);
}
