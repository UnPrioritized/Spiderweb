"""The Properties window's words and numbers: what each line of it shows and what a step's main number or its ×
does to a shape (moved out of properties_window.py; PLAN.md "Properties")."""

import copy

import numpy as np

from files.lang import tr
from notes import steps as S
from notes.custom import ROUND, pack_notes, unpack_notes
from notes.fx import is_page, pages
from notes.recolour import (clean_recolour, page_removed, shape_order, shape_step_removed, stirs_of,
                            tags_of as rc_tags, turned_map)


def kind_text(sh):
    if sh.get("hz"):
        return tr("props.kind_hz")
    if "picture" in sh:
        return tr("props.kind_picture")
    if "notes" in sh:
        return tr("props.kind_notes")
    if sh.get("text"):
        return tr("props.kind_text")
    from notes.engine import KINDS
    return KINDS.get(sh.get("kind"), sh.get("kind") or "?")


MADE_BY = ("plain", "fill_gaps", "fold", "line_flip", "split_lines", "mask")


def made_text(sh):
    """"<command>, from <shape>" for a shape a one-time command made (sh["made_by"]), or ""."""
    mb = sh.get("made_by")
    if not (isinstance(mb, list) and len(mb) == 2 and mb[0] in MADE_BY):
        return ""
    return tr("props.made_" + mb[0], name=mb[1])


def fill_text(sh):
    from window.panel_custom import FILL_CHOICES
    from window.panel_nested import nested_on
    from window.panel_ripples import ripples_on
    from window.panel_tiles import tiles_on
    shown = ("nested" if nested_on(sh) else "tiles" if tiles_on(sh) else "ripples" if ripples_on(sh)
             else sh.get("fill"))
    return next((t for v, t, _ in FILL_CHOICES if v == shown), shown or "?")


def colours_text(sh):
    cy = sh.get("cycle")
    out = tr("props.colours_cycle", n=cy.get("n", 1), by=tr("colours." + cy["by"])) if cy and cy.get("by") \
        else tr("colours.off")
    areas = {a[2] for a in sh.get("areas") or () if len(a) > 2 and a[2]}
    if areas:
        out += tr("props.area_colours", n=len(areas))
    return out


def channel_text(app, sh):
    if sh.get("into"):
        return tr("props.channel_into")
    if sh.get("own_slots"):
        return tr("props.channel_own")
    mode = app.channel_mode.get()
    return tr({"auto": "app.multi_channel", "raw": "app.as_drawn"}.get(mode, "app.single_channel"))


def clumps_hidden(sh):
    """The colour steps of sh showing nothing because Split: Clumps runs after them (hunt 11-10 #5: greyed, a tip)."""
    from notes.recolour import hidden_by_clumps
    rc = clean_recolour(sh.get("recolour")) if not sh.get("hz") else None
    if not rc:
        return set()
    o = shape_order(sh)
    tags = rc_tags(o)
    return {tags[p] for p in hidden_by_clumps(rc, o)}

def number_of(sh, tag, ppq):
    """Step tag's main number as its box shows it (ticks for lengths), or None (empty)."""
    kind, n = tag
    if kind in S.COLOURS:
        rc = clean_recolour(sh.get("recolour")) or {}
        if kind == "mode":
            return rc.get("amount")
        if kind == "stir":
            st = stirs_of(rc)
            return st[n].get("turn", 0.0) if n < len(st) else None
        if kind in ("warp", "liquify"):
            d = rc.get(kind) or {}
            return float(d.get("strength", 100.0))
        return None
    cfg = sh.get(kind)
    if not isinstance(cfg, dict):
        return None
    if kind == "sustain":
        return cfg["most"] * ppq if cfg.get("most") else None
    if kind == "extend":
        return cfg.get("by", 0.0) if cfg.get("unit") == "pct" else cfg.get("by", 0.0) * ppq
    if kind == "subtract":
        return cfg.get("by", 0.0) * ppq
    return None


def with_number(sh, tag, v, ppq):
    """A copy of sh (only the part step tag lives in) with that step's main number v (as its box shows it)."""
    kind, n = tag
    out = dict(sh)
    if kind in S.COLOURS:
        rc = dict(clean_recolour(sh.get("recolour")) or {})
        if kind == "mode":
            rc["amount"] = round(v, 3)
        elif kind == "stir":
            stack = [dict(st) for st in stirs_of(rc)]
            stack[n]["turn"] = round(v, 3)
            rc.pop("stir_held", None)
            rc["stir"] = stack if len(stack) > 1 else stack[0]
        else:
            rc[kind] = dict(rc.get(kind) or {}, strength=round(v, 3))
        out["recolour"] = rc
        return out
    cfg = dict(sh.get(kind) or {})
    if kind == "sustain":
        if v:
            cfg["most"] = v / ppq
        else:
            cfg.pop("most", None)
    elif kind == "extend":
        cfg["by"] = v if cfg.get("unit") == "pct" else v / ppq
    else:
        cfg["by"] = v / ppq
    out[kind] = cfg
    return out


