// Rust twin of rotl.crn: rotate_left maps to the platform rotate instruction.
fn main() {
    let mut total: u64 = 0;
    for i in 0u64..10_000_000 {
        let v = i.wrapping_mul(2_654_435_761) ^ (i >> 3);
        total = (total + v.rotate_left((i & 63) as u32)) & 0x7FFF_FFFF_FFFF_FFFF;
    }
    println!("{}", total);
}
