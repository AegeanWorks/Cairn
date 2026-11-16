// Argument-table read throughput: 12.8M byte reads of argv[0] whose index and
// both branches depend on the byte just read, so gcc cannot hoist the chain.
// The pointer and length bind once (Cairn's argv cache does the same once per
// process); each read pays the clamp a real per-call reader pays. Twin of
// argv_walk.crn, argv_walk.cpp and argv_walk.rs; the checksums differ per
// build path, so the harness compares times, not totals.
#include <stdint.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    (void)argc;
    const char *path = argv[0];
    long long len = (long long)strlen(argv[0]);
    long long total = 0;
    for (long long i = 0; i < 12800000; i++) {
        long long idx = (i + total) & 63;
        long long c = idx < len ? (unsigned char)path[idx] : -1;
        if ((c & 1) == 0) {
            total += c;
        } else {
            total += 1;
        }
    }
    printf("%lld\n", total);
    return 0;
}
