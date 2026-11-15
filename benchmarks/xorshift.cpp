// Serial xorshift64 chain: a latency-dependent integer loop neither optimizer may fold.
// Mechanical C++ twin of xorshift.c (same statements, C++ headers);
// twin of xorshift.crn, xorshift.cpp and xorshift.rs; all four print the identical final state.
#include <stdint.h>
#include <stdio.h>

int main(void) {
    int64_t state = 88172645463325252;
    int64_t i = 0;
    while (i < 200000000) {
        state = state ^ (state << 13);
        state = state ^ (state >> 7);
        state = state ^ (state << 17);
        i = i + 1;
    }
    printf("%lld\n", (long long)state);
    return 0;
}
