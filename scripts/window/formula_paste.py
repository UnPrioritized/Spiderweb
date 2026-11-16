"""Paste formula… (user 2026-11-16: a tester pasted a whole block of code and called it useless; the converting was
the trouble): a window of its own to paste a block of text into (a website's formula, a graphing site's, code, Copy
formula's own text). It lists what it understood line by line (skipped lines grey, unreadable ones red), shows the
shape or pattern it makes, and Use it puts it in the formula window IN PLACE OF everything typed there (user). The
clipboard is never read by itself: only what the user pastes into the box (user: it may hold other data).

read_paste(text, kind) is the reading, on its own (tests call it)."""

import math
import re
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import as_written, fmt, formula
from files.mathpaste import from_latex, normalise, split_pair
from notes.pattern import (formula_loop, formula_shape, pattern_names, polar_xy, shape_names, span_numbers,
                           clean_pattern_graphs)
from window import look
from window.widgets import remember_place

MOST_LINES = 200   # lines read (a whole data file pasted by mistake stays quick)
NAME = r"[^\W\d]\w*"
_LHS = re.compile(rf"^({NAME})\s*(?:\(\s*({NAME})?\s*\))?$")
_RANGE_CALL = re.compile(r"^(?:(?:np|numpy)\s*\.\s*)?(linspace|arange)\s*\((.*)\)$")
_LESS = r"(?:<=|≤|⩽|<)"
_SPAN_BETWEEN = re.compile(rf"^(?:for\s+)?(.+?)\s*{_LESS}\s*({NAME})\s*{_LESS}\s*(.+)$")
_SPAN_IN = re.compile(rf"^(?:for\s+)?({NAME})\s*(?:∈|\bin\b)\s*[\[(](.*)[\])]$")
_SPAN_FROM = re.compile(rf"^(?:for\s+)?({NAME})\s+from\s+(.+?)\s+to\s+(.+)$")
_LEAD_WORDS = re.compile(r"^(?:where|with|let|and|given)\s+", re.I)
_LATEX_COMPARE = re.compile(r"\\(leqslant|leq|le|geq|ge|in|lt|gt)(?![a-zA-Z])")
_COMPARE_SIGNS = {"leqslant": "≤", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "in": "∈", "lt": "<", "gt": ">"}
_EQUALS = re.compile(r"(?<![<>!=≤≥])=(?!=)")
POSITIONS = {"shape": "t", "pattern": "x"}
TARGETS = {"shape": ("x", "y", "polar"), "pattern": ("formula", "along")}


def _split_top(text, seps):
    """text cut at the signs in seps that aren't inside brackets."""
    out, depth, start = [], 0, 0
    for i, c in enumerate(text):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
        elif c in seps and not depth:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return out


def _statement(s):
    return bool(re.search(r"=|≤|<|∈|\bfrom\b|\bin\b", s))


def _pieces(line):
    """A line's statements: cut at ; (code), and at commas between statements ("x = cos t, y = sin t, 0 ≤ t ≤ 2π")."""
    line = re.split(r"#|//", line, maxsplit=1)[0]  # (a code comment)
    line = _LATEX_COMPARE.sub(lambda m: _COMPARE_SIGNS[m.group(1)], normalise(line))
    out = []
    for part in _split_top(line, ";"):
        sub = _split_top(part, ",")
        out += sub if len(sub) > 1 and all(_statement(s) for s in sub) else [part]
    cleaned = []
    for s in out:
        s = _LEAD_WORDS.sub("", s.strip())
        s = re.sub(r"[;,]\s*$", "", s).strip()
        s = re.sub(r"(?<!\d)\.\s*$", "", s).strip()  # (a sentence's full stop; "2." stays a number)
        if s:
            cleaned.append(s)
    return cleaned


def _lhs(text):
    """(name, its bracket's name or None) of the left side of "=", or None."""
    try:
        text = from_latex(text)
    except ValueError:
        return None
    m = _LHS.match(text.strip())
    return (m.group(1), m.group(2)) if m else None


