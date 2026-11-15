// Mandelbrot escape counts over four 960x540 frames in 2^21 fixed point: a multiply-heavy
// inner loop with a data-dependent exit and signed truncating division, none of which an
// optimizer may fold.
// Twin of mandelbrot.crn and mandelbrot.rs; all three print the identical total.
#include <stdint.h>
#include <stdio.h>

int main(void) {
    int64_t total = 0;
    int64_t frame = 0;
    while (frame < 4) {
        int64_t py = 0;
        while (py < 540) {
            int64_t y0 = frame * 8192 + py * 24576 - 1572864;
            int64_t px = 0;
            while (px < 960) {
                int64_t x0 = px * 16384 - 4194304;
                int64_t count = 0;
                int64_t x = 0;
                int64_t y = 0;
                int64_t x2 = 0;
                int64_t y2 = 0;
                while (x2 + y2 <= 8388608 && count < 64) {
                    int64_t next = x2 - y2 + x0;
                    y = x * y / 1048576 + y0;
                    x = next;
                    x2 = x * x;
                    y2 = y * y;
                    count += 1;
                }
                total += count;
                px += 1;
            }
            py += 1;
        }
        frame += 1;
    }
    printf("%lld\n", (long long)total);
    return 0;
}
