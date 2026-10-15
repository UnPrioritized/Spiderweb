"""The sample files an SFZ soundfont names, found the way the synth looks for them (only reading the text, no sound).

An SFZ is a text file: <region> sample=piano C4.wav lokey=60 ... A sample name may hold spaces (it runs up to the
next "opcode=" or the line's end), "\\" or "/", and is found from the first SFZ's folder plus the last
default_path set under <control>. #include "other.sfz" reads another file in its place, #define $NAME value swaps
text. Names starting with * are built-in sounds (*sine).

Each rule below copies what BASSMIDI does (measured, hunt 2026-10-16), so the check neither misses silent keys nor
warns about ones that play: opcode names in small letters only; a line starting with // or /* is a comment, but one
AFTER a name is part of the name (the synth then can't find it); $NAMEs swapped shortest first ($K before $KX);
the text read as UTF-8 only; a full path ignores default_path."""

import os
import re

_BLOCK = re.compile(r"^[ \t]*/\*.*?\*/", re.S | re.M)
_INCLUDE = re.compile(r'#include\s+"([^"]*)"')
_DEFINE = re.compile(r"#define\s+(\$\w+)[ \t]([^\r\n]*)")
_OPCODE = re.compile(r"(?:(?<=[\s>])|^)([A-Za-z0-9_$]+)=|<(\w+)>")
_SPACED = re.compile(r"^(?![ \t]*//)[^\n]*?(?:(?<=[\s>])|^)sample[ \t]+=", re.M)
_ABSOLUTE = re.compile(r"[A-Za-z]:|/")


class Stopped(Exception):
    """cancel() said stop."""


def _read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8-sig", "replace")  # (a name the synth can't read then matches no file)


def samples(path):
    return read(path)[0]


def read(path, cancel=None):
    """(files, spaced): every sample file the SFZ at path names (full paths, each once, in the order met; an
    #include that can't be read is listed too, its keys are silent as well), and whether a line says "sample ="
    with a space before "=" (BASSMIDI opens that, but freeing it later crashed: hunt 2026-10-16). OSError if path
    itself can't be read; Stopped when cancel() turns True."""
    folder = os.path.dirname(os.path.abspath(path))
    found, defines = {}, {}
    state = {"default": "", "header": "", "spaced": False}
    reading = []  # files open now, inner last (a file including itself is skipped)

    def walk(file, text):
        if cancel is not None and cancel():
            raise Stopped
        reading.append(os.path.normcase(file))
        text = _BLOCK.sub("", text)
        state["spaced"] = state["spaced"] or bool(_SPACED.search(text))
        pos = 0
        for m in _INCLUDE.finditer(text):  # (an included file sits where its line was)
            if text[text.rfind("\n", 0, m.start()) + 1:m.start()].lstrip().startswith("//"):
                continue  # (a // line is a comment: the synth skips its #include too)
            lines(text[pos:m.start()])
            pos = m.end()
            inc = os.path.normpath(os.path.join(folder, _name(m.group(1), defines)))
            if os.path.normcase(inc) in reading:
                continue
            try:
                inner = _read(inc)
            except OSError:
                found.setdefault(os.path.normcase(inc), inc)
                continue
            walk(inc, inner)
        lines(text[pos:])
        reading.pop()

    def lines(text):
        for line in text.splitlines():
            if line.lstrip().startswith("//"):
                continue
            d = _DEFINE.match(line.strip())
            if d:
                defines[d.group(1)] = d.group(2).split("//")[0].rstrip()
                continue
            marks = list(_OPCODE.finditer(line))
            for a, b in zip(marks, marks[1:] + [None]):
                if a.group(2):
                    state["header"] = a.group(2)
                    continue
                key = a.group(1)
                value = _name(line[a.end():b.start() if b else len(line)].strip(), defines)
                if key == "default_path" and state["header"] == "control":
                    state["default"] = value
                elif key == "sample" and value and not value.startswith("*"):
                    full = value if _ABSOLUTE.match(value) else state["default"] + value
                    full = os.path.normpath(os.path.join(folder, full))
                    found.setdefault(os.path.normcase(full), full)

    walk(path, _read(path))
    return list(found.values()), state["spaced"]


def _name(text, defines):
    """$NAME swapped for its #define (shortest names first, as the synth does: $K eats $KX); "\\" made "/"."""
    for k in sorted(defines, key=len):
        text = text.replace(k, defines[k])
    return text.replace("\\", "/")
