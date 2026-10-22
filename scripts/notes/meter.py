"""Time signatures + key signatures (the Conductor pane's, user 2026-10-22; they replaced Beats per bar).

Kept in the tempo line: tempo["sigs"] = [[beat, top, bottom]] (the first always on beat 0; default 4/4) and
tempo["keysigs"] = [[beat, sharps (-7 flats .. 7 sharps), minor]]. Beats are quarter notes, as everywhere: a bar
is top x 4 / bottom beats long. Each time signature starts a new bar where it sits (a MIDI file's rule), so one
placed off a bar line cuts the bar before it short. Domino's limits: top 1-255, bottom a power of two 1-128."""

import bisect
import math

DEFAULT = [0.0, 4, 4]
TOPS = (1, 255)
BOTTOMS = (1, 2, 4, 8, 16, 32, 64, 128)
SHARPS = ("C", "G", "D", "A", "E", "B", "F#", "C#")  # (major keys by sharps; minor: 3 steps on, below)
FLATS = ("C", "F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb")
MINOR_SHARPS = ("A", "E", "B", "F#", "C#", "G#", "D#", "A#")
MINOR_FLATS = ("A", "D", "G", "C", "F", "Bb", "Eb", "Ab")
FAR = 1e7  # beats: past this a mark is left out (as the tempo line's points)
EPS = 1e-9


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def clean_sigs(data, beats=None):
    """Time signatures from a file / undo step -> safe ones: sorted, one per beat, the first on beat 0 (beats =
    an old project's Beats per bar for it, as n/4, when none was saved)."""
    out = {}
    for s in data if isinstance(data, list) else []:
        if not (isinstance(s, list) and len(s) >= 3 and all(_num(x) for x in s[:3])):
            continue
        b, top, bottom = float(s[0]), int(s[1]), int(s[2])
        if 0 <= b <= FAR and TOPS[0] <= top <= TOPS[1] and bottom in BOTTOMS:
            out[round(b, 9)] = [round(b, 9), top, bottom]
    if 0 not in out:
        first = [0.0, int(beats), 4] if _num(beats) and TOPS[0] <= int(beats) <= TOPS[1] else list(DEFAULT)
        out[0] = first
    sigs = [out[k] for k in sorted(out)]
    return [s for i, s in enumerate(sigs) if i == 0 or s[1:] != sigs[i - 1][1:]]  # (the same again: nothing)


def clean_keysigs(data):
    """Key signatures from a file / undo step: [[beat, sharps -7..7, minor]], sorted, one per beat."""
    out = {}
    for s in data if isinstance(data, list) else []:
        if not (isinstance(s, list) and len(s) >= 3 and all(_num(x) for x in s[:2])):
            continue
        b, sf = float(s[0]), int(s[1])
        if 0 <= b <= FAR and -7 <= sf <= 7:
            out[round(b, 9)] = [round(b, 9), sf, s[2] is True or s[2] == 1]
    return [out[k] for k in sorted(out)]


def sigs_of(tempo):
    return (tempo or {}).get("sigs") or [list(DEFAULT)]


def bar_len(top, bottom):
    """A bar's length in beats (quarter notes)."""
    return top * 4 / bottom


def parts(sigs):
    """[(start beat, bar length, first bar number (0 = bar 1), end beat or inf)] for each time signature."""
    out, bar = [], 0
    for i, (b, top, bottom) in enumerate(sigs):
        end = sigs[i + 1][0] if i + 1 < len(sigs) else math.inf
        n = bar_len(top, bottom)
        out.append((b, n, bar, end))
        if end < math.inf:
            bar += max(1, math.ceil((end - b) / n - EPS))
    return out


def _part(sigs, beat):
    ps = parts(sigs)
    i = max(0, bisect.bisect_right([p[0] for p in ps], beat + EPS) - 1)
    return ps[i], i


