// Sieve of Eratosthenes over 0..65536 with a flagged element array: one
// store per composite, one load per candidate, nested dynamic indices. Twin
// of sieve.crn and sieve.rs; all three print the identical prime count.
#include <stdint.h>
#include <stdio.h>

static int64_t composite[65536];

int main(void) {
    int64_t count = 0;
    for (int64_t n = 2; n < 65536; n++) {
        if (composite[n] == 0) {
            count += 1;
            for (int64_t m = n * n; m < 65536; m += n) { composite[m] = 1; }
        }
    }
    printf("%lld\n", (long long)count);
    return 0;
}
