// N-queens solution counts by bitmask backtracking: recursion with several live locals per
// frame and a data-dependent scan loop neither optimizer may fold.
// Twin of nqueens.crn and nqueens.rs; all three print the identical total.
#include <stdint.h>
#include <stdio.h>

int64_t queens(int64_t cols, int64_t left, int64_t right) {
    if (cols == 4095) {
        return 1;
    }
    int64_t free = 4095 ^ (cols | left | right);
    int64_t count = 0;
    int64_t bit = 1;
    while (bit <= 2048) {
        if ((free & bit) != 0) {
            count += queens(cols | bit, ((left | bit) << 1) & 4095, (right | bit) >> 1);
        }
        bit = bit << 1;
    }
    return count;
}

int main(void) {
    printf("%lld\n", (long long)queens(0, 0, 0));
    return 0;
}
