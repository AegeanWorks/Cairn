// N-queens solution counts by bitmask backtracking: recursion with several live locals per
// frame and a data-dependent scan loop neither optimizer may fold.
// Twin of nqueens.crn and nqueens.c; all three print the identical total.
fn queens(cols: i64, left: i64, right: i64) -> i64 {
    if cols == 4095 {
        return 1;
    }
    let free: i64 = 4095 ^ (cols | left | right);
    let mut count: i64 = 0;
    let mut bit: i64 = 1;
    while bit <= 2048 {
        if (free & bit) != 0 {
            count += queens(cols | bit, ((left | bit) << 1) & 4095, (right | bit) >> 1);
        }
        bit = bit << 1;
    }
    count
}

fn main() {
    println!("{}", queens(0, 0, 0));
}
