"""Colour codes in one place: "#rrggbb" <-> (r, g, b), mixing two colours, and a colour made light enough for a dark
background. Plain Python (no Tk, no settings), so notes / roll / window code can all use it."""

WHITE, BLACK = "#ffffff", "#000000"


def rgb(colour):
    """"#rrggbb" as (r, g, b) 0-255."""
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def rgb_loose(colour):
    """[r, g, b] of "#rrggbb", "rrggbb", "#rgb" or "rgb"."""
    colour = colour.lstrip("#")
    if len(colour) == 3:
        colour = "".join(c * 2 for c in colour)
    return [int(colour[i:i + 2], 16) for i in (0, 2, 4)]


def hex_of(values):
    """(r, g, b) whole numbers 0-255 -> "#rrggbb"."""
    return "#%02x%02x%02x" % tuple(values)


def mix(colour, toward, by):
    """colour mixed `by` (0 = itself .. 1 = toward) toward another colour, each channel rounded."""
    a, b = rgb(colour), rgb(toward)
    return "#%02x%02x%02x" % (round(a[0] + (b[0] - a[0]) * by), round(a[1] + (b[1] - a[1]) * by),
                              round(a[2] + (b[2] - a[2]) * by))


def lighter(colour):
    """A colour light enough to see on a dark background (its hue kept, a bit more vivid)."""
    import colorsys
    r, g, b = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, lum, s = colorsys.rgb_to_hls(r, g, b)
    lum = max(lum, 0.6)
    if s > 0.1:
        s = max(s, 0.55)
    return "#%02x%02x%02x" % tuple(round(v * 255) for v in colorsys.hls_to_rgb(h, lum, s))
