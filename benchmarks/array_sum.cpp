// Array read-modify-write sweep: one 65536-element array filled, then 100
// read-modify-write passes over every element, then one summation pass. Twin
// of array_sum.crn and array_sum.c; all three print the identical total.
#include <cstdint>
#include <cstdio>

static long long a[65536];

int main() {
    for (long long i = 0; i < 65536; i++) { a[i] = i; }
    for (long long r = 0; r < 100; r++) {
        for (long long i = 0; i < 65536; i++) { a[i] += r; }
    }
    long long total = 0;
    for (long long i = 0; i < 65536; i++) { total += a[i]; }
    printf("%lld\n", total);
    return 0;
}
