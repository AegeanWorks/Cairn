#include <stdint.h>
#include <stdio.h>

int main(void) {
    int64_t total = 0;
    int64_t i = 0;
    while (i < 200000000) {
        total = total + i;
        i = i + 1;
    }
    printf("%lld\n", (long long)total);
    return 0;
}
