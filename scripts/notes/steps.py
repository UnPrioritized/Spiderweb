"""The order of every step done to a shape's notes after they're made (PLAN.md "Properties", stage A, user
2026-11-11: every step in one order, new ones at the end, old files keep their look).

A step is one of:
  "fx"         the n-th entry of sh["fx"] (a note tool page, a flip / turn / velocity drawn after a page, a glue
               place: fx.py), in the order of that list
  "glue"       the shape's glue done before its pages (sh["glue"]'s boxes before the first glue place, fx.glue_from)
  "gate_flip"  Gate aware flip (gateflip.py)
  "sustain", "extend", "subtract"   Sustain / Gate extend / Gate subtract (sustain.py, extend.py, subtract.py)
  "mode", "stir", "warp", "liquify", "turned"   the colour steps (recolour.py ORDER_KINDS: the n-th "stir" = Stir
               tab n, the n-th "turned" = the n-th Reverse)
A step is named by its tag (kind, n): the n-th step of its kind.

ONE ORDER, each relation kept in ONE place:
  - fx entries among themselves: the order of sh["fx"];
  - colour steps among themselves and against the note tool pages: the Recolour's own order (recolour.order_of /
    full_order: its "page" entries);
  - every other step ("glue", "gate_flip", "sustain", "extend", "subtract") against the rest: sh["order"], a list of
    kinds (the n-th of a kind = its n-th step), saved only when it differs from default_order (today's fixed
    places: old files and every shape made before stay as they were). Each such step sits right after the step it
    follows in sh["order"] (the first one there: at the very start); one sh["order"] doesn't hold goes at the END
    (order added).
order_of(sh) puts the three together. sh["off"] = the steps switched off, [[kind, n], ...] (a page's own "off" in
sh["fx"] stays where it is). Stage A only keeps them: the notes are still made in the old fixed order (stage B).
"""

import copy

FX = "fx"
GLUE = "glue"
GATE_FLIP = "gate_flip"
LENGTHS = ("sustain", "extend", "subtract")  # (run in this order today: Sustain, then Gate extend, then subtract)
OWN = (GLUE, GATE_FLIP) + LENGTHS  # (the steps whose place sh["order"] keeps)
COLOURS = ("mode", "stir", "warp", "liquify", "turned")  # (recolour.ORDER_KINDS)
KINDS = (FX,) + OWN + COLOURS
MOST = 5000  # (entries a saved list may hold: fx.MAX_STEPS 2000 + the colour steps + some)


def tags_of(kinds):
    """A list of kinds -> their tags: (kind, n) = the n-th of that kind."""
    seen = {}
    out = []
    for k in kinds:
        out.append((k, seen.get(k, 0)))
        seen[k] = seen.get(k, 0) + 1
    return out


def own_steps(sh):
    """Which of OWN shape sh has (a set of kinds)."""
    from notes.extend import is_extended
    from notes.fx import glue_from
    from notes.gateflip import is_flipped, is_pasted_flipped
    from notes.subtract import is_subtracted
    from notes.sustain import is_sustained
    out = set()
    g = sh.get("glue")
    if g and glue_from(g, sh.get("fx")):
        out.add(GLUE)
    if sh.get("gate_flip") is True and sh.get("kind") == "custom" and (is_flipped(sh) or is_pasted_flipped(sh)):
        out.add(GATE_FLIP)
    for k, has in zip(LENGTHS, (is_sustained, is_extended, is_subtracted)):
        if has(sh):
            out.add(k)
    return out


def _colour_order(sh, n_pages):
    """The shape's colour steps + "page" entries as its Recolour runs them (recolour.full_order), or []."""
    from notes.recolour import clean_recolour, full_order
    r = sh.get("recolour")
    rc = clean_recolour(r) if isinstance(r, dict) and not sh.get("hz") else None  # (never a Hz bass: can_recolour)
    if not rc:
        return []
    return full_order(rc, n_pages)[0]


