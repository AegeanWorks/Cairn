#include <stdint.h>
#include <stdio.h>

int main(void) {
    int64_t i = 0;
    int64_t acc = 0;
    while (i < 20000000) {
        acc = acc + (2 * 3 + 4 * 5 - 6 / 2);
        acc = acc ^ (1 << 4) | 3;
        i = i + 1;
    }
    printf("%lld\n", (long long)acc);
    return 0;
}
