// Stdin byte-stream throughput, Rust twin of char_stream.crn.
use std::io::Read;

fn main() {
    let stdin = std::io::stdin();
    let mut total: i64 = 0;
    for byte in stdin.lock().bytes() {
        match byte {
            Ok(c) => total += c as i64,
            Err(_) => break,
        }
    }
    println!("{}", total);
}
