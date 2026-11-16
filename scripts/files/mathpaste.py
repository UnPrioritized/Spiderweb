"""Formulas as people write them elsewhere (user 2026-11-16: "copy pasting from site should work in here
expectedly"), turned into the words files/mathexpr.py reads. Nothing here works anything out: it only rewrites the
text (mathexpr still checks every part of it, never runs it).

What it reads:
- LaTeX, as graphing sites and encyclopedias copy it: \\frac{a}{b}, \\left( \\right), \\sin, \\theta, \\pi, \\cdot,
  ^{2}, _{1}, \\sqrt{a}, \\sqrt[n]{a}, \\operatorname{name}.
- signs: π × · ÷ −, superscripts ⁰-⁹ ⁻ ⁺, √t, |t|, [ ] { } as brackets, np. / math. before a function.
- a leading "x =", "y(t) =", "r(θ) =".
- textbook habits (as Wolfram Alpha / SymPy read them): sin^2(t) = sin(t)^2, sin^-1(t) = arcsin(t), sin 3t =
  sin(3t) (a function without brackets takes the next term: numbers, names and brackets side by side, a power on
  them, up to + - * / or a comma), multiplying written side by side: 3t, 2pi, (t+1)(t-1), t sin(t), a t, a(t+1)
  (one letter before a bracket; a longer unknown word stays a function = "unknown function").
Letters stuck together stay ONE name ("at" is a number box, not a * t: "depth" must stay a name)."""

import re

GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
         "eta": "η", "theta": "θ", "vartheta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
         "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ",
         "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
         "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω"}
LATEX_SIGNS = {"cdot": "*", "times": "*", "div": "/", "ast": "*"}
LATEX_SPACES = (r"\,", r"\;", r"\:", r"\!", r"\ ", r"\quad", r"\qquad")
SIGNS = {"π": "pi", "×": "*", "·": "*", "⋅": "*", "∙": "*", "÷": "/", "−": "-", "–": "-", "—": "-"}
SUPER = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+"))
INVERSE = {"sin": "asin", "cos": "acos", "tan": "atan", "sinh": "asinh", "cosh": "acosh", "tanh": "atanh"}

_LEAD = re.compile(r"\s*[^\W\d]\w*\s*(\([^()=]*\))?\s*=(?!=)")  # a leading "x =", "y(t) =", "r(θ) ="
_PREFIX = re.compile(r"\b(?:numpy|np|math)\.")
_TOKEN = re.compile(r"(?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)|(?P<name>[^\W\d]\w*)|(?P<sp>\s+)|(?P<other>\*\*|.)")


def without_lead(text):
    """The formula without a leading "x =", "y(t) =", "r(θ) =" (pasted lines often have one)."""
    return _LEAD.sub("", text, count=1).lstrip() if _LEAD.match(text) else text


def _group(text, i, open_="{", close="}"):
    """(inside, index after) of the bracket group starting at text[i] (spaces before it skipped), or None."""
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text) or text[i] != open_:
        return None
    depth = 0
    for j in range(i, len(text)):
        if text[j] == open_:
            depth += 1
        elif text[j] == close:
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
    return None