def removed(app, sh, tag):
    """Step tag taken off shape sh (the ×; the caller made the undo step)."""
    kind, n = tag
    from window.tool_window import TOOL_WINDOWS, ToolWindow
    for attr in TOOL_WINDOWS:  # (a note tool window's try kept first: it follows the pages there are now)
        w = getattr(app, attr, None)
        if w:
            w.settle()
    if kind == S.FX:
        fx = list(sh.get("fx") or [])
        if n >= len(fx):
            return
        if fx[n]["tool"] == "glue":  # (only this place's boxes go: the glue before the pages stays)
            return glue_part_removed(sh, sum(1 for st in fx[:n + 1] if st["tool"] == "glue"))
        if is_page(fx[n]):
            j = sum(1 for st in fx[:n] if is_page(st))
            rc = clean_recolour(sh.get("recolour"))
            if rc:
                got = page_removed(rc, j, len(pages(fx)))
                if got:
                    sh["recolour"] = got
        ToolWindow.put_fx(sh, fx[:n] + fx[n + 1:])
    elif kind == S.GLUE:  # (the boxes glued before the pages only: the later Glue lines keep theirs)
        glue_part_removed(sh, 0)
    elif kind == S.GATE_FLIP:
        from notes.gateflip import turn_velocities
        sh.pop("gate_flip", None)
        turn_velocities(sh)
    elif kind in S.LENGTHS:
        sh.pop(kind, None)
    else:
        colour_removed(sh, tag)
    if kind != S.FX and sh.get("off"):  # (its off mark goes; later ones of its kind move up one)
        off = [[k, m - (k == kind and m > n)] for k, m in sh["off"] if [k, m] != [kind, n]]
        if off:
            sh["off"] = off
        else:
            sh.pop("off", None)


def glue_part_removed(sh, g):
    """One Glue line's boxes taken off sh (g = 0: the glue before the pages, g = k: the k-th glue place's), the others
    kept where they are. Glue on everything (no boxes) = one Glue line: all of it goes. Changes sh."""
    from notes.fx import glue_groups, regrouped, tidy_glue
    from window.tool_window import ToolWindow
    groups = glue_groups(sh.get("glue"), sh.get("fx"))
    if groups is None or not 0 <= g < len(groups):
        sh.pop("glue", None)
    else:
        groups[g] = []
        old = sh.get("fx") or []
        glue, fx = regrouped(groups, old)
        places = iter(st for st in fx or () if st["tool"] == "glue")
        kept = []  # (the places left keep being the same objects: the step order follows them, steps.after_change)
        for j, st in enumerate(old):
            if st["tool"] != "glue":
                kept.append(st)
            elif groups[sum(1 for s in old[:j + 1] if s["tool"] == "glue")]:
                new = next(places)
                st.pop("from", None)
                st.update(new)
                kept.append(st)
        if glue:
            sh["glue"] = glue
        else:
            sh.pop("glue", None)
        ToolWindow.put_fx(sh, kept)
    tidy_glue(sh)


def colour_removed(sh, tag):
    """Colour step tag taken off sh. A Reverse on notes whose stored colours it swapped: once no Recolour keeps
    that swap, the notes get their colours back (else the Reverse would stay, hidden in the notes)."""
    o = shape_order(sh)
    tags = rc_tags(o)
    if tuple(tag) not in tags:
        return
    old = clean_recolour(sh.get("recolour"))
    new = shape_step_removed(sh, tags.index(tuple(tag)))
    if old is None:
        return
    tw = old.get("turned")
    if tw and "notes" in sh and not (new or {}).get("turned"):
        rows = unpack_notes(sh["notes"]).copy()
        low = rows[:, 4] % ROUND
        rows[:, 4] = rows[:, 4] - low + turned_map(tw, back=True)(low)
        sh["notes"] = pack_notes(np.asarray(rows, np.int64))
    if new:
        sh["recolour"] = new
    else:
        sh.pop("recolour", None)


