"""Makes Spiderweb's icons from icon.svg (no packages): scripts/icons/icon.ico (the .exe's icon, every size Windows
asks for) and icon-<size>.png (the windows' title bar / taskbar icon, set by the program).

The SVG's one path is drawn in black over a white fill of its outer rounded square, so the icon shows on dark
taskbars too. Small sizes get thicker lines, else the thin outline blurs into grey. Run it again after changing
icon.svg (build.bat does).
"""

import math
import os
import re
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SVG = os.path.join(HERE, "icon.svg")
OUT = os.path.join(HERE, "scripts", "icons")
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
PNG_SIZES = [16, 24, 32, 48, 64, 256]
MIN_LINE = 1.2  # thinnest line allowed, in pixels of the finished icon


def parse_path(d):
    """SVG path data -> list of subpaths, each a list of (x, y) points (curves and arcs flattened)."""
    tokens = re.findall(r"[A-Za-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", d)
    subs, pts = [], []
    x = y = sx = sy = 0.0
    last_ctrl = None
    cmd = None
    i = 0

    def num():
        nonlocal i
        i += 1
        return float(tokens[i - 1])

    def flag():  # arc flags may be written together with the next number ("01.5")
        nonlocal i
        t = tokens[i]
        tokens[i:i + 1] = [t[0]] + ([t[1:]] if len(t) > 1 else [])
        return num() != 0

    def cubic(p0, p1, p2, p3, steps=16):
        for k in range(1, steps + 1):
            t = k / steps
            a, b, c, e = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3
            pts.append((a * p0[0] + b * p1[0] + c * p2[0] + e * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + e * p3[1]))

    def arc(x1, y1, rx, ry, rot, large, sweep, x2, y2):
        """Endpoint arc -> points (the SVG spec's centre conversion)."""
        if rx == 0 or ry == 0:
            pts.append((x2, y2))
            return
        rx, ry = abs(rx), abs(ry)
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
        xp, yp = c * dx + s * dy, -s * dx + c * dy
        lam = xp * xp / (rx * rx) + yp * yp / (ry * ry)
        if lam > 1:
            rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
        num_ = rx * rx * ry * ry - rx * rx * yp * yp - ry * ry * xp * xp
        f = math.sqrt(max(0.0, num_ / (rx * rx * yp * yp + ry * ry * xp * xp)))
        if large == sweep:
            f = -f
        cxp, cyp = f * rx * yp / ry, -f * ry * xp / rx
        cx, cy = c * cxp - s * cyp + (x1 + x2) / 2, s * cxp + c * cyp + (y1 + y2) / 2
        a1 = math.atan2((yp - cyp) / ry, (xp - cxp) / rx)
        a2 = math.atan2((-yp - cyp) / ry, (-xp - cxp) / rx)
        da = a2 - a1
        if sweep and da < 0:
            da += 2 * math.pi
        elif not sweep and da > 0:
            da -= 2 * math.pi
        steps = max(4, int(abs(da) * 12))
        for k in range(1, steps + 1):
            a = a1 + da * k / steps
            ex, ey = rx * math.cos(a), ry * math.sin(a)
            pts.append((c * ex - s * ey + cx, s * ex + c * ey + cy))

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
        rel = cmd.islower()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        C = cmd.upper()
        if C == "Z":
            if pts:
                subs.append(pts)
            pts = []
            x, y = sx, sy
            last_ctrl = None
            continue
        if C == "M":
            if pts:
                subs.append(pts)
            x, y = ox + num(), oy + num()
            sx, sy = x, y
            pts = [(x, y)]
            cmd = "l" if rel else "L"  # more pairs after a move are lines
            last_ctrl = None
        elif C == "L":
            x, y = ox + num(), oy + num()
            pts.append((x, y))
            last_ctrl = None
        elif C == "H":
            x = ox + num()
            pts.append((x, y))
            last_ctrl = None
        elif C == "V":
            y = oy + num()
            pts.append((x, y))
            last_ctrl = None
        elif C in "CS":
            if C == "C":
                p1 = (ox + num(), oy + num())
            else:  # first control = the last one mirrored
                p1 = (2 * x - last_ctrl[0], 2 * y - last_ctrl[1]) if last_ctrl else (x, y)
            p2 = (ox + num(), oy + num())
            p3 = (ox + num(), oy + num())
            cubic((x, y), p1, p2, p3)
            last_ctrl = p2
            x, y = p3
        elif C == "A":
            rx, ry, rot = num(), num(), num()
            large, sweep = flag(), flag()
            x2, y2 = ox + num(), oy + num()
            arc(x, y, rx, ry, rot, large, sweep, x2, y2)
            x, y = x2, y2
            last_ctrl = None
        else:
            raise ValueError(f"path command {cmd} isn't supported")
    if pts:
        subs.append(pts)
    return subs


