// Stdin byte-stream throughput, C++ twin of char_stream.crn.
#include <cstdio>

int main() {
    long long total = 0;
    int c;
    while ((c = getchar()) != EOF) {
        total += c;
    }
    printf("%lld\n", total);
    return 0;
}
