"""Pictures -> a grid of colour slots (the maths behind "image to notes"; no window, no notes yet).

load() reads a picture file through Tk (PNG / GIF; Tk has no JPG) into linear-light colours + how see-through
each pixel is. cells() averages it down to the note grid (keys x steps), adjust() is brightness / contrast /
saturation, fit_palette() picks up to 16 colours for one or more pictures (several starts, detail-weighted,
locked colours kept), quantise() gives every cell a slot: spread blending (the error of each cell passed on to
its neighbours; cells unlike their surroundings keep their own colour = "keep details"), pattern blending or
none. Colours are matched by how the eye sees them (OKLab) and averaged the way light mixes (linear light).
Grids are rows x cols with row 0 = the picture's top; -1 = empty (see-through)."""

import base64
import hashlib
import os

import numpy as np

MAX_SIDE = 2400  # bigger pictures are averaged down first (memory; detail past the note grid is lost anyway)

SUGGESTED = {  # the "Back to suggested" values (user: only a suggestion; every one is a slider)
    "keys": 128, "steps": 3, "view": "fall", "colours": 15, "focus": 1.0, "blend": "spread", "strength": 1.0,
    "keep": 0.6, "sharpen": 0.0, "brightness": 0.0, "contrast": 0.0, "saturation": 0.0,
    "empty": True,  # see-through parts make no notes (False: they keep the colour under them)
    "step": 1 / 48,  # one grid step, in beats
    "look": "flat", "outline": 1, "shade": True, "join": True,  # how a player draws it (join also makes the notes)
}


# ------------------------------------------------------------------ reading a picture

def fingerprint(path):
    """A short code from the file's bytes (changes if any pixel changes; copying the file keeps it)."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Picture:
    """lin: rows x cols x 3 float32 linear-light colours; alpha: rows x cols float32 0..1 (None = no see-through
    parts); size: the file's own (width, height); path, sig: file and fingerprint."""

    def __init__(self, lin, alpha, size, path=None, sig=None):
        self.lin, self.alpha, self.size, self.path, self.sig = lin, alpha, size, path, sig

    @property
    def aspect(self):
        """width / height of the picture as made."""
        return self.size[0] / self.size[1]


def _ppm_pixels(data):
    """Raw P6 bytes (Tk 9) or base64 text of them (Tk 8.6) -> rows x cols x 3 uint8."""
    if isinstance(data, str):
        data = base64.b64decode(data)
    parts, pos = [], 0
    while len(parts) < 4:  # magic, width, height, maxval (comments not written by Tk)
        while data[pos:pos + 1].isspace():
            pos += 1
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        parts.append(data[pos:end])
        pos = end
    w, h = int(parts[1]), int(parts[2])
    raw = np.frombuffer(data, np.uint8, w * h * 3, pos + 1)
    return raw.reshape(h, w, 3)


def _has_alpha(path):
    """PNG with an alpha channel or a tRNS chunk, or a GIF (may have a see-through colour)."""
    try:
        with open(path, "rb") as f:
            head = f.read(1 << 16)
    except OSError:
        return False
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return head[25] in (4, 6) or b"tRNS" in head
    return head[:3] == b"GIF"


