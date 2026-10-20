"""Unpacking zlib data that came from outside (a shared line, pasted notes in a project or shared line): never more
than FLOOR + GROW x its packed size, so a tiny line made to unpack into gigabytes (a "zip bomb") can't fill the
memory. Measured on real data (2026-10-20): shared lines grow 4-218x but stay a few MB (many alike shapes), pasted
notes grow 1-3x (51x only for millions of identical notes); a zip bomb grows 770-1000x.

Many pieces each under their own limit (a "non-recursive" bomb: 40 pasted-notes shapes in a 0.1 MB line made
2.7 GB) are stopped by an allowance: everything unpacked inside `with allowance(size)` on this thread shares
FLOOR + GROW x size (the whole line's / file's)."""

import threading
import zlib
from contextlib import contextmanager

FLOOR = 64 * 2 ** 20
GROW = 10
_local = threading.local()  # (notes made in a background thread meanwhile never count against a paste)


class TooBig(ValueError):
    """The data would unpack bigger than unzip allows."""


class Allowance:
    """What's left to unpack (bytes); over = something was refused for it; seen = keys already counted (packed notes
    unpacked again once they were forgotten aren't counted twice)."""

    def __init__(self, size):
        self.left, self.over, self.seen = FLOOR + GROW * size, False, set()


@contextmanager
def allowance(size):
    """Everything unzip unpacks on this thread inside the with shares FLOOR + GROW x size bytes. Gives the
    Allowance (its over says whether anything was refused)."""
    kept = getattr(_local, "now", None)
    _local.now = Allowance(size)
    try:
        yield _local.now
    finally:
        _local.now = kept


def unzip(raw, key=None):
    """zlib bytes -> the unpacked bytes. Raises TooBig past the limit (and past what's left of the allowance in
    use; key = what it is, counted once), zlib.error when damaged or cut off. Bytes after the end mark are
    skipped."""
    share = getattr(_local, "now", None)
    if share is not None and key is not None and key in share.seen:
        share = None
    most = FLOOR + GROW * len(raw) if share is None else min(FLOOR + GROW * len(raw), share.left)
    stream = zlib.decompressobj()
    out = stream.decompress(raw, most + 1)  # (stops as soon as it's past the limit: the rest is never made)
    if len(out) > most:
        if share is not None:
            share.over = True
        raise TooBig
    if not stream.eof:
        raise zlib.error("cut off")
    if share is not None:
        share.left -= len(out)
        if key is not None:
            share.seen.add(key)
    return out
