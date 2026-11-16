// Argument-table read throughput, Rust twin of argv_walk.crn and argv_walk.c.
use std::env;

fn main() {
    let path = env::args().next().unwrap_or_default();
    let bytes = path.as_bytes();
    let len = bytes.len() as i64;
    let mut total: i64 = 0;
    for i in 0..12_800_000i64 {
        let idx = ((i + total) & 63) as usize;
        let c: i64 = if (idx as i64) < len { bytes[idx] as i64 } else { -1 };
        total += if (c & 1) == 0 { c } else { 1 };
    }
    println!("{}", total);
}
