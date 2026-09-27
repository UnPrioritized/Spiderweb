"""
Velocity envelopes.

Every shape has a velocity envelope over its own time span (0 = its earliest point, 1 = its latest),
so moving or stretching a shape takes its velocities along. Without one it's a straight vel0 -> vel1 ramp.
"""

import bisect

import numpy as np

# ---------------------------------------------------------------- velocity envelopes
# An envelope is a list of [u, velocity] points sorted by u, straight lines in between.
# Two points on the same u make a jump; a value exactly on a jump takes the right-hand side.

ENV_EPS = 1e-9


def velocity_env(sh):
    return sh.get("vel_env") or [[0.0, float(sh["vel0"])], [1.0, float(sh["vel1"])]]


def env_at(env, us, u, left=False):
    """Envelope value at u (us = the u of every point). left: the value just before u instead."""
    i = (bisect.bisect_left if left else bisect.bisect_right)(us, u)
    if i == 0:
        return env[0][1]
    if i == len(env):
        return env[-1][1]
    (u0, v0), (u1, v1) = env[i - 1], env[i]
    return v0 if u1 == u0 else v0 + (v1 - v0) * (u - u0) / (u1 - u0)


def env_values(env, us):
    """env_at for a whole NumPy array of u at once (the same sums, so the same results)."""
    eu = np.array([p[0] for p in env], float)
    ev = np.array([p[1] for p in env], float)
    i = np.searchsorted(eu, us, "right")
    i0, i1 = np.clip(i - 1, 0, len(eu) - 1), np.clip(i, 0, len(eu) - 1)
    u0, u1, v0, v1 = eu[i0], eu[i1], ev[i0], ev[i1]
    with np.errstate(divide="ignore", invalid="ignore"):
        mid = np.where(u1 == u0, v0, v0 + (v1 - v0) * (us - u0) / (u1 - u0))
    return np.where(i == 0, ev[0], np.where(i == len(eu), ev[-1], mid))


def paint_env(env, pts):
    """Replace env between pts[0] and pts[-1] (sorted by u) with pts, keeping everything outside exactly."""
    us = [p[0] for p in env]
    ua, ub = pts[0][0], pts[-1][0] + ENV_EPS
    before, after = env_at(env, us, ua, left=True), env_at(env, us, ub)
    return ([p for p in env if p[0] < ua] + [[ua, before]] + [list(p) for p in pts] + [[ub, after]]
            + [p for p in env if p[0] > ub])


def tidy_env(env):
    """Drop points outside 0..1 that don't matter, repeats, and points in the middle of a straight stretch."""
    inside = [i for i, p in enumerate(env) if 0 <= p[0] <= 1]
    lo = max(0, inside[0] - 1) if inside else 0
    hi = min(len(env), inside[-1] + 2) if inside else len(env)
    out = []
    for p in env[lo:hi]:
        if out and p == out[-1]:
            continue
        if len(out) >= 2:
            (u0, v0), (u1, v1) = out[-2], out[-1]
            if u0 == u1 == p[0]:
                out[-1] = p  # three on one u: the middle one is never used
                continue
            if u0 < u1 < p[0] and abs(v0 + (p[1] - v0) * (u1 - u0) / (p[0] - u0) - v1) < 1e-7:
                out[-1] = p  # the middle point is on the straight line anyway
                continue
        out.append(p)
    return out
