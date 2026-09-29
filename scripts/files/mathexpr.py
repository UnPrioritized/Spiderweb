"""Lets number boxes accept simple math like 960*4 or (60+4)*16, and reads curve formulas like x^2."""

import ast
import math
import operator

from files.lang import tr

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}


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
            if isinstance(node.op, ast.Pow) and abs(right) > 64:
                raise ValueError(tr("mathexpr.exponent_too_large"))
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return -ev(node.operand) if isinstance(node.op, ast.USub) else ev(node.operand)
        raise ValueError("unsupported")

    try:
        return ev(ast.parse(text, mode="eval"))
    except ZeroDivisionError:
        raise ValueError(tr("mathexpr.division_by_zero"))
    except (SyntaxError, ValueError, TypeError, OverflowError):
        raise ValueError(tr("mathexpr.can_t_read", text=text))


FORMULA_NAMES = {"pi": math.pi, "e": math.e}
FORMULA_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh, "sqrt": math.sqrt, "exp": math.exp,
    "log": math.log, "ln": math.log, "log10": math.log10, "log2": math.log2, "abs": abs, "min": min, "max": max,
    "floor": math.floor, "ceil": math.ceil, "round": round, "pow": pow,
}


def formula(text, named=False, var="x"):
    """A formula of x (like x^2 or sin(x*pi/2)) -> a function of x. Raises ValueError if it can't be read.
    Numbers, x, pi, e, + - * / % ^ ( ) and FORMULA_FUNCS. named: other names (like height) are numbers given
    when it's worked out, fn(x, {"height": 6}); fn.names = them in the order they first appear. var: the name of
    the position (t for shapes drawn by x(t) and y(t))."""
    text = text.strip().replace("×", "*").replace("^", "**")
    names = []
    if not text:
        raise ValueError(tr("mathexpr.type_a_formula"))
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
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
            if isinstance(node.op, ast.Pow) and abs(right) > 64:
                raise ValueError(tr("mathexpr.exponent_too_large"))
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
    return fn


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
