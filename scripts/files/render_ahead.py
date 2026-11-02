"""Rendered ahead (MIDI out playback.AHEAD): the sound is made into audio ahead of the play line, in pieces, by one
of three engines (Merlin and Kestrel on the graphics card through the Kestrel connector, BASSMIDI on the
processor), and played from there. The part not made yet shows a very faint grey in the piano roll.

This file: its settings (Settings → Sound while it's the MIDI out; saved in user/settings.ini [sound]). Pieces,
player and grey come in later steps (spiderweb/PLAN.md stage R)."""

ENGINES = ("merlin", "kestrel", "bassmidi")  # (saved by these names; the list shows them in this order)
SECONDS = (5, 300)  # Render ahead: how far ahead of the play line the sound is made (user 2026-11-02: up to 300)
DEFAULT_SECONDS = 30  # (user 2026-11-02: "sounds right")


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
