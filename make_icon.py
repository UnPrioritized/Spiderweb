"""Makes Spiderweb's icons from its three drawings (no packages): scripts/icons/icon.ico (the .exe's and .spwb files'
icon, every size Windows asks for) and icon-<size>.png (the windows' title bar / taskbar icon, set by the program).

Each size is drawn from its own drawing: icon-tiny.svg for 16 to 24 px (two thick rings: three blend there),
icon-32.svg up to 40 px, icon.svg above. The drawings hold only polygons, lines and circles (fill, stroke, round
joins and caps); edges are smoothed from each pixel's distance to them. Run it again after changing a drawing
(build.bat does).
"""

import math
import os
import re
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
DRAWINGS = [(24, "icon-tiny.svg"), (40, "icon-32.svg"), (10 ** 9, "icon.svg")]  # (up to this size, drawing)
OUT = os.path.join(HERE, "scripts", "icons")
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
PNG_SIZES = [16, 24, 32, 48, 64, 256]


def colour(text):
    text = text.lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))


def read_drawing(path):
    """-> (view size, [(kind, geometry, fill colour or None, stroke colour or None, stroke width)]) in drawing order.
    kind "poly" = closed points, "line" = two points, "circle" = (x, y, r)."""
    text = open(path, encoding="utf-8").read()
    view = float(re.search(r'viewBox="[\d.\s-]*?\s([\d.]+)"', text).group(1))
    parts = []
    for tag, attrs in re.findall(r"<(polygon|line|circle)\b([^>]*)/?>", text):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
        fill = None if a.get("fill", "#000") == "none" else colour(a.get("fill", "#000"))
        stroke = colour(a["stroke"]) if a.get("stroke", "none") != "none" else None
        width = float(a.get("stroke-width", 1)) if stroke else 0.0
        if tag == "polygon":
            nums = [float(v) for v in re.findall(r"[-+]?\d*\.?\d+", a["points"])]
            parts.append(("poly", list(zip(nums[::2], nums[1::2])), fill, stroke, width))
        elif tag == "line":
            pts = [(float(a["x1"]), float(a["y1"])), (float(a["x2"]), float(a["y2"]))]
            parts.append(("line", pts, None, stroke, width))
        else:
            parts.append(("circle", (float(a["cx"]), float(a["cy"]), float(a["r"])), fill, stroke, width))
    return view, parts


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1)
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def inside(px, py, pts):
    hit = False
    for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
        if (ay > py) != (by > py) and px < ax + (py - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


def layers(kind, geo, fill, stroke, width, x, y):
    """-> [(distance outside the painted area, colour)] for one part at a point (negative = inside)."""
    out = []
    if kind == "circle":
        d = math.hypot(x - geo[0], y - geo[1]) - geo[2]
        if fill:
            out.append((d, fill))
        if stroke:
            out.append((abs(d) - width / 2, stroke))
        return out
    pts = geo
    edges = zip(pts, pts[1:] + pts[:1]) if kind == "poly" else [(pts[0], pts[1])]
    d = min(seg_dist(x, y, ax, ay, bx, by) for (ax, ay), (bx, by) in edges)  # (round joins and caps)
    if kind == "poly" and fill:
        out.append((-d if inside(x, y, pts) else d, fill))
    if stroke:
        out.append((d - width / 2, stroke))
    return out


def render(size, drawing):
    """-> RGBA bytes of the icon at size x size."""
    view, parts = drawing
    scale = size / view
    out = bytearray()
    for py in range(size):
        y = (py + 0.5) / scale
        for px in range(size):
            x = (px + 0.5) / scale
            r = g = b = a = 0.0  # (premultiplied)
            for kind, geo, fill, stroke, width in parts:
                for d, (cr, cg, cb) in layers(kind, geo, fill, stroke, width, x, y):
                    cover = min(1.0, max(0.0, 0.5 - d * scale))
                    if cover:
                        r, g, b = cr * cover + r * (1 - cover), cg * cover + g * (1 - cover), cb * cover + b * (1 - cover)
                        a = cover + a * (1 - cover)
            if a:
                out += bytes((round(255 * r / a), round(255 * g / a), round(255 * b / a), round(255 * a)))
            else:
                out += b"\0\0\0\0"
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
    drawings = {name: read_drawing(os.path.join(HERE, name)) for _, name in DRAWINGS}
    os.makedirs(OUT, exist_ok=True)
    pngs = {}
    for size in sorted(set(ICO_SIZES) | set(PNG_SIZES)):
        name = next(n for upto, n in DRAWINGS if size <= upto)
        pngs[size] = png(size, render(size, drawings[name]))
    with open(os.path.join(OUT, "icon.ico"), "wb") as f:
        f.write(ico([(s, pngs[s]) for s in ICO_SIZES]))
    for s in PNG_SIZES:
        with open(os.path.join(OUT, f"icon-{s}.png"), "wb") as f:
            f.write(pngs[s])
    print("Icons written to", OUT)


if __name__ == "__main__":
    main()
