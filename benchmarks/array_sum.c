// Array read-modify-write sweep: one 65536-element array filled, then 100
// read-modify-write passes over every element, then one summation pass. Twin
// of array_sum.crn and array_sum.rs; all three print the identical total.
#include <stdint.h>
#include <stdio.h>

static int64_t a[65536];

int main(void) {
    for (int64_t i = 0; i < 65536; i++) { a[i] = i; }
    for (int64_t r = 0; r < 100; r++) {
        for (int64_t i = 0; i < 65536; i++) { a[i] += r; }
    }
    int64_t total = 0;
    for (int64_t i = 0; i < 65536; i++) { total += a[i]; }
    printf("%lld\n", (long long)total);
    return 0;
}