def where(sigs, beat):
    """(bar number from 0, beats into the bar, the bar's first beat, [top, bottom]) at a beat (before beat 0: the
    first signature's bars counted back, bar -1 and less)."""
    (start, n, first, end), i = _part(sigs, beat)
    k = math.floor((beat - start) / n + EPS)
    at = start + k * n
    return first + k, beat - at, at, sigs[i][1:]


def bar_start(sigs, bar):
    """The first beat of bar number bar (from 0; less than 0 = before the song, by the first signature)."""
    for start, n, first, end in reversed(parts(sigs)):
        if bar >= first or start == 0:
            return start + (bar - first) * n
    return 0.0


def bar_line(sigs, beat, after=False):
    """The bar line at or before beat (after: at or after it)."""
    bar, into, at, _ = where(sigs, beat)
    if after and into > EPS:
        (start, n, first, end), _ = _part(sigs, beat)
        return min(at + n, end)
    return at


def bar_lines(sigs, lo, hi, gap=0.0):
    """[(beat, bar number from 0)] of the bar lines from lo to hi beats, beat 0 on; zoomed out (bars closer than gap
    beats) only every 2nd, 4th... bar by number, and where a signature starts."""
    out = []
    for start, n, first, end in parts(sigs):
        if end < lo or start > hi:
            continue
        every = 1
        while n * every < gap:
            every *= 2
        k = max(0, math.floor((lo - start) / n))
        while True:
            b = start + k * n
            if b > hi or b >= end - EPS:
                break
            if b >= lo - EPS and ((first + k) % every == 0 or k == 0):
                out.append((b, first + k))
            k += 1
    return out


def beat_lines(sigs, lo, hi, gap=0.0):
    """Beats lo..hi of the lines between bar lines: each signature's beat (its bottom note: 6/8 = every eighth),
    none where they'd be closer than gap beats."""
    out = []
    for (start, n, first, end), sig in zip(parts(sigs), sigs):
        if end < lo or start > hi:
            continue
        unit = 4 / sig[2]
        if unit < gap:
            continue
        j = max(0, math.floor((lo - start) / unit))
        while True:
            b = start + j * unit
            if b > hi or b >= end - EPS:
                break
            into = (b - start) / n
            if b >= lo - EPS and abs(into - round(into)) > EPS:
                out.append(b)
            j += 1
    return out


def bar_beat_tick(sigs, ticks, ppq):
    """Ticks -> (bar, beat, tick) as the status line shows them (bar and beat from 1; beats = the signature's own
    note: 6/8 counts eighths, ticks within it). Before the song's start: bar 0 and less."""
    bar, into, at, (top, bottom) = where(sigs, ticks / ppq)
    unit = 4 * ppq / bottom  # (ticks a beat of this signature)
    rest = ticks - round(at * ppq)
    beat = int(rest // unit)
    return bar + 1, beat + 1, int(round(rest - beat * unit))


def from_bar_beat_tick(sigs, bar, beat, tick, ppq):
    """(bar, beat, tick) as shown -> beats (bar_beat_tick's way back; beat / tick past the bar run on)."""
    at = bar_start(sigs, bar - 1)
    bottom = where(sigs, at)[3][1]
    return at + (beat - 1) * 4 / bottom + tick / ppq


def at_beat(marks, beat):
    """The mark in effect at a beat (marks sorted by beat), or None before the first."""
    i = bisect.bisect_right([m[0] for m in marks], beat + EPS) - 1
    return marks[i] if i >= 0 else None


def sig_text(sig):
    return f"{sig[1]}/{sig[2]}" if len(sig) > 2 else f"{sig[0]}/{sig[1]}"


def key_name(sf, minor):
    """'Ab Maj' / 'F min' (the names as many MIDI programs show them)."""
    if minor:
        return (MINOR_SHARPS[sf] if sf >= 0 else MINOR_FLATS[-sf]) + " min"
    return (SHARPS[sf] if sf >= 0 else FLATS[-sf]) + " Maj"


def first_bar_beats(tempo):
    """The first bar's length in beats (what Beats per bar was)."""
    s = sigs_of(tempo)[0]
    return bar_len(s[1], s[2])
