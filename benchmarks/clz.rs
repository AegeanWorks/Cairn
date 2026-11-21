// Rust twin of clz.crn: leading_zeros maps to the platform count-leading-zeros.
fn main() {
    let mut total: u64 = 0;
    for i in 0u64..10_000_000 {
        let v = i.wrapping_mul(2_654_435_761) ^ (i >> 3);
        total += v.leading_zeros() as u64;
    }
    println!("{}", total);
}
