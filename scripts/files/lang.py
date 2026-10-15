"""Every text Spiderweb shows comes from a language folder: lang/en/ (English, the default), other languages as
lang/<code>/ (e.g. ko/, zh-CN/) with the same keys. Inside: any .json files (en has main.json, tools.json, hz.json;
which file holds a key doesn't matter) and help/<topic>.txt = the Help page help.<topic>.text, one line per line (an
empty line = a new paragraph). A key or file missing from a language is shown in English, so a translation can be
done bit by bit. An old one-file translation lang/<code>.json still reads. The program asks for a text by its key:
tr("join.button").

Texts with numbers in them have {name} places, e.g. "Split into {n} shapes": tr("...", n=3). A text whose word
changes for one thing has a second key ending "_one" ("Split into {n} shape"): trn("...", 1, n=1) picks it.

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
DEVICES = re.compile(r"(con|prn|aux|nul|com\d|lpt\d)", re.I)  # (Windows device names: never a file)
TEMPLATE = "_template"  # (Settings → Language's "Start a translation": a copy of the English folder to fill in)
COPIED = "_english.json"  # (in a template: the English texts as copied, so ones left untranslated follow English)
_texts = {}
_translated = set()  # (keys the language in use has itself: trn)
problems = {}  # file name (e.g. "ko/main.json") -> why it couldn't be read (the last time _load tried it)


def usable_code(code):
    return bool(re.fullmatch(CODE, code)) and not DEVICES.fullmatch(code)


def read_language():
    """The language picked in Settings → Language ("en" when none was, or it's not a usable code)."""
    code = str(settings.get("language", "code") or "").strip()
    return code if usable_code(code) else "en"


def save_language(code):
    settings.put("language", "code", code)


def languages():
    """[(code, the name shown), ...] of every language file found, English first. The name is the file's own
    "language" text (e.g. "한국어 (Korean)"); two alike get their file name added; a file that can't be read is
    listed by its file name (problems says why); one beside Spiderweb with a name that can't be used is said too."""
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
            if ext.lower() != ".json" or code == TEMPLATE:
                continue
            if not usable_code(code):
                if folder == OWN:
                    problems[name] = ("name", 0)
                continue
            if code not in found:
                own = _load(code).get("language")
                found[code] = own.strip() if isinstance(own, str) and own.strip() else code
    found.setdefault("en", "English")
    counts = {}
    for name in found.values():
        counts[name.casefold()] = counts.get(name.casefold(), 0) + 1
    shown = {code: f"{name} ({code})" if counts[name.casefold()] > 1 and name != code else name
             for code, name in found.items()}
    return sorted(shown.items(), key=lambda c: (c[0] != "en", c[1].casefold()))


def template_path():
    return os.path.join(OWN, TEMPLATE)


def make_template():
    """A copy of the built-in English folder in the lang folder beside Spiderweb, to fill in and rename (e.g. ko):
    its path. One made before is replaced (Settings asks first). The "language" line is left empty for the
    translator, the size lines out (they're for when longer words need them), and _english.json remembers the
    English as copied: a text still the same then follows English after an update. Raises OSError."""
    import shutil
    from files.safefile import write_text
    source = os.path.join(BUILT_IN, "en")
    if not os.path.isdir(source):
        raise OSError("no English folder")
    path = template_path()
    if os.path.isdir(path):
        shutil.rmtree(path)
    copied = {}
    for here, _, names in os.walk(source):
        to = os.path.join(path, os.path.relpath(here, source))
        os.makedirs(to, exist_ok=True)
        for name in sorted(names):
            if name.lower().endswith(".json"):
                part = _read_json(os.path.join(here, name), "en/" + name)
                part = {k: v for k, v in part.items() if not k.startswith("size.")}
                if "language" in part:
                    part["language"] = ""
                copied.update(part)
                write_text(os.path.join(to, name), json.dumps(part, ensure_ascii=False, indent=1) + "\n")
            elif name.lower().endswith(".txt"):
                with open(os.path.join(here, name), encoding="utf-8-sig") as f:
                    text = f.read()
                lines = _page_lines(text)
                if lines is not None and os.path.basename(here) == "help":
                    copied[f"help.{os.path.splitext(name)[0].lower()}.text"] = lines
                write_text(os.path.join(to, name), text)
    copied.pop("language", None)
    write_text(os.path.join(path, COPIED), json.dumps(copied, ensure_ascii=False) + "\n")
    return path


# (the one in use; SPIDERWEB_LANG in the environment wins: the saved tests run in English whatever the user picked)
_forced = os.environ.get("SPIDERWEB_LANG") or ""
LANGUAGE = _forced if re.fullmatch(CODE, _forced) else read_language()


def _where(folder, code):
    """(path, is a folder) of the language in one place: its folder, else its one file. (None, False) when there's
    neither."""
    path = os.path.join(folder, code)
    if os.path.isdir(path):
        return path, True
    if os.path.isfile(path + ".json"):
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
    if not isinstance(got, dict):
        problems[shown] = ("unreadable", 0)
        return {}
    return got


def _page_lines(text):
    """A Help page's text as its lines (None when it's empty)."""
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n") if text.strip() else None


def _read_page(path, shown):
    """A Help page .txt as its lines (None when it's empty or can't be read)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return _page_lines(f.read())
    except (OSError, ValueError):
        problems[shown] = ("unreadable", 0)
        return None


def _load_at(folder, code):
    """The language's texts in one place: every .json in its folder + its help/*.txt pages (or its one old .json
    file). Texts still the same as the English its template copied (_english.json) are left out."""
    path, is_folder = _where(folder, code)
    if path is None:
        return {}
    if not is_folder:
        return _read_json(path, f"{code}.json")
    got = {}
    try:
        names = sorted(os.listdir(path))
    except OSError:
        problems[code] = ("unreadable", 0)
        names = []
    for name in names:
        if name.lower().endswith(".json") and not name.startswith("_") and os.path.isfile(os.path.join(path, name)):
            got.update(_read_json(os.path.join(path, name), f"{code}/{name}"))
    help_folder = os.path.join(path, "help")
    try:
        pages = sorted(os.listdir(help_folder)) if os.path.isdir(help_folder) else []
    except OSError:
        problems[f"{code}/help"] = ("unreadable", 0)
        pages = []
    for name in pages:
        topic, ext = os.path.splitext(name)
        if ext.lower() == ".txt":
            lines = _read_page(os.path.join(help_folder, name), f"{code}/help/{name}")
            if lines is not None:
                got[f"help.{topic.lower()}.text"] = lines
    if os.path.isfile(os.path.join(path, COPIED)):
        copied = _read_json(os.path.join(path, COPIED), f"{code}/{COPIED}")
        got = {k: v for k, v in got.items() if k not in copied or copied[k] != v}
    return got


def _load(code):
    """The language's texts: the built-in ones, then the ones beside Spiderweb on top (each text by itself, so a
    small folder there only changes what it holds)."""
    for name in [p for p in problems if p in (code, f"{code}.json") or p.startswith(f"{code}/")]:
        del problems[name]
    got = _load_at(BUILT_IN, code) if os.path.abspath(BUILT_IN) != os.path.abspath(OWN) else {}
    got.update({k: v for k, v in _load_at(OWN, code).items() if _usable(k, v)})
    return got


def _usable(k, v):
    """A text, or a Help page = a list of texts, or a number for a size key (anything else in a translation:
    English is shown)."""
    if k.startswith("size.") and isinstance(v, (int, float)) and not isinstance(v, bool):
        return True
    return (isinstance(v, str) and v) or (isinstance(v, list) and v and all(isinstance(s, str) for s in v))


def texts():
    if not _texts:
        _texts.update(_load("en"))
        if LANGUAGE != "en":
            own = {k: v for k, v in _load(LANGUAGE).items() if _usable(k, v)}
            _texts.update(own)
            _translated.update(own)
    return _texts


def size(key_, usual):
    """A width a translation may change (pixels at 100 % scaling, e.g. "size.panel_width": "380" for longer
    words): the language's number, kept between half and three times the usual one; usual if it's not a number."""
    try:
        got = float(str(texts().get(key_, usual)).strip())
    except ValueError:
        return usual
    return min(max(got, usual / 2), usual * 3) if got == got else usual


_english = {}  # (the built-in English: english_width)
_only_english = []  # (not empty while english_width makes its names: tr gives the built-in English)


def english_width(make):
    """How many letters wide a box must be for the texts make() gives IN ENGLISH (+1): the synth window's boxes
    keep their English size, so a longer translation gets smaller text there instead of moving the knobs."""
    if not _english:
        _english.update(_load_at(BUILT_IN, "en"))
    _only_english.append(True)
    try:
        return max(len(n) for n in make()) + 1
    finally:
        _only_english.pop()


def tr(key_, **values):
    """The text for key in the current language (English if it's missing there; the key itself if it's missing
    everywhere), with {name} places filled in from values. A list (a Help page) comes back as lines."""
    text = (_english if _only_english else texts()).get(key_, key_)
    if isinstance(text, list):
        text = "\n".join(text)
    if values and not ODD_PLACE.search(text):
        try:
            text = text.format(**values)
        except Exception:  # (a translation with a broken {place}: shown as it is)
            pass
    return text


def trn(key_, count_, **values):
    """tr for a text holding a count: key + "_one" when the count is exactly 1. A translation that has the key but
    not its "_one" keeps its own key (never half English)."""
    one = key_ + "_one"
    found = _english if _only_english else texts()
    if count_ != 1 or one not in found or (key_ in _translated and one not in _translated and not _only_english):
        return tr(key_, **values)
    return tr(one, **values)


def trnum(key_, place, text, **values):
    """trn for a number already written as text ("+1", "1,000") put in {place}: "_one" when it's 1 or -1."""
    try:
        count = abs(float(str(text).replace(",", "")))
    except ValueError:
        count = None
    return trn(key_, count, **{place: text}, **values)


def plural(n, one, many):
    """A word for n things (keys): one when n is exactly 1."""
    return tr(one) if n == 1 else tr(many)


ODD_PLACE = re.compile(r"(?<!\{)\{[^{}:!]*[.\[]")  # ({name.x} / {name[0]}: never filled in, shown as typed)
