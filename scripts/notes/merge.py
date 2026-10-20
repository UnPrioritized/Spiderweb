"""Gate sensitive merge (user, 2026-10-08, first try: plain notes): two shapes' notes become one. The shape that
stays keeps its notes; each key row of the other one slides sideways in time on its own until its notes meet the
staying shape's note on that key (gate end = next start), lengths kept. Rows with no note of the staying shape on
their key ("leftovers") slide by the smallest slide a row made and are handed back apart (user: their own shape,
to delete or keep).

"Keep shapes separate" (the merge window's tick, user 2026-10-19; any number of shapes): the others stay as they are,
the one that moves becomes fixed notes remembering the shape it was (sh["unmerge"]): Split gives it back
(unmerged); its leftovers are a "Merge leftovers" shape of their own. Also up / down (up_down_plan)."""

import copy
import json
import math

import numpy as np


def comes_from_left(stay, slide):
    """True if slide's middle is before stay's."""
    return np.asarray(slide)[:, :2].mean() < np.asarray(stay)[:, :2].mean()


def gate_merge(stay, slide, from_left=None, rest_slides=True):
    """stay, slide: (start, end, pitch, velocity[, track]) rows in ticks. Returns (stay + slide's rows that met,
    slide's leftover rows), or None when no row meets. Each row comes in from far away on that side (from_left;
    None = the side slide's middle is on), so it meets the staying shape's outer edge there: a row inside a hollow
    shape or overlapping it goes out to that edge. rest_slides: the leftover rows slide by the smallest slide (off:
    they stay where they are)."""
    got = _merge(stay, slide, from_left, rest_slides)
    return got and got[:2]


def _merge(stay, slide, from_left, rest_slides=True):
    """gate_merge, and also the smallest slide, how much later everything went (to stay after tick 0) and each met
    key row's slide {key: ticks}. stay a list (keep shapes separate, several shapes): each shape on its own; one wholly
    behind the row on its key (all its notes there end before the row starts: right; left: start after it ends)
    is left out (hunt 2026-10-19: rows slid backwards behind it)."""
    shapes = None
    if isinstance(stay, list):
        shapes = []
        for s in stay:
            s = np.asarray(s, np.int64).reshape(len(s), -1)
            s = s[np.argsort(s[:, 2], kind="stable")]
            shapes.append((s, s[:, 2]))
        stay = np.concatenate([s for s, _ in shapes]) if shapes else np.zeros((0, 4), np.int64)
    stay = np.asarray(stay, np.int64)
    moved = np.array(slide, np.int64, copy=True)
    if not len(stay) or not len(moved):
        return None
    if from_left is None:
        from_left = comes_from_left(stay, moved)
    met = np.zeros(len(moved), bool)
    shifts, by_key, room = [], {}, True
    for key in np.unique(moved[:, 2]):
        rows = moved[:, 2] == key
        behind = []
        if shapes is None:
            here = stay[stay[:, 2] == key]
        else:
            got = []
            for s, keys in shapes:
                h = s[np.searchsorted(keys, key):np.searchsorted(keys, key, "right")]
                if len(h) and (h[:, 1].max() > moved[rows, 0].min() if from_left
                               else h[:, 0].min() < moved[rows, 1].max()):
                    got.append(h)
                elif len(h):
                    behind.append(h)
            here = np.concatenate(got) if got else stay[:0]
        if not len(here):
            continue
        if from_left:  # its last note's end meets the staying shape's first note on this key
            shift = here[:, 0].min() - moved[rows, 1].max()
        else:  # its first note's start meets the staying shape's last note's end
            shift = here[:, 1].max() - moved[rows, 0].min()
        if behind:  # (slid back onto a shape left out as wholly behind: it can't go through it, audit)
            b = np.concatenate(behind)
            room = room and bool(moved[rows, 0].min() + shift >= b[:, 1].max() if from_left
                                 else moved[rows, 1].max() + shift <= b[:, 0].min())
        moved[rows, :2] += shift
        met |= rows
        shifts.append(int(shift))
        by_key[int(key)] = int(shift)
    if not shifts:
        return None
    least = min(shifts, key=abs)
    if rest_slides:
        moved[~met, :2] += least
    out, rest = np.concatenate([stay, moved[met]]), moved[~met]
    first = min(out[:, 0].min(), rest[:, 0].min() if len(rest) else 0)
    late = max(0, -int(first))
    if late:  # (slid before the song's start: everything waits)
        out[:, :2] += late
        rest[:, :2] += late
    return out, rest, least, late, by_key, room


def side_plan(stays, slide, right):
    """How Merge to right / left (keep shapes separate) slides the slide rows (start, end, pitch, ...) onto the stays
    (a list: each shape's rows): (each met key row's slide {key: ticks}, the smallest slide (the rows with nothing to
    meet), how much later everything goes to stay after tick 0, False when a row would go onto a shape left out as
    behind it), or None when no row meets."""
    got = _merge(list(stays), slide, right)
    return got and (got[4], got[2], got[3], got[5])


def mover(parts, way, skip=None):
    """Which of several shapes' (start, end, pitch, ...) rows moves in a Gate sensitive merge "way" (right / left /
    up / down), not one in skip: right = the earliest by middle time, left = the latest, up = the one with the lowest
    note, down = the highest (ties: by middle key); None when all are skipped. (A Merged notes made this way before
    moves again first: JoinSplit.merge_mover.)"""
    rows = [np.asarray(p, np.int64).reshape(len(p), -1) for p in parts]
    ks = [k for k in range(len(rows)) if k not in (skip or ())]
    if not ks:
        return None
    if way in ("right", "left"):
        mid = {k: rows[k][:, :2].mean() for k in ks}
        return (min if way == "right" else max)(ks, key=mid.get)
    if way == "up":
        return min(ks, key=lambda k: (rows[k][:, 2].min(), rows[k][:, 2].mean()))
    return max(ks, key=lambda k: (rows[k][:, 2].max(), rows[k][:, 2].mean()))