def core(sh, s=None):
    """The fx entries and the colour steps in the order they run (tags): a colour step sits before the page its
    Recolour puts it in front of, the rest after every page. Between two pages (or after the last), the flips /
    turns / velocities / glue places there and the colour steps there go as the saved order s (tags) has them (one
    missing from s: after the others; no s: the fx entries first, as engine.pages_coloured does)."""
    from notes.fx import is_page
    from notes.recolour import PAGE
    fx = sh.get("fx") or []
    n_pages = sum(1 for st in fx if is_page(st))
    segs, seen = [[]], {}
    for k in _colour_order(sh, n_pages):
        if k == PAGE:
            segs.append([])
        else:
            segs[-1].append((k, seen.get(k, 0)))
            seen[k] = seen.get(k, 0) + 1
    pos = {t: p for p, t in enumerate(s)} if s else None
    far = len(s or ()) + 1

    def merged(a, b):
        if pos is None or not a or not b:
            return a + b
        out, i, k = [], 0, 0
        while i < len(a) and k < len(b):
            if pos.get(b[k], far) < pos.get(a[i], far):
                out.append(b[k])
                k += 1
            else:
                out.append(a[i])
                i += 1
        return out + a[i:] + b[k:]
    out, j, run = [], 0, []
    for i, st in enumerate(fx):
        if is_page(st):
            out += merged(run, segs[j] if j < len(segs) else [])
            run = []
            j += 1
            out.append((FX, i))
        else:
            run.append((FX, i))
    return out + merged(run, [t for seg in segs[j:] for t in seg])


def default_order(sh):
    """Today's fixed order of shape sh's steps (tags; engine.fx_notes): Gate aware flip + glue first (no fx: the flip
    first, glue after it; with fx and a flip: the flip after every fx entry but the velocities / glue places at the
    end, the glue before the fx when a page comes before the flip, else after the flip), the fx entries and colour
    steps (core), Sustain, Gate extend and subtract after the last fx entry, the colour steps after every page last."""
    from notes.fx import pages
    have = own_steps(sh)
    out = core(sh)
    fx = sh.get("fx") or []
    last = max((p for p, t in enumerate(out) if t[0] == FX), default=-1)
    out[last + 1:last + 1] = [(k, 0) for k in LENGTHS if k in have]
    lead = []
    if GATE_FLIP in have and fx:
        k = len(fx)
        while k and fx[k - 1]["tool"] in ("vel", "glue"):
            k -= 1
        glue_first = GLUE in have and bool(pages(fx[:k]))
        flip_at = (out.index((FX, k - 1)) + 1) if k else 0
        after = [(GATE_FLIP, 0)] + ([(GLUE, 0)] if GLUE in have and not glue_first else [])
        out[flip_at:flip_at] = after
        if glue_first:
            lead = [(GLUE, 0)]
    else:
        lead = [(k, 0) for k in (GATE_FLIP, GLUE) if k in have]
    return lead + out


def saved(sh):
    """sh["order"] as tags, or None (none saved: the default)."""
    o = sh.get("order")
    if not isinstance(o, list):
        return None
    return tags_of([k for k in o[:MOST] if k in KINDS])


def order_of(sh, kept=None):
    """Shape sh's steps in the order they run (tags; see the module's notes). kept: a saved order to use (tags)
    instead of sh["order"]."""
    s = kept if kept is not None else saved(sh)
    if s is None:
        return default_order(sh)
    have = own_steps(sh)
    base = core(sh, s)
    there = set(base)
    groups, anchor, placed = {}, None, set()
    for t in s:
        if t[0] in OWN:
            if t[1] == 0 and t[0] in have and t[0] not in placed:
                groups.setdefault(anchor, []).append(t)
                placed.add(t[0])
        elif t in there:
            anchor = t
    out = list(groups.get(None, ()))
    for t in base:
        out.append(t)
        out += groups.get(t, ())
    return out + [(k, 0) for k in OWN if k in have and k not in placed]


# RUNNING IN THAT ORDER (stage B): a shape with an order of its own (sh["order"]) or steps switched off is made as
# expanded(sh): every step one more entry of its note tool steps (the runner's own tools below, engine.fx_notes runs
# them one after another like the pages, each made on top of the notes before it, remembered per step). A shape
# without either goes the old way (engine.fx_notes as it was = old files and every shape made before, exactly).
RUNNER = ("glue0", "gate_flip", "sustain", "extend", "subtract", "colours")  # ("colours": a group of colour steps)
TURNS = ("flip", "turn")
ALWAYS = [False]  # (checks only: every shape made by expanded, its default order too: the same notes as the old way)


