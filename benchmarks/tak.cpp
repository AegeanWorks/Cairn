// Takeuchi tak(18, 12, 6): three-argument recursion with a data-dependent call order,
// a call-machinery stress neither optimizer may fold.
// Mechanical C++ twin of tak.c (same statements, C++ headers);
// twin of tak.crn, tak.cpp and tak.rs; all four print the identical result.
#include <stdint.h>
#include <stdio.h>

static int64_t tak(int64_t x, int64_t y, int64_t z) {
    if (y >= x) {
        return z;
    }
    return tak(tak(x - 1, y, z), tak(y - 1, z, x), tak(z - 1, x, y));
}

int main(void) {
    printf("%lld\n", (long long)tak(18, 12, 6));
    return 0;
}
