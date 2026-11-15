// A three-argument function on the hot path: every call walks the argument-passing path
// with non-pure operands (a shift and a mask), inside a data-dependent loop. Twin of
// call_chain.crn and call_chain.rs; all three print the identical total.
#include <stdint.h>
#include <stdio.h>

static int64_t mix3(int64_t a, int64_t b, int64_t c) {
    int64_t t = (a * 3) ^ (b << 2);
    if (t > c) {
        t = t - c;
    } else {
        t = t + c;
    }
    return t;
}

int main(void) {
    int64_t x = 1;
    int64_t i = 0;
    int64_t total = 0;
    while (i < 20000000) {
        x = mix3(x, i >> 2, i & 65535);
        total += x & 255;
        i = i + 1;
    }
    printf("%lld\n", (long long)total);
    return 0;
}
