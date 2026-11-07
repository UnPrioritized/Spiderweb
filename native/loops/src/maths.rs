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

/// np.rint: to the nearest whole number, halves to the even one.
#[inline(always)]
pub fn rint(x: f64) -> f64 {
    x.round_ties_even()
}
