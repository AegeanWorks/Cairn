// Serial xorshift64 chain: a latency-dependent integer loop neither optimizer may fold.
// Twin of xorshift.crn and xorshift.rs; all three print the identical final state.
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
