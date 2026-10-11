"""Every text Spiderweb shows comes from a language folder: lang/en/ (English, the default), other languages as
lang/<code>/ (e.g. ko/, zh-CN/) with the same keys. Inside: any .json files (en has main.json, tools.json, hz.json;
which file holds a key doesn't matter) and help/<topic>.txt = the Help page help.<topic>.text, one line per line (an
empty line = a new paragraph). A key or file missing from a language is shown in English, so a translation can be
done bit by bit. An old one-file translation lang/<code>.json still reads. The program asks for a text by its key:
tr("join.button").

Texts with numbers in them have {name} places, e.g. "Split into {n} shapes": tr("...", n=3).

Languages are looked for next to Spiderweb (a lang folder beside spiderweb.py or the .exe) first, then the ones
built in. Settings → Language picks one (user/settings.ini [language] code), put on at the next start; each names
itself with its "language" text, e.g. "한국어 (Korean)"."""

import json
import os
import re
import sys

from files import settings
from files.about import HERE


BUILT_IN = os.path.join(getattr(sys, "_MEIPASS", os.path.join(HERE, "scripts")), "lang")
OWN = os.path.join(HERE, "lang")  # (beside spiderweb.py / the exe: translations added by users, looked at first)
CODE = r"[A-Za-z0-9][A-Za-z0-9_-]{0,19}"  # (a file / folder name only, never a path; "_template" is never listed)
TEMPLATE = "_template"  # (Settings → Language's "Start a translation": a copy of the English folder to fill in)
_texts = {}
problems = {}  # file name (e.g. "ko/main.json") -> why it couldn't be read (the last time _load tried it)


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
            if os.path.isdir(os.path.join(folder, name)):
                code, ext = name, ".json"
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
    """A copy of the English folder in the lang folder beside Spiderweb, to fill in and rename (e.g. ko): its path.
    Raises OSError."""
    from files.safefile import write_text
    source, is_folder = _where("en")
    if not is_folder:
        raise OSError("no English folder")
    path = os.path.join(OWN, TEMPLATE)
    for here, _, names in os.walk(source):
        to = os.path.join(path, os.path.relpath(here, source))
        os.makedirs(to, exist_ok=True)
        for name in names:
            if name.lower().endswith((".json", ".txt")):
                with open(os.path.join(here, name), encoding="utf-8-sig") as f:
                    text = f.read()
                if "language" in json_part(name, text):  # (the name line is for the translator to fill in)
                    part = json.loads(text)
                    part["language"] = ""
                    text = json.dumps(part, ensure_ascii=False, indent=1) + "\n"
                write_text(os.path.join(to, name), text)
    return path


def json_part(name, text):
    """A .json file's texts ({} for anything else or when it can't be read)."""
    if not name.lower().endswith(".json"):
        return {}
    try:
        got = json.loads(text)
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


# (the one in use; SPIDERWEB_LANG in the environment wins: the saved tests run in English whatever the user picked)
_forced = os.environ.get("SPIDERWEB_LANG") or ""
LANGUAGE = _forced if re.fullmatch(CODE, _forced) else read_language()


def _where(code):
    """(path, is a folder) of the language: its folder, else its one file; beside Spiderweb first. (None, False)
    when there's neither."""
    for folder in (OWN, BUILT_IN):
        path = os.path.join(folder, code)
        if os.path.isdir(path):
            return path, True
        if os.path.exists(path + ".json"):
            return path + ".json", False
    return None, False


def _read_json(path, shown):
    """One .json file's texts ({} when it can't be read: problems[shown] says why). "UTF-8 with BOM" reads too."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            got = json.load(f)
    except json.JSONDecodeError as e:
        problems[shown] = ("line", e.lineno)
        return {}
    except (OSError, ValueError):
        problems[shown] = ("unreadable", 0)
        return {}
    return got if isinstance(got, dict) else {}


def _read_page(path, shown):
    """A Help page .txt as its lines (None when it's empty or can't be read)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except (OSError, ValueError):
        problems[shown] = ("unreadable", 0)
        return None
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n") if text.strip() else None


def _load(code):
    """The language's texts: every .json in its folder + its help/*.txt pages (or its one old .json file)."""
    path, is_folder = _where(code)
    for name in [p for p in problems if p == f"{code}.json" or p.startswith(f"{code}/")]:
        del problems[name]
    if path is None:
        return {}
    if not is_folder:
        return _read_json(path, f"{code}.json")
    got = {}
    try:
        names = sorted(os.listdir(path))
    except OSError:
        names = []
    for name in names:
        if name.lower().endswith(".json") and os.path.isfile(os.path.join(path, name)):
            got.update(_read_json(os.path.join(path, name), f"{code}/{name}"))
    help_folder = os.path.join(path, "help")
    try:
        pages = sorted(os.listdir(help_folder))
    except OSError:
        pages = []
    for name in pages:
        topic, ext = os.path.splitext(name)
        if ext.lower() == ".txt":
            lines = _read_page(os.path.join(help_folder, name), f"{code}/help/{name}")
            if lines is not None:
                got[f"help.{topic}.text"] = lines
    return got


def _usable(v):
    """A text, or a Help page = a list of texts (anything else in a translation: English is shown)."""
    return (isinstance(v, str) and v) or (isinstance(v, list) and v and all(isinstance(s, str) for s in v))


def texts():
    if not _texts:
        _texts.update(_load("en"))
        if LANGUAGE != "en":
            _texts.update({k: v for k, v in _load(LANGUAGE).items() if _usable(v)})
    return _texts


def size(key_, usual):
    """A width a translation may change (pixels at 100 % scaling, e.g. "size.panel_width": "380" for longer
    words): the language's number, kept between half and three times the usual one; usual if it's not a number."""
    try:
        got = float(str(texts().get(key_, usual)).strip())
    except ValueError:
        return usual
    return min(max(got, usual / 2), usual * 3) if got == got else usual


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
