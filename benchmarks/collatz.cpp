// Collatz total stopping times for every probe below a constant bound: a branchy, division
// heavy loop with data-dependent control flow neither optimizer may fold.
// Mechanical C++ twin of collatz.c (same statements, C++ headers);
// twin of collatz.crn, collatz.cpp and collatz.rs; all four print the identical total.
#include <stdint.h>
#include <stdio.h>

int main(void) {
    int64_t n = 1;
    int64_t k = 0;
    int64_t steps = 0;
    int64_t total = 0;
    while (n < 1000000) {
        k = n;
        steps = 0;
        while (k > 1) {
            if (k % 2 == 0) {
                k = k / 2;
            } else {
                k = 3 * k + 1;
            }
            steps = steps + 1;
        }
        total = total + steps;
        n = n + 1;
    }
    printf("%lld\n", (long long)total);
    return 0;
}
