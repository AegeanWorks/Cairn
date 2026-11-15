// Binary GCD (Stein's algorithm) over one million generated pairs: subtraction and shift
// loops with a swap, no division. Twin of binary_gcd.crn and binary_gcd.rs; all three print
// the identical total.
#include <stdint.h>
#include <stdio.h>

static int64_t bgcd(int64_t a, int64_t b) {
    if (a == 0) { return b; }
    if (b == 0) { return a; }
    int64_t k = 0;
    while ((a & 1) == 0 && (b & 1) == 0) {
        a = a >> 1;
        b = b >> 1;
        k = k + 1;
    }
    while ((a & 1) == 0) { a = a >> 1; }
    while ((b & 1) == 0) { b = b >> 1; }
    while (b != 0) {
        if (a > b) {
            int64_t t = a; a = b; b = t;
        }
        b = b - a;
        if (b == 0) { break; }
        while ((b & 1) == 0) { b = b >> 1; }
    }
    return a << k;
}

int main(void) {
    int64_t i = 1;
    int64_t total = 0;
    while (i <= 1000000) {
        int64_t a = (i * 48271) % 2147483647;
        int64_t b = ((i * 1103515245) % 2147483647) >> 8;
        if (b == 0) { b = 3; }
        total += bgcd(a, b + 1);
        i = i + 1;
    }
    printf("%lld\n", (long long)total);
    return 0;
}
