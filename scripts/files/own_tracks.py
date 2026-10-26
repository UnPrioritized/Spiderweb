"""Tracks the user added by hand (track list right-click: Add Track, Add Tracks…; user 2026-10-24, like Domino's).
app.own_tracks = [{"id", "name", "ch" 0-15, "after"}] in the order they show. "after" = how many of Spiderweb's
automatic tracks (the slots, midi_out.track_slots) come before it; -1 = after all of them (so is a number past
them). The automatic tracks keep their slot order around them. Saved in the project, undone with the shapes."""

NAME_MOST = 100  # letters in a track's name at most
HAND = 1 << 40   # the track list's id of own track n: HAND + n (slots are >= 0, a loaded MIDI's tracks < 0)
COUNT_MOST = 65534  # own tracks at most (a MIDI file's most tracks)
ID_MOST = 1 << 20  # ids below this (a shape's "into", engine.clean_shape; its notes' slot, engine.own_slot)


def clean_name(text):
    """A track name as kept: no line breaks / hidden characters, NAME_MOST letters at most."""
    return "".join(c for c in str(text) if c.isprintable())[:NAME_MOST]


def clean_own_tracks(raw):
    """A project's "own_tracks" as read: anything that isn't a good track is left out (ids unique)."""
    out, seen = [], set()
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, dict) or len(out) >= COUNT_MOST:
            continue
        i, ch, after = t.get("id"), t.get("ch"), t.get("after", -1)
        if (type(i) is not int or not 0 <= i < ID_MOST or i in seen or type(ch) is not int or not 0 <= ch <= 15
                or type(after) is not int or after < -1 or after > 1 << 30):
            continue
        seen.add(i)
        out.append({"id": i, "name": clean_name(t.get("name", "")) if isinstance(t.get("name", ""), str) else "",
                    "ch": ch, "after": after})
    return out


def placed(own, autos):
    """Where each own track goes among autos automatic tracks: [(own track, automatic tracks before it)], in the
    order they show."""
    at = [(t, autos if t["after"] < 0 else min(t["after"], autos)) for t in own]
    return sorted(at, key=lambda p: p[1])  # (stable: the list's own order among equals)


def plan(slots, own):
    """The tracks in the order they're written and listed: each one a slot (int) or an own track (its dict)."""
    slots = [int(s) for s in slots]
    out, k = [], 0
    for t, before in placed(own, len(slots)):
        out += slots[k:before]
        k = before
        out.append(t)
    return out + slots[k:]


def new_id(own):
    return max((t["id"] for t in own), default=-1) + 1


def insert_at(own, autos, row):
    """(list index, "after") for tracks added just after row `row` of Spiderweb's own tracks in plan's order (None:
    at the end; -1: first of them all). own in placed order; autos = how many automatic tracks there are."""
    if row is None:
        return len(own), -1
    if row < 0:
        return 0, 0
    at = placed(own, autos)
    befores = [b for _, b in at]
    hands = seen = 0  # (own / automatic tracks up to the row)
    for _ in range(row + 1):
        if hands < len(at) and befores[hands] <= seen:
            hands += 1
            last = ("own", hands - 1)
        else:
            seen += 1
            last = ("auto", seen)
    if last[0] == "own":  # (right after that own track, in its group)
        return last[1] + 1, own[last[1]]["after"]
    return sum(1 for b in befores if b < last[1]), last[1]  # (before the own tracks already after that one)


def add_tracks(own, autos, row, chs):
    """own with one new track per channel in chs added after own row `row` (insert_at); (new list, new ids)."""
    out = [dict(t) for t in placed_order(own, autos)]
    at, after = insert_at(out, autos, row)
    ids, first = [], new_id(out)
    for k, ch in enumerate(chs):
        out.insert(at + k, {"id": first + k, "name": "", "ch": ch, "after": after})
        ids.append(first + k)
    return out, ids


def placed_order(own, autos):
    """own sorted the way it shows (placed): kept so, adding next to a track is a plain insert."""
    return [t for t, _ in placed(own, autos)]