def off_tags(sh):
    """The steps of sh switched off (a set of tags; pages: their own "off", not here)."""
    return {(k, n) for k, n in clean_off(sh.get("off")) or ()}


def runs_own_way(sh):
    """Is sh made by expanded (an order of its own, steps switched off, or a step being edited with the steps after
    it skipped: "_upto", App.edit_view)?"""
    return not sh.get("_run") and (ALWAYS[0] or sh.get("order") is not None or bool(sh.get("off"))
                                   or sh.get("_upto") is not None or sh.get("_before") is not None)


def before_view(sh, tag):
    """sh made only up to the step BEFORE step tag (tag and every later one skipped): the notes tag gets (a note
    tool page's window: Trim's longest row, Copy along a path's preview; fx.before_page). Never saved."""
    return dict(sh, _before=[tag[0], tag[1]])


# EDITING ONE STEP (stage C, user 2026-11-11: every step, like the colour effects): while a step's own window
# changes it, the steps after it are skipped (sh["_upto"] = its tag, never saved; App.edit_view), so it's quick and
# shows exactly what it does; they come back when the user leaves it.

def hidden_after(sh, tag):
    """The steps of sh after step tag (kind, n) that run now (switched-off ones left out): what editing tag hides;
    [] = none (tag is the last, or sh doesn't have it)."""
    tags = order_of(sh)
    tag = tuple(tag)
    if tag not in tags:
        return []
    off = off_tags(sh)
    fx = sh.get("fx") or []
    return [t for t in tags[tags.index(tag) + 1:]
            if t not in off and not (t[0] == FX and t[1] < len(fx) and fx[t[1]].get("off") is True)]


def cut_needed(sh, tag):
    """Does editing tag need the runner (sh["_upto"])? Yes on a shape made its own way, or when a step that isn't a
    colour effect comes after it (a page, Sustain...); the colour effects alone after it: the Recolour's own cut (rc
    "_upto", as before)."""
    return runs_own_way(sh) or tag[0] not in COLOURS or any(t[0] not in COLOURS for t in hidden_after(sh, tag))


def upto_view(sh, tag):
    """sh made only up to step tag (the ones after it skipped): a shape the runner makes."""
    return dict(sh, _upto=[tag[0], tag[1]])


def colour_input(sh, tag):
    """The shape whose notes colour step tag of sh works on, with only the colour effects of its group up to tag as
    its Recolour (E["recolour"]), or None: sh isn't made its own way (its Recolour's notes are the ones without it,
    as before) or tag doesn't run. For the overlays and Live brushing (roll_centre.stir_made, roll_liquify)."""
    if not runs_own_way(sh) or tuple(tag) not in order_of(sh):
        return None
    E = expanded(upto_view(sh, tag))
    return E if E is not None and "recolour" in E else None


