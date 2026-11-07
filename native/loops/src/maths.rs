//! Python / NumPy's own rules for the small sums the loops share, so the numbers come out the same to the bit.

/// Python's max(a, b): a unless b is bigger.
#[inline(always)]
pub fn pmax(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python's min(a, b): a unless b is smaller.
#[inline(always)]
pub fn pmin(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// np.mod for floats (the sign of b, as NumPy's divmod).
#[inline(always)]
pub fn npmod(a: f64, b: f64) -> f64 {
    let mut m = a % b; // (C's fmod)
    if b == 0.0 {
        return m;
    }
    if m != 0.0 {
        if (b < 0.0) != (m < 0.0) {
            m += b;
        }
    } else {
        m = 0.0f64.copysign(b);
    }
    m
}

/// Numba's int(x) / np.int64(x) of a float as this processor does it (cut toward 0; NaN or out of range = the
/// lowest whole number, what x86-64's conversion gives; Rust's own `as` would give the nearest end instead).
#[inline(always)]
pub fn f2i(x: f64) -> i64 {
    if (-9223372036854775808.0..9223372036854775808.0).contains(&x) {
        x as i64
    } else {
        i64::MIN
    }
}

/// Python's a // b for whole numbers (rounded down, not toward 0).
#[inline(always)]
pub fn fdiv(a: i64, b: i64) -> i64 {
    let q = a / b;
    if a % b != 0 && ((a < 0) != (b < 0)) {
        q - 1
    } else {
        q
    }
}

/// Python's a % b for whole numbers (the sign of b).
#[inline(always)]
pub fn fmodi(a: i64, b: i64) -> i64 {
    let r = a % b;
    if r != 0 && ((r < 0) != (b < 0)) {
        r + b
    } else {
        r
    }
}

/// Python's min / max for whole numbers.
#[inline(always)]
pub fn imin(a: i64, b: i64) -> i64 {
    if b < a {
        b
    } else {
        a
    }
}
#[inline(always)]
pub fn imax(a: i64, b: i64) -> i64 {
    if b > a {
        b
    } else {
        a
    }
}

/// np.rint: to the nearest whole number, halves to the even one.
#[inline(always)]
pub fn rint(x: f64) -> f64 {
    x.round_ties_even()
}
