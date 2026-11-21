// C++ twin of clz.crn: the compiler's count-leading-zeros builtin.
#include <cstdint>
#include <cstdio>

int main() {
    uint64_t total = 0;
    for (uint64_t i = 0; i < 10000000; i++) {
        uint64_t v = (i * 2654435761ULL) ^ (i >> 3);
        total += (uint64_t)__builtin_clzll(v);
    }
    printf("%llu\n", (unsigned long long)total);
    return 0;
}
