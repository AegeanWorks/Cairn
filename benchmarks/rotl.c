/* Portable twin of rotl.crn: rotate left with the standard shift twiddle. */
#include <stdint.h>
#include <stdio.h>

static uint64_t rotl64(uint64_t x, unsigned n) {
    n &= 63;
    return (x << n) | (x >> ((64 - n) & 63));
}

int main(void) {
    uint64_t total = 0;
    for (uint64_t i = 0; i < 10000000; i++) {
        uint64_t v = (i * 2654435761ULL) ^ (i >> 3);
        total = (total + rotl64(v, (unsigned)(i & 63))) & 0x7FFFFFFFFFFFFFFFULL;
    }
    printf("%llu\n", (unsigned long long)total);
    return 0;
}
