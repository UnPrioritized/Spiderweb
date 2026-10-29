"""Names for the MIDI messages that ride along with notes (pasted notes' CC events, notes/passengers.py; Hz bass CC
lines, hz_cc.py), so the track list and the piano roll's ruler can say "Pan 64" instead of numbers (user 2026-10-29:
"i don't remember the CC number at all"). Messages as App.cc_messages gives them: (tick, status without the channel,
data 1, data 2, ...)."""

from files.lang import tr, trn

# General MIDI controller numbers with a common name (others: "CC n")
NAMED = (0, 1, 2, 4, 5, 6, 7, 8, 10, 11, 32, 64, 65, 66, 67, 71, 72, 73, 74, 91, 93, 120, 121, 123)


def kind_of(status, one):
    """A message's kind, the same for every value it takes: ("cc", number) / "bend" / "program" / "pressure" /
    "poly", None for anything else."""
    if status == 0xB0:
        return ("cc", one)
    return {0xE0: "bend", 0xC0: "program", 0xD0: "pressure", 0xA0: "poly"}.get(status)


def file_kind(k):
    """A loaded MIDI's kind number (files/midi_index CC_KINDS: 0-127 controllers, then instrument, bend, channel
    pressure, key pressure) as kind_of gives it."""
    return ("cc", k) if k < 128 else ("program", "bend", "pressure", "poly")[k - 128]


def kind_name(kind):
    """The kind's name in words ("Pan", "Pitch bend", "CC 20")."""
    if isinstance(kind, tuple):
        n = kind[1]
        return tr(f"cc.name_{n}") if n in NAMED else tr("cc.number", n=n)
    return tr(f"cc.{kind}")


def value_of(status, one, two):
    """A message's value as shown: a CC's value, a bend from its middle (-8192..8191), a program 1-based (like
    Domino), pressure's amount."""
    if status == 0xE0:
        return (one | two << 7) - 8192
    if status == 0xC0:
        return one + 1
    if status == 0xD0:
        return one
    return two


def kinds_text(counts):
    """{kind: how many} -> "Volume, Pan, Pitch bend (12 events)", kinds in the order first met."""
    total = sum(counts.values())
    return trn("cc.kinds", total, names=", ".join(kind_name(k) for k in counts), n=f"{total:,}")
