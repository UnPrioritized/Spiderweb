"""Rendered ahead (MIDI out playback.AHEAD): the sound is made into audio ahead of the play line, in pieces, by one
of three engines (Merlin and Kestrel on the graphics card through the Kestrel connector, BASSMIDI on the
processor), and played from there. The part not made yet shows a very faint grey in the piano roll.

This file: its settings (Settings → Sound while it's the MIDI out; saved in user/settings.ini [sound]). Pieces,
player and grey come in later steps (spiderweb/PLAN.md stage R)."""

ENGINES = ("merlin", "kestrel", "bassmidi")  # (saved by these names; the list shows them in this order)
SECONDS = (5, 300)  # Render ahead: how far ahead of the play line the sound is made (user 2026-11-02: up to 300)
DEFAULT_SECONDS = 30  # (user 2026-11-02: "sounds right")

# R1b (user 2026-11-02): its own engine settings, apart from Kestrel live play's (nothing has to sound at once
# here), EXCEPT the graphics card (shared: App.kestrel_opts["card"]). No "Lower it by itself" (Merlin drops no
# note, Kestrel making ahead can take its time). Exact copies stay merged (pieces: Kestrel's coalescing on,
# Merlin folds them itself; user: never lose that speed). App.ahead_opts keys:
BLOCKS = (512, 1024, 2048, 4096, 8192)  # (bigger = easier on a weak graphics card; pieces were checked at 4096)
BASS_VOICES = (1, 100000)  # (BASSMIDI's voice limit, as Built-in's)
DEFAULTS = {"voices": 1 << 20,  # (Kestrel's own default: making ahead can afford more than live play's 300k)
            "block": 4096, "interpolation": 1, "min_velocity": 0,
            "limiter": 2, "ceiling_db": 0.0,  # (Spiderweb's own limiter over the joined pieces: Balanced first)
            "bass_voices": 1000}
# rows shown for each engine ("card" = the shared one; Merlin reads neither Max voices nor Interpolation, and
# can't skip quiet notes yet: those pieces would go to Kestrel)
ROWS = {"merlin": ("card", "block", "limiter", "ceiling_db"),
        "kestrel": ("card", "voices", "block", "interpolation", "min_velocity", "limiter", "ceiling_db"),
        "bassmidi": ("bass_voices", "limiter", "ceiling_db")}


def check_opt(key, value):
    """A setting typed / saved / picked, as stored (whole numbers, ceiling to tenths), or ValueError."""
    from files.kestrel import CHOICES, DECIMALS, RANGES, VOICES_RANGE, read_voices
    from files.mathexpr import calc
    if isinstance(value, bool):
        raise ValueError(value)
    try:
        if key == "voices" and isinstance(value, str):
            try:
                v = read_voices(value)  # ("300k", "1m")
            except ValueError:
                v = int(round(float(calc(value))))
        elif key == "ceiling_db":
            v = round(float(calc(value) if isinstance(value, str) else value), 1) + 0.0
        else:
            v = int(round(float(calc(value) if isinstance(value, str) else value)))
    except (ZeroDivisionError, OverflowError, TypeError):
        raise ValueError(value)
    lo, hi = {"voices": VOICES_RANGE, "bass_voices": BASS_VOICES, "min_velocity": RANGES["min_velocity"],
              "ceiling_db": DECIMALS["ceiling_db"], "interpolation": (0, CHOICES["interpolation"] - 1),
              "limiter": (0, CHOICES["limiter"] - 1), "block": (BLOCKS[0], BLOCKS[-1])}[key]
    if not lo <= v <= hi or key == "block" and v not in BLOCKS:
        raise ValueError(value)
    return v


def take_opts(saved):
    """Saved settings (settings.ini / autosave: {key: value}), each checked; wrong / missing = DEFAULTS."""
    out = {}
    for k, d in DEFAULTS.items():
        try:
            v = saved.get(k)
            out[k] = d if v is None or isinstance(v, str) else check_opt(k, v)
        except ValueError:
            out[k] = d
    return out


def engines():
    """The engines that work here: Merlin and Kestrel need the Kestrel connector (64-bit Windows / Linux)."""
    from files import kestrel
    return ENGINES if kestrel.available() is None else ("bassmidi",)


def engine_used(saved):
    """The engine a saved name plays with here: itself when it works here, else the first that does (the saved
    name is kept, so the settings file brought to another PC still picks it there)."""
    here = engines()
    return saved if saved in here else here[0]


def read_seconds(text):
    """Render ahead typed (whole seconds in SECONDS), or ValueError."""
    from files.mathexpr import calc
    try:
        v = int(round(float(calc(text))))
    except (ZeroDivisionError, OverflowError):
        raise ValueError(text)
    if not SECONDS[0] <= v <= SECONDS[1]:
        raise ValueError(text)
    return v
