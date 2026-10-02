"""Sharing shapes as text: copied shapes (piano roll) or a drawing (drawer) as one line that can be pasted into a
chat and pasted back into Spiderweb on another PC.

The line is "SPIDERWEB1:" + base64(zlib(JSON)). The 1 is the format's version: a newer one is told apart before
anything is unpacked. The JSON says what it holds: {"kind": "shapes", "shapes": [...]} (shapes as a project
file saves them, in beats, so PPQ / BPM don't matter) or {"kind": "drawing", "name": ..., "strokes": [...],
"areas": [...]} (as a saved library shape). zlib's own check number catches a line that was cut off or changed.
Everything unpacked goes through the same checks as a project file (clean_shape, clean_strokes, clean_areas).
"""

import base64
import binascii
import json
import re
import zlib

from files.project import short_shape
from notes.areas import clean_areas
from notes.custom import clean_strokes
from notes.engine import clean_shape

VERSION = 1
FIND = re.compile(r"SPIDERWEB(\d+):([A-Za-z0-9+/=]*)")
CHAT_LIMIT = 2000  # characters in one message on common chat apps


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
    try:
        got = json.loads(zlib.decompress(base64.b64decode(found.group(2), validate=True)).decode("utf-8"))
    except (binascii.Error, zlib.error, UnicodeDecodeError, ValueError):
        raise ShareError("broken") from None
    if not isinstance(got, dict) or got.get("kind") not in ("shapes", "drawing"):
        raise ShareError("broken")
    return got


def shapes_line(shapes):
    """Shapes (as saved in a project) -> the shared line."""
    return pack('{"kind": "shapes", "shapes": [' + ", ".join(short_shape(sh) for sh in shapes) + "]}")


def read_shapes(got):
    """An unpacked "shapes" dict -> the shapes, checked like a project file's. Raises ShareError("broken") if
    none can be used."""
    out = []
    for sh in got.get("shapes") if isinstance(got.get("shapes"), list) else ():
        try:
            sh = clean_shape(sh) if isinstance(sh, dict) else None
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            sh = None
        if sh:
            out.append(sh)
    if not out:
        raise ShareError("broken")
    return out


def drawing_line(name, strokes, areas=()):
    """A drawer drawing -> the shared line."""
    body = {"kind": "drawing", "name": name, "strokes": strokes}
    if areas:
        body["areas"] = [[round(u, 6), round(v, 6), c] for u, v, c in areas]
    return pack(json.dumps(body, separators=(",", ":")))


def read_drawing(got):
    """An unpacked "drawing" dict -> (name, strokes, areas), checked like a library shape. Raises
    ShareError("broken") if it has no usable strokes."""
    try:
        strokes = clean_strokes(got.get("strokes"))
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        strokes = None
    if not strokes:
        raise ShareError("broken")
    return str(got.get("name") or ""), strokes, clean_areas(got.get("areas"))
