"""Lets number boxes accept simple math like 960*4 or (60+4)*16, and reads curve formulas like x^2."""

import ast
import functools
import math
import operator

from files import mathpaste
from files.lang import tr
from files.mathpaste import without_lead, split_pair  # noqa: F401 (used through here)

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}


def _power(left, right):
    """left ** right, refused when the exponent is over 64 or the answer would be past any number a box takes
    (whole numbers are worked out exactly, digit by digit: pow(9, 99999999) or powers of powers froze it)."""
    if abs(right) > 64:
        raise ValueError(tr("mathexpr.exponent_too_large"))
    if isinstance(left, int) and isinstance(right, int) and abs(left) > 1 and right * math.log2(abs(left)) > 1100:
        raise OverflowError
    return left ** right


def calc(text):
    """Evaluate simple math. Only numbers and + - * / // % ** ( ); x and ^ work too."""
    text = text.replace("x", "*").replace("×", "*").replace("^", "**")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow):
                return _power(left, right)
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return -ev(node.operand) if isinstance(node.op, ast.USub) else ev(node.operand)
        raise ValueError("unsupported")

    try:
        value = ev(ast.parse(text, mode="eval"))
        finite = math.isfinite(value)
    except ZeroDivisionError:
        raise ValueError(tr("mathexpr.division_by_zero"))
    except (SyntaxError, ValueError, TypeError, OverflowError):
        raise ValueError(tr("mathexpr.can_t_read", text=text))
    if not finite:  # (1e400, 1e308*10: endless numbers crashed the boxes)
        raise ValueError(tr("mathexpr.out_of_range"))
    return value


FORMULA_NAMES = {"pi": math.pi, "e": math.e}
FORMULA_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh, "sqrt": math.sqrt, "exp": math.exp,
    "log": math.log, "ln": math.log, "log10": math.log10, "log2": math.log2, "abs": abs, "min": min, "max": max,
    "floor": math.floor, "ceil": math.ceil, "round": round, "pow": _power,
    # (as other places write them: pasted formulas, user 2026-11-16)
    "arcsin": math.asin, "arccos": math.acos, "arctan": math.atan, "asinh": math.asinh, "acosh": math.acosh,
    "atanh": math.atanh, "arsinh": math.asinh, "arcosh": math.acosh, "artanh": math.atanh,
    "sec": lambda a: 1 / math.cos(a), "csc": lambda a: 1 / math.sin(a), "cot": lambda a: 1 / math.tan(a),
}


@functools.lru_cache(maxsize=1024)  # (the same text is read several times per shape: project loading, hunt)
def as_written(text, var="x"):
    """A formula as people write it elsewhere (LaTeX, 3t, sin^2(t), |t|, π, x = ..., np.sin, θ: files/mathpaste.py)
    in the words formula reads (user 2026-11-16: "copy pasting from site should work in here expectedly").
    ValueError (a message) when it can't be read."""
    try:
        return mathpaste.as_written(text, var, FORMULA_FUNCS, FORMULA_NAMES)
    except mathpaste.UnknownWord as e:
        raise ValueError(tr("mathexpr.unknown_latex", word=e.word))
    except RecursionError:  # (thousands of brackets inside each other: hunt)
        raise ValueError(tr("mathexpr.can_t_read_it_check_the"))


