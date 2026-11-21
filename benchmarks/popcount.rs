// Rust twin of popcount.crn: count_ones maps to the platform popcount.
fn main() {
    let mut total: u64 = 0;
    for i in 0u64..10_000_000 {
        let v = i.wrapping_mul(2_654_435_761) ^ (i >> 3);
        total += v.count_ones() as u64;
    }
    println!("{}", total);
}
