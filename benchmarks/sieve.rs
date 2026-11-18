// Sieve of Eratosthenes over 0..65536 with a flagged element array: one
// store per composite, one load per candidate, nested dynamic indices. Twin
// of sieve.crn and sieve.c; all three print the identical prime count.
fn main() {
    let mut composite = vec![0i64; 65536];
    let mut count: i64 = 0;
    for n in 2..65536i64 {
        if composite[n as usize] == 0 {
            count += 1;
            let mut m = n * n;
            while m < 65536 { composite[m as usize] = 1; m += n; }
        }
    }
    println!("{}", count);
}
