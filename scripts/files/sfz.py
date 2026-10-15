"""The sample files an SFZ soundfont names, found the way the synth looks for them (only reading the text, no sound).

An SFZ is a text file: <region> sample=piano C4.wav lokey=60 ... A sample name may hold spaces (it runs up to the
next "opcode=" or the line's end), "\\" or "/", and is found from the SFZ's own folder plus the last
default_path. #include "other.sfz" reads another file in its place (its names are found from the first SFZ's folder
too), #define $NAME value swaps text, // and /* */ are comments. Names starting with * are built-in sounds (*sine)."""

import os
import re

_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_INCLUDE = re.compile(r'#include\s+"([^"]*)"')
_DEFINE = re.compile(r"#define\s+(\$\w+)[ \t]+([^\r\n]*)")
_OPCODE = re.compile(r"(?:(?<=[\s>])|^)([A-Za-z0-9_$]+)=|<\w+>")
_MAX_DEPTH = 20  # #include inside #include ... (a file including itself stops here)


def _read(path):
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def samples(path):
    """Every sample file the SFZ at path names (full paths, each once, in the order met). OSError if path itself
    can't be read; an #include that can't be read is listed too (in place of what it would name)."""
    folder = os.path.dirname(os.path.abspath(path))
    found, defines, state = {}, {}, {"default": ""}

    def walk(text, depth):
        text = _COMMENTS.sub("", text)
        pos = 0
        for m in _INCLUDE.finditer(text):  # (an included file sits where its line was)
            lines(text[pos:m.start()])
            pos = m.end()
            if depth < _MAX_DEPTH:
                inc = os.path.normpath(os.path.join(folder, _name(m.group(1), defines)))
                try:
                    walk(_read(inc), depth + 1)
                except OSError:  # (listed, so the check finds it missing: its keys are silent too)
                    found.setdefault(os.path.normcase(inc), inc)
        lines(text[pos:])

    def lines(text):
        for line in text.splitlines():
            d = _DEFINE.match(line.strip())
            if d:
                defines[d.group(1)] = d.group(2).strip()
                continue
            marks = list(_OPCODE.finditer(line))
            for a, b in zip(marks, marks[1:] + [None]):
                if not a.group(1):
                    continue
                key = a.group(1).lower()
                value = _name(line[a.end():b.start() if b else len(line)].strip(), defines)
                if key == "default_path":
                    state["default"] = value
                elif key == "sample" and value and not value.startswith("*"):
                    full = os.path.normpath(os.path.join(folder, state["default"] + value))
                    found.setdefault(os.path.normcase(full), full)

    walk(_read(path), 0)
    return list(found.values())


def _name(text, defines):
    """$NAME swapped for its #define (longest names first, so $A doesn't eat $AB); "\\" made this PC's folder mark."""
    for k in sorted(defines, key=len, reverse=True):
        text = text.replace(k, defines[k])
    return text.replace("\\", "/")
