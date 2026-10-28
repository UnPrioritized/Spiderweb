"""Channels moved between tracks in the track list (PLAN #11 C2, folders): a sub-row (one channel of a track) or a
whole track dragged INTO another track, or a sub-row dragged out into a track of its own. Only YOUR notes move: each
colour laid on the track keeps its channel there (sh["into_ch"] + "into_home", App.laid_slots_of), so a track holds
several. A loaded MIDI's tracks can take channels in; their own channels never move (stage D)."""

from files.own_tracks import FILE0, HAND, key_track, ordered
from notes.engine import aligned_ch


def entry_of(s):
    """A track list id as a shape's "into" entry: yours = its id, a loaded MIDI's track n = FILE0 + n; an automatic
    track (>= 0, < HAND) = None (made yours first: TrackList.take_all)."""
    if s >= HAND:
        return s - HAND
    return FILE0 + key_track(-1 - s) if s < 0 else None  # (a part: its track, PLAN #11 D)


def laid_channels(app, sh, ids, own):
    """The channel each placed_on entry of sh plays on now."""
    from window.app import laid_slots_of
    return [s & 15 for s in laid_slots_of(sh, ids, own, app.picture_use10)]


def move_channels(app, src, chans, dst, to=None):
    """Every colour of your shapes on track src (a track list id) playing on one of chans laid on dst, keeping its
    channel there (to: {channel: the one it takes instead}, the same-channel ask's Move). True if any moved."""
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
                into[k], chs[k], home[k] = b, (to or {}).get(laid[k], laid[k]), b
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
    (-1); its sub-row order kept only while it holds 2+ (own_tracks "order"). A channel in its order holds even
    without notes: an empty "Ch n" row (an empty track dragged in, check-in C 2026-10-28), until dragged out."""
    have = track_channels(app)
    new, changed = [], {}
    for t in app.own_tracks:
        cs = have.get(t["id"], set()) | set(t.get("order") or ())
        t = dict(t)
        if cs and t["ch"] not in cs:
            t["ch"] = ordered(t.get("order"), cs)[0]
            changed[t["id"]] = t["ch"]
        if len(cs) > 1:
            t["order"] = ordered(t.get("order"), cs)
        else:
            t.pop("order", None)
        new.append(t)
    if new != app.own_tracks:  # (the list is replaced on a change only: App.shapes_changed runs this each time)
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


def free_channels(app, used, n):
    """n channels none of used (a track's), lowest first; channel 10 only with Use channel 10 (picture_use10).
    Fewer when there aren't that many."""
    ok = [c for c in range(16) if c not in used and (c != 9 or app.picture_use10)]
    return ok[:n]


def ask_clash(app, name, clash, free, merge_first, need=None):
    """The same-channel question (PLAN #11 decisions 5 + 10, one for all): channels clash (sorted) are in track
    `name` already. Merge into them / Move to free channels (free: need of them, need = how many clash (several
    tracks may bring the same one), default one each; fewer = Move isn't offered) / Cancel; merge_first = Merge is
    the button Enter presses (nothing would sound different). "Don't ask again until Spiderweb is closed" = the
    answer remembered (App.big_skip "same_channel", app.same_channel). "merge" / "move" / "cancel"."""
    from files.lang import tr
    from window.load_midi import ask_choice
    need = len(clash) if need is None else need
    if "same_channel" in app.big_skip:
        kept = getattr(app, "same_channel", "merge")
        return kept if kept == "merge" or len(free) == need else "merge"
    chs = ", ".join(tr("tracks.channel", ch=c + 1) for c in clash)
    lines = [tr("tracks.clash_one" if len(clash) == 1 else "tracks.clash_many", chs=chs, name=name),
             tr("tracks.clash_merge_why")]
    buttons = [("merge", tr("tracks.clash_merge"))]
    if len(free) == need:
        lines.append(tr("tracks.clash_move_why"))
        to = ", ".join(tr("tracks.channel", ch=c + 1) for c in free)
        buttons.append(("move", tr("tracks.clash_move", chs=to)))
    else:
        lines.append(tr("tracks.clash_full", name=name))
    buttons.append(("cancel", tr("tracks.clash_cancel")))
    if not merge_first and len(buttons) == 3:
        buttons[:2] = buttons[1::-1]
    skip = []
    got = ask_choice(app, tr("tracks.clash_title"), "\n\n".join(lines), buttons, skip=skip)
    if got != "cancel" and skip and skip[0]:
        app.big_skip.add("same_channel")
        app.same_channel = got
    return got


def set_order(app, dst, chans, pos, now):
    """Track dst's sub-rows (yours; now = their order before the drop) with chans (a list, in its order) put at
    place pos (None = the end)."""
    if dst < HAND:
        return
    rest = [c for c in now if c not in chans]
    pos = len(rest) if pos is None else sum(1 for c in now[:pos] if c not in chans)
    order = rest[:pos] + list(chans) + rest[pos:]
    app.own_tracks = [dict(t, order=order) if HAND + t["id"] == dst else t for t in app.own_tracks]


def forget_channels(app, src, chans):
    """Channels moved out of your track src: no longer in its sub-rows' order (an empty one would stay listed)."""
    if src < HAND:
        return
    app.own_tracks = [dict(t, order=[c for c in t["order"] if c not in chans])
                      if HAND + t["id"] == src and t.get("order") else t for t in app.own_tracks]
