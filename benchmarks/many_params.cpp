// Six-argument call throughput: each iteration passes four arguments in registers and two
// on the stack. The loop bound comes from argc so no compiler can fold the whole program
// into one print (ADR-071, ADR-074); run bare, argc is 1 and the bound is exactly
// 10,000,000. The masked product keeps the mix a live recurrence rather than a closed form,
// and the total stays below 2^57 so no twin overflows.
// Twin of many_params.crn, many_params.c and many_params.rs; all four print the identical
// total.
#include <cstdint>
#include <iostream>

int64_t mix(int64_t a, int64_t b, int64_t c, int64_t d, int64_t e, int64_t f) {
    return ((a * b) & 65535) ^ (c + d * 43 + e * 47 + f * 53);
}

int main(int argc, char **argv) {
    (void)argv;
    int64_t n = 10000000 + argc - 1;
    int64_t i = 0;
    int64_t total = 0;
    while (i < n) {
        total = total + mix(i, i + 1, i + 2, i + 3, i + 4, i + 5);
        i = i + 1;
    }
    std::cout << total << "\n";
    return 0;
}