def expanded(sh):
    """sh as the runner makes it, or None (the old way: runs_own_way says no, or nothing to run). Its steps in
    order_of's order, the ones switched off left out, the ones after a colour step being edited (rc "_upto") too;
    each OWN step = a runner entry of sh["fx"] (glue before the pages stays at the bottom while it runs first);
    colour steps next to each other = one "colours" entry (recolour.part_of; "full": no page after it, so it colours
    as the Recolour at the end does), the ones at the very end = the shape's Recolour as always. A flip / turn right
    after colour steps runs before them (the colours follow a flip by their frame, recolour.carried: the same look).
    A picture: no glue / pages (as always), only Sustain, Gate extend / subtract and the colours."""
    if not runs_own_way(sh):
        return None
    from notes.recolour import PAGE, clean_recolour, full_order, part_of, with_order as rc_order
    from notes.fx import is_page
    tags = order_of(sh)
    if not tags:
        return None
    raw = sh.get("recolour") if isinstance(sh.get("recolour"), dict) else None
    rc = clean_recolour(raw) if raw else None
    fx = sh.get("fx") or []
    colours_all = [t for t in tags if t[0] in COLOURS]
    u = raw.get("_upto") if raw else None
    if rc and isinstance(u, int) and not isinstance(u, bool):  # (a colour step being edited: the later ones skipped)
        o, places = full_order(rc, sum(1 for st in fx if is_page(st)))
        if u in places and o[places.index(u)] != PAGE:
            p = places.index(u)
            t = (o[p], o[:p].count(o[p]))
            if t in tags:
                tags = tags[:tags.index(t) + 1]
    upto = sh.get("_upto")  # (any step being edited: the later ones skipped, stage C)
    if isinstance(upto, (list, tuple)) and len(upto) == 2 and tuple(upto) in tags:
        tags = tags[:tags.index(tuple(upto)) + 1]
    before = sh.get("_before")  # (the notes a step gets: it and the later ones skipped, before_view)
    if isinstance(before, (list, tuple)) and len(before) == 2 and tuple(before) in tags:
        tags = tags[:tags.index(tuple(before))]
    inside = in_whole(sh)  # (a sliced piece: run inside its whole's steps)
    # (glue ordered after one of those: it glues the piece's notes as they come out of the whole, so it can't go to
    # the bottom; there it glued before the Sustain and held notes outside the box changed, sweep 292805004)
    glue_late = bool(inside) and (GLUE, 0) in tags and any(
        t[0] in inside for t in tags[:tags.index((GLUE, 0))])
    tags = [t for t in tags if t[0] not in inside]
    off = off_tags(sh)
    tags = [t for t in tags if t[0] == FX or t not in off]
    if "picture" in sh:
        tags = [t for t in tags if t[0] not in (FX, GLUE)]
    seq = []
    for t in tags:
        if t[0] == FX and fx[t[1]]["tool"] in TURNS:
            j = len(seq)
            while j and seq[j - 1][0] in COLOURS:
                j -= 1
            seq.insert(j, t)
        else:
            seq.append(t)
    items = []
    for t in seq:
        if t[0] in COLOURS and items and items[-1][0] == "c":
            items[-1][1].append(t)
        else:
            items.append(["c", [t]] if t[0] in COLOURS else ["t", t])
    groups = [it[1] for it in items if it[0] == "c"]
    last_page = max((p for p, t in enumerate(seq) if t[0] == FX and is_page(fx[t[1]])), default=-1)
    have = own_steps(sh)
    E = {k: v for k, v in sh.items() if k not in ("order", "off", "recolour", "_upto", "_before") + LENGTHS}
    E["_run"] = True  # (made so: never expanded again)
    if raw and sh.get("cut") and sh.get("order") is not None and (FX in sh["order"] or GLUE in sh["order"]) \
            and not fx:
        # (a Stir piece naming its whole's pages: the order it was cut with, for engine.stirred_piece to make the
        # whole as it was, else its colour steps ran after the pages and the piece took other colours than the whole)
        E["_order_was"] = [list(sh["order"]), copy.deepcopy(raw.get("order"))]
    if inside:  # (its whole made with them, in this order: sliced.source)
        E["_whole"] = dict({k: copy.deepcopy(sh[k]) for k in inside}, order=list(sh["order"]),
                           **({"off": copy.deepcopy(sh["off"])} if sh.get("off") else {}))
        if "sustain" in inside:  # (held inside the whole: its colours keep the held overlaps, case 492791693)
            E["_sus"] = True
    if GATE_FLIP in have:
        E.pop("gate_flip", None)
    if GLUE in have:
        E["_noglue0"] = True  # (no glue at the bottom unless it runs first: below)
    base = dict(rc, most=raw["most"]) if rc and raw.get("most") == 16 else rc
    out, gate = [], False
    for idx, (what, t) in enumerate(items):
        if what == "c":
            if len(groups) == 1 and t == colours_all and idx == len(items) - 1:  # (every colour step together at the
                # end: the Recolour as it is)
                sub = rc_order({k: v for k, v in raw.items() if k != "_upto"}, [k for k in full_order(rc, 0)[0]
                                                                              if k != PAGE])
            else:
                sub = part_of(base, t, t is groups[0])
            if sub is None:
                continue
            if idx == len(items) - 1:
                E["recolour"] = sub
                if gate:
                    E["_gate"] = True
                continue
            full = seq.index(t[0]) > last_page
            out.append(dict({"tool": "colours", "rc": sub}, **({"full": True, "gate": gate} if full else {})))
            continue
        k, n = t
        if k == FX:
            out.append(fx[n])
        elif k == GLUE:
            if not out and not glue_late:
                del E["_noglue0"]
            else:
                out.append({"tool": "glue0"})
        elif k == GATE_FLIP:
            out.append({"tool": "gate_flip"})
            gate = True
        else:
            out.append({"tool": k, "cfg": sh[k]})
            if k == "sustain":
                E["_sus"] = True
    E.pop("fx", None)
    if out:
        E["fx"] = out
    return E


