"""Image to notes: the picture colours as text for a MIDI player's settings ("Copy colours"), in a format the user
can shape (window/format_window.py), and colours read back out of any text ("Paste colours", read_colours).

A format: {"name", "lists": [{"start", "each", "end"}], "sep": line / comma / space / none / custom, "custom":
the custom text between colours (\\n \\t \\r \\\\ \\xNN \\uNNNN codes), "alpha": what {a} writes, "pad": write at
least this many colours (0 = just the picture's), "filler": the colour written for those extra ones}. Tags work in
"each" only: {n} {n0} {n:2} {hex} {HEX} {r} {g} {b} {a}; {{ and }} = a plain { and }. Lists are written one after
another, a line apart. The user's formats are kept in spiderweb/colour_formats.json (built-in ones can't change)."""

import json
import os
import re

from files.about import HERE
from files.lang import tr
from files.safefile import write_text

FORMATS_FILE = os.path.join(HERE, "colour_formats.json")
TAG = re.compile(r"\{\{|\}\}|\{[^{}\s]*\}?")
GOOD = re.compile(r"^\{(n|n0|n:\d|hex|HEX|r|g|b|a)\}$")
SEPS = {"line": "\n", "comma": ",", "space": " ", "none": ""}
CHANNELS_ALL = list(range(16))  # (0-based)


def built_in():
    """Spiderweb's own formats (named for what they are, not for any program)."""
    return [
        {"id": "rgba_channel", "name": tr("colour_list.fmt_rgba"), "sep": "line", "custom": "\\n", "alpha": "EE",
         "pad": 0, "filler": "000000",
         "lists": [{"start": "", "each": "Ch-{n:2}-NoteRGBA={HEX}{a}", "end": ""},
                   {"start": "\n", "each": "Ch-{n:2}-ActiveKeyColor={HEX}{a}", "end": ""}]},
        {"id": "xml_rgb", "name": tr("colour_list.fmt_xml"), "sep": "line", "custom": "\\n", "alpha": "FF",
         "pad": 16, "filler": "E7FF33",
         "lists": [{"start": "<Colors>\n", "each": '    <Color R="{r}" G="{g}" B="{b}" />', "end": "\n</Colors>"}]},
        {"id": "hex", "name": tr("colour_list.fmt_hex"), "sep": "line", "custom": "\\n", "alpha": "FF", "pad": 0,
         "filler": "000000", "lists": [{"start": "", "each": "#{HEX}", "end": ""}]},
    ]


def unescape(s):
    """The custom text between colours with its codes worked out -> (text, ok); a broken code = (s, False)."""
    try:
        return s.encode("latin-1", "backslashreplace").decode("unicode_escape"), True
    except UnicodeDecodeError:
        return s, False


def numbers(count, by, use10=False):
    """{n} of each colour: by "channel" = its MIDI channel number (10 left out unless use10), else 1, 2, 3..."""
    if by == "channel":
        chans = [c + 1 for c in CHANNELS_ALL if use10 or c != 9]
        return [chans[k] if k < len(chans) else k + 1 for k in range(count)]
    return list(range(1, count + 1))


def fill(tpl, n, hexs, alpha):
    """One colour's "each" -> [(text, ok)] pieces (ok False = an unknown tag, written as it is)."""
    out, pos = [], 0
    r, g, b = (int(hexs[i:i + 2], 16) for i in (0, 2, 4))
    for m in TAG.finditer(tpl):
        out.append((tpl[pos:m.start()], True))
        t = m.group()
        if t in ("{{", "}}"):
            out.append((t[0], True))
        elif not GOOD.match(t):
            out.append((t, False))
        else:
            k = t[1:-1]
            v = {"n": str(n), "n0": str(n - 1), "hex": hexs.lower(), "HEX": hexs.upper(), "r": str(r), "g": str(g),
                 "b": str(b), "a": alpha}.get(k)
            out.append((v if v is not None else str(n).zfill(int(k[2:])), True))
        pos = m.end()
    out.append((tpl[pos:], True))
    return out


