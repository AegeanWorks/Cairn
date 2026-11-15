// Hex formatting throughput: every iteration prints one value in lowercase hex plus a
// newline, one format-plus-flush per value (the line-buffered stdout flushes at the
// newline). Twin of hex_io.crn, hex_io.c and hex_io.cpp; all four print the identical
// checksum.
fn main() {
    let mut checksum: u64 = 0;
    for i in 0u64..400000 {
        let value = i.wrapping_mul(2654435761);
        println!("{:x}", value);
        checksum ^= value;
    }
    println!("{}", checksum);
}