def _lengths_inside(sh):
    """sh's Sustain / Gate extend / subtract ordered before one of its running note tool pages (switched-off ones
    left out): [(kind, place among sh's fx entries)], in their order."""
    from notes.fx import is_page
    fx = sh.get("fx") or []
    if sh.get("order") is None or not (fx or sh.get("glue")):
        return []
    off = off_tags(sh)
    tags = [t for t in order_of(sh) if t[0] == FX or (t[0] in LENGTHS and t not in off and t[0] in sh)
            or (t == (GLUE, 0) and sh.get("glue") and t not in off)]
    # (any of the whole's entries after it, a velocity line / glue place too: case 492791693, Glue after Sustain on
    # the whole merged stacked held notes; the piece held its notes only after the whole's Glue and they came back)
    last = max((p for p, t in enumerate(tags) if t[0] == FX and (is_page(fx[t[1]]) or p > 0 and any(
        q[0] == FX and is_page(fx[q[1]]) for q in tags[:p])) and not fx[t[1]].get("off")), default=-1)
    # (the whole's own Glue boxes ordered after a Sustain / Gate extend / subtract count the same: they glue the notes
    # as the hold left them; the piece held only after the Glue, so notes the Glue had merged came back, sweep cases
    # 319179595 / 419192726 / 419174800)
    last = max([last] + [p for p, t in enumerate(tags) if t[0] == GLUE and any(q[0] in LENGTHS for q in tags[:p])])
    out, n = [], 0
    for p, t in enumerate(tags):
        if t[0] == FX:
            n += 1
        elif t[0] in LENGTHS and p < last:
            out.append((t[0], n))
    return out


def in_whole(sh):
    """The Sustain / Gate extend / subtract a sliced piece sh runs INSIDE its whole's steps (ordered before one of
    the whole's pages: made with the whole, on all its notes, before the piece is cut out: sliced.source hands them
    over as sh["_whole"], engine.whole_tools / the whole's own runner put them in), so the piece's own runner leaves
    them out. An empty set for every other shape."""
    if not sh.get("cut") or sh.get("order") is None or not any(k in sh for k in LENGTHS):
        return set()
    if (GATE_FLIP in sh["order"] and sh.get("gate_flip") is True and sh["kind"] == "custom"
            and not sh.get("fx")):  # (a drawn piece's Gate aware flip is done inside its whole (gateflip.LEFT_OUT), so
        # a Sustain / extend / subtract ordered BEFORE it must run there too, else the flip came first and the
        # piece's notes were another set than the whole's, cut: sweep 219191833)
        before = sh["order"][:sh["order"].index(GATE_FLIP)]
        off = off_tags(sh)
        return {k for k in LENGTHS if k in before and k in sh and (k, 0) not in off}
    if FX not in sh["order"] and not (GLUE in sh["order"] and not sh.get("glue") and sh["cut"].get("whole", {}).get(
            "glue")):  # (an order saying nothing about the whole's pages (made on the piece: glue added after its
        return set()   # Sustain): they keep their default place, first; sweep 292805004. The WHOLE's glue boxes
        # ordered after the Sustain are the other way: the Sustain goes inside the whole)
    from notes.fx import is_page
    own_fx = sh.get("fx") or []
    if own_fx and not any(is_page(st) for st in own_fx) and sh["order"].count(FX) <= len(own_fx):
        return set()  # (the "fx" in the order is only the piece's own velocity line / flip, drawn after its Sustain:
        # not the whole's pages; else the Sustain was made inside the whole and stacked notes came back, sweep 492804781)
    from notes.sliced import source, whole_made
    if not whole_made(sh["cut"]):
        return set()
    got = source(sh)
    return {k for k, _ in _lengths_inside(got[0])} if got else set()


