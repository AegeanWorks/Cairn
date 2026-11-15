// Six-argument call throughput: each iteration passes four arguments in registers and two
// on the stack, multiplied and accumulated in a body no optimizer can fold. The total
// stays below 2^57, so no twin overflows.
// Twin of many_params.crn, many_params.c and many_params.rs; all four print the
// identical total.
#include <cstdint>
#include <iostream>

int64_t mix(int64_t a, int64_t b, int64_t c, int64_t d, int64_t e, int64_t f) {
    return a * 31 + b * 37 + c * 41 + d * 43 + e * 47 + f * 53;
}

int main() {
    int64_t i = 0;
    int64_t total = 0;
    while (i < 10000000) {
        total = total + mix(i, i + 1, i + 2, i + 3, i + 4, i + 5);
        i = i + 1;
    }
    std::cout << total << "\n";
    return 0;
}
