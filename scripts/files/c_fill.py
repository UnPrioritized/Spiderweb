"""Some Windows-compatible systems ship a C runtime (ucrtbase.dll) whose complex-number functions are only stand-ins
that END THE PROGRAM when called, and NumPy calls crealf while it loads. fill() finds those stand-ins and makes them
work, before NumPy is first loaded. Nothing happens on real Windows (system.WINE is None there), on 32-bit, or when
every function is real.

A stand-in is told apart by its own code, never by calling it (that would end the program): x64
  sub rsp,28h / lea rcx,[file name] / lea rdx,[function name] / call
with the name it points at = the function's own name. Anything else is left alone, so a system with other code (a
newer version that has them all, ARM code) is never touched. Each one found is overwritten with a jump to a plain
Python stand-in (cmath; slow, but Spiderweb's own work never uses complex numbers), crealf / cimagf / creal...
with a few bytes of real code. Untested on most such systems: only one version was tried."""

import cmath
import ctypes
import math
import struct

from files.system import WINE

_kept = []  # (the Python stand-ins: they must live as long as the program)
NAN = complex(math.nan, math.nan)

# Machine code (plain x64, nothing newer) for the tiny ones. _Fcomplex arrives packed in rcx, a _Dcomplex (long
# double = double here) by its address in rcx.
_CODE = {
    "crealf": bytes.fromhex("66 0f 6e c1 c3"),              # movd xmm0, ecx; ret
    "cimagf": bytes.fromhex("48 c1 e9 20 66 0f 6e c1 c3"),  # shr rcx, 32; movd xmm0, ecx; ret
    "creal": bytes.fromhex("f2 0f 10 01 c3"),               # movsd xmm0, [rcx]; ret
    "creall": bytes.fromhex("f2 0f 10 01 c3"),
    "cimag": bytes.fromhex("f2 0f 10 41 08 c3"),            # movsd xmm0, [rcx+8]; ret
    "cimagl": bytes.fromhex("f2 0f 10 41 08 c3"),
}

_ONE = {"exp": cmath.exp, "log": cmath.log, "sqrt": cmath.sqrt, "sin": cmath.sin, "cos": cmath.cos,
        "tan": cmath.tan, "sinh": cmath.sinh, "cosh": cmath.cosh, "tanh": cmath.tanh, "asin": cmath.asin,
        "acos": cmath.acos, "atan": cmath.atan, "asinh": cmath.asinh, "acosh": cmath.acosh, "atanh": cmath.atanh,
        "conj": lambda z: z.conjugate(),
        "proj": lambda z: complex(math.inf, math.copysign(0.0, z.imag)) if cmath.isinf(z) else z}
_REAL = {"abs": abs, "arg": cmath.phase}
_BASES = (*_ONE, *_REAL, "pow")


def _safe(f, *a):
    try:
        return f(*a)
    except (ValueError, OverflowError, ZeroDivisionError):
        return NAN


def _pow(z, w):
    if w == 0:
        return complex(1.0, 0.0)
    if z == 0:
        return complex(0.0, 0.0)
    return z ** w


def _unpack_f(v):
    re, im = struct.unpack("<ff", struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF))
    return complex(re, im)


def _pack_f(z):
    try:
        return struct.unpack("<Q", struct.pack("<ff", z.real, z.imag))[0]
    except (OverflowError, struct.error):  # (past float's range: infinity, like C)
        f = lambda x: x if abs(x) <= 3.4e38 or x != x else math.copysign(math.inf, x)
        return struct.unpack("<Q", struct.pack("<ff", f(z.real), f(z.imag)))[0]


def _read_d(p):
    re, im = struct.unpack("<dd", ctypes.string_at(p, 16))
    return complex(re, im)


def _write_d(p, z):
    ctypes.memmove(p, struct.pack("<dd", z.real, z.imag), 16)
    return p