def whole_steps(sh):
    """A sliced piece's whole sh's note tool steps as engine.whole_tools runs them: (its fx, with a "glue0" entry
    where its glue before the pages runs when its order puts that later, and its Sustain / Gate extend / subtract
    ordered before a page as entries there (in_whole); does that glue run first, before them all? (False: put in, or
    switched off)."""
    fx = list(sh.get("fx") or [])
    first = True
    if runs_own_way(sh) and GLUE in own_steps(sh):
        if (GLUE, 0) in off_tags(sh):
            first = False
        else:
            tags = [t for t in order_of(sh) if t[0] in (FX, GLUE)]
            k = tags.index((GLUE, 0))
            if k:
                n = sum(1 for t in tags[:k] if t[0] == FX)
                fx = fx[:n] + [{"tool": "glue0"}] + fx[n:]
                first = False
    inside = _lengths_inside(sh)
    if inside:
        out, n = [], 0
        put = {}
        for k, at in inside:
            put.setdefault(at, []).append({"tool": k, "cfg": sh[k]})
        for st in fx:
            out += put.pop(n, [])
            out.append(st)
            if st["tool"] != "glue0":
                n += 1
        for rest in put.values():  # (held after every page: the whole's Glue comes after it)
            out += rest
        fx = out
    return fx, first


def moved(sh, a, b):
    """A copy of shape sh with the step at place a of order_of(sh) moved to place b (the Properties window's drag,
    stage D), or None when that order can't be kept (a flip / turn / velocity line / glue place put before every
    note tool page: there it would be the drawing itself, not a step). The three places the order lives in are
    rewritten: sh["fx"] (pages and the entries between them in their new order: the page tabs follow), the
    Recolour's order (its "page" entries; a Stir passing a Stir = the tabs swap, like the Effect order window did)
    and sh["order"] (the other steps)."""
    from notes.fx import is_page
    from notes.recolour import PAGE, _restacked, clean_recolour
    from notes.sliced import steps_kept
    tags = order_of(sh)
    if not (0 <= a < len(tags) and 0 <= b < len(tags)) or a == b:
        return None
    new = list(tags)
    new.insert(b, new.pop(a))
    fx = sh.get("fx") or []
    fx_tags = [t for t in new if t[0] == FX]
    if fx_tags and not is_page(fx[fx_tags[0][1]]) and not steps_kept(sh):
        return None
    out = dict(sh)
    at = {}
    if fx:
        out["fx"] = [fx[n] for _, n in fx_tags]
        at = {n: k for k, (_, n) in enumerate(fx_tags)}
    raw = sh.get("recolour")
    rc = clean_recolour(raw) if isinstance(raw, dict) and not sh.get("hz") else None
    if rc:
        seq = [(PAGE, 0) if t[0] == FX else t for t in new
               if t[0] in COLOURS or t[0] == FX and is_page(fx[t[1]])]
        got = _restacked(rc, seq)
        if got:
            out["recolour"] = got
        else:
            out.pop("recolour", None)
    seen, want = {}, []
    for k, n in new:
        if k == FX:
            want.append((FX, at[n]))
        elif k in COLOURS:
            want.append((k, seen.get(k, 0)))
            seen[k] = seen.get(k, 0) + 1
        else:
            want.append((k, n))
    off = [[k, n] for k, n in (out.get("off") or ())]
    if off and any(k in COLOURS for k, _ in off):  # (an off colour step keeps being the one switched off)
        place = {}
        for k, n in new:
            if k in COLOURS:
                place.setdefault(k, []).append(n)
        off = [[k, place[k].index(n)] if k in place and n in place[k] else [k, n] for k, n in off]
        out["off"] = off
    with_order(out, want)
    return out if order_of(out) == want else None


def with_order(sh, tags):
    """sh (changed) keeping order tags (order_of's form): saved only when it differs from the default."""
    if tags == default_order(sh):
        sh.pop("order", None)
    else:
        sh["order"] = [k for k, _ in tags]
    return sh


def clean_order(o):
    """A saved sh["order"] -> a list of kinds, or None."""
    if not isinstance(o, list):
        return None
    out = [k for k in o[:MOST] if isinstance(k, str) and k in KINDS]
    return out or None


