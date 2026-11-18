// Sieve of Eratosthenes over 0..65536 with a flagged element array: one
// store per composite, one load per candidate, nested dynamic indices. Twin
// of sieve.crn and sieve.c; all three print the identical prime count.
#include <cstdint>
#include <cstdio>

static long long composite[65536];

int main() {
    long long count = 0;
    for (long long n = 2; n < 65536; n++) {
        if (composite[n] == 0) {
            count += 1;
            for (long long m = n * n; m < 65536; m += n) { composite[m] = 1; }
        }
    }
    printf("%lld\n", count);
    return 0;
}
