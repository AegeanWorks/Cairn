// Array read-modify-write sweep: one 65536-element array filled, then 100
// read-modify-write passes over every element, then one summation pass. Twin
// of array_sum.crn and array_sum.c; all three print the identical total.
fn main() {
    let mut a = vec![0i64; 65536];
    for i in 0..65536i64 { a[i as usize] = i; }
    for r in 0..100i64 {
        for i in 0..65536i64 { a[i as usize] += r; }
    }
    let mut total: i64 = 0;
    for i in 0..65536i64 { total += a[i as usize]; }
    println!("{}", total);
}