def clean_off(o):
    """A saved sh["off"] -> [[kind, n], ...] (no doubles, OWN / colour kinds only), or None."""
    if not isinstance(o, list):
        return None
    out = []
    for t in o[:MOST]:
        if (isinstance(t, (list, tuple)) and len(t) == 2 and t[0] in OWN + COLOURS and type(t[1]) is int
                and 0 <= t[1] < MOST and [t[0], t[1]] not in out):
            out.append([t[0], t[1]])
    return out or None


def kept_in(out, sh):
    """clean_shape's part: sh's order and off list put into out (the shape being cleaned; its steps already in),
    the order only when it still differs from the default there."""
    o = clean_order(sh.get("order"))
    if o:
        with_order(out, order_of(out, tags_of(o)))
    off = clean_off(sh.get("off"))
    if off:
        out["off"] = off
    return out


def is_off(sh, tag):
    """Is step tag (kind, n) of sh switched off? (A page: its own "off" in sh["fx"].)"""
    if tag[0] == FX:
        fx = sh.get("fx") or []
        return 0 <= tag[1] < len(fx) and fx[tag[1]].get("off") is True
    return [tag[0], tag[1]] in (sh.get("off") or ())


def with_off(sh, tag, off):
    """sh (changed) with step tag switched off (off True) or on. A page: its own "off"; other fx entries can't be."""
    if tag[0] == FX:
        from notes.fx import is_page
        fx = sh.get("fx") or []
        if 0 <= tag[1] < len(fx) and is_page(fx[tag[1]]):
            st = {k: v for k, v in fx[tag[1]].items() if k != "off"}
            if off:
                st["off"] = True
            sh["fx"] = fx[:tag[1]] + [st] + fx[tag[1] + 1:]
        return sh
    got = [t for t in (sh.get("off") or ()) if list(t) != [tag[0], tag[1]]]
    if off:
        got.append([tag[0], tag[1]])
    if got:
        sh["off"] = got
    else:
        sh.pop("off", None)
    return sh


# ORDER ADDED (App.shapes_changed, after every change): a step that wasn't there before goes at the end; one taken
# away leaves the order (so put back later, it goes at the end again).

def signature(sh):
    """What after_change compares: sh's fx entries (the very dicts), its OWN steps, its colour steps (the Stir tabs
    and Reverse swaps, how many of each kind)."""
    return list(sh.get("fx") or ()), frozenset(own_steps(sh)), _colour_sig(sh)


def _colour_sig(sh):
    r = sh.get("recolour")
    if not isinstance(r, dict):
        return None
    from notes.recolour import order_counts, turns_of
    st = r.get("stir")
    return list(st) if isinstance(st, list) else [st] if st else [], list(turns_of(r)), order_counts(r)


def _colours_gone(old, new):
    """The colour steps (tags, numbered as in old) taken away from signature part old to new: a Stir tab / Reverse
    by which one went (the last ones when it can't tell), the others by kind."""
    if not old:
        return []
    new = new or ([], [], {})
    out = []
    for kind in COLOURS:
        a, b = old[2].get(kind, 0), new[2].get(kind, 0)
        if a <= b:
            continue
        at = None
        if kind in ("stir", "turned"):
            i = 0 if kind == "stir" else 1
            if len(old[i]) == a and len(new[i]) == b:
                at = _gone_at(old[i], new[i])
        out += [(kind, n) for n in (at if at is not None else range(b, a))]
    return out


def _gone_at(old, new):
    """Where entries of the fx list old were taken out to give new (indices in old), or None: can't tell."""
    out, j = [], 0
    for i, st in enumerate(old):
        if j < len(new) and new[j] is st:
            j += 1
        else:
            out.append(i)
    if j == len(new) and len(old) - len(out) == len(new):
        return out
    out, j = [], 0  # (copies of the dicts: matched by their tool, then by what they hold)
    for i, st in enumerate(old):
        if j < len(new) and new[j] == st:
            j += 1
        else:
            out.append(i)
    return out if j == len(new) and len(old) - len(out) == len(new) else None


