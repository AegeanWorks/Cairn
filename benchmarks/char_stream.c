// Stdin byte-stream throughput, C twin of char_stream.crn: one buffered
// getchar() loop over the harness's 4 MiB pipe.
#include <stdio.h>

int main(void) {
    long long total = 0;
    int c;
    while ((c = getchar()) != EOF) {
        total += c;
    }
    printf("%lld\n", total);
    return 0;
}