def _span_of(s):
    """(position name, [from, to]) when s gives a range ("0 ≤ t ≤ 2π", "t ∈ [0, 2π]", "t from 0 to 2pi",
    "t = np.linspace(0, 2*np.pi)", "t = 0:0.01:2*pi"), else None."""
    m = _SPAN_BETWEEN.match(s)
    if m:
        return m.group(2), [m.group(1), m.group(3)]
    m = _SPAN_IN.match(s)
    if m:
        parts = _split_top(m.group(2), ",")
        return (m.group(1), [parts[0], parts[1]]) if len(parts) == 2 else None
    m = _SPAN_FROM.match(s)
    if m:
        return m.group(1), [m.group(2), m.group(3)]
    eq = _EQUALS.search(s)
    if not eq:
        return None
    side = _lhs(s[:eq.start()])
    if not side or side[1]:
        return None
    rhs = s[eq.end():].strip()
    m = _RANGE_CALL.match(rhs)
    if m:
        parts = _split_top(m.group(2), ",")
        return (side[0], parts[:2]) if len(parts) >= 2 else None
    parts = _split_top(rhs, ":")  # (another language's 0:0.01:2*pi)
    if len(parts) in (2, 3):
        return side[0], [parts[0], parts[-1]]
    return None


def _span_texts(span):
    """[from, to] in the words the formula reader takes ("2*np.pi" -> "2*pi"); ValueError if they can't be read."""
    out = [_tidy(as_written(s, "\0")).replace(" ", "") for s in span]  # (numbers only: "2 *pi" -> "2*pi")
    span_numbers(out)
    return out


def _tidy(text):
    """The formula reader's words with plain spaces: one between words, none just inside brackets."""
    text = re.sub(r"\s+", " ", text.replace("\x01", " "))
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", text)).strip()


def _put_in(text, helpers):
    """Helper names (r = 1 - sin(t) on a line of its own) replaced by their formulas, in brackets."""
    for _ in range(10):
        new = re.sub(rf"\b{NAME}\b", lambda m: f"({helpers[m.group()]})" if m.group() in helpers else m.group(), text)
        if new == text:
            break
        text = new
    return text


def _renamed(text, old, new):
    """The word old as new (code that calls the position theta or u)."""
    return text if old == new else re.sub(rf"(?<!\\)\b{re.escape(old)}\b", new, text)


class Found:
    """What read_paste understood. texts: {"x", "y"} or {"polar"} (a shape), {"formula", maybe "along"} (a pattern),
    in the formula reader's words; span: [from, to] or None; values: {name: number} for every name in them; notes:
    [(line number or None, "used" / "skip" / "bad" / "info", text)] in line order; ready: all a formula needs."""

    def __init__(self):
        self.texts, self.span, self.values, self.notes, self.ready = {}, None, {}, [], False