def after_change(sh, before):
    """sh (changed) after an edit: before = signature(sh as it was). New OWN steps and fx entries go at the end of
    its order; taken away ones leave it. Nothing to do: sh as it is."""
    fx_old, own_old = before[:2]
    col_old = before[2] if len(before) > 2 else None
    fx_new = list(sh.get("fx") or ())
    own_new = own_steps(sh)
    added = [k for k in OWN if k in own_new and k not in own_old]
    gone = {k for k in own_old if k not in own_new}
    grew = len(fx_new) > len(fx_old) and all(a is b or a == b for a, b in zip(fx_old, fx_new))
    shrank = len(fx_new) < len(fx_old)
    s = saved(sh)
    lost = _colours_gone(col_old, _colour_sig(sh)) if s is not None else []
    if (s is not None and not (added or gone or grew or shrank or lost) and fx_new and len(fx_new) == len(fx_old)
            and fx_new[-1] is not fx_old[-1] and fx_new[-1].get("tool") == fx_old[-1].get("tool") == "vel"
            and all(a is b for a, b in zip(fx_old[:-1], fx_new[:-1]))):
        # (a velocity line drawn over the last one replaced it (fx.with_velocity): drawn on the notes as shown, so it
        # goes at the END like a new one; left in the old one's place before a Gate aware flip it set the mirrored
        # notes, sweep 177544490)
        # (only past a Gate aware flip / Sustain / Gate extend / subtract after it: moved past colour steps, it changed
        # how a Warp coloured the notes, sweep 192797645)
        last = (FX, len(fx_new) - 1)
        s = order_of(sh, s)
        later = s[s.index(last) + 1:] if last in s else []
        if any(t[0] in (GATE_FLIP,) + tuple(LENGTHS) for t in later):
            return with_order(sh, order_of(sh, [t for t in s if t != last] + [last]))
        return sh
    if not (added or gone or grew or shrank or lost):
        return sh
    if lost:  # (a Stir tab / Reverse taken out: its own entry leaves, so the later ones keep their places)
        s = tags_of([k for k, n in s if (k, n) not in lost])
    if s is None:
        if not added and not grew:
            return sh  # (the default is still right)
        was = dict(sh)  # (the order as it was: without the new steps)
        if grew:
            was["fx"] = fx_new[:len(fx_old)] or None
        for k in added:
            was = _without(was, k)
        s = default_order(was)
    elif not shrank or sum(1 for t in s if t[0] == FX) != len(fx_old):
        s = order_of(sh, s)  # (a saved list not matching the old fx entries one by one: as it reads now)
        shrank = False
    if shrank:
        at = _gone_at(fx_old, fx_new)
        if at is None:  # (can't tell which: the last ones)
            at = list(range(len(fx_new), len(fx_old)))
        drop = set(at)
        kinds, n = [], 0
        for k, _ in s:
            if k == FX:
                if n not in drop:
                    kinds.append(k)
                n += 1
            else:
                kinds.append(k)
        s = tags_of(kinds)
    s = [t for t in s if t[0] not in gone]
    new = []
    if grew:
        n = sum(1 for t in s if t[0] == FX)
        new = [(FX, i) for i in range(n, len(fx_new))]
    new += [(k, 0) for k in added if (k, 0) not in s]
    if len(new) > 1:  # (several new in one change (glue + a page...): among themselves as the default has them)
        d = {t: p for p, t in enumerate(default_order(sh))}
        new.sort(key=lambda t: d.get(t, len(d)))
    return with_order(sh, order_of(sh, s + new))


def _without(sh, kind):
    """sh without its OWN step kind (a copy where needed)."""
    out = dict(sh)
    if kind == GLUE:
        out.pop("glue", None)
        from notes.fx import without_glue
        if out.get("fx"):
            out["fx"] = without_glue(out["fx"])
    elif kind == GATE_FLIP:
        out.pop("gate_flip", None)
    else:
        out.pop(kind, None)
    return out


def carried(sh, src):
    """sh (changed) taking src's saved order and off list (an Add between step from its nearer end, a copy)."""
    for key in ("order", "off"):
        if src.get(key):
            sh[key] = copy.deepcopy(src[key])
        else:
            sh.pop(key, None)
    o = clean_order(sh.get("order"))
    if o:
        with_order(sh, order_of(sh, tags_of(o)))
    if sh.get("off"):  # (only the steps sh has: an Add between step without a step only one end had)
        have = set(order_of(sh))
        off = [t for t in sh["off"] if (t[0], t[1]) in have]
        if off:
            sh["off"] = off
        else:
            sh.pop("off")
    return sh
