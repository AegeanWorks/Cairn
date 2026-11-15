// Prime candidates 2..300000 by trial division: a nested loop paying an idiv- or
// magic-multiply-modulo per step, guarded by a flag exit neither optimizer may fold.
// Twin of primes.crn and primes.c; all three print the identical total.
fn main() {
    let mut n: i64 = 2;
    let mut total: i64 = 0;
    while n <= 300000 {
        let mut prime: i64 = 0;
        if n == 2 {
            prime = 1;
        } else {
            if n % 2 != 0 {
                prime = 1;
                let mut d: i64 = 3;
                while d * d <= n && prime == 1 {
                    if n % d == 0 {
                        prime = 0;
                    }
                    d += 2;
                }
            }
        }
        if prime == 1 {
            total += n;
        }
        n += 1;
    }
    println!("{}", total);
}
