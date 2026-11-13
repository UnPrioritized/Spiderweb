"""Small MIDI byte helpers shared by the writers (export, Rendered ahead pieces, playback). No imports on purpose:
any files/ module can use it without import loops."""


def vlq(n):
    """A wait / length as a MIDI variable-length number: 7 bits a byte, highest first, top bit = more follow.
    A negative n is written as 0 (it would never end the loop below)."""
    if n < 0:
        n = 0
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))
