// Collatz total stopping times for every probe below a constant bound: a branchy, division
// heavy loop with data-dependent control flow neither optimizer may fold.
// Twin of collatz.crn and collatz.c; all three print the identical total.
fn main() {
    let mut n: i64 = 1;
    let mut k: i64 = 0;
    let mut steps: i64 = 0;
    let mut total: i64 = 0;
    while n < 1_000_000 {
        k = n;
        steps = 0;
        while k > 1 {
            if k % 2 == 0 {
                k = k / 2;
            } else {
                k = 3 * k + 1;
            }
            steps = steps + 1;
        }
        total = total + steps;
        n = n + 1;
    }
    println!("{}", total);
}
