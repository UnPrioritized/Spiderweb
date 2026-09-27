"""Lets number boxes accept simple math like 960*4 or (60+4)*16, and reads curve formulas like x^2."""

import ast
import math
import operator

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
                raise ValueError("exponent too large")
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return -ev(node.operand) if isinstance(node.op, ast.USub) else ev(node.operand)
        raise ValueError("unsupported")

    try:
        return ev(ast.parse(text, mode="eval"))
    except ZeroDivisionError:
        raise ValueError("division by zero")
    except (SyntaxError, ValueError, TypeError, OverflowError):
        raise ValueError(f"can't read \"{text}\"")


FORMULA_NAMES = {"pi": math.pi, "e": math.e}
FORMULA_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh, "sqrt": math.sqrt, "exp": math.exp,
    "log": math.log, "ln": math.log, "log10": math.log10, "log2": math.log2, "abs": abs, "min": min, "max": max,
    "floor": math.floor, "ceil": math.ceil, "round": round, "pow": pow,
}


def formula(text):
    """A formula of x (like x^2 or sin(x*pi/2)) -> a function of x. Raises ValueError if it can't be read.
    Numbers, x, pi, e, + - * / % ^ ( ) and FORMULA_FUNCS."""
    text = text.strip().replace("×", "*").replace("^", "**")
    if not text:
        raise ValueError("type a formula")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise ValueError("can't read it (check the brackets and signs)")

    def check(node):
        if isinstance(node, ast.Expression):
            return check(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return
        if isinstance(node, ast.Name):
            if node.id != "x" and node.id not in FORMULA_NAMES:
                raise ValueError(f"unknown name \"{node.id}\" (use x for the position)")
            return
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            check(node.left)
            check(node.right)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return check(node.operand)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            if node.func.id not in FORMULA_FUNCS:
                raise ValueError(f"unknown function \"{node.func.id}\"")
            for a in node.args:
                check(a)
            return
        raise ValueError("only numbers, x, + - * / ^ and functions like sin( ) work")

    check(tree)

    def ev(node, x):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return x if node.id == "x" else FORMULA_NAMES[node.id]
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left, x), ev(node.right, x)
            if isinstance(node.op, ast.Pow) and abs(right) > 64:
                raise ValueError("exponent too large")
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand, x)
            return -v if isinstance(node.op, ast.USub) else v
        return FORMULA_FUNCS[node.func.id](*(ev(a, x) for a in node.args))

    def fn(x):
        v = ev(tree.body, x)
        if isinstance(v, complex):
            raise ValueError("not a real number")
        return float(v)
    return fn


def calc_int(text, lo=None, hi=None):
    value = calc(text)
    if value != int(value):
        raise ValueError("whole number needed")
    value = int(value)
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ValueError("out of range")
    return value


def fmt(x):
    """Number -> short text: 960.0 -> '960', 0.3333333 -> '0.333'."""
    if abs(x - round(x)) < 1e-6:
        return str(int(round(x)))
    return f"{x:.3f}".rstrip("0").rstrip(".")
