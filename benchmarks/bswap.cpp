// C++ twin of bswap.crn: the compiler's builtin byte-order reversal.
#include <cstdint>
#include <cstdio>

int main() {
    uint64_t total = 0;
    for (uint64_t i = 0; i < 10000000; i++) {
        uint64_t v = (i * 2654435761ULL) ^ (i >> 3);
        total = (total + __builtin_bswap64(v)) & 0x7FFFFFFFFFFFFFFFULL;
    }
    printf("%llu\n", (unsigned long long)total);
    return 0;
}
