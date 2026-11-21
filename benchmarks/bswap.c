/* Portable twin of bswap.crn: reverse the eight bytes with a shift loop. */
#include <stdint.h>
#include <stdio.h>

static uint64_t swap64(uint64_t x) {
    uint64_t r = 0;
    for (int k = 0; k < 8; k++) {
        r = (r << 8) | (x & 0xFFULL);
        x >>= 8;
    }
    return r;
}

int main(void) {
    uint64_t total = 0;
    for (uint64_t i = 0; i < 10000000; i++) {
        uint64_t v = (i * 2654435761ULL) ^ (i >> 3);
        total = (total + swap64(v)) & 0x7FFFFFFFFFFFFFFFULL;
    }
    printf("%llu\n", (unsigned long long)total);
    return 0;
}