def fill(subs, n, scale):
    """Nonzero fill of the subpaths on an n x n grid (a point is inside when its cell's middle is) -> one int per
    row, bit x set = cell x filled."""
    edges = []
    for poly in subs:
        for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
            if y0 != y1:
                edges.append((x0 * scale, y0 * scale, x1 * scale, y1 * scale))
    rows = []
    for r in range(n):
        yc = r + 0.5
        hits = []
        for x0, y0, x1, y1 in edges:
            if (y0 <= yc < y1) or (y1 <= yc < y0):
                hits.append((x0 + (yc - y0) * (x1 - x0) / (y1 - y0), 1 if y1 > y0 else -1))
        hits.sort()
        bits, wind = 0, 0
        for k, (hx, w) in enumerate(hits):
            before = wind
            wind += w
            if before == 0 and wind != 0:
                start = hx
            elif before != 0 and wind == 0:
                a, b = max(0, math.ceil(start - 0.5)), min(n, math.ceil(hx - 0.5))
                if b > a:
                    bits |= ((1 << (b - a)) - 1) << a
        rows.append(bits)
    return rows


def grow(rows, r, n):
    """Every filled cell spreads r cells around it (a round brush)."""
    if r <= 0:
        return rows
    full = (1 << n) - 1
    wide = {0: rows}
    for k in range(1, r + 1):  # wide[k] = spread k sideways
        wide[k] = [(b | (b << 1) | (b >> 1)) & full for b in wide[k - 1]]
    out = [0] * n
    for dy in range(-r, r + 1):
        src = wide[int(math.sqrt(r * r - dy * dy))]
        for y in range(max(0, -dy), min(n, n - dy)):
            out[y] |= src[y + dy]
    return out


def render(size, subs, view):
    """-> RGBA bytes of the icon at size x size."""
    ss = 8 if size <= 64 else 4  # cells per pixel each way (smooth edges)
    n = size * ss
    scale = n / view
    black = fill(subs, n, scale)
    white = fill(subs[:1], n, scale)  # the outer rounded square
    line = 17.25 * size / view  # the SVG's line width in pixels
    black = grow(black, round(max(0.0, (MIN_LINE - line) / 2) * ss), n)
    white = [w | b for w, b in zip(white, black)]
    cell = (1 << ss) - 1
    out = bytearray()
    for py in range(size):
        for px in range(size):
            nb = nw = 0
            for yy in range(py * ss, py * ss + ss):
                b = (black[yy] >> (px * ss)) & cell
                w = (white[yy] >> (px * ss)) & cell & ~b
                nb += bin(b).count("1")
                nw += bin(w).count("1")
            cover = nb + nw
            grey = round(255 * nw / cover) if cover else 0
            out += bytes((grey, grey, grey, round(255 * cover / (ss * ss))))
    return bytes(out)


def png(size, rgba):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\0" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(images):
    """images = [(size, png bytes)] -> .ico bytes (PNG entries, fine for Windows Vista and later)."""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    table, data = b"", b""
    for size, p in images:
        table += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(p), offset + len(data))
        data += p
    return head + table + data


def main():
    text = open(SVG, encoding="utf-8").read()
    view = float(re.search(r'viewBox="[\d.\s-]*?\s([\d.]+)"', text).group(1))
    subs = parse_path(re.search(r'\sd="([^"]+)"', text).group(1))
    os.makedirs(OUT, exist_ok=True)
    pngs = {}
    for size in sorted(set(ICO_SIZES) | set(PNG_SIZES)):
        pngs[size] = png(size, render(size, subs, view))
    with open(os.path.join(OUT, "icon.ico"), "wb") as f:
        f.write(ico([(s, pngs[s]) for s in ICO_SIZES]))
    for s in PNG_SIZES:
        with open(os.path.join(OUT, f"icon-{s}.png"), "wb") as f:
            f.write(pngs[s])
    print("Icons written to", OUT)


if __name__ == "__main__":
    main()
