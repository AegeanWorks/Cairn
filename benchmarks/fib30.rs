// Rust twin of fib30.c: naive recursion to 30 calls deep, print, exit.
fn fib(n: i64) -> i64 {
    if n < 2 {
        return n;
    }
    fib(n - 1) + fib(n - 2)
}

fn main() {
    println!("{}", fib(30));
}
