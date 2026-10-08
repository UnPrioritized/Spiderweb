"""Gate sensitive merge (user, 2026-10-08, first try: plain notes): two shapes' notes become one. The shape that
stays keeps its notes; each key row of the other one slides sideways in time on its own until its notes meet the
staying shape's note on that key (gate end = next start), lengths kept. Rows with no note of the staying shape on
their key stay where they are."""

import numpy as np


def comes_from_left(stay, slide):
    """True if slide's middle is before stay's: the side it comes in from unless the user picks the other."""
    return np.asarray(slide)[:, :2].mean() < np.asarray(stay)[:, :2].mean()


def gate_merge(stay, slide, from_left=None):
    """stay, slide: (start, end, pitch, velocity[, track]) rows in ticks. Returns all rows together, slide's moved.
    Each row comes in from far away on that side (from_left; None = the side slide's middle is on), so it meets
    the staying shape's outer edge there: a row inside a hollow shape or overlapping it goes out to that edge."""
    stay = np.asarray(stay, np.int64)
    moved = np.array(slide, np.int64, copy=True)
    if not len(stay) or not len(moved):
        return np.concatenate([stay, moved]) if len(stay) or len(moved) else stay
    if from_left is None:
        from_left = comes_from_left(stay, moved)
    for key in np.unique(moved[:, 2]):
        here = stay[stay[:, 2] == key]
        if not len(here):
            continue
        rows = moved[:, 2] == key
        if from_left:  # its last note's end meets the staying shape's first note on this key
            shift = here[:, 0].min() - moved[rows, 1].max()
        else:  # its first note's start meets the staying shape's last note's end
            shift = here[:, 1].max() - moved[rows, 0].min()
        moved[rows, :2] += shift
    out = np.concatenate([stay, moved])
    if out[:, 0].min() < 0:  # (slid before the song's start: everything waits)
        out[:, :2] -= out[:, 0].min()
    return out