def spread(first, end, vals, count, low):
    """Per time piece 0 .. count-1: the lowest (low; else highest) of vals over the notes sounding through pieces
    first .. end-1 (a tree of halves: each note touches ~2 log(count) parts, not every piece; None where nothing
    sounds = 1 << 40 / -1)."""
    empty = (1 << 40) if low else -1
    size = 1 << max(1, (count - 1).bit_length())
    tree = np.full(2 * size, empty, np.int64)
    put = np.minimum.at if low else np.maximum.at
    lo, hi, vals = first + size, end + size, np.asarray(vals, np.int64)
    while len(lo):
        keep = lo < hi
        if keep.sum() * 2 < len(keep):  # (done ones dropped now and then: dropping costs as much as a step)
            lo, hi, vals, keep = lo[keep], hi[keep], vals[keep], keep[keep]
        odd = keep & (lo & 1).astype(bool)
        put(tree, lo[odd], vals[odd])
        lo += odd
        odd = keep & (hi & 1).astype(bool)
        hi -= odd
        put(tree, hi[odd], vals[odd])
        lo >>= 1
        hi >>= 1
    pick = np.minimum if low else np.maximum
    at = 1
    while at < size:  # (each part's value down to its halves)
        top = tree[at:2 * at]
        tree[2 * at:4 * at:2] = pick(tree[2 * at:4 * at:2], top)
        tree[2 * at + 1:4 * at:2] = pick(tree[2 * at + 1:4 * at:2], top)
        at *= 2
    return tree[size:size + count]


def up_down_plan(stays, rows, up, top):
    """Merge up / down (user 2026-10-19, from their pictures): rows (start, end, pitch, ...) are cut where the stays'
    (a list: each shape's rows) notes and its own start and end; each time piece slides as a block until its highest note is
    right under the LOWEST note sounding then of the shapes not wholly below it (down: mirrored; the outer edge,
    like left / right's rows: a line inside a hollow shape goes out under it; a shape wholly below is left out, hunt);
    nothing in its way = up to key top (down to key 0), a leftover. None when no piece meets them; else a plan for
    up_down_rows: "moved" False when no piece moves, "room" False when a piece can't go under (key 0 / top in the
    way), "slid" = the smallest slide of the pieces that met (keys, + = up)."""
    stays = [np.asarray(s, np.int64).reshape(len(s), -1) for s in stays]
    stays = [s for s in stays if len(s)]
    rows = np.asarray(rows, np.int64).reshape(len(rows), -1)
    if not stays or not len(rows):
        return None
    rk = rows[:, 2] if up else top - rows[:, 2]  # (down = up, upside down)
    # (its own note ends cut too, user 2026-10-20: a shorter bottom row left the rest hanging a key above)
    cuts = np.unique(np.concatenate([s[:, :2].ravel() for s in stays] + [rows[:, :2].ravel()]))
    count = len(cuts) + 1  # (time pieces: piece t between cuts t - 1 and t)

    def sounding(notes):  # (each note sounds through time pieces first .. end - 1)
        return np.searchsorted(cuts, notes[:, 0], "right"), np.searchsorted(cuts, notes[:, 1], "left") + 1
    first, end = sounding(rows)
    high, least = spread(first, end, rk, count, False), spread(first, end, rk, count, True)
    far = 1 << 40
    low = np.full(count, far, np.int64)
    under = np.full(count, -1, np.int64)  # (the highest note of the shapes left out)
    for s in stays:  # (per shape: left out where all its notes are below the piece's lowest)
        sk, at = (s[:, 2] if up else top - s[:, 2]), sounding(s)
        s_low, s_high = spread(*at, sk, count, True), spread(*at, sk, count, False)
        low = np.where(s_high >= least, np.minimum(low, s_low), low)
        under = np.where(s_high >= least, under, np.maximum(under, s_high))
    used = high >= 0
    met = used & (low < far)
    if not met.any():
        return None
    move = np.where(met, low - 1 - high, top - high)
    # (under it would be past key 0, or onto a shape left out as wholly below: it can't go through it, audit)
    room = not (used & ((move < -least) | (least + move <= under))).any()
    move = np.where(used, np.maximum(move, -least), 0)
    wall = used & ~met
    code = move * 2 + wall
    hit = move[met]
    return {"cuts": cuts, "change": cuts[np.flatnonzero(code[1:] != code[:-1])], "move": move, "wall": wall,
            "up": up, "moved": bool((move[used] != 0).any()), "room": room,
            "slid": int(hit[np.argmin(np.abs(hit))]) * (1 if up else -1)}


def up_down_rows(plan, rows):
    """up_down_plan's rows (the same notes, any more columns after pitch) cut where the slide changes and slid ->
    (rows, True where a row is a leftover)."""
    rows = np.asarray(rows, np.int64).reshape(len(rows), -1)
    cuts = plan["change"]  # (notes are only cut where the slide changes)
    lo = np.searchsorted(cuts, rows[:, 0], "right")  # (cuts strictly inside each note: lo .. hi - 1)
    hi = np.searchsorted(cuts, rows[:, 1], "left")
    n = np.maximum(hi - lo, 0) + 1
    src = np.repeat(np.arange(len(rows)), n)
    j = np.arange(len(src)) - np.repeat(np.cumsum(n) - n, n)  # (piece number in its note)
    last = max(len(cuts) - 1, 0)
    out = rows[src].copy()
    if len(cuts):
        out[:, 0] = np.where(j == 0, out[:, 0], cuts[np.clip(lo[src] + j - 1, 0, last)])
        out[:, 1] = np.where(j == n[src] - 1, out[:, 1], cuts[np.clip(lo[src] + j, 0, last)])
    at = np.searchsorted(plan["cuts"], out[:, 0], "right")
    out[:, 2] += plan["move"][at] if plan["up"] else -plan["move"][at]
    return out, plan["wall"][at]