def from_latex(text):
    """LaTeX (\\frac{a}{b}, \\sin\\left(t\\right), ^{2}...) in plain words; text without a backslash or ^{ / _{
    comes back as it was."""
    if "\\" not in text and "^{" not in text and "_{" not in text:
        return text
    for sp in LATEX_SPACES:
        text = text.replace(sp, " ")
    text = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)(?![A-Za-z])\s*\\?", "", text)  # (\left( -> (, \left\{ -> {)
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+)", text[i:])
            if not m:
                i += 1
                continue
            word, i = m.group(1), i + len(m.group(0))
            if word in ("frac", "dfrac", "tfrac"):
                a = _group(text, i)
                b = a and _group(text, a[1])
                if not b:  # (\frac12 = 1/2: one sign each)
                    rest = text[i:].lstrip()
                    if len(rest) >= 2:
                        out.append(f"(({from_latex(rest[0])})/({from_latex(rest[1])}))")
                        i = len(text) - len(rest) + 2
                    continue
                out.append(f"(({from_latex(a[0])})/({from_latex(b[0])}))")
                i = b[1]
            elif word == "sqrt":
                n = _group(text, i, "[", "]")
                if n:
                    i = n[1]
                a = _group(text, i)
                if a:
                    inside, i = from_latex(a[0]), a[1]
                    out.append(f"(({inside})^(1/({from_latex(n[0])})))" if n else f"sqrt({inside})")
                else:
                    out.append("√")
            elif word == "operatorname" or word == "mathrm" or word == "text":
                a = _group(text, i)
                if a:
                    out.append(a[0])
                    i = a[1]
            elif word in GREEK:  # (spaces round words: A\sin, a_{1}\cos, 2\pi t stay apart = multiplied)
                out.append(f" {GREEK[word]} ")
            elif word in LATEX_SIGNS:
                out.append(LATEX_SIGNS[word])
            else:  # \sin, \cos, \ln, \exp...: the word itself
                out.append(f" {word} ")
            continue
        if c in "^_" and i + 1 < len(text) and text[i + 1] == "{":
            a = _group(text, i + 1)
            if a:
                out.append(f"^({from_latex(a[0])})" if c == "^" else "_" + re.sub(r"\W", "", from_latex(a[0])))
                i = a[1]
                continue
        out.append(c)
        i += 1
    return "".join(out)


def split_pair(text):
    """A pasted point "(x(t), y(t))" (graphing sites copy a curve like that) -> (x text, y text), or None."""
    t = from_latex(without_lead(text.strip())).strip()
    if not (t.startswith("(") and t.endswith(")")):
        return None
    depth, cut = 0, None
    for i, c in enumerate(t):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0 and i != len(t) - 1:
                return None  # (the first bracket closes before the end: not one pair)
        elif c == "," and depth == 1:
            if cut is not None:
                return None
            cut = i
    return (t[1:cut].strip(), t[cut + 1:-1].strip()) if cut else None


def _bars(text):
    """|a| -> abs(a): a bar after a value closes, any other opens."""
    out, open_ = [], 0
    for i, c in enumerate(text):
        if c != "|":
            out.append(c)
            continue
        before = "".join(out).rstrip()
        closes = open_ > 0 and before and (before[-1].isalnum() or before[-1] in ")]}._")
        if closes:
            out.append(")")
            open_ -= 1
        else:
            out.append("abs(")
            open_ += 1
    return "".join(out)


def _superscripts(text):
    """t⁴ -> t^(4), t⁻¹ -> t^(-1)."""
    return re.sub("[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+", lambda m: "^(" + "".join(SUPER[c] for c in m.group()) + ")", text)


def _tokens(text):
    return [(m.lastgroup, m.group()) for m in _TOKEN.finditer(text)]


def _atom_end(toks, i, funcs):
    """Where the term starting at toks[i] ends: numbers, names and bracket groups side by side, each with a power on
    it (sin 3t^2 = sin(3t^2)); stops at + - * / , a closing bracket or another function (sin t cos t)."""
    j, n, took = i, len(toks), False
    while j < n:
        kind, tok = toks[j]
        if kind == "sp":
            j += 1
            continue
        if kind == "name" and tok in funcs and took:
            break
        if kind in ("num", "name") or tok == "(":
            if tok == "(":
                depth = 0
                while j < n:
                    depth += toks[j][1] == "("
                    depth -= toks[j][1] == ")"
                    j += 1
                    if depth == 0:
                        break
            else:
                j += 1
                if kind == "name" and j < n and toks[j][1] == "(":
                    continue  # (a function's own brackets: taken next round)
            took = True
            k = j
            while k < n and toks[k][0] == "sp":
                k += 1
            if k < n and toks[k][1] in ("^", "**"):  # (a power on it)
                j = k + 1
                while j < n and toks[j][0] == "sp":
                    j += 1
                if j < n and toks[j][1] in "+-":
                    j += 1
            continue
        break
    # (spaces after the term stay outside it)
    while took and j > i and toks[j - 1][0] == "sp":
        j -= 1
    return j