def formula(text, named=False, var="x"):
    """A formula of x (like x^2 or sin(x*pi/2)) -> a function of x. Raises ValueError if it can't be read.
    Numbers, x, pi, e, + - * / % ^ ( ) and FORMULA_FUNCS, also written as on websites (as_written). named: other
    names (like height) are numbers given when it's worked out, fn(x, {"height": 6}); fn.names = them in the order
    they first appear. var: the name of the position (t for shapes drawn by x(t) and y(t))."""
    text = as_written(text.strip(), var).strip().replace("^", "**")
    names = []
    if not text:
        raise ValueError(tr("mathexpr.type_a_formula"))
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, RecursionError, MemoryError):
        raise ValueError(tr("mathexpr.can_t_read_it_check_the"))

    def check(node):
        if isinstance(node, ast.Expression):
            return check(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return
        if isinstance(node, ast.Name):
            if node.id != var and node.id not in FORMULA_NAMES:
                if not named or node.id in FORMULA_FUNCS:
                    raise ValueError(tr("mathexpr.unknown_name_use_x_for_the", id=node.id))
                if node.id not in names:
                    names.append(node.id)
            return
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            check(node.left)
            check(node.right)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return check(node.operand)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            if node.func.id not in FORMULA_FUNCS:
                raise ValueError(tr("mathexpr.unknown_function", id=node.func.id))
            for a in node.args:
                check(a)
            return
        raise ValueError(tr("mathexpr.only_numbers_x_and_functions_like"))

    check(tree)

    def ev(node, x, values):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return x if node.id == var else FORMULA_NAMES[node.id] if node.id in FORMULA_NAMES else values[node.id]
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left, x, values), ev(node.right, x, values)
            if isinstance(node.op, ast.Pow):
                return _power(left, right)
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand, x, values)
            return -v if isinstance(node.op, ast.USub) else v
        return FORMULA_FUNCS[node.func.id](*(ev(a, x, values) for a in node.args))

    def fn(x, values=None):
        v = ev(tree.body, x, values or {})
        if isinstance(v, complex):
            raise ValueError(tr("mathexpr.not_a_real_number"))
        return float(v)
    fn.names = names
    fn.arrays = lambda x, values=None, keep_bad=False: _arrays(tree, var, x, values or {}, keep_bad)
    return fn


def _np_power(left, right, keep_bad=False):
    """_power for arrays (keep_bad: NaN where the power is too big, instead of ValueError)."""
    import numpy as np
    big = np.abs(right) > 64
    if np.any(big):
        if not keep_bad:
            raise ValueError(tr("mathexpr.exponent_too_large"))
        return np.where(big, np.nan, np.power(np.asarray(left, float), np.where(big, 1.0, right)))
    return np.power(np.asarray(left, float), right)


def _np_funcs():
    import numpy as np

    def many(f):
        return lambda *a: functools.reduce(f, a)
    return {"sin": np.sin, "cos": np.cos, "tan": np.tan, "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
            "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh, "sqrt": np.sqrt, "exp": np.exp, "log": np.log,
            "ln": np.log, "log10": np.log10, "log2": np.log2, "abs": np.abs, "min": many(np.minimum),
            "max": many(np.maximum), "floor": np.floor, "ceil": np.ceil,
            "round": lambda a, n=0: np.round(a, int(n)), "pow": _np_power,
            "arcsin": np.arcsin, "arccos": np.arccos, "arctan": np.arctan, "asinh": np.arcsinh, "acosh": np.arccosh,
            "atanh": np.arctanh, "arsinh": np.arcsinh, "arcosh": np.arccosh, "artanh": np.arctanh,
            "sec": lambda a: 1 / np.cos(a), "csc": lambda a: 1 / np.sin(a), "cot": lambda a: 1 / np.tan(a)}


def _arrays(tree, var, x, values, keep_bad=False):
    """A formula worked out for many x at once (NumPy arrays; values may be arrays too, broadcast against x): what
    fn gives one by one, as an array. ValueError where it can't be worked out (math's errors give NaN / inf here);
    keep_bad: NaN there instead (the caller leaves those places out)."""
    import numpy as np
    funcs = _np_funcs()
    if keep_bad:
        funcs["pow"] = lambda a, b: _np_power(a, b, True)

    def ev(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return x if node.id == var else FORMULA_NAMES[node.id] if node.id in FORMULA_NAMES else values[node.id]
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow):
                return _np_power(left, right, keep_bad)
            return _OPS[type(node.op)](np.asarray(left, float), right)
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        return funcs[node.func.id](*(ev(a) for a in node.args))

    with np.errstate(all="ignore"):
        try:
            v = np.broadcast_to(np.asarray(ev(tree.body), float), np.shape(x))
        except (TypeError, KeyError, ZeroDivisionError, OverflowError):
            raise ValueError(tr("mathexpr.not_a_real_number"))
    if not np.all(np.isfinite(v)):
        if keep_bad:
            return np.where(np.isfinite(v), v, np.nan)
        raise ValueError(tr("mathexpr.not_a_real_number"))
    return v


def calc_int(text, lo=None, hi=None):
    value = calc(text)
    if value != int(value):
        raise ValueError(tr("mathexpr.whole_number_needed"))
    value = int(value)
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ValueError(tr("mathexpr.out_of_range"))
    return value


def fmt(x):
    """Number -> short text: 960.0 -> '960', 0.3333333 -> '0.333'."""
    if abs(x - round(x)) < 1e-6:
        return str(int(round(x)))
    return f"{x:.3f}".rstrip("0").rstrip(".")