def write(fmt, colours, by="channel", use10=False):
    """The colour list as [(text, ok)] pieces. colours = "RRGGBB" strings in slot order."""
    colours = list(colours) + [fmt.get("filler", "000000")] * max(0, int(fmt.get("pad", 0) or 0) - len(colours))
    nums = numbers(len(colours), by, use10)
    joiner = SEPS.get(fmt.get("sep")) if fmt.get("sep") != "custom" else unescape(fmt.get("custom", ""))[0]
    out = []
    for li, lst in enumerate(fmt["lists"]):
        if li:
            out.append(("\n", True))
        out.append((lst.get("start", ""), True))
        for k, h in enumerate(colours):
            if k:
                out.append((joiner or "", True))
            out += fill(lst.get("each", ""), nums[k], h, fmt.get("alpha", "FF"))
        out.append((lst.get("end", ""), True))
    return out


def text_of(pieces):
    return "".join(t for t, _ in pieces)


def unknown(pieces):
    return sum(1 for _, ok in pieces if not ok)


# ------------------------------------------------------------------ the user's formats (a file)

def clean_format(f):
    """A format from the file -> valid, or None."""
    if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not f["name"].strip():
        return None
    lists = []
    for lst in f.get("lists") or []:
        if isinstance(lst, dict) and all(isinstance(lst.get(k, ""), str) for k in ("start", "each", "end")):
            lists.append({k: lst.get(k, "") for k in ("start", "each", "end")})
    if not lists:
        return None
    pad = f.get("pad", 0)
    filler = f.get("filler", "000000")
    return {"name": f["name"].strip()[:100], "lists": lists[:8],
            "sep": f.get("sep") if f.get("sep") in list(SEPS) + ["custom"] else "line",
            "custom": f.get("custom", "") if isinstance(f.get("custom"), str) else "",
            "alpha": f.get("alpha", "FF") if isinstance(f.get("alpha"), str) else "FF",
            "pad": pad if type(pad) is int and 0 <= pad <= 256 else 0,
            "filler": filler if isinstance(filler, str) and re.fullmatch(r"[0-9A-Fa-f]{6}", filler) else "000000"}


def load_formats():
    """The user's formats ([] when there's no file or it can't be read)."""
    try:
        with open(FORMATS_FILE, encoding="utf-8") as f:
            items = json.load(f).get("formats", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [g for g in (clean_format(f) for f in items if isinstance(items, list)) if g]


def save_formats(items):
    """The user's formats -> the file. Raises OSError if it can't be written."""
    write_text(FORMATS_FILE, json.dumps({"formats": items}, indent=1, ensure_ascii=False))


# ------------------------------------------------------------------ Paste colours: colours read out of any text

OFF = re.compile(r"^\s*(;|//|#(?![0-9A-Fa-f]{6}\b))")  # a switched-off line (a "#ff8800" line is a colour)
RGB3 = re.compile(r'R\s*=\s*"?(\d{1,3})"?\s*,?\s*G\s*=\s*"?(\d{1,3})"?\s*,?\s*B\s*=\s*"?(\d{1,3})"?', re.I)
HEX = re.compile(r"(?<![0-9A-Za-z])#?([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?(?![0-9A-Za-z])")
NUM3 = re.compile(r"(?<![\d.])(\d{1,3})\s*[,; ]\s*(\d{1,3})\s*[,; ]\s*(\d{1,3})(?![\d.])")


def read_colours(text):
    """Colours in any text -> (lists, switched-off line numbers). lists: [(pattern, [(line, start, end, "RRGGBB",
    alpha or None, number or None)])], grouped by how their lines look (the line with its digits and colour taken
    out), in the order first seen; number = the first number before the colour on its line (a channel number)."""
    groups, skipped = {}, []
    for ln, line in enumerate(text.split("\n"), 1):
        if OFF.match(line):
            skipped.append(ln)
            continue
        for rx, kind in ((RGB3, "rgb"), (HEX, "hex"), (NUM3, "rgb")):
            m = rx.search(line)
            if not m:
                continue
            if kind == "hex":
                hexs, alpha = m.group(1).upper(), m.group(2)
            else:
                rgb = [int(v) for v in m.groups()]
                if max(rgb) > 255:
                    continue
                hexs, alpha = "%02X%02X%02X" % tuple(rgb), None
            num = re.search(r"\d+", line[:m.start()])
            key = re.sub(r"\d+", "..", (line[:m.start()] + "…" + line[m.end():]).strip())
            groups.setdefault(key, []).append((ln, m.start(), m.end(), hexs, alpha.upper() if alpha else None,
                                               int(num.group()) if num else None))
            break
    return list(groups.items()), skipped
