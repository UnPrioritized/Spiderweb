"""Every text Spiderweb shows comes from a language file: lang/en.json (English, the default), other languages as
lang/<code>.json (e.g. ko.json, zh-CN.json) with the same keys. A key missing from a language file is shown in
English, so a translation can be done bit by bit. The program asks for a text by its key: tr("join.button").

Texts with numbers in them have {name} places, e.g. "Split into {n} shapes": tr("...", n=3). A Help page is a list of
lines (an empty line = a new paragraph), so there are no "\\n" in the file.

Language files are looked for next to Spiderweb (a lang folder beside spiderweb.py or the .exe) first, then the ones
built in. Settings → Language picks one (user/settings.ini [language] code), put on at the next start; each file
names itself with its "language" text, e.g. "한국어 (Korean)"."""

import json
import os
import re
import sys

from files import settings
from files.about import HERE


BUILT_IN = os.path.join(getattr(sys, "_MEIPASS", os.path.join(HERE, "scripts")), "lang")
OWN = os.path.join(HERE, "lang")  # (beside spiderweb.py / the exe: translations added by users, looked at first)
CODE = r"[A-Za-z0-9][A-Za-z0-9_-]{0,19}"  # (a file name only, never a path; "_template" is never listed)
TEMPLATE = "_template.json"  # (Settings → Language's "Start a translation": a copy of English to fill in)
_texts = {}
problems = {}  # file name -> why it couldn't be read (the last time _load tried it)


def read_language():
    """The language picked in Settings → Language ("en" when none was, or it's not a usable code)."""
    code = str(settings.get("language", "code") or "").strip()
    return code if re.fullmatch(CODE, code) else "en"


def save_language(code):
    settings.put("language", "code", code)


def languages():
    """[(code, the name shown), ...] of every language file found, English first. The name is the file's own
    "language" text (e.g. "한국어 (Korean)"); two alike get their file name added; a file that can't be read is
    listed by its file name (problems says why)."""
    found = {}
    problems.clear()
    for folder in (OWN, BUILT_IN):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            code, ext = os.path.splitext(name)
            if ext.lower() == ".json" and re.fullmatch(CODE, code) and code not in found:
                own = _load(code).get("language")
                found[code] = own.strip() if isinstance(own, str) and own.strip() else code
    found.setdefault("en", "English")
    counts = {}
    for name in found.values():
        counts[name.casefold()] = counts.get(name.casefold(), 0) + 1
    shown = {code: f"{name} ({code})" if counts[name.casefold()] > 1 and name != code else name
             for code, name in found.items()}
    return sorted(shown.items(), key=lambda c: (c[0] != "en", c[1].casefold()))


def make_template():
    """A copy of the English texts in the lang folder beside Spiderweb, to fill in and rename (e.g. ko.json): its
    path. Raises OSError."""
    from files.safefile import write_text
    english = dict(_load("en"))
    english["language"] = ""
    os.makedirs(OWN, exist_ok=True)
    path = os.path.join(OWN, TEMPLATE)
    write_text(path, json.dumps(english, ensure_ascii=False, indent=1) + "\n")
    return path


# (the one in use; SPIDERWEB_LANG in the environment wins: the saved tests run in English whatever the user picked)
_forced = os.environ.get("SPIDERWEB_LANG") or ""
LANGUAGE = _forced if re.fullmatch(CODE, _forced) else read_language()


def _load(code):
    """The file's texts ({} when it can't be read: problems[file] says why). A "UTF-8 with BOM" file reads too."""
    for folder in (OWN, BUILT_IN):
        path = os.path.join(folder, f"{code}.json")
        if os.path.exists(path):
            problems.pop(f"{code}.json", None)
            try:
                with open(path, encoding="utf-8-sig") as f:
                    got = json.load(f)
            except json.JSONDecodeError as e:
                problems[f"{code}.json"] = ("line", e.lineno)
                return {}
            except (OSError, ValueError):
                problems[f"{code}.json"] = ("unreadable", 0)
                return {}
            return got if isinstance(got, dict) else {}
    return {}


def _usable(v):
    """A text, or a Help page = a list of texts (anything else in a translation: English is shown)."""
    return (isinstance(v, str) and v) or (isinstance(v, list) and v and all(isinstance(s, str) for s in v))


def texts():
    if not _texts:
        _texts.update(_load("en"))
        if LANGUAGE != "en":
            _texts.update({k: v for k, v in _load(LANGUAGE).items() if _usable(v)})
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
