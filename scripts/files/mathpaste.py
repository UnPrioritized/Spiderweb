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
import unicodedata

GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
         "eta": "η", "theta": "θ", "vartheta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
         "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ",
         "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
         "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω"}
LATEX_SIGNS = {"cdot": "*", "times": "*", "div": "/", "ast": "*"}
LATEX_SPACES = (r"\,", r"\;", r"\:", r"\!", r"\ ", r"\quad", r"\qquad")
SIGNS = {"×": "*", "·": "*", "⋅": "*", "∙": "*", "÷": "/", "−": "-", "–": "-", "—": "-"}
SUPER = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+"))
INVERSE = {"sin": "asin", "cos": "acos", "tan": "atan", "sinh": "asinh", "cosh": "acosh", "tanh": "atanh"}

GREEK_SIGNS = set(GREEK.values())
# PAD keeps words apart that were written together (2πt, A\sin, cosθ) without counting as a space typed between
# them: "t/2π" stays tight (t/(2π)), "t/2 π" doesn't. Turned into a plain space at the very end.
PAD = "\x01"
_LEAD = re.compile(r"\s*[^\W\d]\w*\s*(\([^()=]*\))?\s*=(?!=)")  # a leading "x =", "y(t) =", "r(θ) ="
_PREFIX = re.compile(r"\b(?:numpy|np|math)\.")
_TOKEN = re.compile(r"(?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)|(?P<name>[^\W\d]\w*)|(?P<sp>\s+)|(?P<pad>\x01+)"
                    r"|(?P<other>\*\*|.)")
GAPS = ("sp", "pad")
MOST_NESTED = 150  # brackets inside brackets (Python's own reader stops at 200)
LATEX_GROUP_WORDS = ("operatorname", "mathrm", "text", "mathit", "mathbf", "mathsf", "textrm")
LATEX_DROPPED = ("displaystyle", "textstyle", "limits", "nolimits")
LATEX_BRACKETS = {"lfloor": " floor(", "rfloor": ")", "lceil": " ceil(", "rceil": ")"}


class UnknownWord(ValueError):
    """A LaTeX word (\\pm, \\infty...) that can't be read: an error naming it, never a made-up number box."""
    def __init__(self, word):
        super().__init__(word)
        self.word = word


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


def from_latex(text, funcs=None):
    """LaTeX (\\frac{a}{b}, \\sin\\left(t\\right), ^{2}...) in plain words; text without a backslash or ^{ / _{
    comes back as it was. funcs (the function names known): a \\word that is none of them, nor Greek / a sign /
    a bracket = UnknownWord (None: left as a name). \\log = base 10 (as graphing sites mean it), \\log_{b} = base b."""
    if "\\" not in text and "^{" not in text and "_{" not in text:
        return text
    for sp in LATEX_SPACES:
        text = text.replace(sp, " ")
    # (\left( -> (, \left\{ -> {, \left| -> |; \left\lfloor -> \lfloor keeps its own backslash)
    text = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)(?![A-Za-z])\s*(?:\\(?![A-Za-z]))?", "", text)
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
                        out.append(f"(({from_latex(rest[0], funcs)})/({from_latex(rest[1], funcs)}))")
                        i = len(text) - len(rest) + 2
                    continue
                out.append(f"(({from_latex(a[0], funcs)})/({from_latex(b[0], funcs)}))")
                i = b[1]
            elif word == "sqrt":
                n = _group(text, i, "[", "]")
                if n:
                    i = n[1]
                a = _group(text, i)
                if a:
                    inside, i = from_latex(a[0], funcs), a[1]
                    out.append(f"(({inside})^(1/({from_latex(n[0], funcs)})))" if n else f"sqrt({inside})")
                else:
                    out.append("√")
            elif word in LATEX_GROUP_WORDS:
                a = _group(text, i)
                if a:
                    out.append(f"{PAD}{a[0]}{PAD}")
                    i = a[1]
            elif word in LATEX_DROPPED:
                pass
            elif word == "log":  # (\log = base 10 as graphing sites mean it; \log_{2}, \log_{10}, \log_{e})
                m = re.match(r"\s*_\s*(?:\{\s*([^{}]*?)\s*\}|(\d+|e))", text[i:])
                base = (m.group(1) or m.group(2)) if m else "10"
                if m:
                    i += m.end()
                names = {"10": "log10", "2": "log2", "e": "ln"}
                if base not in names:
                    if funcs is not None:
                        raise UnknownWord(f"log_{base}")
                    names[base] = "log"
                out.append(f"{PAD}{names[base]}{PAD}")
            elif word in LATEX_BRACKETS:
                out.append(LATEX_BRACKETS[word])
            elif word in GREEK:  # (PAD round words: A\sin, a_{1}\cos, 2\pi t stay apart = multiplied)
                out.append(f"{PAD}{GREEK[word]}{PAD}")
            elif word in LATEX_SIGNS:
                out.append(LATEX_SIGNS[word])
            elif funcs is None or word in funcs:  # \sin, \cos, \ln, \exp...: the word itself
                out.append(f"{PAD}{word}{PAD}")
            else:
                raise UnknownWord(word)
            continue
        if c in "^_" and i + 1 < len(text) and text[i + 1] == "{":
            a = _group(text, i + 1)
            if a:
                out.append(f"^({from_latex(a[0], funcs)})" if c == "^" else
                           "_" + re.sub(r"\W", "", from_latex(a[0], funcs)))
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
    if not cut:
        return None
    return tuple(re.sub(r"\s+", " ", s.replace(PAD, " ")).strip() for s in (t[1:cut], t[cut + 1:-1]))


