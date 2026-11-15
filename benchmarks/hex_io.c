// Hex formatting throughput: every iteration prints one value in lowercase hex plus a
// newline, one unbuffered stdio call per value. Twin of hex_io.crn, hex_io.cpp and
// hex_io.rs; all four print the identical checksum.
#include <stdint.h>
#include <stdio.h>

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    uint64_t checksum = 0;
    for (uint64_t i = 0; i < 400000; i++) {
        uint64_t value = i * 2654435761ULL;
        printf("%llx\n", (unsigned long long)value);
        checksum ^= value;
    }
    printf("%llu\n", (unsigned long long)checksum);
    return 0;
}