def read_paste(text, kind):
    """A pasted block of text (kind "shape" or "pattern") -> Found."""
    labels = {key: tr("pattern_dialog." + label).rstrip("= ").strip() for key, label in
              (("x", "xt"), ("y", "yt"), ("polar", "rt"), ("formula", "y"), ("along", "along"))}
    span_text = re.escape(tr("pattern_dialog.copy_span", lo="QQloQQ", hi="QQhiQQ"))  # (Copy formula's own line)
    copy_span = re.compile("^" + span_text.replace("QQloQQ", "(?P<lo>.+?)").replace("QQhiQQ", "(?P<hi>.+?)") + "$")
    pos = POSITIONS[kind]
    out = Found()
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    pieces = [(n, p) for n, line in enumerate(lines[:MOST_LINES], 1) for p in _pieces(line)]
    if len(lines) > MOST_LINES and any(s.strip() for s in lines[MOST_LINES:]):
        out.notes.append((None, "info", tr("formula_paste.most_lines", n=MOST_LINES)))

    # the position's own name in this text (code often says theta, a graphing site θ: both = t)
    named = pos
    for _, p in pieces:
        got = _span_of(p) if kind == "shape" else None
        if got and got[0] not in ("t", "θ"):
            named = got[0]
            break
        eq = _EQUALS.search(p)
        side = _lhs(p[:eq.start()]) if eq else None
        if side and side[1] and side[1] not in (pos, "θ") and side[0] in ("x", "X", "y", "Y", "r", "R", "ρ", "f"):
            named = side[1]
            break

    def words(rhs):
        """rhs in the formula reader's words, its position called pos (ValueError if it can't be read)."""
        rhs = _renamed(rhs, named, pos) if named != pos else rhs
        got = _tidy(as_written(rhs, pos))
        formula(got, named=True, var=pos)
        return got

    targets, numbers, helpers, r_lines, lone = {}, {}, {}, [], []
    keep = {}  # key -> line number it came from

    def target(n, key, rhs, label):
        try:
            got = words(rhs)
        except ValueError as e:
            out.notes.append((n, "bad", tr("formula_paste.bad", what=label, e=e)))
            return
        if key in targets:
            out.notes.append((keep[key], "skip", tr("formula_paste.again", what=labels[key])))
        targets[key], keep[key] = got, n

    def number_or_helper(n, name, rhs):
        try:
            fn = formula(rhs, named=True, var="\0")
            if all(a in numbers for a in fn.names):
                v = float(fn(0.0, {a: numbers[a][1] for a in fn.names}))
                if math.isfinite(v):
                    numbers[name] = (n, v)
                    helpers.pop(name, None)
                    return
        except (ValueError, ArithmeticError, TypeError, KeyError):
            pass
        try:
            helpers[name] = words(rhs)
            keep[("helper", name)] = n
            numbers.pop(name, None)
        except ValueError as e:
            out.notes.append((n, "bad", tr("formula_paste.bad", what=name, e=e)))

    for n, p in pieces:
        m = copy_span.match(p)
        got = (pos, [m.group("lo"), m.group("hi")]) if m else _span_of(p)
        if got:
            if kind != "shape":
                out.notes.append((n, "skip", tr("formula_paste.pattern_span")))
                continue
            try:
                out.span = _span_texts(got[1])
            except ValueError as e:
                out.notes.append((n, "bad", tr("formula_paste.bad", what=tr("formula_paste.span_what"), e=e)))
                continue
            keep["span"] = n
            continue
        label_key = next((key for key in TARGETS[kind] if p.startswith(labels[key] + " =") or
                          p.startswith(labels[key] + "=")), None)  # (Copy formula's own lines, any language)
        if label_key and labels[label_key] not in ("x", "y"):
            target(n, label_key, p[p.index("=") + 1:], labels[label_key])
            continue
        eq = _EQUALS.search(p)
        if not eq:
            pair = split_pair(p) if kind == "shape" else None
            if pair:
                target(n, "x", pair[0], labels["x"])
                target(n, "y", pair[1], labels["y"])
            elif kind == "pattern":
                lone.append((n, p))
            else:
                out.notes.append((n, "skip", tr("formula_paste.not_formula")))
            continue
        side, rhs = _lhs(p[:eq.start()]), p[eq.end():]
        if not side:
            out.notes.append((n, "skip", tr("formula_paste.not_formula")))
            continue
        name, arg = side
        low = name.lower()
        if kind == "shape":
            if low in ("x", "y"):
                target(n, low, rhs, labels[low])
            elif name in ("r", "ρ"):
                r_lines.append((n, rhs, arg))
            elif name in (pos, named, "θ"):
                out.notes.append((n, "skip", tr("formula_paste.position_shape")))
            elif arg:
                pair = split_pair(rhs)
                if pair:
                    target(n, "x", pair[0], labels["x"])
                    target(n, "y", pair[1], labels["y"])
                else:
                    out.notes.append((n, "skip", tr("formula_paste.not_shape", name=f"{name}({arg})")))
            else:
                number_or_helper(n, name, rhs)
        else:
            if name == "along":
                target(n, "along", rhs, labels["along"])
            elif low in ("y", "f", "g") or arg in (pos, named):
                target(n, "formula", rhs, labels["formula"])
            elif name in (pos, named):
                out.notes.append((n, "skip", tr("formula_paste.position_pattern")))
            else:
                number_or_helper(n, name, rhs)
    if kind == "shape":  # r: the shape itself (r(θ) = ...), or a number / part of x and y when they're given
        for n, rhs, arg in r_lines:
            if "x" in targets or "y" in targets:
                number_or_helper(n, "r", rhs)
            else:
                target(n, "polar", rhs, labels["polar"])
        if "polar" in targets and ("x" in targets or "y" in targets):
            out.notes.append((keep["polar"], "skip", tr("formula_paste.xy_too")))
            targets.pop("polar")
    if kind == "pattern" and lone:
        if "formula" in targets:
            out.notes += [(n, "skip", tr("formula_paste.not_formula")) for n, _ in lone]
        else:
            for n, p in lone[:-1]:
                out.notes.append((n, "skip", tr("formula_paste.not_formula")))
            n, p = lone[-1]
            try:
                targets["formula"], keep["formula"] = words(p), n
            except ValueError:
                out.notes.append((n, "skip", tr("formula_paste.not_formula")))

    # the helpers put into the formulas; then the names left = number boxes
    used = set()
    for text_ in targets.values():
        todo = [text_]
        while todo:
            for w in re.findall(rf"\b{NAME}\b", todo.pop()):
                if w in helpers and w not in used:
                    used.add(w)
                    todo.append(helpers[w])
    for key in targets:
        targets[key] = _put_in(targets[key], helpers)
    for name in helpers:
        out.notes.append((keep[("helper", name)], "used" if name in used else "skip",
                          tr("formula_paste.helper", name=name) if name in used else
                          tr("formula_paste.unused", name=name)))
    for key in TARGETS[kind]:
        if key in targets:
            out.notes.append((keep[key], "used", f"{labels[key]} = {targets[key]}"))
    if out.span:
        out.notes.append((keep["span"], "used", tr("formula_paste.span", lo=out.span[0], hi=out.span[1])))
    try:
        names = (shape_names(targets.get("x", "0"), targets.get("y", "0")) if kind == "shape" and "polar" not in
                 targets else shape_names(targets["polar"], "0") if kind == "shape" else
                 pattern_names(targets.get("formula", "0"), targets.get("along", "")))
    except ValueError as e:
        out.notes.append((None, "bad", str(e)))
        names = None
    for name, (n, v) in numbers.items():
        if names is not None and name in names:
            out.notes.append((n, "used", f"{name} = {fmt(v)}"))
        else:
            out.notes.append((n, "skip", tr("formula_paste.unused", name=name)))
    for name in names or ():
        if name not in numbers:
            out.notes.append((None, "info", tr("formula_paste.no_number", name=name)))
    out.values = {name: numbers[name][1] if name in numbers else 1.0 for name in names or ()}
    out.texts = targets
    if kind == "shape":
        have_xy = "x" in targets and "y" in targets
        out.ready = names is not None and (have_xy or "polar" in targets)
        if not out.ready and ("x" in targets) != ("y" in targets):
            missing = "y" if "x" in targets else "x"
            out.notes.append((None, "bad", tr("formula_paste.missing", what=labels[missing])))
    else:
        out.ready = names is not None and "formula" in targets
        if not out.ready and "along" in targets:
            out.notes.append((None, "bad", tr("formula_paste.missing", what=labels["formula"])))
    out.notes.sort(key=lambda note: (note[0] is None, note[0] or 0))
    return out


