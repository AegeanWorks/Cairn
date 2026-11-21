/* Portable twin of popcount.crn: Brian Kernighan's clear-lowest-bit loop. */
#include <stdint.h>
#include <stdio.h>

static int bits(uint64_t x) {
    int count = 0;
    while (x) { x &= x - 1; count++; }
    return count;
}

int main(void) {
    uint64_t total = 0;
    for (uint64_t i = 0; i < 10000000; i++) {
        uint64_t v = (i * 2654435761ULL) ^ (i >> 3);
        total += (uint64_t)bits(v);
    }
    printf("%llu\n", (unsigned long long)total);
    return 0;
}
