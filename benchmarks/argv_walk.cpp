// Argument-table read throughput, C++ twin of argv_walk.crn and argv_walk.c.
#include <cstdint>
#include <cstdio>
#include <cstring>

int main(int argc, char **argv) {
    (void)argc;
    const char *path = argv[0];
    const long long len = static_cast<long long>(strlen(path));
    long long total = 0;
    for (long long i = 0; i < 12800000; i++) {
        const long long idx = (i + total) & 63;
        const long long c = idx < len ? static_cast<unsigned char>(path[idx]) : -1;
        if ((c & 1) == 0) {
            total += c;
        } else {
            total += 1;
        }
    }
    printf("%lld\n", total);
    return 0;
}
