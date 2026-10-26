"""Tracks the user added by hand (track list right-click: Add Track, Add Tracks…; user 2026-10-24, like Domino's).
app.own_tracks = [{"id", "name", "ch" 0-15, "after"}] in the order they show. "after" = how many of Spiderweb's
automatic tracks WITH NOTES (the slots, midi_out.track_slots; empty ones kept between channels don't count:
counted) come before it; -1 = after all of them (so is a number past them). The automatic tracks keep their slot
order around them. Saved in the project, undone with the shapes."""

NAME_MOST = 100  # letters in a track's name at most
HAND = 1 << 40   # the track list's id of own track n: HAND + n (slots are >= 0, a loaded MIDI's tracks < 0)
COUNT_MOST = 65534  # own tracks at most (a MIDI file's most tracks)
ID_MOST = 1 << 20  # ids below this (a shape's "into", engine.clean_shape; its notes' slot, engine.own_slot)
INTO_MOST = COUNT_MOST  # tracks one shape's colours go on at most (its "into": one per colour)


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
        if t.get("auto") is True:  # (added by Spiderweb for a shape's colours: App.fit_into)
            out[-1]["auto"] = True
    return out


def into_ids(sh):
    """The tracks a shape's colours were laid on, one per colour (Draw into a track, App.fit_into): a track added by
    hand's id (>= 0), or -1 - an automatic track's slot (pinned there)."""
    v = sh.get("into")
    return [v] if type(v) is int else list(v) if isinstance(v, list) else []


def next_channel(ch, use10):
    """The channel after ch for a track App.fit_into adds (10 skipped while it isn't used, 16 -> 1)."""
    ch = (ch + 1) % 16
    return ch + 1 if ch == 9 and not use10 else ch


def placed(own, autos):
    """Where each own track goes among autos automatic tracks: [(own track, automatic tracks before it)], in the
    order they show."""
    at = [(t, autos if t["after"] < 0 else min(t["after"], autos)) for t in own]
    return sorted(at, key=lambda p: p[1])  # (stable: the list's own order among equals)


def counted(slots, filled):
    """The places in slots an own track's "after" counts: only automatic tracks with notes (filled: the slots that
    have some; None = all), so empty ones kept between channels ("Keep empty tracks") never move it (hunt
    2026-10-26)."""
    if filled is None:
        return list(range(len(slots)))
    filled = {int(s) for s in filled}
    return [k for k, s in enumerate(slots) if s in filled]


def plan(slots, own, filled=None):
    """The tracks in the order they're written and listed: each one a slot (int) or an own track (its dict). An
    own track goes right after the "after"-th automatic track with notes (counted), -1 = after them all."""
    slots = [int(s) for s in slots]
    cuts = [0] + [k + 1 for k in counted(slots, filled)]
    out, k = [], 0
    for t, before in placed(own, len(cuts) - 1):
        at = len(slots) if t["after"] < 0 else cuts[before]
        out += slots[k:at]
        k = max(k, at)
        out.append(t)
    return out + slots[k:]


def new_id(own, floor=0):
    """The next track id: past every track there and never below floor (the project's ids so far, app.own_next:
    an id is never used twice, hunt 2026-10-26: a copy from a removed track landed in a new one with its id)."""
    return max(max((t["id"] for t in own), default=-1) + 1, floor)


def add_tracks(own, slots, row, chs, floor=0, filled=None):
    """own with one new track per channel in chs added just after row `row` of plan's order (None: at the end; -1:
    first of them all); (new list, new ids). floor: new_id's; slots, filled: plan's."""
    rows = plan(slots, own, filled)
    at = len(rows) if row is None else 0 if row < 0 else row + 1
    first = new_id(own, floor)
    new = [{"id": first + k, "name": "", "ch": ch, "after": -1} for k, ch in enumerate(chs)]
    return from_rows(rows[:at] + new + rows[at:], slots, own + new, filled), [t["id"] for t in new]


def move_rows(rows, moved, gap):
    """rows (plan's) with the ones at the places in moved taken out and put back together, in their order, where
    row gap was (gap = len(rows): at the end): the track list's drag (group C)."""
    keep = [r for k, r in enumerate(rows) if k not in moved]
    at = sum(1 for k in range(gap) if k not in moved)
    return keep[:at] + [rows[k] for k in sorted(moved)] + keep[at:]


def from_rows(rows, slots, own, filled=None):
    """The own tracks (own: as they are now, by id) in the order rows (plan's, maybe with tracks no longer there)
    has them, each "after" the automatic tracks with notes (plan's filled; None = all in slots) before it (-1 when
    no automatic track comes after it). Own tracks rows doesn't list go at the end, as they were."""
    live, by_id = {int(s) for s in slots}, {t["id"]: t for t in own}
    count = live if filled is None else live & {int(s) for s in filled}
    out, seen, listed = [], 0, set()
    later = [False] * len(rows)  # (an automatic track somewhere after this row)
    for k in range(len(rows) - 2, -1, -1):
        later[k] = later[k + 1] or (type(rows[k + 1]) is int and rows[k + 1] in live)
    for k, r in enumerate(rows):
        if type(r) is int:
            seen += r in count
        elif r["id"] in by_id and r["id"] not in listed:
            listed.add(r["id"])
            out.append(dict(by_id[r["id"]], after=seen if later[k] else -1))
    return out + [t for t in own if t["id"] not in listed]

