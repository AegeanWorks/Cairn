// Hex formatting throughput: every iteration prints one value in lowercase hex plus a
// newline, flushed once per value. Twin of hex_io.crn, hex_io.c and hex_io.rs; all four
// print the identical checksum.
#include <cstdint>
#include <iostream>

int main() {
    std::ios::sync_with_stdio(false);
    std::cout << std::hex;
    std::uint64_t checksum = 0;
    for (std::uint64_t i = 0; i < 400000; i++) {
        std::uint64_t value = i * 2654435761ULL;
        std::cout << value << '\n' << std::flush;
        checksum ^= value;
    }
    std::cout << std::dec << checksum << '\n';
    return 0;
}
