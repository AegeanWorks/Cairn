// Ackermann's function A(3, 9): millions of two-argument calls with data-dependent depth,
// a call-machinery stress neither optimizer may fold.
// Twin of ackermann.crn and ackermann.rs; all three print the identical result.
#include <stdint.h>
#include <stdio.h>

static int64_t ack(int64_t m, int64_t n) {
    if (m == 0) {
        return n + 1;
    }
    if (n == 0) {
        return ack(m - 1, 1);
    }
    return ack(m - 1, ack(m, n - 1));
}

int main(void) {
    printf("%lld\n", (long long)ack(3, 9));
    return 0;
}
