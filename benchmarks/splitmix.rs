// One hundred million splitmix64 mixes: a serial multiply-latency chain over 64-bit
// wraparound constants, the opposite bottleneck of xorshift's shift-xor chain.
// Twin of splitmix.crn and splitmix.c; all three print the identical result.
fn main() {
    let mut x: u64 = 88172645463325252;
    let mut total: i64 = 0;
    let mut i: i64 = 0;
    while i < 100000000 {
        x = x.wrapping_add(0x9E3779B97F4A7C15);
        let mut z: u64 = x;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D049BB133111EB);
        z = z ^ (z >> 31);
        total += ((z >> 60) & 15) as i64;
        i += 1;
    }
    println!("{}", total);
}
