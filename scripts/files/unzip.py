"""Unpacking zlib data that came from outside (a shared line, pasted notes in a project or shared line): never more
than FLOOR + GROW x its packed size, so a tiny line made to unpack into gigabytes (a "zip bomb") can't fill the
memory. Measured on real data (2026-10-20): shared lines grow 4-218x but stay a few MB (many alike shapes), pasted
notes grow 1-3x (51x only for millions of identical notes); a zip bomb grows 770-1000x."""

import zlib

FLOOR = 64 * 2 ** 20
GROW = 10


class TooBig(ValueError):
    """The data would unpack bigger than unzip allows."""


def unzip(raw):
    """zlib bytes -> the unpacked bytes. Raises TooBig past the limit, zlib.error when damaged or cut off. Bytes
    after the end mark are skipped."""
    most = FLOOR + GROW * len(raw)
    stream = zlib.decompressobj()
    out = stream.decompress(raw, most + 1)  # (stops as soon as it's past the limit: the rest is never made)
    if len(out) > most:
        raise TooBig
    if not stream.eof:
        raise zlib.error("cut off")
    return out
