"""The tempo side of the track list's boxes (user 2026-11-05, like Domino's Conductor track): a box on the Conductor
row (alone or with tracks under it) and a ruler band hold the tempo, time signatures and key signatures of their
stretch. Copied / cut / deleted / moved / pasted with the Conductor pane's own tools (TempoPane.clip_of,
empty_spans, put_copies, put_domino_sigs), in the same undo step as the notes when both go."""

from notes.meter import at_beat, bar_line, on_bars, sigs_of
from window.tempo_pane import joined

CONDUCTOR = -1  # a box's rows starting here hold the Conductor row (track_list.CONDUCTOR_ROW)


def tempo_spans(boxes):
    """[(lo, hi)] beats of the boxes holding the tempo (a ruler band, a box from the Conductor row), joined."""
    return joined([(b["t0"], b["t1"]) for b in boxes if b["rows"] is None or b["rows"][0] <= CONDUCTOR])


def note_boxes(boxes):
    """The boxes as the notes' code knows them: the Conductor row left out (a box on it alone dropped)."""
    out = []
    for b in boxes:
        r = b["rows"]
        if r is None or r[0] >= 0:
            out.append(b)
        elif r[1] >= 0:
            out.append(dict(b, rows=(0, r[1])))
    return out


def visual(app):
    """The Conductor pane drawing the song's tempo ("Draw: Visual tempo"): the Conductor row always works on it,
    never on a Keep timing part's heard line (hunt 2026-11-05, user picked A: Paste from Domino's rule)."""
    pane = app.tempo_pane
    if pane.box.mode != "visual":
        pane.box.set_mode("visual")
    return pane


def _inside(spans):
    return lambda x: any(lo - 1e-9 <= x < hi - 1e-9 for lo, hi in spans)


def clip_of(app, spans):
    """What a copy of the spans holds: the Conductor pane's copy ({"from", "length", "points", "catch", "spans"})
    + "sigs" / "keys" [[beat from the copy's start, ...]]; None when nothing changes there."""
    if not spans:
        return None
    clip = visual(app).clip_of(spans=spans)
    lo, hi = spans[0][0], spans[-1][1]
    if clip is None:
        clip = {"from": lo, "length": hi - lo, "points": [], "catch": [], "spans": [(a - lo, b - lo) for a, b in spans]}
    inside, lo = _inside(spans), clip["from"]
    tempo = app.tempo
    clip["sigs"] = [[m[0] - lo, *m[1:]] for m in sigs_of(tempo) if inside(m[0])]
    clip["keys"] = [[m[0] - lo, *m[1:]] for m in tempo.get("keysigs") or [] if inside(m[0])]
    if not (clip["points"] or clip["catch"] or clip["sigs"] or clip["keys"]):
        return None
    return clip


def changes(app, spans):
    """How many of the Conductor's own items (tempo points, catch-ups, time / key signatures) start inside the
    spans: 0 = nothing to delete or move there (a copy still holds the held tempo, cut at the edges)."""
    inside, tempo = _inside(spans), visual(app).ed()
    n = sum(1 for p in tempo["points"] if inside(p[0])) + sum(1 for c in tempo["catch"] if inside(c[0]))
    return n + sum(1 for m in sigs_of(app.tempo) if m[0] > 0 and inside(m[0])) + \
        sum(1 for m in app.tempo.get("keysigs") or [] if inside(m[0]))


def count(clip):
    """How many items a copy holds (the status line's number)."""
    return sum(len(clip.get(k) or ()) for k in ("points", "catch", "sigs", "keys"))


def empty(app, spans, name):
    """The tempo changes and signatures inside the spans go (in the caller's undo step); the tempo before each
    holds on, the first time signature stays (only ever changed)."""
    if not spans:
        return
    pane, tempo = visual(app), app.tempo
    pane.empty_spans(spans, name, push=False)
    inside = _inside(spans)
    sigs = [m for m in sigs_of(tempo) if m[0] == 0 or not inside(m[0])]
    keys = [m for m in tempo.get("keysigs") or [] if not inside(m[0])]
    if len(sigs) != len(sigs_of(tempo)) or len(keys) != len(tempo.get("keysigs") or []):
        tempo["sigs"] = on_bars(sigs)
        tempo["keysigs"] = keys
        pane.keys_on_bars()
        app.meter_changed()


def put(app, clip, at, name, backs=None):
    """clip laid from beat at (in the caller's undo step): its stretch's tempo replaced, its signatures on the bar
    lines there (the Conductor pane's paste from Domino rule; backs: put_domino_sigs')."""
    pane = visual(app)
    if clip["points"] or clip["catch"]:
        pane.put_copies(clip, at, name, push=False)
    if clip.get("sigs") or clip.get("keys"):
        pane.put_domino_sigs(clip.get("sigs") or [], clip.get("keys") or [], at, clip["length"], "replace",
                             backs=backs)


def _moved_sigs(app, spans):
    inside = _inside(spans)
    return [m for m in sigs_of(app.tempo) if m[0] > 0 and inside(m[0])]


def sig_dt(app, spans, dt, copying):
    """dt changed so the first time signature in the spans lands on a bar line where it goes (in the meter left once
    it's moved away; copying: the meter as it is), the tempo and notes moving the same: a moved 3/4 lands where its
    box lands (hunt 2026-11-05, user picked A: before, the mid-bar rule pulled it back to its old place)."""
    moved = _moved_sigs(app, spans) if dt else []
    if not moved:
        return dt
    sigs = sigs_of(app.tempo)
    left = sigs if copying else on_bars([m for m in sigs if not any(m is x for x in moved)])
    s0 = moved[0][0]
    target = max(0.0, s0 + dt)
    a, b = bar_line(left, target), bar_line(left, target, after=True)
    return (a if target - a <= b - target else b) - s0


def _backs(app, spans, end):
    """What a moved stretch's end at beat end gives back after it (put_domino_sigs' backs): the time / key
    signature the song had there before the move, none when that was one of the moved ones (it goes on, as it did:
    a moved 3/4 that ran to the song's end still does, hunt 2026-11-05)."""
    inside, out = _inside(spans), {}
    for key, marks in (("sigs", sigs_of(app.tempo)), ("keysigs", app.tempo.get("keysigs") or [])):
        m = at_beat(marks, end)
        out[key] = None if m is None or (key == "keysigs" or m[0] > 0) and inside(m[0]) else list(m)
    return out


def move(app, spans, dt, copying, name):
    """The spans' tempo moved dt beats (copying: copied there, the old stays), in the caller's undo step. Nothing
    changing inside them: nothing (a held stretch laid elsewhere would change the tempo there)."""
    clip = clip_of(app, spans)
    if clip is None or not changes(app, spans):
        return
    at = max(0.0, clip["from"] + dt)
    backs = None
    if not copying:
        backs = _backs(app, spans, at + clip["length"])
        empty(app, spans, name)
    put(app, clip, at, name, backs)
