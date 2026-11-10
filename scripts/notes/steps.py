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
    rc = clean_recolour(r) if isinstance(r, dict) else None
    if not rc:
        return []
    return full_order(rc, n_pages)[0]


def core(sh):
    """The fx entries and the colour steps in the order they run (tags): a colour step sits right before the page
    its Recolour puts it in front of (after the flips / velocities before that page, as engine.pages_coloured
    does), the rest after every fx entry."""
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
    out, j = [], 0
    for i, st in enumerate(fx):
        if is_page(st):
            if j < len(segs):
                out += segs[j]
            j += 1
        out.append((FX, i))
    for s in segs[j:]:
        out += s
    return out


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
    base = core(sh)
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
    """What after_change compares: sh's fx entries (the very dicts) and its OWN steps."""
    return list(sh.get("fx") or ()), frozenset(own_steps(sh))


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
    fx_old, own_old = before
    fx_new = list(sh.get("fx") or ())
    own_new = own_steps(sh)
    added = [k for k in OWN if k in own_new and k not in own_old]
    gone = {k for k in own_old if k not in own_new}
    grew = len(fx_new) > len(fx_old) and all(a is b or a == b for a, b in zip(fx_old, fx_new))
    shrank = len(fx_new) < len(fx_old)
    if not (added or gone or grew or shrank):
        return sh
    s = saved(sh)
    if s is None:
        if not added and not (grew and own_new):
            return sh  # (the default is still right: nothing of OWN to keep in place)
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
    if grew:
        n = sum(1 for t in s if t[0] == FX)
        s = s + [(FX, i) for i in range(n, len(fx_new))]
    s = s + [(k, 0) for k in added if (k, 0) not in s]
    return with_order(sh, order_of(sh, s))


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
    return sh