def _bars(text):
    """|a| -> abs(a): a bar after a value closes, any other opens (" abs(" with a space: a|t| = a * abs(t))."""
    out, open_, last = [], 0, ""  # (last: the last sign written that wasn't a space)
    for c in text:
        if c != "|":
            out.append(c)
            if not c.isspace() and c != PAD:
                last = c
            continue
        if open_ > 0 and last and (last.isalnum() or last in ")]}._"):
            out.append(")")
            open_ -= 1
            last = ")"
        else:
            out.append(PAD + "abs(")
            open_ += 1
            last = "("
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
        if kind in GAPS:
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
            while k < n and toks[k][0] in GAPS:
                k += 1
            if k < n and toks[k][1] in ("^", "**"):  # (a power on it)
                j = k + 1
                while j < n and toks[j][0] in GAPS:
                    j += 1
                if j < n and toks[j][1] in "+-":
                    j += 1
            continue
        break
    # (spaces after the term stay outside it)
    while took and j > i and toks[j - 1][0] in GAPS:
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
        while j < n and toks[j][0] in GAPS:
            j += 1
        power = None
        if j < n and toks[j][1] in ("^", "**"):  # sin^2 / sin^(2) / sin^-1 / sin^(-1)
            k = j + 1
            while k < n and toks[k][0] in GAPS:
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
            while j < n and toks[j][0] in GAPS:
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


def _split_functions(toks, funcs):
    """sin2t, cos3θ (no space, as plain-text books write): a known function name + a name starting with a digit
    split (sin 2t). Names without digits ("cost") stay names; the longest function wins (log10t is not log 10t)."""
    out = []
    for kind, tok in toks:
        if kind == "name" and tok not in funcs:
            heads = [f for f in funcs if tok.startswith(f) and len(tok) > len(f)]
            head = max(heads, key=len) if heads else None
            if head and tok[len(head)].isdigit():
                out += [("name", head), ("pad", PAD)] + _tokens(tok[len(head):])
                continue
        out.append((kind, tok))
    return out


def _factor_end(toks, i):
    """Where one factor starting at toks[i] ends: a number, a name, a name's own brackets (a function), a bracket
    group, with a power on it."""
    n = len(toks)
    if i >= n:
        return i
    kind, tok = toks[i]
    if tok == "(":
        depth, j = 0, i
        while j < n:
            depth += toks[j][1] == "("
            depth -= toks[j][1] == ")"
            j += 1
            if depth == 0:
                break
    elif kind in ("num", "name"):
        j = i + 1
        if kind == "name" and j < n and toks[j][1] == "(":
            return _factor_end(toks, j)
    else:
        return i
    if j < n and toks[j][1] in ("^", "**"):
        k = j + 1
        if k < n and toks[k][1] in "+-":
            k += 1
        return _factor_end(toks, k) if k < n else k
    return j