def step_keys(sh, tags):
    """Shape sh's steps (tags in order) as keys alike across shapes: the colour effects, Glue, Gate aware flip,
    Sustain, Gate extend / subtract by kind and number; an fx entry by what it is (its tool, a flip's way, a turn's
    angle) and how many of those came before it ("Strum 2" = the 2nd Strum page)."""
    fx = sh.get("fx") or []
    seen, out = {}, []
    for kind, n in tags:
        if kind != S.FX:
            out.append((kind, n))
            continue
        st = fx[n] if n < len(fx) else {}
        what = (st.get("tool"), st.get("axis"), st.get("deg"))
        m = seen.get(what, 0)
        seen[what] = m + 1
        out.append((S.FX,) + what + (m,))
    return out


def place_in(notes):
    """(first tick, length in ticks, lowest key, highest key) of notes, or None (no notes)."""
    if not len(notes):
        return None
    a, z = int(notes[:, 0].min()), int(notes[:, 1].max())
    return a, z - a, int(notes[:, 2].min()), int(notes[:, 2].max())


def is_custom(sh):
    """A drawn custom shape with its own inside (not plain notes / an image)."""
    return sh.get("kind") == "custom" and "notes" not in sh and "picture" not in sh


LIKE_KEYS = ("fx", "glue", "sustain", "extend", "subtract")


def made_like(sh, src):
    """Shape sh's steps made like src's: every step, its order, its numbers, its off tick (Make the others like
    this). Steps sh can't have are left out: colour effects on a Hz bass, note tool pages / glue on an image, Sustain
    / Gate extend / subtract on a Hz bass or an image, Gate aware flip on a shape that isn't a flipped custom shape;
    a sliced piece keeps its own flips (its notes come from its whole's pages). Returns how many were left out.
    Changes sh."""
    from notes.fx import tidy_glue
    from notes.gateflip import is_flipped, is_pasted_flipped, turn_velocities
    from notes.mask import is_mask
    from notes.recolour import can_recolour, turns_of
    from notes.sliced import steps_kept
    if is_mask(sh):
        return 1
    left = 0
    have, want = S.own_steps(sh), S.own_steps(src)
    if (S.GATE_FLIP in want) != (S.GATE_FLIP in have):
        can = sh.get("kind") == "custom" and (is_flipped(sh) or is_pasted_flipped(sh))
        if S.GATE_FLIP in want and not can:
            left += 1
        else:  # (switched as Gate aware flip does: the velocities turned round too)
            if S.GATE_FLIP in have:
                del sh["gate_flip"]
            else:
                sh["gate_flip"] = True
            turn_velocities(sh)
    picture, hz = "picture" in sh, bool(sh.get("hz"))
    for key in LIKE_KEYS:
        if (picture and key in ("fx", "glue") or (picture or hz) and key in S.LENGTHS
                or key == "fx" and steps_kept(sh)):
            left += bool(src.get(key))
            continue
        if src.get(key):
            sh[key] = copy.deepcopy(src[key])
        else:
            sh.pop(key, None)
    tidy_glue(sh)
    old = clean_recolour(sh.get("recolour"))
    got = clean_recolour(src.get("recolour"))
    if not can_recolour(sh):
        left += bool(got)
    else:
        new = None
        if got:
            new = copy.deepcopy(got)
            turns = turns_of(got)
            new.pop("turned", None)
            new.pop("turns", None)
            if old and old.get("turned"):  # (its notes hold their own swap: it stays, unswapped at the start)
                new["turned"] = old["turned"]
            if turns:
                new["turns"] = turns
            new = clean_recolour(new)
        tw = (old or {}).get("turned")
        if tw and "notes" in sh and not (new or {}).get("turned"):  # (as colour_removed: the notes' colours back)
            rows = unpack_notes(sh["notes"]).copy()
            low = rows[:, 4] % ROUND
            rows[:, 4] = rows[:, 4] - low + turned_map(tw, back=True)(low)
            sh["notes"] = pack_notes(np.asarray(rows, np.int64))
        if new:
            sh["recolour"] = new
        else:
            sh.pop("recolour", None)
    S.carried(sh, src)  # (its order and off ticks)
    if sh.get("off"):  # (steps it couldn't take: their off ticks go)
        there = set(S.order_of(sh))
        off = [t for t in sh["off"] if tuple(t) in there]
        if off:
            sh["off"] = off
        else:
            sh.pop("off", None)
    return left


def settle_steps(app, sh):
    """sh's steps were set as a whole here: App.steps_noted takes them as they are (it would put steps new to sh at
    the end of its order)."""
    sigs = getattr(app, "_step_sigs", None)
    if sigs is not None:
        sigs[id(sh)] = (sh, S.signature(sh))
