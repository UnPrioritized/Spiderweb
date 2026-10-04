"""Sharing shapes as text: copied shapes (piano roll) or a drawing (drawer) as one line that can be pasted anywhere
as text and pasted back into Spiderweb on another PC.

The line is "SPIDERWEB1:" + base64(zlib(JSON)). The 1 is the format's version: a newer one is told apart before
anything is unpacked. The JSON says what it holds: {"kind": "shapes", "shapes": [...]} (shapes as a project
file saves them, in beats, so PPQ / BPM don't matter) or {"kind": "drawing", "name": ..., "strokes": [...],
"areas": [...]} (as a saved library shape), plus "app": the Spiderweb version that made it (none = 1.4.0, the
first with sharing; another version than this one = a heads-up, made_by). zlib's own check number catches a line
that was cut off or changed. Everything unpacked goes through the same checks as a project file (clean_shape,
clean_strokes, clean_areas).
"""

import base64
import binascii
import json
import re
import zlib

from files.about import VERSION as APP
from files.project import short_shape
from notes.areas import clean_areas
from notes.custom import clean_strokes
from notes.engine import clean_shape, sane

VERSION = 1
# (spaces and line breaks inside are skipped: email and some sites wrap a long line; zlib's end mark tells where
# the line stops, so words right after it don't matter)
FIND = re.compile(r"SPIDERWEB(\d+):([A-Za-z0-9+/=\s]*)")
DATA = re.compile(r"[A-Za-z0-9+/]*={0,2}")  # (padding ends the line)
LONG_LINE = 2000  # past this many characters, some places may not take the whole line in one go


class ShareError(ValueError):
    """Why a line couldn't be used: "none" (no shared line in the text), "newer" (made by a newer Spiderweb),
    "broken" (cut off or changed)."""

    def __init__(self, why):
        super().__init__(why)
        self.why = why


def pack(body):
    """A JSON text -> the shared line."""
    raw = zlib.compress(body.encode("utf-8"), 9)
    return f"SPIDERWEB{VERSION}:" + base64.b64encode(raw).decode("ascii")


def unpack(text):
    """A text holding a shared line (anything around it is skipped) -> the unpacked dict. Raises ShareError."""
    found = FIND.search(text or "")
    if not found:
        raise ShareError("none")
    if int(found.group(1)) > VERSION:
        raise ShareError("newer")
    data = DATA.match(re.sub(r"\s+", "", found.group(2))).group()
    try:
        unzip = zlib.decompressobj()
        body = unzip.decompress(base64.b64decode(data[:len(data) // 4 * 4], validate=True))
        if not unzip.eof:  # cut off
            raise ValueError
        got = json.loads(body.decode("utf-8"))
    except (binascii.Error, zlib.error, UnicodeDecodeError, ValueError, RecursionError):
        raise ShareError("broken") from None
    if not isinstance(got, dict) or got.get("kind") not in ("shapes", "drawing"):
        raise ShareError("broken")
    return got


def made_by(got):
    """An unpacked line -> None if this Spiderweb made it (or can't tell), else ("older" / "newer", its version):
    things may have changed since, so it may come out a little different."""
    made = got.get("app") or "1.4.0"
    try:
        theirs, ours = (tuple(int(x) for x in str(v).split(".")) for v in (made, APP))
    except ValueError:
        return None
    return None if theirs == ours else ("older" if theirs < ours else "newer", str(made))


def shapes_line(shapes):
    """Shapes (as saved in a project) -> the shared line."""
    return pack(f'{{"kind": "shapes", "app": "{APP}", "shapes": [' + ", ".join(short_shape(sh) for sh in shapes)
                + "]}")


def read_shapes(got):
    """An unpacked "shapes" dict -> (the shapes, checked like a project file's, how many couldn't be read and were
    left out). Raises ShareError("broken") if none can be used."""
    out, left = [], 0
    for sh in got.get("shapes") if isinstance(got.get("shapes"), list) else ():
        try:
            sh = clean_shape(sh) if isinstance(sh, dict) else None
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            sh = None
        if sh:
            out.append(sh)
        else:
            left += 1
    if not out:
        raise ShareError("broken")
    return out, left


def drawing_line(name, strokes, areas=()):
    """A drawer drawing -> the shared line."""
    body = {"kind": "drawing", "app": APP, "name": name, "strokes": strokes}
    if areas:
        body["areas"] = [[round(u, 6), round(v, 6), c] for u, v, c in areas]
    return pack(json.dumps(body, separators=(",", ":")))


def read_drawing(got):
    """An unpacked "drawing" dict -> (name, strokes, areas), checked like a library shape. Raises
    ShareError("broken") if it has no usable strokes."""
    try:
        strokes = clean_strokes(got.get("strokes")) if sane(got) else None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        strokes = None
    if not strokes:
        raise ShareError("broken")
    return str(got.get("name") or ""), strokes, clean_areas(got.get("areas"))