def _stand_in(name):
    """A ctypes function for name (C99 complex.h, Microsoft's way of passing them), or None."""
    if not name.startswith("c"):
        return None
    base, kind = name[1:], "d"  # (long double = double here: the "l" ones work like the plain ones)
    if base[-1:] in ("f", "l") and base[:-1] in _BASES:
        base, kind = base[:-1], ("f" if base[-1] == "f" else "d")
    F, D = ctypes.c_uint64, ctypes.c_void_p
    if base in _ONE:
        f = _ONE[base]
        if kind == "f":
            return ctypes.CFUNCTYPE(F, F)(lambda z: _pack_f(_safe(f, _unpack_f(z))))
        return ctypes.CFUNCTYPE(D, D, D)(lambda out, z: _write_d(out, _safe(f, _read_d(z))))  # (out = hidden address)
    if base in _REAL:
        f = _REAL[base]
        if kind == "f":
            return ctypes.CFUNCTYPE(ctypes.c_float, F)(lambda z: _safe(f, _unpack_f(z)).real)
        return ctypes.CFUNCTYPE(ctypes.c_double, D)(lambda z: _safe(f, _read_d(z)).real)
    if base == "pow":
        if kind == "f":
            return ctypes.CFUNCTYPE(F, F, F)(lambda z, w: _pack_f(_safe(_pow, _unpack_f(z), _unpack_f(w))))
        return ctypes.CFUNCTYPE(D, D, D, D)(lambda out, z, w: _write_d(out, _safe(_pow, _read_d(z), _read_d(w))))
    return None


NAMES = list(_CODE) + ["c" + b + s for b in _BASES for s in ("", "f", "l")]  # (every one a stand-in may be)


def _is_stand_in(address, name):
    """True when the code at address is a do-nothing stand-in naming name (see the top)."""
    b = ctypes.string_at(address, 23)
    if not (b[:4] == b"\x48\x83\xec\x28" and b[4:7] == b"\x48\x8d\x0d" and b[11:14] == b"\x48\x8d\x15"
            and b[18] == 0xE8):
        return False
    text = address + 18 + int.from_bytes(b[14:18], "little", signed=True)
    return ctypes.string_at(text, len(name) + 1) == name.encode() + b"\0"


# Where the stand-ins can sit: the C runtime itself, and the small "API set" file programs link to, whose functions
# usually lead into it but may be stand-ins of their own in some versions (one already filled = real code there).
MODULES = ("ucrtbase", "api-ms-win-crt-math-l1-1-0")


def _kernel():
    k32 = ctypes.WinDLL("kernel32")
    k32.GetProcAddress.restype = ctypes.c_void_p
    k32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    k32.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    k32.FlushInstructionCache.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    return k32


def patch(k32, at, name):
    """Fills in the stand-in for name at address at. True when done."""
    if not at or not _is_stand_in(at, name):
        return False
    code = _CODE.get(name)
    if code is None:
        f = _stand_in(name)
        if f is None:
            return False
        _kept.append(f)
        code = b"\x48\xb8" + struct.pack("<Q", ctypes.cast(f, ctypes.c_void_p).value) + b"\xff\xe0"  # mov rax; jmp rax
    old = ctypes.c_uint32()
    if not k32.VirtualProtect(at, len(code), 0x40, ctypes.byref(old)):  # (PAGE_EXECUTE_READWRITE)
        return False
    ctypes.memmove(at, code, len(code))
    k32.VirtualProtect(at, len(code), old.value, ctypes.byref(ctypes.c_uint32()))
    k32.FlushInstructionCache(k32.GetCurrentProcess(), at, len(code))
    return True


def fill():
    """Makes the stand-ins work. Returns the names filled (empty: nothing to do). Never raises."""
    if not WINE or ctypes.sizeof(ctypes.c_void_p) != 8:
        return []
    done = []
    try:
        k32 = _kernel()
    except (OSError, AttributeError):
        return done
    for module_name in MODULES:
        try:
            module = ctypes.c_void_p(ctypes.WinDLL(module_name)._handle)
        except (OSError, AttributeError):
            continue
        for name in dict.fromkeys(NAMES):
            try:
                if patch(k32, k32.GetProcAddress(module, name.encode()), name):
                    done.append(name)
            except Exception:  # (a start must never fail here: that function stays as it was)
                continue
    return done