def slid_up_down(stay, rows, up, top):
    """up_down_plan + up_down_rows in one: (rows slid, True if a piece had nothing in its way) or None."""
    plan = up_down_plan([stay], rows, up, top)
    if plan is None:
        return None
    out, wall = up_down_rows(plan, rows)
    return out, bool(wall.any())


def merge_parts(sh):
    """The shapes kept inside a Gate sensitive merge: its recipe's (sh["merge"]) or the one its notes Split back
    into (sh["unmerge"])."""
    return [p for key in ("merge", "unmerge") if isinstance(sh.get(key), dict)
            for p in sh[key].get("parts") or () if isinstance(p, dict)]


def clean_unmerge(u, clean_shape):
    """A two-shapes merge's memory from a file (sh["unmerge"]: "parts" = the shape Split gives back as it was when
    merged, "box" = the notes' box then, "slid" = its smallest slide in beats, "kslid" = in keys (up / down),
    "vrev" = flipped sideways an odd number of times since, "way" = right / left / up / down (merged that way
    again: it moves again), "id" = its "Merge leftovers" shape's "leftover_of") -> valid, or None."""
    try:
        parts = [clean_shape(p) for p in u["parts"] if isinstance(p, dict)]
        box = [[float(b), float(p)] for b, p in u["box"]]
    except (KeyError, TypeError, ValueError):
        return None
    if not parts or not all(parts) or len(box) != 3 or not all(math.isfinite(x) for pt in box for x in pt):
        return None
    out = {"parts": parts, "box": box}
    for k in ("slid", "kslid"):
        if isinstance(u.get(k), (int, float)) and math.isfinite(u[k]):
            out[k] = float(u[k])
    if u.get("way") in ("right", "left", "up", "down"):
        out["way"] = u["way"]
    if isinstance(u.get("id"), str):
        out["id"] = u["id"][:64]
    if u.get("vrev") is True:
        out["vrev"] = True
    return out


def low_corner(box):
    """A box's (3 corners: the 4th = 1 + 2 - 0) earliest time and lowest key, as an array."""
    b = np.asarray(box, float)
    return np.vstack([b, b[1] + b[2] - b[0]]).min(axis=0)


def unmerged(sh):
    """Split of a two-shapes merge's notes: the shape they were made from, moved / turned / slanted / stretched /
    flipped the way the notes' box was since (a shape that can't be turned that way: only moved, as far as the
    box's lowest, earliest corner). Moved, flipped, stretched: where it was before it slid, carried along (user
    2026-10-19, one rule); turned / slanted: round where its notes are (time no longer runs along time); up / down
    flipped upside down: where its notes are too."""
    u = sh["unmerge"]
    parts = copy.deepcopy(u["parts"])
    got = box_map(u["box"], sh["pts"])
    if got is None:
        return parts
    L, o = got
    if not np.allclose(L, np.eye(2), atol=1e-9) or u.get("vrev"):
        level = abs(L[0, 1]) < 1e-9 and abs(L[1, 0]) < 1e-9  # (time still along time: flips / stretches)
        d = 0.0 if level else u.get("slid", 0.0)  # (turned: first as far as it slid, the smallest slide)
        # (up / down: also turned, or flipped upside down: else it lands off the piano roll, hunt)
        dk = 0.0 if level and L[1, 1] > 0 else u.get("kslid", 0.0)
        turned = [mapped_part(p, lambda b, k: [float(x) for x in L @ (b + d, k + dk) + o], L, bool(u.get("vrev")))
                  for p in parts]
        if all(q is not None for q in turned):
            return turned
    db, dp = (float(x) for x in low_corner(sh["pts"]) - low_corner(u["box"]))  # (a flip moves the first corner)
    for p in parts:
        p["pts"] = [[b + db, k + dp] for b, k in p["pts"]]
    return parts


def preview_parts(sh, ppq, keys):
    """The merged shape's shapes placed where their notes are, for the outline gate's preview line: [(shape, how far
    each key row of it slid in beats {key: beats} or None)]; the sliding one per row, roughly (a line crossing
    rows goes with the row at its middle). [] = can't tell."""
    m = sh["merge"]
    if m.get("way") in ("up", "down"):  # (no key row slides sideways)
        return []
    got = reshaped_parts(sh)
    if got:
        parts, slide, from_left, (L, o, least) = got
        rows = part_rows(parts, ppq, keys)
        upright = abs(L[0, 0]) <= 1e-9 * max(1.0, abs(L[1, 0]))
        done = None if upright else _merge(rows[1 - slide], rows[slide], from_left, rest_slides=False)
        slid = {k: v / ppq for k, v in done[4].items()} if done else {}  # (rows not met: where they are)
        return [(p, slid if i == slide else None) for i, p in enumerate(parts)]
    sides = made_sides(m)
    if not sides:
        return []
    slide, from_left, least, late = sides
    a, b = part_rows(m["parts"], m["ppq"], m["keys"])
    done = _merge((a, b)[1 - slide], (a, b)[slide], from_left)
    at = m.get("at") or sh["pts"][0]
    db, dp = sh["pts"][0][0] - at[0] + late / m["ppq"], sh["pts"][0][1] - at[1]
    out = []
    for i, p in enumerate(m["parts"]):
        q = mapped_part(p, lambda b, k: [b + db, k + dp])
        if q is None:
            continue
        if i == slide:  # (every row of it: met = its own slide, the rest the smallest)
            ks = np.unique((a, b)[slide][:, 2])
            out.append((q, {int(k) + round(dp): done[4].get(int(k), least) / m["ppq"] for k in ks}))
        else:
            out.append((q, None))
    return out


# THE RECIPE (user: saved as both shapes + the way, not the notes): the merged shape is a custom shape of plain
# notes with sh["merge"] = {"parts": [the two shapes as they were], "right": Merge to right?, "ppq", "keys",
# "apart": keys whose rows became the "Merge leftovers" shape}. Project files leave its "notes" out; loading makes
# them again from the recipe.

