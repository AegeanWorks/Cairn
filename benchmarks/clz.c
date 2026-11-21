/* Portable twin of clz.crn: count leading zeros with a shift loop. */
#include <stdint.h>
#include <stdio.h>

static int leading_zeros(uint64_t x) {
    if (x == 0) return 64;
    int n = 0;
    while ((x >> 63) == 0) { x <<= 1; n++; }
    return n;
}

int main(void) {
    uint64_t total = 0;
    for (uint64_t i = 0; i < 10000000; i++) {
        uint64_t v = (i * 2654435761ULL) ^ (i >> 3);
        total += (uint64_t)leading_zeros(v);
    }
    printf("%llu\n", (unsigned long long)total);
    return 0;
}