def _functions(toks, funcs):
    """sin^2(t) -> sin(t)^2, sin^-1(t) -> asin(t), sin 3t -> sin(3t)."""
    out, i, n = [], 0, len(toks)
    while i < n:
        kind, tok = toks[i]
        if kind != "name" or tok not in funcs:
            out.append(tok)
            i += 1
            continue
        j = i + 1
        while j < n and toks[j][0] == "sp":
            j += 1
        power = None
        if j < n and toks[j][1] in ("^", "**"):  # sin^2 / sin^(2) / sin^-1 / sin^(-1)
            k = j + 1
            while k < n and toks[k][0] == "sp":
                k += 1
            p_start = k
            if k < n and toks[k][1] == "(":
                depth = 0
                while k < n:
                    depth += toks[k][1] == "("
                    depth -= toks[k][1] == ")"
                    k += 1
                    if depth == 0:
                        break
            else:
                if k < n and toks[k][1] in "+-":
                    k += 1
                if k < n and toks[k][0] == "num":
                    k += 1
            power = "".join(t for _, t in toks[p_start:k]).replace(" ", "")
            j = k
            while j < n and toks[j][0] == "sp":
                j += 1
        name = tok
        if power in ("-1", "(-1)") and tok in INVERSE:
            name, power = INVERSE[tok], None
        if j < n and toks[j][1] == "(":  # its own brackets: as they are
            depth, k = 0, j
            while k < n:
                depth += toks[k][1] == "("
                depth -= toks[k][1] == ")"
                k += 1
                if depth == 0:
                    break
            inner = "".join(t for _, t in _functions(toks[j + 1:k - 1], funcs)) if k - 1 > j else ""
            out.append(f"{name}({inner})")
            i = k
        elif j < n and (toks[j][0] in ("num", "name")):  # no brackets: the next term
            k = _atom_end(toks, j, funcs)
            inner = "".join(t for _, t in _functions(toks[j:k], funcs)).strip()
            out.append(f"{name}({inner})")
            i = k
        else:
            out.append(name + (f"^{power}" if power else ""))
            i = j
            continue
        if power:
            out.append(f"^{power}" if power.startswith("(") else f"^({power})")
    return [("x", t) for t in out]


def _multiply(text, funcs, constants):
    """Multiplying written side by side made a "*": 3t, 2pi, )(, )t, t sin(t), a t, a(t)."""
    out, last, last_tok = [], None, None
    toks = _tokens(text)
    for idx, (kind, tok) in enumerate(toks):
        if kind == "sp":
            out.append(tok)
            continue
        here = "open" if tok == "(" else "close" if tok == ")" else kind
        spaced = idx > 0 and toks[idx - 1][0] == "sp"
        times = (last == "num" and here in ("name", "open")  # 3t, 2pi, 2(t)
                 or last == "close" and here in ("name", "open", "num")  # )t, )(, )2
                 or last == "name" and last_tok not in funcs and (
                     here in ("name", "num") and spaced  # t sin(t), a t
                     or here == "open" and (len(last_tok) == 1 or last_tok in constants)))  # a(t + 1), pi(t)
        if times:
            out.append("*")
        out.append(tok)
        last, last_tok = here, tok
    return "".join(out)


def as_written(text, var, funcs, constants):
    """text as people write it -> the words mathexpr reads (see the top). var: the position's name (θ = it when it's
    t); funcs / constants: the names mathexpr knows."""
    text = without_lead(from_latex(text))
    text = _PREFIX.sub("", text)
    for sign, word in SIGNS.items():
        text = text.replace(sign, word)
    if var == "t":
        text = text.replace("θ", "t")
    text = _superscripts(text)
    text = _bars(text)
    text = text.replace("[", "(").replace("]", ")").replace("{", "(").replace("}", ")")
    text = re.sub(r"√\s*((?:\d+\.?\d*|\.\d+)|[^\W\d]\w*)", r"sqrt(\1)", text).replace("√", "sqrt")
    text = "".join(t for _, t in _functions(_tokens(text), funcs))
    return _multiply(text, funcs, constants)
