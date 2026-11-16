// String-literal byte scan, Rust twin of str_scan.crn: the same 200,000 passes,
// one bounds-checked byte read per byte, hidden from constant folding.
use std::hint::black_box;

fn main() {
    let text = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-*/";
    let len = text.len();
    let mut total: i64 = 0;
    let mut r = 0i64;
    while r < 200000 {
        let mut j = 0usize;
        while j < len {
            total += black_box(text[j]) as i64;
            j += 1;
        }
        r += 1;
    }
    println!("{}", total);
}
