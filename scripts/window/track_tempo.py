"""The tempo side of the track list's boxes (user 2026-11-05, like Domino's Conductor track): a box on the Conductor
row (alone or with tracks under it) and a ruler band hold the tempo, time signatures and key signatures of their
stretch. Copied / cut / deleted / moved / pasted with the Conductor pane's own tools (TempoPane.clip_of,
empty_spans, put_copies, put_domino_sigs), in the same undo step as the notes when both go."""

from notes.meter import on_bars, sigs_of
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


def _inside(spans):
    return lambda x: any(lo - 1e-9 <= x < hi - 1e-9 for lo, hi in spans)


def clip_of(app, spans):
    """What a copy of the spans holds: the Conductor pane's copy ({"from", "length", "points", "catch", "spans"})
    + "sigs" / "keys" [[beat from the copy's start, ...]]; None when nothing changes there."""
    if not spans:
        return None
    clip = app.tempo_pane.clip_of(spans=spans)
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
    inside, tempo = _inside(spans), app.tempo_pane.ed()
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
    pane, tempo = app.tempo_pane, app.tempo
    pane.empty_spans(spans, name, push=False)
    inside = _inside(spans)
    sigs = [m for m in sigs_of(tempo) if m[0] == 0 or not inside(m[0])]
    keys = [m for m in tempo.get("keysigs") or [] if not inside(m[0])]
    if len(sigs) != len(sigs_of(tempo)) or len(keys) != len(tempo.get("keysigs") or []):
        tempo["sigs"] = on_bars(sigs)
        tempo["keysigs"] = keys
        pane.keys_on_bars()
        app.meter_changed()


def put(app, clip, at, name):
    """clip laid from beat at (in the caller's undo step): its stretch's tempo replaced, its signatures on the bar
    lines there (the Conductor pane's paste from Domino rule)."""
    pane = app.tempo_pane
    if clip["points"] or clip["catch"]:
        pane.put_copies(clip, at, name, push=False)
    if clip.get("sigs") or clip.get("keys"):
        pane.put_domino_sigs(clip.get("sigs") or [], clip.get("keys") or [], at, clip["length"], "replace")


def move(app, spans, dt, copying, name):
    """The spans' tempo moved dt beats (copying: copied there, the old stays), in the caller's undo step. Nothing
    changing inside them: nothing (a held stretch laid elsewhere would change the tempo there)."""
    clip = clip_of(app, spans)
    if clip is None or not changes(app, spans):
        return
    if not copying:
        empty(app, spans, name)
    put(app, clip, max(0.0, clip["from"] + dt), name)
