// Alloy probe: integer-only subset. No strings, no DI, no match, no pointers.
// Checked against Alloy SPEC §16; see ../alloy_probe README for the box walk.
function sum_to(n: i32): i32 {
    var total: i32 = 0;
    var i: i32 = 1;
    while i <= n {
        total = total + i;
        i = i + 1;
    }
    return total;
}

function main(): i32 {
    let total: i32 = sum_to(10);
    return total - 55;
}
