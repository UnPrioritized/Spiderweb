"""Kept canvas items (redraw hunt, user 2026-10-31): a redraw says every item it wants, and items made the same way
as last time stay on the canvas. Only the others are deleted / made, in their place in the stacking order, so Tk
paints again only where something changed (a full wipe made it paint the whole board on every click)."""
import bisect
import collections


def in_order(places):
    """The indexes of the longest run of places (None = left out) that only goes up: the kept items that can stay
    where they are."""
    tops, ends, back = [], [], {}  # tops[n] = smallest last place of a run n + 1 long, ends[n] = its index
    for k, p in enumerate(places):
        if p is None:
            continue
        n = bisect.bisect_left(tops, p)
        back[k] = ends[n - 1] if n else None
        if n == len(tops):
            tops.append(p)
            ends.append(k)
        else:
            tops[n], ends[n] = p, k
    out, k = set(), ends[-1] if ends else None
    while k is not None:
        out.add(k)
        k = back[k]
    return out


def _plain(v):
    """An option's value as something that can be compared and remembered: a list as a tuple, a Font (can't be) by
    its Tk name, which is what Tk is given anyway."""
    if isinstance(v, list):
        return tuple(v)
    try:
        hash(v)
        return v
    except TypeError:
        return str(v)


class KeptItems:
    """keep = KeptItems(canvas); keep.begin(); keep.create_line(...) etc. as on the canvas; keep.finish().
    Items the canvas gets any other way are left alone (made after finish = above the kept ones), unless
    finish(rest=True): then they go, as a wipe of the whole canvas would. Anything else asked of it (winfo_width,
    bbox...) is the canvas's own."""

    def __init__(self, canvas):
        self.canvas = canvas
        self.items = []  # [(how it was made, item id)], bottom to top
        self.want = []

    def __getattr__(self, name):
        if name in ("canvas", "items", "want"):  # (not made yet: no endless loop)
            raise AttributeError(name)
        return getattr(self.canvas, name)

    def begin(self):
        self.want = []

    def forget(self):
        """The canvas was wiped some other way: nothing kept."""
        self.items = []

    def _add(self, kind, coords, kw):
        if len(coords) == 1 and isinstance(coords[0], (list, tuple)):
            coords = coords[0]
        self.want.append((kind, tuple(coords), tuple(sorted((k, _plain(v)) for k, v in kw.items()))))

    def create_line(self, *coords, **kw):
        self._add("line", coords, kw)

    def create_rectangle(self, *coords, **kw):
        self._add("rectangle", coords, kw)

    def create_oval(self, *coords, **kw):
        self._add("oval", coords, kw)

    def create_polygon(self, *coords, **kw):
        self._add("polygon", coords, kw)

    def create_image(self, *coords, **kw):
        self._add("image", coords, kw)

    def create_text(self, *coords, **kw):
        self._add("text", coords, kw)

    def finish(self, rest=False, spare=None):
        """rest: everything else on the canvas goes too (but items tagged spare)."""
        c, want = self.canvas, self.want
        if rest:  # (everything else on the canvas goes; kept items deleted some other way are forgotten)
            there = set(c.find_all())
            mine = {iid for _, iid in self.items} | (set(c.find_withtag(spare)) if spare else set())
            strays = [i for i in there if i not in mine]
            if strays:
                c.delete(*strays)
            self.items = [it for it in self.items if it[1] in there]
        pool = collections.defaultdict(collections.deque)  # how made -> (place, id) of the items made so
        for k, (how, iid) in enumerate(self.items):
            pool[how].append((k, iid))
        got = [pool[how].popleft() if pool.get(how) else None for how in want]
        gone = [iid for q in pool.values() for _, iid in q]
        stay = in_order([g[0] if g else None for g in got])
        for k, g in enumerate(got):  # (one that changed places among the kept ones, e.g. picked handles drawn
            if g and k not in stay:  # last: made again in its new place)
                gone.append(g[1])
                got[k] = None
        if gone:
            c.delete(*gone)
        above = [None] * len(want)  # the next kept item above each place: new ones go just under it
        nxt = None
        for k in range(len(want) - 1, -1, -1):
            above[k] = nxt
            if got[k]:
                nxt = got[k][1]
        items = []
        for k, how in enumerate(want):
            if got[k]:
                iid = got[k][1]
            else:
                kind, coords, kw = how
                iid = getattr(c, "create_" + kind)(*coords, **dict(kw))
                if above[k] is not None:
                    c.tag_lower(iid, above[k])
            items.append((how, iid))
        self.items, self.want = items, []
