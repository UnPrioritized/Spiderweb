"""The marks files/fastloops.py's loops carry (njit, prange, List, set_threads): plain Python here, so those loops run
as they're written. They're the last resort of our own compiled loops (files/native.py): a call the library refuses
(a value it can't take, a bad index caught inside) is done by its fastloops twin, slowly but the same. (Until 2026-11
these marks were Numba's, compiling fastloops; the library replaced it, PLAN.md B.)"""


def njit(*args, **kw):
    """@njit or @njit(...) on a function: the function as it is."""
    if len(args) == 1 and callable(args[0]) and not kw:
        return args[0]
    return lambda f: f


prange = range
List = list


def set_threads():
    """How many threads the shared-out loops use: nothing to set for plain Python."""
