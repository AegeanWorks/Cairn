// String-literal byte scan, C++ twin of str_scan.crn: the same 200,000 passes,
// one volatile byte load per byte, so no load folds away.
#include <cstdio>

static volatile const char text[] =
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789+-*/";

int main() {
    long long len = 0;
    while (text[len]) {
        len += 1;
    }
    long long total = 0;
    long long r = 0;
    while (r < 200000) {
        long long j = 0;
        while (j < len) {
            total += text[j];
            j += 1;
        }
        r += 1;
    }
    printf("%lld\n", total);
    return 0;
}