def _multiply(text, funcs, constants):
    """Multiplying written side by side made a "*": 3t, 2pi, )(, )t, t sin(t), a t, a(t). Written TIGHT (no space)
    right after "/" or "^", the run stays together (Desmos's own reading: 1/2t = 1/(2t), t/2π = t/(2π), e^-2t =
    e^(-2t)); with a space it doesn't (1/2 t = t/2)."""
    out, last, last_tok = [], None, None  # out: (kind, text); kind "impl" = a "*" put in, "impl_sp" = across a space
    toks = _tokens(text)
    gap = ""  # (the gap kinds since the last token: "sp" a typed space, "pad" written apart by us)
    for kind, tok in toks:
        if kind in GAPS:
            out.append((kind, tok))
            gap += kind[0]
            continue
        here = "open" if tok == "(" else "close" if tok == ")" else kind
        apart, typed_space = bool(gap), "s" in gap
        gap = ""
        times = (last == "num" and here in ("name", "open")  # 3t, 2pi, 2(t)
                 or last == "close" and here in ("name", "open", "num")  # )t, )(, )2
                 or last == "name" and last_tok not in funcs and (
                     here in ("name", "num") and apart  # t sin(t), a t, kθ
                     or here == "open" and (len(last_tok) == 1 or last_tok in constants)))  # a(t + 1), pi(t)
        if times:
            out.append(("impl_sp" if typed_space else "impl", "*"))
        out.append((kind, tok))
        last, last_tok = here, tok
    i = 0
    while i < len(out):  # a tight run right after / or ^ in brackets
        if out[i][1] in ("/", "^", "**"):
            j = i + 1
            while j < len(out) and out[j][0] in GAPS:
                j += 1
            start = j
            if out[i][1] != "/" and j < len(out) and out[j][1] in "+-":
                j += 1
            end, joined = _factor_end(out, j), False
            while True:
                k = end
                while k < len(out) and out[k][0] == "pad":  # (2πt: the marks round π aren't typed spaces)
                    k += 1
                if k < len(out) and out[k][0] == "impl":
                    k += 1
                    while k < len(out) and out[k][0] == "pad":
                        k += 1
                    end, joined = _factor_end(out, k), True
                else:
                    break
            if joined:
                out[start:end] = [("other", "(")] + out[start:end] + [("other", ")")]
        i += 1
    return "".join(t for _, t in out).replace(PAD, " ")


ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))  # (hidden signs web copies carry)
LOOK_ALIKES = {"∗": "*", "∕": "/", "⁄": "/", "‐": "-", "‑": "-", "‒": "-", "∛": PAD + "∛", "√": PAD + "√"}


def as_written(text, var, funcs, constants):
    """text as people write it -> the words mathexpr reads (see the top). var: the position's name (θ = it when it's
    t); funcs / constants: the names mathexpr knows. ValueError (UnknownWord) for a LaTeX word it can't read."""
    depth = most = 0  # (hundreds of brackets inside each other: no formula, and slow to rewrite: hunt)
    for c in text:
        depth += c in "([{"
        depth -= c in ")]}"
        most = max(most, depth)
    if most > MOST_NESTED or text.count("|") > 2 * MOST_NESTED or text.count("\\") > 20 * MOST_NESTED:
        raise RecursionError
    text = _superscripts(text.translate(ZERO_WIDTH))  # (before NFKC: it makes ² a plain 2 = t2, one name)
    text = unicodedata.normalize("NFKC", text)  # (full-width （）＝，２ as plain signs, ϑ = θ)
    text = without_lead(from_latex(text, funcs)).strip()
    text = re.sub(r"[;,.]\s*$", "", text) if not re.search(r"\d\.\s*$", text) else text  # (a line's own end)
    text = _PREFIX.sub("", text)
    text = re.sub(r"\blog_\(?(10|2|e)\)?(?!\d)", lambda m: {"10": "log10", "2": "log2", "e": "ln"}[m.group(1)] + PAD,
                  text)  # (log_10 t, log_2(t) as typed)
    for sign, word in {**SIGNS, **LOOK_ALIKES}.items():
        text = text.replace(sign, word)
    for c in set(text) & GREEK_SIGNS:  # (a symbol of its own: 2πt = 2 pi t, cosθ = cos t, kθ = k t, ωt = ω t)
        word = "pi" if c == "π" else "t" if c == "θ" and var == "t" else c
        text = text.replace(c, PAD + word + PAD)
    text = _bars(text)
    text = text.replace("[", "(").replace("]", ")").replace("{", "(").replace("}", ")")
    text = re.sub(r"∛\s*((?:\d+\.?\d*|\.\d+)|[^\W\d]\w*|\([^()]*\))", r"((\1)^(1/3))", text)
    text = re.sub(r"√\s*((?:\d+\.?\d*|\.\d+)|[^\W\d]\w*)", r"sqrt(\1)", text).replace("√", "sqrt")
    text = "".join(t for _, t in _functions(_split_functions(_tokens(text), funcs), funcs))
    return _multiply(text, funcs, constants).strip()
