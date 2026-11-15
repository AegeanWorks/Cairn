// Ackermann's function A(3, 9): millions of two-argument calls with data-dependent depth,
// a call-machinery stress neither optimizer may fold.
// Twin of ackermann.crn and ackermann.c; all three print the identical result.
fn ack(m: i64, n: i64) -> i64 {
    if m == 0 {
        return n + 1;
    }
    if n == 0 {
        return ack(m - 1, 1);
    }
    return ack(m - 1, ack(m, n - 1));
}

fn main() {
    println!("{}", ack(3, 9));
}