def load(path, tk_root):
    """Reads the picture file (PNG / GIF) -> Picture. Raises tk.TclError / OSError if it can't be read."""
    import tkinter as tk
    img = tk.PhotoImage(master=tk_root, file=path)
    try:
        w, h = img.width(), img.height()
        rgb = _ppm_pixels(tk_root.tk.call(img, "data", "-format", "ppm"))
        alpha = None
        if _has_alpha(path):
            alpha = _alpha(img, tk_root, w, h)
    finally:
        img = None
    lin = to_lin(rgb.astype(np.float32) / 255)
    k = max(1, -(-max(w, h) // MAX_SIDE))
    if k > 1:  # average k x k blocks (cuts the edge rows / columns that don't fill a block)
        hh, ww = h // k * k, w // k * k
        lin = lin[:hh, :ww].reshape(hh // k, k, ww // k, k, 3).mean(axis=(1, 3))
        if alpha is not None:
            alpha = alpha[:hh, :ww].reshape(hh // k, k, ww // k, k).mean(axis=(1, 3))
    return Picture(np.ascontiguousarray(lin, np.float32), alpha, (w, h), path, fingerprint(path))


def _alpha(img, tk_root, w, h):
    """How see-through each pixel is, 0..1 (Tk 9: one call; Tk 8.6: pixel by pixel on a smaller copy)."""
    import tkinter as tk
    try:
        rows = tk_root.tk.call(img, "data", "-format", "default -colorformat argb")
        if isinstance(rows, str):
            text = rows.replace("{", " ").replace("}", " ")
        else:
            text = " ".join(r if isinstance(r, str) else " ".join(r) for r in rows)
        argb = np.frombuffer(bytes.fromhex(text.replace("#", "")), np.uint8)
        if argb.size == w * h * 4:
            a = argb.reshape(h, w, 4)[..., 0].astype(np.float32) / 255
            return a if a.min() < 1 else None
    except (tk.TclError, ValueError):
        pass
    k = max(1, -(-max(w, h) // 512))
    small = tk.PhotoImage(master=tk_root)
    tk_root.tk.call(small, "copy", img, "-subsample", k, k)
    sw, sh = small.width(), small.height()
    a = np.ones((sh, sw), np.float32)
    for y in range(sh):
        for x in range(sw):
            if small.transparency_get(x, y):
                a[y, x] = 0
    if a.min() == 1:
        return None
    return np.repeat(np.repeat(a, k, 0), k, 1)[:h, :w]


# ------------------------------------------------------------------ colour maths

def to_lin(c):
    """sRGB 0..1 -> linear light."""
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929], [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468], [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])


def oklab(lin):
    """linear light -> OKLab (distances there are close to how different two colours look)."""
    return np.cbrt(np.clip(lin, 0, None) @ _M1.T) @ _M2.T


def hex_of(lin):
    """one linear colour -> "RRGGBB"."""
    return "%02X%02X%02X" % tuple(int(v * 255 + 0.5) for v in to_srgb(np.asarray(lin, float)))


def lin_of(hexs):
    """"RRGGBB" -> one linear colour."""
    return to_lin(np.array([int(hexs[i:i + 2], 16) for i in (0, 2, 4)], float) / 255)


def blur(a, r):
    """Box blur of radius r (edges repeat), any number of channels."""
    k = 2 * r + 1
    pad = ((r, r), (r, r)) + ((0, 0),) * (a.ndim - 2)
    c = np.pad(a, pad, mode="edge").cumsum(0).cumsum(1)
    c = np.pad(c, ((1, 0), (1, 0)) + ((0, 0),) * (a.ndim - 2))
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


# ------------------------------------------------------------------ the note grid

def grid_size(pic, keys, steps, view):
    """(rows, cols) of the grid: keys across a falling picture's width / up a piano roll picture's height, and
    `steps` grid steps per key the other way (keeping the picture's shape)."""
    if view == "roll":
        return keys, max(1, round(keys * pic.aspect)) * steps
    return max(1, round(keys / pic.aspect)) * steps, keys


def _edges(n, m):
    return np.linspace(0, n, m + 1).round().astype(int)


def cells(pic, rows, cols):
    """Box-average the picture down to rows x cols -> (linear colours, alpha or None). A grid finer than the
    picture repeats its pixels."""
    a = pic.lin
    if rows > a.shape[0] or cols > a.shape[1]:
        ys = np.minimum((np.arange(rows) + 0.5) * a.shape[0] / rows, a.shape[0] - 1).astype(int)
        xs = np.minimum((np.arange(cols) + 0.5) * a.shape[1] / cols, a.shape[1] - 1).astype(int)
        al = None if pic.alpha is None else pic.alpha[ys][:, xs]
        return a[ys][:, xs].astype(float), al
    ys, xs = _edges(a.shape[0], rows), _edges(a.shape[1], cols)
    n = np.diff(ys)[:, None] * np.diff(xs)[None, :]
    s = np.add.reduceat(np.add.reduceat(a.astype(float), ys[:-1], 0), xs[:-1], 1)
    al = None
    if pic.alpha is not None:
        al = np.add.reduceat(np.add.reduceat(pic.alpha.astype(float), ys[:-1], 0), xs[:-1], 1) / n
    return s / n[..., None], al


def adjust(cl, brightness=0.0, contrast=0.0, saturation=0.0, sharpen=0.0):
    """Picture adjustments before colours are picked (all 0 = unchanged). brightness / contrast / saturation
    -1..1 (done in sRGB, like image editors), sharpen 0..1."""
    if not (brightness or contrast or saturation or sharpen):
        return cl
    c = to_srgb(cl)
    if brightness:
        c = c + brightness * 0.5
    if contrast:
        c = (c - 0.5) * (1 + contrast) ** 2 + 0.5 if contrast > 0 else (c - 0.5) * (1 + contrast) + 0.5
    if saturation:
        grey = c @ np.array([0.299, 0.587, 0.114])
        c = grey[..., None] + (c - grey[..., None]) * (1 + saturation)
    out = to_lin(np.clip(c, 0, 1))
    if sharpen:
        out = np.clip(out + sharpen * 1.6 * (out - blur(out, 1)), 0, 1)
    return out


def detail(cl):
    """How much each cell differs from its neighbours (OKLab), relative to the picture's average, capped at 3
    (a rain streak must not take colours away from faces)."""
    L = oklab(cl)
    g = np.zeros(cl.shape[:2])
    g[1:] += np.sqrt(((L[1:] - L[:-1]) ** 2).sum(-1))
    g[:, 1:] += np.sqrt(((L[:, 1:] - L[:, :-1]) ** 2).sum(-1))
    m = g.mean()
    return np.minimum(g / m, 3.0) if m > 0 else np.ones_like(g)


def fit_palette(pictures, k, locked=(), tries=4, seed=1, samples=12000, init="spread", pick="look"):
    """pictures: [(cells, alpha or None, share, focus)] -> k linear colours fitted to all of them together.
    share = how much this picture counts (1 = normal); focus 0..1 = how much details (faces, eyes) count more
    than big flat areas. locked: [(slot, linear colour)] kept as they are (the others fit around them).
    Several starts spread from dark to light; kept: the one whose picture LOOKS closest (pick "look"; the
    smallest group error, "error", picked worse colours: dev/scratch/picture_diff.py). Each colour = the plain
    average of every cell it stands for (weighted by detail it drifted to outline tones)."""
    labs, lins, wts = [], [], []
    for cl, al, share, focus in pictures:
        lab = oklab(cl).reshape(-1, 3)
        w = (1 - focus) + focus * (0.2 + detail(cl).reshape(-1))
        if al is not None:
            keep = al.reshape(-1) >= 0.5
            lab, w, lin = lab[keep], w[keep], cl.reshape(-1, 3)[keep]
        else:
            lin = cl.reshape(-1, 3)
        if len(w):
            labs.append(lab)
            lins.append(lin)
            wts.append(w / w.sum() * share)
    locked = [(s, np.asarray(c, float)) for s, c in locked if 0 <= s < k]
    if not labs:
        pal = np.zeros((k, 3))
        for s, c in locked:
            pal[s] = c
        return pal
    lab, lin, wt = np.concatenate(labs), np.concatenate(lins), np.concatenate(wts)
    rng = np.random.default_rng(seed)
    look = None
    if pick == "look":  # judge each try by how close the picture looks (blurred a little), not the group error
        look = [(oklab(c), oklab(blur(c, 2)), a) for c, a, _, _ in pictures]
    lab_all, lin_all = lab, lin
    if len(lab) > samples:
        sel = rng.choice(len(lab), samples, replace=False, p=None)
        lab, lin, wt = lab[sel], lin[sel], wt[sel]
    fixed = {s: oklab(c[None])[0] for s, c in locked}
    free = [s for s in range(k) if s not in fixed]
    p = wt / wt.sum()
    best, best_err = None, None
    for _ in range(max(1, tries)):
        cen = np.zeros((k, 3))
        for s, c in fixed.items():
            cen[s] = c
        have = list(fixed.values())
        for s in free:  # first guesses far from the ones already taken
            if have and init == "spread":
                d = ((lab[:, None] - np.array(have)[None]) ** 2).sum(2).min(1) * wt
                d = d / d.sum() if d.sum() > 0 else p
            else:
                d = p
            cen[s] = lab[rng.choice(len(lab), p=d)]
            have.append(cen[s])
        for _ in range(25):
            grp = ((lab[:, None] - cen[None]) ** 2).sum(2).argmin(1)
            moved = 0.0
            for s in free:
                m = grp == s
                if m.any():
                    new = np.average(lab[m], 0, wt[m])
                    moved = max(moved, float(np.abs(new - cen[s]).max()))
                    cen[s] = new
            if moved < 1e-4:
                break
        grp = ((lab[:, None] - cen[None]) ** 2).sum(2).argmin(1)
        if look is None:
            err = (((lab - cen[grp]) ** 2).sum(1) * wt).sum()
        else:
            err = 0.0
            for L, Lb, _a in look:
                g_ = _nearest(L, cen)
                err += float(np.sqrt(((blur(cen[g_], 2) - Lb) ** 2).sum(-1)).mean())
        if best_err is None or err < best_err:
            best, best_err = (cen.copy(), grp), err
    cen = best[0]
    grp = np.concatenate([_nearest(lab_all[i:i + 50000], cen) for i in range(0, len(lab_all), 50000)])
    pal = np.zeros((k, 3))
    for s in range(k):  # each free colour = the plain average (in light) of every cell it stands for
        if s in fixed:
            pal[s] = dict(locked)[s]
        elif (grp == s).any():
            pal[s] = lin_all[grp == s].mean(0)
        else:
            pal[s] = lin_all[rng.integers(len(lin_all))]
    return pal


def _nearest(lab, pal_lab):
    return ((lab[..., None, :] - pal_lab) ** 2).sum(-1).argmin(-1)


_BAYER = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) + 0.5) / 16 - 0.5


def quantise(cl, pal, blend="spread", strength=1.0, keep=0.6, alpha=None, empty=True):
    """Every cell -> a palette slot. blend: "spread" (error passed on, Floyd-Steinberg weights, many cells at
    once along slanted lines), "pattern" (a fixed 4x4 pattern) or "none". strength 0..1; keep 0..1 = how
    readily small details keep their own colour (no error in or out). alpha + empty: cells at least half
    see-through -> -1."""
    rows, cols = cl.shape[:2]
    pal_lab = oklab(pal)
    if blend == "pattern" and strength > 0:
        c = to_srgb(cl)
        b = _BAYER[np.arange(rows)[:, None] % 4, np.arange(cols)[None] % 4][..., None]
        step = np.sort(to_srgb(pal), 0)
        spread = float(np.median(np.diff(step, axis=0).max(1))) if len(pal) > 1 else 0.2
        idx = _nearest(oklab(to_lin(np.clip(c + b * strength * max(spread, 0.05), 0, 1))), pal_lab)
    elif blend == "spread" and strength > 0:
        free = np.ones((rows, cols))
        if keep > 0:
            d = np.sqrt(((oklab(cl) - oklab(blur(cl, 2))) ** 2).sum(-1))
            free = 1 - np.clip((d - 0.075 * (1 - keep)) / 0.05, 0, 1)
        if alpha is not None and empty:
            free = free * (alpha >= 0.5)
        idx = _spread(cl, pal, pal_lab, strength, free)
    else:
        idx = _nearest(oklab(cl), pal_lab)
    if alpha is not None and empty:
        idx = np.where(alpha >= 0.5, idx, -1)
    return idx


def _spread(cl, pal, pal_lab, strength, free):
    """Error spreading, left to right, top to bottom. A cell needs its left neighbour and the three above it
    done, so every cell on one slanted line (x + 2y the same) can go at once."""
    rows, cols = cl.shape[:2]
    a = cl.astype(float).copy()
    idx = np.zeros((rows, cols), int)
    ys_all = np.arange(rows)
    for line in range(cols + 2 * (rows - 1)):
        ys = ys_all[(line - 2 * ys_all >= 0) & (line - 2 * ys_all < cols)]
        if not len(ys):
            continue
        xs = line - 2 * ys
        c = np.clip(a[ys, xs], 0, 1)
        i = _nearest(oklab(c), pal_lab)
        idx[ys, xs] = i
        e = (a[ys, xs] - pal[i]) * (strength * free[ys, xs])[:, None]
        for dy, dx, f in ((0, 1, 7 / 16), (1, -1, 3 / 16), (1, 0, 5 / 16), (1, 1, 1 / 16)):
            yy, xx = ys + dy, xs + dx
            ok = (yy < rows) & (xx >= 0) & (xx < cols)
            if ok.any():
                a[yy[ok], xx[ok]] += e[ok] * f * free[yy[ok], xx[ok]][:, None]
    return idx


# ------------------------------------------------------------------ the placed picture (a custom shape)
# A placed picture is a custom shape holding notes like pasted notes do (custom.py "notes": packed rows of
# (start, end, key row, velocity, track) in grid steps; track = colour slot), plus sh["picture"]: where it came
# from and how it was made, so it always opens and plays even if the file is gone, and can be made again.

_RANGES = {"keys": (1, 256), "steps": (1, 8), "colours": (2, 16), "focus": (0, 1), "strength": (0, 1),
           "keep": (0, 1), "sharpen": (0, 1), "brightness": (-1, 1), "contrast": (-1, 1), "saturation": (-1, 1),
           "share": (0.1, 10), "step": (1 / 65536, 64), "outline": (0, 8)}
_CHOICES = {"view": ("fall", "roll"), "blend": ("spread", "pattern", "none"), "look": ("flat", "outlined")}
_FLAGS = ("empty", "shade", "join")


def clean_settings(s):
    """Settings from a file -> every one of SUGGESTED's (+ share, and the colours "pal" / "locked" when there),
    a broken one = its suggested value."""
    out = dict(SUGGESTED, share=1.0)
    s = s if isinstance(s, dict) else {}
    for k, v in s.items():
        if k in _CHOICES and v in _CHOICES[k]:
            out[k] = v
        elif k in _FLAGS and isinstance(v, bool):
            out[k] = v
        elif k in _RANGES and isinstance(v, (int, float)) and not isinstance(v, bool) and np.isfinite(v):
            lo, hi = _RANGES[k]
            out[k] = max(lo, min(hi, int(v) if isinstance(SUGGESTED.get(k), int) else float(v)))
    pal = s.get("pal")
    if isinstance(pal, list) and 0 < len(pal) <= 16 and all(
            isinstance(h, str) and len(h) == 6 and all(c in "0123456789ABCDEFabcdef" for c in h) for h in pal):
        out["pal"] = [h.upper() for h in pal]
        lk = s.get("locked")
        if isinstance(lk, list):
            out["locked"] = sorted({k for k in lk if type(k) is int and 0 <= k < len(pal)})
    return out


def clean_picture(p):
    """sh["picture"] from a file -> valid, or None. file = the picture's path (relative to the project when near
    it), sig = its fingerprint, size = its own width / height, grid = [steps, keys] of the notes, set = settings."""
    if not isinstance(p, dict) or not isinstance(p.get("file"), str) or not p["file"] or len(p["file"]) > 4096:
        return None
    try:
        steps, keys = (int(n) for n in p["grid"])
        w, h = (int(n) for n in p["size"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 < steps <= 10 ** 7 and 0 < keys <= 256 and w > 0 and h > 0):
        return None
    sig = p.get("sig") if isinstance(p.get("sig"), str) and len(p.get("sig")) <= 64 else ""
    return {"file": p["file"], "sig": sig, "size": [w, h], "grid": [steps, keys], "set": clean_settings(p.get("set"))}


def grid_notes(grid, view, join=True):
    """A colour grid -> (rows, steps, keys): rows = (start, end, key row, velocity, slot) notes in grid steps, one
    per run of the same colour on a key (join=False: one per cell). Falling: keys across the picture, its bottom
    row first; piano roll: keys up the picture (bottom row = lowest key), its left column first. Empty cells (-1)
    make no note."""
    a = grid[::-1].T if view == "fall" else grid[::-1]  # keys x steps
    keys, steps = a.shape
    change = np.ones(a.shape, bool)
    if join:
        change[:, 1:] = a[:, 1:] != a[:, :-1]
    ks, ts = np.nonzero(change)  # (key by key, in time order)
    ends = np.full(len(ts), steps)
    same = ks[1:] == ks[:-1]
    ends[:-1] = np.where(same, ts[1:], steps)
    val = a[ks, ts]
    on = val >= 0
    rows = np.column_stack([ts, ends, ks, np.full(len(ts), 100), val])[on].astype(np.int64)
    return rows, steps, keys


def picture_shape(grid, info, b0, k0, step_beats, vel=127):
    """A new placed picture: its box from beat b0 and key k0 (lowest key) up, one grid step = step_beats beats.
    info = sh["picture"] without "grid" (file, sig, size, set). None if the grid has no colour at all."""
    from notes.custom import BOX_STROKE, box_frame, pack_notes
    rows, steps, keys = grid_notes(grid, info["set"]["view"], info["set"]["join"])
    if not len(rows):
        return None
    name = os.path.basename(info["file"])
    return dict(kind="custom", name=name, strokes=[dict(BOX_STROKE)], fill="empty", notes=pack_notes(rows),
                picture=dict(info, grid=[steps, keys]), vel0=vel, vel1=vel,
                pts=box_frame(b0, k0 - 0.5, b0 + steps * step_beats, k0 - 0.5 + keys))


def make(pic, s, pal=None):
    """The whole way for one picture with settings s (SUGGESTED's keys) -> (grid, palette, cells). pal: the
    project's shared colours (None = fit them to this picture alone)."""
    rows, cols = grid_size(pic, s["keys"], s["steps"], s["view"])
    cl, al = cells(pic, rows, cols)
    cl = adjust(cl, s["brightness"], s["contrast"], s["saturation"], s["sharpen"])
    if pal is None:
        pal = fit_palette([(cl, al, 1.0, s["focus"])], s["colours"])
    grid = quantise(cl, pal, s["blend"], s["strength"], s["keep"], al)
    return grid, pal, cl
