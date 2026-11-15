// Prime candidates 2..300000 by trial division: a nested loop paying an idiv- or
// magic-multiply-modulo per step, guarded by a flag exit neither optimizer may fold.
// Mechanical C++ twin of primes.c (same statements, C++ headers);
// twin of primes.crn, primes.cpp and primes.rs; all four print the identical total.
#include <stdint.h>
#include <stdio.h>

int main(void) {
    int64_t n = 2;
    int64_t total = 0;
    while (n <= 300000) {
        int64_t prime = 0;
        if (n == 2) {
            prime = 1;
        } else {
            if (n % 2 != 0) {
                prime = 1;
                int64_t d = 3;
                while (d * d <= n && prime == 1) {
                    if (n % d == 0) {
                        prime = 0;
                    }
                    d += 2;
                }
            }
        }
        if (prime == 1) {
            total += n;
        }
        n += 1;
    }
    printf("%lld\n", (long long)total);
    return 0;
}
