"""Channels moved between tracks in the track list (PLAN #11 C2, folders): a sub-row (one channel of a track) or a
whole track dragged INTO another track, or a sub-row dragged out into a track of its own. Only YOUR notes move: each
colour laid on the track keeps its channel there (sh["into_ch"] + "into_home", App.laid_slots_of), so a track holds
several. A loaded MIDI's tracks can take channels in; their own channels never move (stage D)."""

from files.own_tracks import FILE0, HAND, ordered
from notes.engine import aligned_ch


def entry_of(s):
    """A track list id as a shape's "into" entry: yours = its id, a loaded MIDI's track n = FILE0 + n; an automatic
    track (>= 0, < HAND) = None (made yours first: TrackList.take_all)."""
    if s >= HAND:
        return s - HAND
    return FILE0 - 1 - s if s < 0 else None


def laid_channels(app, sh, ids, own):
    """The channel each placed_on entry of sh plays on now."""
    from window.app import laid_slots_of
    return [s & 15 for s in laid_slots_of(sh, ids, own, app.picture_use10)]


def move_channels(app, src, chans, dst):
    """Every colour of your shapes on track src (a track list id) playing on one of chans laid on dst, keeping its
    channel there. True if any moved."""
    from window.app import placed_on
    a, b = entry_of(src), entry_of(dst)
    if a is None or b is None or a == b:
        return False
    own, moved = app.own_channels(), False
    for sh in app.shapes:
        ids = placed_on(sh, own)
        if a not in ids:
            continue
        laid = laid_channels(app, sh, ids, own)
        into = list(sh["into"]) if isinstance(sh.get("into"), list) else [sh["into"]]
        got = aligned_ch(dict(sh, into=into)) if "into_ch" in sh else {}
        chs = got.get("into_ch", [-1] * len(into))
        # (no home = its own channel anywhere: kept so, on the track it's on)
        home = got.get("into_home", [v if c >= 0 else -1 for v, c in zip(into, chs)])
        for k, v in enumerate(ids):
            if v == a and laid[k] in chans:
                into[k], chs[k], home[k] = b, laid[k], b
                moved = True
        sh["into"], sh["into_ch"], sh["into_home"] = into, chs, home
    return moved


def track_channels(app):
    """{your track's id or FILE0 + n: the channels your shapes play on it}."""
    from window.app import placed_on
    own, out = app.own_channels(), {}
    for sh in app.shapes:
        ids = placed_on(sh, own)
        if not ids:
            continue
        laid = laid_channels(app, sh, ids, own)
        if app.channel_mode.get() != "auto":  # (Single channel / As drawn: every colour on the first)
            ids, laid = ids[:1], laid[:1]
        for v, c in zip(ids, laid):
            if v >= 0:
                out.setdefault(v, set()).add(c)
    return out


def tidy_tracks(app):
    """After channels moved: a track of yours whose own channel left takes the first one it still holds (Track
    Properties shows it, empty tracks are written on it), the colours on it then marked as on the track's channel
    (-1); its sub-row order kept only while it holds 2+ (own_tracks "order")."""
    have = track_channels(app)
    new, changed = [], {}
    for t in app.own_tracks:
        cs = have.get(t["id"], set())
        t = dict(t)
        if cs and t["ch"] not in cs:
            t["ch"] = ordered(t.get("order"), cs)[0]
            changed[t["id"]] = t["ch"]
        if len(cs) > 1:
            t["order"] = ordered(t.get("order"), cs)
        else:
            t.pop("order", None)
        new.append(t)
    app.own_tracks = new
    ch_of = {t["id"]: t["ch"] for t in new}
    for sh in app.shapes:
        if "into_ch" not in sh:
            continue
        sh.update(aligned_ch(sh))
        into, chs, home = sh["into"], sh["into_ch"], sh.get("into_home")
        for k, v in enumerate(into):
            if 0 <= v < FILE0 and chs[k] == ch_of.get(v) and (home is None or home[k] == v):
                chs[k] = -1  # (on the track's own channel: follows Track Properties again)
        if all(c < 0 for c in chs):
            sh.pop("into_ch", None)
            sh.pop("into_home", None)


def set_order(app, dst, chans, pos, now):
    """Track dst's sub-rows (yours; now = their order before the drop) with chans put at place pos (None = the
    end)."""
    if dst < HAND:
        return
    rest = [c for c in now if c not in chans]
    pos = len(rest) if pos is None else sum(1 for c in now[:pos] if c not in chans)
    order = rest[:pos] + [c for c in now if c in chans] + [c for c in sorted(chans) if c not in now] + rest[pos:]
    app.own_tracks = [dict(t, order=order) if HAND + t["id"] == dst else t for t in app.own_tracks]
