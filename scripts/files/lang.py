"""Every text Spiderweb shows comes from a language file: lang/en.json (English, the default), other languages as
lang/<code>.json (e.g. ko.json, zh-CN.json) with the same keys. A key missing from a language file is shown in
English, so a translation can be done bit by bit. The program asks for a text by its key: tr("join.button").

Texts with numbers in them have {name} places, e.g. "Split into {n} shapes": tr("...", n=3). A Help page is a list of
lines (an empty line = a new paragraph), so there are no "\\n" in the file.

Language files are looked for next to Spiderweb (a lang folder beside spiderweb.py or the .exe) first, then the ones
built in. There's no language picker yet (only English exists): LANGUAGE picks it."""

import json
import os
import sys

from files.about import HERE

LANGUAGE = "en"
BUILT_IN = os.path.join(getattr(sys, "_MEIPASS", os.path.join(HERE, "scripts")), "lang")
_texts = {}


def _load(code):
    for folder in (os.path.join(HERE, "lang"), BUILT_IN):
        path = os.path.join(folder, f"{code}.json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    got = json.load(f)
                return got if isinstance(got, dict) else {}
            except (OSError, ValueError):
                return {}
    return {}


def texts():
    if not _texts:
        _texts.update(_load("en"))
        if LANGUAGE != "en":
            _texts.update({k: v for k, v in _load(LANGUAGE).items() if isinstance(v, (str, list)) and v})
    return _texts


def tr(key_, **values):
    """The text for key in the current language (English if it's missing there; the key itself if it's missing
    everywhere), with {name} places filled in from values. A list (a Help page) comes back as lines."""
    text = texts().get(key_, key_)
    if isinstance(text, list):
        text = "\n".join(text)
    if values:
        try:
            text = text.format(**values)
        except (KeyError, IndexError, ValueError):
            pass  # (a translation with a broken {place}: shown as it is)
    return text