def part_rows(parts, ppq, keys, rounds=False):
    """Each shape's notes as (start, end, pitch, velocity, track) rows: its own colours (areas, outline colours,
    Colours turns) kept (user 2026-10-16: always the colours they had). Track = the channel slot each colour gets
    with Multi channel when the two shapes stand as they are (engine.assign_slots, like render): shapes apart share
    slots, overlapping ones stay apart; render counts a merged shape's tracks in order (ordered), so track k keeps
    slot k. Without their Sustain / Gate subtract: the merged shape holds those itself. rounds: + a 6th column, each
    note's Colours round ("New channels each Colours round"): the first part's rounds, then the second's (a part
    without Colours = one round)."""
    from notes.custom import ROUND, tracks_apart
    from notes.engine import assign_slots, shape_notes_tracks, unique_inverse
    from notes.gateflip import colours_back, unflipped_starts
    units, forced, firsts, made, laps, after = [], [], [], [], [], 0
    for p in parts:
        p = {k: v for k, v in p.items() if k not in ("sustain", "subtract")}
        notes, tracks = shape_notes_tracks(dict(p, _rounds=True) if rounds else p, ppq, keys)
        notes = np.asarray(notes, np.int64).reshape(-1, 4)
        tracks = np.zeros(len(notes), np.int64) if tracks is None or not len(tracks) else np.asarray(tracks, np.int64)
        if rounds:
            # (in order from 0: a Tiles fill's rounds left of its centre tile are below 0)
            laps.append(unique_inverse(tracks // ROUND)[1].reshape(-1) + after if len(tracks) else tracks)
            tracks = tracks % ROUND
            after = int(laps[-1].max()) + 1 if len(tracks) else after + 1
        ids, which = unique_inverse(tracks) if len(tracks) else (tracks, tracks)
        starts = unflipped_starts(notes) if len(notes) and colours_back(p) else notes[:, 0]
        mine = range(len(units), len(units) + len(ids))
        for k in range(len(ids)):
            units.append(notes[which == k])
            firsts.append(int(starts[which == k].min()))
        if len(ids) > 1 and tracks_apart(p):
            forced.append(mine)
        made.append((notes, which, mine.start))
    slots = np.asarray(assign_slots(units, "key", forced, firsts) if units else [], np.int64)
    out = [np.column_stack([notes, slots[at + which] if len(which) else which]) for notes, which, at in made]
    if rounds and not any(has_colours(p, turns=True) for p in parts):
        laps = colour_laps(out, laps)
    return [np.column_stack([o, lap]) for o, lap in zip(out, laps)] if rounds else out


def colour_laps(rows, laps):
    """part_rows' rounds when only colours given by hand repeat (no part takes turns): each colour counted on its
    own, so a colour's next piece (in this part or the next) is its next repeat and a colour used once stays
    (user 2026-10-19)."""
    if not any(len(r) for r in rows):
        return laps
    slot = np.concatenate([r[:, 4] for r in rows])
    lap = np.concatenate(laps)
    pair, at = np.unique(np.column_stack([slot, lap]), axis=0, return_inverse=True)  # (sorted: slot, then lap)
    rank = np.arange(len(pair)) - np.searchsorted(pair[:, 0], pair[:, 0])  # (its place among its slot's laps)
    got = rank[at.reshape(-1)]
    return np.split(got, np.cumsum([len(r) for r in rows])[:-1])


def merged(parts, right, ppq, keys):
    """gate_merge of two shapes' notes (each (start, end, pitch, velocity[, track]) rows): parts = [left, right] by
    their middles; right = the left one slides right."""
    a, b = (np.asarray(p, np.int64).reshape(len(p), -1) for p in parts)
    if len(a) and len(b) and comes_from_left(a, b):  # (a = the one on the left)
        a, b = b, a
    return gate_merge(b, a, True) if right else gate_merge(a, b, False)


def merged_up_down(parts, up, keys, slide=None, spans=None, was_time=None, need_room=False):
    """Merge up / down as ONE shape (user 2026-10-19): two shapes' rows (start, end, pitch, velocity, ...) ->
    (both, the moving one's pieces with nothing in their way: up to the top key / down to key 0), or None when none
    met. slide: which part moves (None: merge.mover's). A recipe made again (spans: [[start, end] ticks] of the pieces
    made "Merge leftovers"): those left out; pieces newly with nothing in their way stay in it, slid by the smallest
    slide (like left / right's rows; was_time(ticks): where a piece's time was when made, a flipped / stretched box).
    need_room: False (not None) when a piece can't go out under the other shape (key 0 / the top key in the way)."""
    a, b = (np.asarray(p, np.int64).reshape(len(p), -1) for p in parts)
    if not len(a) or not len(b):
        return None
    if slide is None:
        slide = mover([a, b], "up" if up else "down")
    stay, moving = (a, b)[1 - slide], (a, b)[slide]
    plan = up_down_plan([stay], moving, up, keys - 1)
    if plan is None:
        return None
    if need_room and not plan["room"]:  # (out under the other shape would pass key 0 / the top key)
        return False
    rows, wall = up_down_rows(plan, moving)
    if spans is not None:
        mid = (rows[:, 0] + rows[:, 1]) / 2
        if was_time is not None:
            mid = was_time(mid)
        s = np.asarray(spans, float).reshape(-1, 2)
        s = s[np.argsort(s[:, 0])]  # (apart from each other: the one starting last before each middle)
        at = np.searchsorted(s[:, 0], mid, "right") - 1
        out = (at >= 0) & (mid < s[np.maximum(at, 0), 1]) if len(s) else wall & False
        at = np.searchsorted(plan["cuts"], rows[:, 0], "right")
        back = plan["move"][at] if up else -plan["move"][at]
        rows = rows.copy()
        rows[:, 2] = np.where(wall, rows[:, 2] - back + plan["slid"], rows[:, 2])  # (newly: the smallest slide)
        rows, wall = rows[~(wall & out)], wall[~(wall & out)] & False
    return np.concatenate([stay, rows[~wall]]), rows[wall]


def spans_of(rows):
    """[[start, end]] ticks: the time the rows cover, overlapping / touching ones joined (Merge up / down's leftovers,
    the recipe's "spans")."""
    rows = np.asarray(rows, np.int64)
    if not len(rows):
        return []
    o = np.argsort(rows[:, 0], kind="stable")
    s, e = rows[o, 0], np.maximum.accumulate(rows[o, 1])
    new = np.r_[True, s[1:] > e[:-1]]
    ends = np.r_[np.flatnonzero(new)[1:] - 1, len(s) - 1]
    return [[int(x), int(y)] for x, y in zip(s[new], e[ends])]


def merged_by(parts, m, spans=None, was_time=None):
    """The recipe's way of merging two parts' rows: merged_up_down (m["way"] up / down; spans in beats, "spans") or
    merged (right / left)."""
    if m.get("way") in ("up", "down"):
        ticks = None if spans is None else (np.asarray(spans, float).reshape(-1, 2) * m["ppq"]).tolist()
        return merged_up_down(parts, m["way"] == "up", m["keys"], m.get("slide"), ticks, was_time)
    return merged(parts, m["right"], m["ppq"], m["keys"])


def edge_moves(m):
    """How far each part's notes' first start and last end went in the merge, [(d0, d1)] in beats (the sliding
    one's rows each slid their own way), for giving the parts the merged shape's velocities on Split."""
    rows = [np.column_stack([r, np.full(len(r), i, np.int64)])  # (+ which part each note is from)
            for i, r in enumerate(part_rows(m["parts"], m["ppq"], m["keys"]))]
    got = merged_by(rows, m)
    if got is None or not all(len(r) for r in rows):
        return [(0.0, 0.0)] * 2
    now = np.concatenate(got)
    out = []
    for i, was in enumerate(rows):
        mine = now[now[:, 5] == i]
        out.append(((mine[:, 0].min() - was[:, 0].min()) / m["ppq"], (mine[:, 1].max() - was[:, 1].max()) / m["ppq"]))
    return out


def recipe_notes(m):
    """The merged shape's packed notes (custom.notes_shape) made from its recipe, or None. Leftover rows stay in
    it (user: never removed without the user's say)."""
    got = recipe_shape(m)
    return got and got["notes"]


def recipe_shape(m):
    """custom.notes_shape of the recipe's notes ("notes" packed, "pts" = their box where they were made), or None
    (no notes at all). No row meeting any more (a part's settings changed): both as they are, nothing slid."""
    from notes.custom import notes_shape
    out = recipe_rows(m)
    if out is None:
        return None
    notes = np.column_stack([out[:, 0], out[:, 1] - out[:, 0], out[:, 2], out[:, 3], out[:, 4]])
    return notes_shape(notes, m["ppq"], "")


def recipe_rows(m, rounds=False):
    """The recipe's merged (start, end, pitch, velocity, track[, round]) rows (part_rows), in the order its packed
    notes keep them, or None (no notes)."""
    parts = part_rows(m["parts"], m["ppq"], m["keys"], rounds)
    if m.get("way") in ("up", "down"):  # (its leftovers' times left out, pieces newly with nothing in their way kept)
        got = merged_by(parts, m, spans=m.get("spans", []))
        out = np.concatenate(parts) if got is None else got[0]
        return out if len(out) else None
    got = merged(parts, m["right"], m["ppq"], m["keys"])
    if got is None:
        out = np.concatenate(parts)
    else:
        out, rest = got
        out = np.concatenate([out, rest[~np.isin(rest[:, 2], m["apart"])]])  # (not the ones made "Merge leftovers")
    return out if len(out) else None


LAP = 1 << 16  # (with_rounds: track + LAP x round still fits the packed notes' 32 bits)


def has_colours(sh, turns=False):
    """Does sh take turns over colours that can start again ("Colours", a Nested / Tiles fill, Copy along a path's
    colours: "New channels each Colours round"; colours given by hand used more than once, custom.paints), itself or
    any part of it (merges of merges too)? turns: only colours that take turns (not the ones given by hand)."""
    from notes.custom import cycling, has_pattern, paints, path_colours
    return (cycling(sh) or has_pattern(sh) or path_colours(sh) or not turns and paints(sh) or bool(sh.get("lapped"))
            or any(isinstance(p, dict) and has_colours(p, turns) for p in (sh.get("merge") or {}).get("parts", ())))


def with_rounds(sh):
    """The merged shape sh with its packed notes' tracks as track + LAP x each note's Colours round (its parts'
    rounds, recipe_rows), so they ride through whatever places its notes; None = can't (notes not the recipe's)."""
    from notes.custom import pack_notes, unpack_notes
    rows = unpack_notes(sh["notes"])
    out = recipe_rows(sh["merge"], True)
    if out is None or len(out) != len(rows) or out[:, 4].max() >= LAP or out[:, 5].max() >= 1 << 14:
        return None
    t0, k0 = int(out[:, 0].min()), int(out[:, 2].min())
    if not np.array_equal(rows[:, :5], np.column_stack([out[:, :2] - t0, out[:, 2] - k0, out[:, 3:5]])):
        return None
    return dict(sh, notes=pack_notes(np.column_stack([rows[:, :4], rows[:, 4] + LAP * out[:, 5]])))


# PANEL SETTINGS (user: the merged shape acts as one custom shape, but its outline is never edited, it's made of
# two shapes): the custom shape panel changes both parts (App.custom_targets), then refit makes the notes again.

BIG_KEYS = ("strokes", "pts", "areas", "polygon", "from", "cut", "text", "notes", "picture", "merge")


def settings_key(m):
    """The parts' settings (not their drawings) as one string: changed = refit."""
    return json.dumps([{k: v for k, v in p.items() if k not in BIG_KEYS} for p in m["parts"]], sort_keys=True)


def refit(sh):
    """A merged shape made again from its parts after their settings changed: new notes, its box fitted to them,
    still moved / turned / slanted / stretched as it was."""
    m = sh["merge"]
    got = recipe_shape(m)
    if not got:
        return
    was = m.get("box")
    mp = box_map(was, sh["pts"]) if was else None
    if mp is None:  # (an old recipe: only moved)
        at = m.get("at") or sh["pts"][0]
        mp = np.eye(2), np.asarray(sh["pts"][0], float) - np.asarray(at, float)
    L, o = mp
    made = [[float(b), float(p)] for b, p in got["pts"]]
    sh["notes"] = got["notes"]
    sh["pts"] = [[float(x) for x in L @ pt + o] for pt in made]
    m["box"], m["at"] = made, list(made[0])


# TURNED / SLANTED / STRETCHED (user: like one custom shape, gates kept): both parts get the same turn / slant /
# stretch as the merged shape's box ("box" = its corners when made), then they merge again. The sliding part first
# goes as far as its smallest slide (where it is on screen), so turning moves it round the right spot. Moved only:
# the notes kept (block_notes). A part that can't be turned this way (pasted notes, text, Hz bass, funnel...): the
# merged notes sampled like a turned image (turned_notes).

def made_sides(m):
    """(sliding part's index, from the left?, its smallest slide, how much later all went) as the recipe was made,
    ticks at m["ppq"]; None = nothing met."""
    from notes.engine import shape_notes
    a, b = (shape_notes({k: v for k, v in p.items() if k not in ("sustain", "subtract")}, m["ppq"], m["keys"])
            for p in m["parts"])
    left = 1 if len(a) and len(b) and comes_from_left(a, b) else 0
    slide = left if m["right"] else 1 - left
    got = _merge((a, b)[1 - slide], (a, b)[slide], m["right"])
    return got and (slide, m["right"], got[2], got[3])


def made_keys(m):
    """Merge up / down recipe: (moving part's index, its smallest slide in keys, + = up) as made; None = none met."""
    from notes.engine import shape_notes
    a, b = (np.asarray(shape_notes({k: v for k, v in p.items() if k not in ("sustain", "subtract")}, m["ppq"],
                                   m["keys"]),
                       np.int64).reshape(-1, 4) for p in m["parts"])
    if not len(a) or not len(b):
        return None
    up = m["way"] == "up"
    slide = m.get("slide")
    if slide is None:
        slide = mover([a, b], m["way"])
    plan = up_down_plan([(a, b)[1 - slide]], (a, b)[slide], up, m["keys"] - 1)
    return plan and (slide, plan["slid"])


def box_map(was, now):
    """The 2x2 matrix L and the offset o taking the box was to the box now (beats, keys): x -> L x + o; None when
    was is flat."""
    w, n = np.asarray(was, float), np.asarray(now, float)
    a = np.column_stack([w[1] - w[0], w[2] - w[0]])
    if abs(np.linalg.det(a)) < 1e-12:
        return None
    L = np.column_stack([n[1] - n[0], n[2] - n[0]]) @ np.linalg.inv(a)
    return L, n[0] - L @ w[0]


def mapped_part(p, f, L=None, vrev=None):
    """Shape p with every point through f(beat, key) -> [beat, key], or None if it can't be turned that way. L (the
    map's 2x2 matrix): what a Flip / Turn 90° does to a shape's settings is done too (flipped_settings)."""
    import copy
    from notes.engine import cached_arrays
    from notes.convert import has_tumours
    from notes.tumour import LINE_KINDS
    if p.get("cut") or isinstance(p.get("glue"), list):
        return None  # (notes from the shape it was cut from / glue boxes fixed in the song)
    q = copy.deepcopy(p)
    if p["kind"] == "custom":
        if "notes" in p or p.get("text") or p.get("hz") or p.get("merge"):
            return None
    elif p["kind"] not in LINE_KINDS:
        return None
    elif p["kind"] == "arc" or has_tumours(p) or p.get("shape") or p.get("pattern"):
        paths = cached_arrays(p)  # (as drawn, made into points: an arc turned or slanted isn't an arc)
        if len(paths) != 1:
            return None
        for k in ("tumour", "tumours", "shape", "pattern", "k", "smooth"):
            q.pop(k, None)
        q.update(kind="poly", pts=[f(b, k) for b, k in paths[0]])
        return flipped_settings(q, L, vrev)
    q["pts"] = [f(b, k) for b, k in p["pts"]]
    return flipped_settings(q, L, vrev)


def flipped_settings(q, L, vrev=None):
    """The settings a Flip / Turn 90° changes along with the drawing (fx.flip_shape / turn_shape: velocities flip
    sideways, a gate Range turns / flips), done as the map L does: mostly a quarter turn = turned that way, then
    mirrored (det < 0) = flipped, sideways when time runs backwards, or a half turn. vrev: the velocities run the
    other way (the merged shape's sideways flips, counted as they're done: both flips and a half turn give the same
    box, but only flips turn velocities round); None = worked out from L (older recipes). Changes q."""
    from notes.fx import reverse_velocity
    from notes.gaterange import flipped_range, turned_range
    if vrev:
        reverse_velocity(q)
    if L is None:
        return q
    if abs(L[0, 0]) < abs(L[1, 0]):  # (time now runs mostly along the keys: a quarter turn, clockwise = down)
        cw = L[1, 0] < 0
        for k in ("range", "range_kept"):
            if q.get(k):
                q[k] = turned_range(q[k], cw)
        L = np.array([[0, -1], [1, 0]] if cw else [[0, 1], [-1, 0]], float) @ L  # (the rest after turning back)
    if np.linalg.det(L) < 0:
        sideways = L[0, 0] < 0
        if sideways and vrev is None:
            reverse_velocity(q)
        for k in ("range", "range_kept"):
            if q.get(k):
                q[k] = flipped_range(q[k], sideways)
    elif L[0, 0] < 0:  # (a half turn: the Range runs the other way on both axes)
        for k in ("range", "range_kept"):
            if q.get(k):
                q[k] = flipped_range(flipped_range(q[k], True), False)
    return q


def velocity_reversed(sh):
    """Do the merged shape sh's parts' velocities run the other way? Its sideways flips counted (m["vrev"]), or for
    an older recipe worked out from its box as before."""
    m = sh["merge"]
    if "vrev" in m:
        return m["vrev"]
    got = m.get("box") and box_map(m["box"], sh["pts"])
    if not got:
        return False
    L = got[0]
    if abs(L[0, 0]) < abs(L[1, 0]):
        L = np.array([[0, -1], [1, 0]] if L[1, 0] < 0 else [[0, 1], [-1, 0]], float) @ L
    return bool(np.linalg.det(L) < 0 and L[0, 0] < 0)


def reshaped_parts(sh, split=False):
    """(the two parts turned / slanted / stretched with the merged shape's box, the sliding one at its smallest
    slide; sliding part's index; from the left?; (L, o, smallest slide in beats)) or None (only moved, an old
    recipe, nothing met, a part that can't be turned)."""
    m = sh["merge"]
    got = m.get("box") and box_map(m["box"], sh["pts"])
    if not got or np.allclose(got[0], np.eye(2), atol=1e-9) and not m.get("vrev"):
        return None  # (moved only; flipped both ways and half turned back still has its velocities turned round)
    L, o = got
    if m.get("way") in ("up", "down"):
        # (flipped / stretched: both where they were before it slid, merged again (from where it slid, a piece out
        # under the other shape went over it, hunt); turned / slanted: the notes sampled (turned_notes), Split (split)
        # = round where its notes are, the moving one at its smallest slide)
        level = abs(L[0, 1]) <= 1e-9 and abs(L[1, 0]) <= 1e-9
        if not level and not split:
            return None
        got = made_keys(m)
        if not got:
            return None
        slide, kslid = got
        parts = []
        for i, p in enumerate(m["parts"]):
            d = kslid if i == slide and not level else 0
            q = mapped_part(p, lambda b, k, d=d: [float(x) for x in L @ (b, k + d) + o], L, m.get("vrev"))
            if q is None:
                return None
            parts.append(q)
        return parts, slide, None, (L, o, kslid)
    sides = made_sides(m)
    if not sides:
        return None
    slide, from_left, least, late = sides
    parts = []
    for i, p in enumerate(m["parts"]):
        d = (late + (least if i == slide else 0)) / m["ppq"]
        q = mapped_part(p, lambda b, k, d=d: [float(x) for x in L @ (b + d, k) + o], L, m.get("vrev"))
        if q is None:
            return None
        parts.append(q)
    if abs(L[0, 0]) > 1e-9:  # (time flipped: it comes from the other side; turned upright: by the middles)
        from_left = from_left == (L[0, 0] > 0)
    else:
        from_left = None
    return parts, slide, from_left, (L, o, least / m["ppq"])


def reshaped_notes(sh, ppq, keys, rounds=False):
    """The merged shape's notes, (start, end, pitch, velocity, track[, round: part_rows]) rows, when its box was
    turned / slanted / stretched: the parts made again that way and merged again (rows that newly meet nothing stay
    in it, user), or None (see reshaped_parts)."""
    got = reshaped_parts(sh)
    if not got:
        return None
    parts, slide, from_left, (L, o, least) = got
    rows = part_rows(parts, ppq, keys, rounds)
    m = sh["merge"]
    if m.get("way") in ("up", "down"):  # (flipped upside down: the other way; its leftovers' times found back)
        up = (m["way"] == "up") == (L[1, 1] > 0)
        spans = (np.asarray(m.get("spans", []), float).reshape(-1, 2) * ppq).tolist()
        again = merged_up_down(rows, up, keys, slide, spans, lambda t: (t / ppq - o[0]) / L[0, 0] * ppq)
        return np.concatenate(again) if again else np.concatenate(rows)
    stay, moved = rows[1 - slide], rows[slide]
    apart = sh["merge"]["apart"]
    if len(moved) and apart:  # (rows made "Merge leftovers": their notes stay out, found where they were drawn)
        mid = np.column_stack([(moved[:, 0] + moved[:, 1]) / (2 * ppq), moved[:, 2] + 0.5])
        was = (mid - o) @ np.linalg.inv(L).T
        moved = moved[~np.isin(np.floor(was[:, 1]).astype(np.int64), apart)]
    # (rows with nothing to meet stay where they are: the sliding part already sits at its slide; a quarter turn: no
    # key row runs along the old ones, nothing merged again)
    upright = abs(L[0, 0]) <= 1e-9 * max(1.0, abs(L[1, 0]))
    merged_rows = (gate_merge(stay, moved, from_left, rest_slides=False) if len(stay) and len(moved) and not upright
                   else None)
    return np.concatenate(merged_rows) if merged_rows else np.concatenate([stay, moved])


def slid_preview(got, slid, ppq):
    """custom.edge_inner's preview (("rows", (start, end, key) ticks) or ("lines", (b0, k0, b1, k1, b_in, k_in))
    with each key row moved as far as it slid (slid: {key: beats}; a line goes with the row at its middle, an edge
    between two rows with the one on its inner side)."""
    kind, a = got
    if kind == "rows":
        a = np.array(a, np.int64, copy=True)
        a[:, :2] += np.round(np.array([slid.get(int(k), 0.0) for k in a[:, 2]]) * ppq).astype(np.int64)[:, None]
        return kind, a
    a = np.array(a, float, copy=True)
    mid = (a[:, 1] + a[:, 3]) / 2
    rows = np.round(mid + np.clip(a[:, 5] - mid, -0.25, 0.25)).astype(np.int64)
    a[:, [0, 2, 4]] += np.array([slid.get(int(k), 0.0) for k in rows])[:, None]
    return kind, a


MOST_SAMPLES = 20_000_000  # (key rows x time steps, like a turned picture's)


def turned_notes(sh, ppq):
    """A turned / slanted merged shape's notes, (start, end, pitch, velocity, track) rows (user: it turns as the one
    shape seen on screen, like a turned image, not chopped into staircases). Every key row crossing the box is
    sampled one tick at a time (fewer past MOST_SAMPLES): the note of the merged notes under each spot, so each
    stretch of one note = a flat note; touching notes stay apart."""
    from notes.custom import unpack_notes
    rows = unpack_notes(sh["notes"])  # (start, end, key, velocity, track), from the box's corner
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    ub, up, vb, vp = b1 - b0, p1 - p0, b2 - b0, p2 - p0
    det = ub * vp - up * vb
    if abs(det) < 1e-12 or not len(rows):
        return np.zeros((0, 5), np.int64)
    t_all, k_all = float(rows[:, 1].max()), float(rows[:, 2].max() + 1)
    cb, cp = [b0, b1, b2, b1 + b2 - b0], [p0, p1, p2, p1 + p2 - p0]
    lo_k, hi_k = int(np.ceil(min(cp) - 1e-9)), int(np.floor(max(cp) + 1e-9))  # (the edges: see inside below)
    t0, span = min(cb), max(cb) - min(cb)
    n = max(1, int(np.ceil(span * ppq)))
    if hi_k < lo_k:
        return np.zeros((0, 5), np.int64)
    n = min(n, max(1, MOST_SAMPLES // (hi_k - lo_k + 1)))
    step = span / n
    kk = np.arange(lo_k, hi_k + 1, dtype=float)[:, None]
    bb = t0 + (np.arange(n) + 0.5)[None, :] * step
    u = ((bb - b0) * vp - (kk - p0) * vb) / det
    v = ((kk - p0) * ub - up * (bb - b0)) / det
    # (a row's middle right on an edge: the bottom / left one in, the top / right one out, also when a move left
    # the box a hair off; else the row came and went as it was moved, user)
    e = 1e-9
    inside = (u >= -e) & (u < 1 - e) & (v >= -e) & (v < 1 - e)
    key = np.clip((v * k_all).astype(np.int64), 0, int(k_all) - 1)
    tick = u * t_all
    order = np.lexsort((rows[:, 0], rows[:, 2]))  # by key, then start (notes on one key don't overlap)
    srt = rows[order]
    flat = srt[:, 2] * (t_all + 1) + srt[:, 0]  # (one sorted number per note start)
    at = np.searchsorted(flat, key * (t_all + 1) + tick, side="right") - 1
    ok = inside & (at >= 0)
    at = np.clip(at, 0, len(srt) - 1)
    ok &= (srt[at, 2] == key) & (tick < srt[at, 1])
    a = np.where(ok, at, -1)  # key rows x time steps: which note, -1 = none
    change = np.ones(a.shape, bool)
    change[:, 1:] = a[:, 1:] != a[:, :-1]
    ks, ts = np.nonzero(change)
    ends = np.full(len(ts), n)
    ends[:-1] = np.where(ks[1:] == ks[:-1], ts[1:], n)
    val = a[ks, ts]
    on = val >= 0
    start = np.round((t0 + ts * step) * ppq).astype(np.int64)
    end = np.round((t0 + ends * step) * ppq).astype(np.int64)
    got = srt[np.maximum(val, 0)]
    out = np.column_stack([start, end, ks + lo_k, got[:, 3], got[:, 4]])[on]
    return out[out[:, 1] > out[:, 0]]


def clean_merge(m, clean_shape):
    """A recipe from a file -> valid, or None."""
    try:
        parts = [clean_shape(p) if isinstance(p, dict) else None for p in m["parts"]]
        ppq, keys = int(m["ppq"]), int(m["keys"])
        apart = sorted({int(k) for k in m.get("apart", [])})
        at = [float(x) for x in m["at"]] if m.get("at") is not None else None
    except (KeyError, TypeError, ValueError):
        return None
    if len(parts) != 2 or not all(parts) or not 1 <= ppq <= 65535 or keys not in (128, 256):
        return None
    out = {"parts": parts, "right": m.get("right") is True, "ppq": ppq, "keys": keys, "apart": apart}
    if m.get("way") in ("up", "down"):  # (Merge up / down: which part moved, its leftovers' times in beats)
        out["way"] = m["way"]
        if m.get("slide") in (0, 1):
            out["slide"] = m["slide"]
        try:
            spans = [[float(a), float(b)] for a, b in m.get("spans", [])]
        except (TypeError, ValueError):
            spans = []
        out["spans"] = [s for s in spans if all(map(math.isfinite, s))]
    if at and len(at) == 2 and all(map(math.isfinite, at)):
        out["at"] = at  # (its box's first corner when made: Split moves the parts as far as it moved)
    try:
        box = [[float(b), float(p)] for b, p in m["box"]] if m.get("box") is not None else None
    except (TypeError, ValueError):
        box = None
    if box and len(box) == 3 and all(math.isfinite(x) for pt in box for x in pt):
        out["box"] = box  # (its box when made: turned / slanted / stretched since = the parts too, merged again)
    if "vrev" in m:
        out["vrev"] = m["vrev"] is True  # (flipped sideways an odd number of times: velocities the other way)
    from notes.gateflip import lifted
    flips = lifted(parts)
    if flips:  # (both parts gate flipped, as merged shapes were before 2026-10-11: the whole flipped instead)
        out["lift"] = True
    elif isinstance(m.get("flips"), list) and len(m["flips"]) == 2:
        flips = [f is True for f in m["flips"]]
    if flips and any(flips):
        out["flips"] = flips  # (the parts were gate flipped when merged: Split flips them again, gateflip.put_back)
    return out
