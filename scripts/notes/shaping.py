"""Joined letters + right-to-left order for the text tool (Arabic, Persian, Urdu, Hebrew; Hindi, Thai... joined).
Two optional packages: uharfbuzz (joins letters, reads font files) and python-bidi (Unicode's right-to-left rules).
Without them the text tool draws as before: letters one by one, left to right (available() is False).
Loaded only when first asked for (never at start)."""
import re

_state = {}


def _load():
    if "ok" not in _state:
        try:
            import uharfbuzz
            from bidi.bidi import get_display_inner
            _state.update(ok=True, hb=uharfbuzz, display=get_display_inner)
        except Exception:  # (not installed / broken copy: today's drawing)
            _state["ok"] = False
    return _state["ok"]


def available():
    """Both packages load here."""
    return _load()


_LEVEL = re.compile(r"Level\(\s*(\d+),?\s*\)")


def levels(text, base=None):
    """Each letter's Unicode direction level (even = left to right, odd = right to left), from python-bidi's own
    Rust rules (current Unicode, bracket pairs included). base = "L" / "R" forces the line's direction, None = from
    its first strong letter. Its Rust part only shows levels in its debug text (one per UTF-8 byte): read from
    there; if that text ever changes shape, the package's older Python rules give them (no bracket pairs)."""
    if not text or not _load():
        return [0] * len(text)
    try:
        info = _state["display"](text, base, True)
        part = info[info.index("levels:"):info.index("paragraphs:")]
        per_byte = [int(n) for n in _LEVEL.findall(part)]
        out, at = [], 0
        for ch in text:
            out.append(per_byte[at])
            at += len(ch.encode("utf-8"))
        if at == len(per_byte):
            return out
    except (ValueError, IndexError):
        pass
    return _python_levels(text, base)


def _python_levels(text, base):
    from bidi import algorithm as alg
    st = alg.get_empty_storage()
    lv = alg.get_base_level(text) if base is None else alg.PARAGRAPH_LEVELS[base]
    st["base_level"], st["base_dir"] = lv, ("L", "R")[lv]
    alg.get_embedding_levels(text, st)
    for step in (alg.explicit_embed_and_overrides, alg.resolve_weak_types, alg.resolve_neutral_types,
                 alg.resolve_implicit_levels):
        step(st, False)
    return [c["level"] for c in st["chars"]]


def visual_order(lv):
    """Unicode rule L2: typed-order indexes in the order they show, left to right (reverse every run at or above
    each level, from the highest down to the lowest odd one)."""
    order = list(range(len(lv)))
    odd = [n for n in lv if n % 2]
    if not odd:
        return order
    for level in range(max(lv), min(odd) - 1, -1):
        i = 0
        while i < len(order):
            if lv[order[i]] < level:
                i += 1
                continue
            j = i
            while j < len(order) and lv[order[j]] >= level:
                j += 1
            order[i:j] = order[i:j][::-1]
            i = j
    return order
