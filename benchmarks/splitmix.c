// One hundred million splitmix64 mixes: a serial multiply-latency chain over 64-bit
// wraparound constants, the opposite bottleneck of xorshift's shift-xor chain.
// Twin of splitmix.crn and splitmix.rs; all three print the identical total.
#include <stdint.h>
#include <stdio.h>

int main(void) {
    uint64_t x = 88172645463325252ULL;
    int64_t total = 0;
    int64_t i = 0;
    while (i < 100000000) {
        x += 0x9E3779B97F4A7C15ULL;
        uint64_t z = x;
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
        z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
        z = z ^ (z >> 31);
        total += (int64_t)((z >> 60) & 15);
        i += 1;
    }
    printf("%lld\n", (long long)total);
    return 0;
}