def pasted_pattern(found, kind, old, k):
    """The curve's new shape / pattern from found: everything typed before replaced (user); only what belongs to the
    curve rather than the formula stays (a pattern's Loops, Each piece, its Loops graph). ValueError if it can't be
    worked out (the preview's message)."""
    if kind == "shape":
        p = {"preset": "", "vars": dict(found.values), "k": k, "mirror": False}
        if found.span and span_numbers(found.span) != (0.0, 1.0):
            p["t_span"] = list(found.span)
        if "polar" in found.texts:
            p["polar"] = found.texts["polar"]
            p["x"], p["y"] = polar_xy(p["polar"], p.get("t_span"))
        else:
            p["x"], p["y"] = found.texts["x"], found.texts["y"]
        formula_shape(p)
        return p
    p = {"preset": "", "formula": found.texts["formula"], "vars": dict(found.values), "loops": old["loops"],
         "each": old.get("each", False), "k": k, "mirror": False, "scale": old.get("scale", 1.0)}
    if found.texts.get("along", "").strip():
        p["along"] = found.texts["along"]
    graphs = clean_pattern_graphs(old.get("graphs"), {})  # (the Loops graph only)
    if graphs:
        p["graphs"] = graphs
    formula_loop(p)
    return p


class FormulaPaste(tk.Toplevel):
    """Opened from the formula window (dlg, pattern_dialog.FormulaDialog); Use it calls dlg.use_pasted(pattern)."""

    def __init__(self, dlg):
        super().__init__(dlg)
        self.dlg, self.kind = dlg, dlg.kind
        self.title(tr("formula_paste.title"))
        self.transient(dlg)
        self.resizable(False, False)
        self.pat, self.job = None, None
        s = dlg.scale
        self.w, self.h = int(420 * s), int(190 * s)
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=tr("formula_paste.hint_" + self.kind), wraplength=int(420 * s),
                  justify="left").pack(anchor="w")
        self.text = tk.Text(body, height=8, width=64, font=look.mono(9), wrap="none", undo=True)
        self.text.pack(fill="x", pady=(4, 8))
        self.text.bind("<<Modified>>", self.changed)
        ttk.Label(body, text=tr("formula_paste.found")).pack(anchor="w")
        self.found = tk.Text(body, height=7, width=64, font=look.font(9), wrap="word", background=look.FIELD_SOFT,
                             relief="flat", cursor="arrow")
        self.found.pack(fill="x", pady=(2, 8))
        for tag, color in (("skip", look.FAINTER_TEXT), ("bad", look.ERROR_DARK), ("info", look.SOFT_TEXT),
                           ("line", look.FAINTER_TEXT)):
            self.found.tag_config(tag, foreground=color)
        self.canvas = tk.Canvas(body, width=self.w, height=self.h, bg=look.CHART_BG, highlightthickness=1,
                                highlightbackground=look.PAT_BORDER)
        self.canvas.pack()
        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text=tr("pattern_dialog.cancel"), command=self.close).pack(side="right")
        self.use_btn = ttk.Button(btns, text=tr("formula_paste.use"), command=self.use, state="disabled")
        self.use_btn.pack(side="right", padx=4)
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.read()
        remember_place(self, "formula_paste")
        self.grab_set()  # (the formula window waits; its own grab comes back on close)
        self.text.focus_set()

    def changed(self, _=None):
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        if self.job:
            self.after_cancel(self.job)
        self.job = self.after(120, self.read)  # (typing: once it stops for a moment)

    def read(self):
        self.job = None
        found = read_paste(self.text.get("1.0", "end-1c"), self.kind)
        self.pat, problem = None, None
        if found.ready:
            try:
                self.pat = pasted_pattern(found, self.kind, self.dlg.pat, self.dlg.k if self.kind == "shape"
                                          else self.dlg.pat["k"])
            except ValueError as e:
                problem = tr("formula_paste.can_t_use", e=e)
        self.show_found(found, problem)
        self.use_btn.config(state="normal" if self.pat else "disabled")
        self.draw()

    def show_found(self, found, problem):
        f = self.found
        f.config(state="normal")
        f.delete("1.0", "end")
        notes = list(found.notes) + ([(None, "bad", problem)] if problem else [])
        if not notes:
            f.insert("end", tr("formula_paste.nothing"), "info")
        for i, (n, kind, text) in enumerate(notes):
            if i:
                f.insert("end", "\n")
            if n is not None:
                f.insert("end", tr("formula_paste.line", n=n) + "  ", "line")
            f.insert("end", text, kind)
        f.config(state="disabled")

    def draw(self):
        c = self.canvas
        c.delete("all")
        if not self.pat:
            return
        try:
            u, v = formula_shape(self.pat) if self.kind == "shape" else formula_loop(self.pat)
        except ValueError:
            return
        length = 1.0 if self.kind == "shape" else self.dlg.loop_len()
        us, vs = [a * length for a in u.tolist()], v.tolist()
        lo_u, hi_u = min(us + [0.0]), max(us + [length])
        if self.kind == "pattern":
            tall = max([abs(b) for b in vs] + [0.5])
            lo_v, hi_v = -tall, tall
        else:
            lo_v, hi_v = min(vs + [0.0]), max(vs + [0.0])
        pad = 18 * self.dlg.scale
        s = min((self.w - 2 * pad) / max(1e-9, hi_u - lo_u), (self.h - 2 * pad) / max(1e-9, hi_v - lo_v))
        x0 = self.w / 2 - (lo_u + hi_u) / 2 * s
        ym = self.h / 2 + (lo_v + hi_v) / 2 * s
        c.create_line(x0, ym, x0 + length * s, ym, fill=look.PAT_ORIGIN, dash=(6, 4))
        pts = [q for a, b in zip(us, vs) for q in (x0 + a * s, ym - b * s)]
        if len(pts) >= 4:
            c.create_line(*pts, fill=look.PAT_LINE, width=2)

    def use(self):
        if not self.pat:
            return
        pat = self.pat
        self.close()
        self.dlg.use_pasted(pat)

    def close(self):
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        self.grab_release()
        self.destroy()
        try:
            self.dlg.grab_set()
            self.dlg.focus_set()
        except tk.TclError:
            pass
