// Mandelbrot escape counts over four 960x540 frames in 2^21 fixed point: a multiply-heavy
// inner loop with a data-dependent exit and signed truncating division, none of which an
// optimizer may fold.
// Twin of mandelbrot.crn and mandelbrot.c; all three print the identical total.
fn main() {
    let mut total: i64 = 0;
    let mut frame: i64 = 0;
    while frame < 4 {
        let mut py: i64 = 0;
        while py < 540 {
            let y0: i64 = frame * 8192 + py * 24576 - 1572864;
            let mut px: i64 = 0;
            while px < 960 {
                let x0: i64 = px * 16384 - 4194304;
                let mut count: i64 = 0;
                let mut x: i64 = 0;
                let mut y: i64 = 0;
                let mut x2: i64 = 0;
                let mut y2: i64 = 0;
                while x2 + y2 <= 8388608 && count < 64 {
                    let next: i64 = x2 - y2 + x0;
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
    println!("{}", total);
}
