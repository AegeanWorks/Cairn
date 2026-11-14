// Serial xorshift64 chain: a latency-dependent integer loop neither optimizer may fold.
// Twin of xorshift.c and xorshift.crn; all three print the identical final state.
fn main() {
    let mut state: i64 = 88172645463325252;
    let mut i: i64 = 0;
    while i < 200_000_000 {
        state = state ^ (state << 13);
        state = state ^ (state >> 7);
        state = state ^ (state << 17);
        i = i + 1;
    }
    println!("{}", state);
}
