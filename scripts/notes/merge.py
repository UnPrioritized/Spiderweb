"""Gate sensitive merge (user, 2026-10-08, first try: plain notes): two shapes' notes become one. The shape that
stays keeps its notes; each key row of the other one slides sideways in time on its own until its notes meet the
staying shape's note on that key (gate end = next start), lengths kept. Rows with no note of the staying shape on
their key ("leftovers") slide by the smallest slide a row made and are handed back apart (user: their own shape,
to delete or keep).

"Keep as two shapes" (menu tick, user 2026-10-19): the staying shape stays as it is, the sliding one becomes fixed
notes (leftovers in it) remembering the shape it was (sh["unmerge"]): Split gives it back (unmerged)."""

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
    key row's slide {key: ticks}."""
    stay = np.asarray(stay, np.int64)
    moved = np.array(slide, np.int64, copy=True)
    if not len(stay) or not len(moved):
        return None
    if from_left is None:
        from_left = comes_from_left(stay, moved)
    met = np.zeros(len(moved), bool)
    shifts, by_key = [], {}
    for key in np.unique(moved[:, 2]):
        here = stay[stay[:, 2] == key]
        if not len(here):
            continue
        rows = moved[:, 2] == key
        if from_left:  # its last note's end meets the staying shape's first note on this key
            shift = here[:, 0].min() - moved[rows, 1].max()
        else:  # its first note's start meets the staying shape's last note's end
            shift = here[:, 1].max() - moved[rows, 0].min()
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
    return out, rest, least, late, by_key


def slide_plan(parts, right):
    """How Merge to right / left slides two shapes' (start, end, pitch, ...) rows: (which part slides (right: the
    one on the left, by middles), each met key row's slide {key: ticks}, the smallest slide (the rows with nothing
    to meet), how much later everything goes to stay after tick 0), or None when no row meets."""
    a, b = (np.asarray(p, np.int64).reshape(len(p), -1) for p in parts)
    if not len(a) or not len(b):
        return None
    left = 1 if comes_from_left(a, b) else 0
    slide = left if right else 1 - left
    got = _merge((a, b)[1 - slide], (a, b)[slide], right)
    return got and (slide, got[4], got[2], got[3])


def merge_parts(sh):
    """The shapes kept inside a Gate sensitive merge: its recipe's (sh["merge"]) or the one its notes Split back
    into (sh["unmerge"])."""
    return [p for key in ("merge", "unmerge") if isinstance(sh.get(key), dict)
            for p in sh[key].get("parts") or () if isinstance(p, dict)]


def clean_unmerge(u, clean_shape):
    """A two-shapes merge's memory from a file (sh["unmerge"]: "parts" = the shape Split gives back as it was when
    merged, "box" = the notes' box then, "slid" = its smallest slide in beats, "vrev" = flipped sideways an odd
    number of times since) -> valid, or None."""
    try:
        parts = [clean_shape(p) for p in u["parts"] if isinstance(p, dict)]
        box = [[float(b), float(p)] for b, p in u["box"]]
    except (KeyError, TypeError, ValueError):
        return None
    if not parts or not all(parts) or len(box) != 3 or not all(math.isfinite(x) for pt in box for x in pt):
        return None
    out = {"parts": parts, "box": box}
    if isinstance(u.get("slid"), (int, float)) and math.isfinite(u["slid"]):
        out["slid"] = float(u["slid"])
    if u.get("vrev") is True:
        out["vrev"] = True
    return out


def unmerged(sh):
    """Split of a two-shapes merge's notes: the shape they were made from, moved / turned / slanted / stretched /
    flipped the way the notes' box was since (a shape that can't be turned that way: only moved, as far as the
    box's corner)."""
    u = sh["unmerge"]
    parts = copy.deepcopy(u["parts"])
    got = box_map(u["box"], sh["pts"])
    if got is None:
        return parts
    L, o = got
    if not np.allclose(L, np.eye(2), atol=1e-9) or u.get("vrev"):
        d = u.get("slid", 0.0)  # (turned round where its notes are: first as far as it slid, the smallest slide)
        turned = [mapped_part(p, lambda b, k: [float(x) for x in L @ (b + d, k) + o], L, bool(u.get("vrev")))
                  for p in parts]
        if all(q is not None for q in turned):
            return turned
    db, dp = (float(x) for x in np.asarray(sh["pts"][0], float) - np.asarray(u["box"][0], float))
    for p in parts:
        p["pts"] = [[b + db, k + dp] for b, k in p["pts"]]
    return parts


def preview_parts(sh, ppq, keys):
    """The merged shape's shapes placed where their notes are, for the outline gate's preview line: [(shape, how far
    each key row of it slid in beats {key: beats} or None)]; the sliding one per row, roughly (a line crossing
    rows goes with the row at its middle). [] = can't tell."""
    m = sh["merge"]
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
    slot k. Without their Sustain: the merged shape holds that itself (sustain.py). rounds: + a 6th column, each
    note's Colours round ("New channels each Colours round"): the first part's rounds, then the second's (a part
    without Colours = one round)."""
    from notes.custom import ROUND, tracks_apart
    from notes.engine import assign_slots, shape_notes_tracks, unique_inverse
    from notes.gateflip import colours_back, unflipped_starts
    units, forced, firsts, made, laps, after = [], [], [], [], [], 0
    for p in parts:
        p = {k: v for k, v in p.items() if k != "sustain"}
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


def edge_moves(m):
    """How far each part's notes' first start and last end went in the merge, [(d0, d1)] in beats (the sliding
    one's rows each slid their own way), for giving the parts the merged shape's velocities on Split."""
    rows = [np.column_stack([r, np.full(len(r), i, np.int64)])  # (+ which part each note is from)
            for i, r in enumerate(part_rows(m["parts"], m["ppq"], m["keys"]))]
    got = merged(rows, m["right"], m["ppq"], m["keys"])
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
    return (cycling(sh) or has_pattern(sh) or path_colours(sh) or not turns and paints(sh)
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
    a, b = (shape_notes({k: v for k, v in p.items() if k != "sustain"}, m["ppq"], m["keys"]) for p in m["parts"])
    left = 1 if len(a) and len(b) and comes_from_left(a, b) else 0
    slide = left if m["right"] else 1 - left
    got = _merge((a, b)[1 - slide], (a, b)[slide], m["right"])
    return got and (slide, m["right"], got[2], got[3])


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


def reshaped_parts(sh):
    """(the two parts turned / slanted / stretched with the merged shape's box, the sliding one at its smallest
    slide; sliding part's index; from the left?; (L, o, smallest slide in beats)) or None (only moved, an old
    recipe, nothing met, a part that can't be turned)."""
    m = sh["merge"]
    got = m.get("box") and box_map(m["box"], sh["pts"])
    if not got or np.allclose(got[0], np.eye(2), atol=1e-9) and not m.get("vrev"):
        return None  # (moved only; flipped both ways and half turned back still has its velocities turned round)
    L, o = got
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
