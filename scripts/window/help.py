"""Tips and the Help window. A tip pops up the first time a tool or feature is used (which ones were seen is
remembered with the window settings; they can be turned off or all shown again). The Help window (F1) lists every
topic of help_texts.py, with a search box. Topics can have short clips (clips/*.gif, inside the .exe, played in
the Help window; which ones go where: see help_texts.py)."""

import os
import re
import sys
import tkinter as tk
import webbrowser
from tkinter import font as tkfont, ttk

from files.lang import size as lang_size, tr
from files.about import BANNER, BUILT, FULL_VERSION, HERE, LICENSE, VERSION, WEBSITE
from files import system
from files.system import open_path
from window import look
from window.widgets import Tooltip, WrapRow, placed, remember_place, set_least
from window.help_texts import (BY_ID, DRAWER_TOOL_TOPICS, NEXT, PAGE_LINK, SECTION_NAMES, SECTIONS, SEE,
                               TOOL_TOPICS, TOPICS, link_words)

TOOL_TIPS = set(TOOL_TOPICS.values()) | set(DRAWER_TOOL_TOPICS.values())
# the tips' place once dragged (window_places; "tip" was saved where the first tip showed, moved or not: left behind)
TIP_PLACE = "tip_moved"
CLIPS = os.path.join(getattr(sys, "_MEIPASS", HERE), "clips")  # (the .exe carries them inside)
TOPIC_LINKS = 3  # a page with this many "## " headings or more gets a line of links to them at its top (user 2026-10-29)


def with_build_date(page):
    """What's new with this build's date line under its own version's heading, when the page has none there yet
    (released versions have theirs written in; the newest gets one at its next release). From source: none."""
    if not BUILT:
        return page
    head = "## Version " + VERSION.split("-")[0] + "\n"
    at = page.find(head)
    if at < 0 or page.startswith("^^ ", at + len(head)):
        return page
    at += len(head)
    return page[:at] + "^^ " + BUILT + "\n" + page[at:]


def gif_frames(path):
    """Split a GIF into its frames without decoding them: ((width, height), [(one-frame GIF, x, y, delay in ms,
    disposal)]), or None if it isn't a GIF. (Tk's own "gif -index n" decodes every frame before n again, so loading
    a clip frame by frame took seconds.)"""
    with open(path, "rb") as f:
        d = f.read()
    if d[:3] != b"GIF" or len(d) < 13:
        return None
    size = (int.from_bytes(d[6:8], "little"), int.from_bytes(d[8:10], "little"))
    flags, i = d[10], 13
    if flags & 0x80:
        i += 3 * (2 << (flags & 7))
    table = d[13:i]
    frames, gce = [], None
    try:
        while i < len(d) and d[i] != 0x3B:
            if d[i] == 0x21:  # extension; only the frame's timing / transparency one is kept
                start, label = i, d[i + 1]
                i += 2
                while d[i]:
                    i += d[i] + 1
                i += 1
                if label == 0xF9:
                    gce = d[start:i]
            elif d[i] == 0x2C:  # a frame: its box, then (maybe) its own colours and the picture data
                start = i
                x, y, w, h = (int.from_bytes(d[i + 1 + 2 * k:i + 3 + 2 * k], "little") for k in range(4))
                f = d[i + 9]
                i += 10
                if f & 0x80:
                    i += 3 * (2 << (f & 7))
                i += 1
                while d[i]:
                    i += d[i] + 1
                i += 1
                delay, disposal = 0, 0
                if gce:
                    delay, disposal = int.from_bytes(gce[4:6], "little"), (gce[3] >> 2) & 7
                one = (b"GIF89a" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes([flags & 0x87, 0, 0])
                       + table + (gce or b"") + b"\x2C" + bytes(4) + d[start + 5:i] + b"\x3B")
                # (like web browsers: no delay or a tiny one = a tenth of a second)
                frames.append((one, x, y, delay * 10 if delay >= 2 else 100, disposal))
                gce = None
            else:
                break
    except IndexError:  # cut off: play the frames that are whole
        pass
    return (size, frames) if frames else None


class Clip:
    """A GIF playing in a label. Each frame is decoded when it's shown and drawn over the picture so far, so a
    clip opens at once, GIFs that only store the pixels that changed play right, and the GIF's own timing is kept."""

    def __init__(self, parent, path):
        (w, h), self.frames = gif_frames(path)
        self.image = tk.PhotoImage(width=w, height=h)
        self.clear = tk.PhotoImage(width=1, height=1)  # (see-through: copied over a spot to empty it)
        self.label = tk.Label(parent, image=self.image, borderwidth=0)
        self.i, self.saved, self.job = 0, None, None
        self.step()

    def step(self):
        if not self.label.winfo_exists():
            return
        img, n = self.image, self.i % len(self.frames)
        if n == 0:
            img.blank()
        else:  # what the frame before asked for once it's been shown
            _, x, y, _, disposal = prev = self.frames[n - 1]
            if disposal == 2:
                w, h = self.size(prev)
                img.tk.call(img, "copy", self.clear, "-to", x, y, x + w, y + h, "-compositingrule", "set")
            elif disposal == 3 and self.saved:
                img.tk.call(img, "copy", self.saved, "-compositingrule", "set")
        data, x, y, delay, disposal = self.frames[n]
        if disposal == 3:
            self.saved = tk.PhotoImage()
            self.saved.tk.call(self.saved, "copy", img)
        try:
            pic = tk.PhotoImage(data=data, format="gif")
            img.tk.call(img, "copy", pic, "-to", x, y)
        except tk.TclError:  # a broken frame: skip it
            pass
        self.i += 1
        self.job = self.label.after(delay, self.step)

    @staticmethod
    def size(frame):
        d = frame[0]
        return int.from_bytes(d[6:8], "little"), int.from_bytes(d[8:10], "little")

    def stop(self):
        if self.job:
            self.label.after_cancel(self.job)
            self.job = None


def companion_bar(app):
    """The floating bar while drawing on Domino (window/domino_companion.py), else None."""
    c = getattr(app, "companion", None)
    bar = getattr(c, "bar", None)
    try:
        return bar if bar is not None and bar.winfo_exists() else None
    except tk.TclError:
        return None


def topic_words(t):
    return " ".join((t["title"], t["tip"], t["text"], t.get("words", ""))).lower()


class Tips:
    """Which tips were seen, whether they show at all, and the popup."""

    def __init__(self, app):
        self.app = app
        self.seen = set()
        self.on = tk.BooleanVar(value=True)
        self.popup = None
        self.waiting = []  # [(topic, parent, force)] shown one after another once the open tip is closed

    def show(self, topic_id, parent=None, force=False, wait=False, done=False):
        """The topic's tip, unless it was seen before or tips are off (force: anyway). wait: if another tip is
        open, show it once that one is closed instead of replacing it. done: the open tip was read ("Got it" going
        on to the next one), so it isn't shown again later."""
        if topic_id not in BY_ID or not force and (not self.on.get() or topic_id in self.seen):
            return
        open_now = self.popup is not None and self.popup.winfo_exists()
        if wait and open_now and self.popup.topic != topic_id:
            if topic_id not in [w[0] for w in self.waiting]:
                self.waiting.append((topic_id, parent, force))
            return
        self.waiting = [w for w in self.waiting if w[0] != topic_id]
        if open_now and not done and self.popup.topic != topic_id and self.popup.topic not in TOOL_TIPS:
            # a tip pushed aside (by a tool's tip, say) comes back next; one tool's tip replacing another's doesn't
            self.waiting.insert(0, (self.popup.topic, self.popup.master, True))
        self.seen.add(topic_id)
        self.app.schedule_autosave()
        parent = parent or self.app
        bar = companion_bar(self.app)
        if parent is self.app and bar is not None:
            parent = bar  # (drawing on Domino: the main window is hidden, so is anything it owns)
        if open_now and self.popup.master is parent:
            self.popup.set_topic(topic_id)
        else:
            self.close()
            self.popup = TipPopup(self, parent, topic_id)

    def close(self):
        if self.popup is not None and self.popup.winfo_exists():
            self.popup.destroy()
        self.popup = None

    def closed(self):
        """A tip was closed: the next one waiting, if any (skipping ones seen in the meantime). Tips turned off:
        the waiting ones are dropped too."""
        if not self.on.get():
            self.waiting = []
            return
        while self.waiting and (self.popup is None or not self.popup.winfo_exists()):
            topic_id, parent, force = self.waiting.pop(0)
            if parent is None or parent.winfo_exists():
                self.show(topic_id, parent, force=force)

    def reset(self):
        self.seen.clear()
        self.app.schedule_autosave()

    def state(self):
        """For the autosave's window settings."""
        return {"tips_seen": sorted(self.seen), "tips_on": self.on.get()}

    def restore(self, win):
        seen = win.get("tips_seen")
        if isinstance(seen, list):
            self.seen = {str(s) for s in seen}
        self.on.set(win.get("tips_on") is not False)


class TipPopup(tk.Toplevel):
    """A small window in the top right corner of the roll (or the drawer): the tip, "More…" (the Help window at
    that topic), "Got it". It doesn't block anything."""
    APP_ONLY = True  # (keys pressed here never change the shapes: App.app_only)

    def __init__(self, tips, parent, topic_id):
        try:
            f = parent.focus_get()  # (before this window exists)
        except (KeyError, tk.TclError):  # (a dropdown's list has it: older Pythons raise)
            f = None
        super().__init__(parent)
        self.tips, self.parent = tips, parent
        s = self.scale = tips.app.scale
        self.title(tr("help.tip"))
        self.transient(parent)
        self.resizable(False, False)
        try:
            self.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        box = ttk.Frame(self, padding=(12, 10, 12, 10))
        box.pack(fill="both", expand=True)
        self.head = ttk.Label(box, font=look.font(10, "bold"))
        self.head.pack(anchor="w")
        self.body = ttk.Label(box, wraplength=int(lang_size("size.tip_width", 380) * s), justify="left")
        self.body.pack(anchor="w", pady=(4, 10))
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Checkbutton(row, text=tr("help.show_tips"), variable=tips.on,
                        command=tips.app.schedule_autosave).pack(side="left")
        ttk.Button(row, text=tr("help.got_it"), command=self.got_it).pack(side="right")
        ttk.Button(row, text=tr("help.more"), command=self.more).pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda e: self.got_it())
        self.protocol("WM_DELETE_WINDOW", lambda: (self.destroy(), tips.closed()))
        self.set_topic(topic_id)
        # back to what you were doing: the popup doesn't take the keyboard (a box being typed in keeps it)
        back = f if f is not None else parent  # (in whichever window it is: a number box in Settings keeps it)
        self.after(30, lambda: (back if back.winfo_exists() else parent).focus_force() if parent.winfo_exists()
                   else None)

    def set_topic(self, topic_id):
        t = BY_ID[topic_id]
        self.topic = topic_id
        self.head.config(text=t["title"])
        self.body.config(text=t["tip"])
        self.update_idletasks()
        p = self.parent
        if p is companion_bar(self.tips.app):  # (drawing on Domino: right under the floating bar)
            x = p.winfo_rootx() + p.winfo_width() - self.winfo_reqwidth()
            y = p.winfo_rooty() + p.winfo_height() + int(8 * self.scale)
            left, top, right, bottom = system.work_area(self, x, y)
            x, y = min(max(x, left), right - self.winfo_reqwidth()), min(max(y, top), bottom - self.winfo_reqheight())
        else:
            area = getattr(p, "roll", None) or getattr(p, "canvas", None) or p
            x = area.winfo_rootx() + area.winfo_width() - self.winfo_reqwidth() - int(16 * self.scale)
            y = area.winfo_rooty() + int(16 * self.scale)
        # dragged once: every tip stays where it was put; until then each one goes to its own window's corner
        places = self._root().__dict__.setdefault("window_places", {})
        moved = placed(self, places.get(TIP_PLACE, ""))
        if moved:
            self.put = None
            self.geometry(moved)
        else:
            self.put = (max(0, x), max(0, y))
            self.geometry("+%d+%d" % self.put)
        if not getattr(self, "watched", False):
            self.watched = True
            self.bind("<Configure>", self.keep_place, add="+")
        self.deiconify()

    def keep_place(self, e):
        """Remembered only when the user moved it (not where it was put)."""
        if e.widget is not self or not self.winfo_ismapped():
            return
        m = re.search(r"\+(-?\d+)\+(-?\d+)$", self.wm_geometry())
        if m and (self.put is None or abs(int(m[1]) - self.put[0]) > 2 or abs(int(m[2]) - self.put[1]) > 2):
            self._root().window_places[TIP_PLACE] = f"+{m[1]}+{m[2]}"
            self.put = None

    def got_it(self):
        """Close it, or go on to the tip that follows this one (NEXT) if that wasn't seen yet."""
        nxt = NEXT.get(self.topic)
        if nxt and self.tips.on.get() and nxt not in self.tips.seen:
            self.tips.show(nxt, parent=self.parent, done=True)
        else:
            self.destroy()
            self.tips.closed()

    def more(self):
        """The Help window at this topic. A tip waiting stays waiting (it would take the keyboard from Help)."""
        open_help(self.tips.app, self.topic)
        self.destroy()


class HelpWindow(tk.Toplevel):
    """Every topic: a list by section with a search box (all the words typed have to be in the topic), the chosen
    topic on the right (the words searched for highlighted), its clip if there is one."""
    APP_ONLY = True  # (Ctrl+Z / Ctrl+A / Ctrl+V... here never change the shapes behind it: App.app_only)

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        s = self.scale = app.scale
        self.title(tr("help.spiderweb_help", VERSION=VERSION))
        self.geometry(f"{int(lang_size('size.help_width', 900) * s)}x{int(620 * s)}")
        self.minsize(int(600 * s), int(360 * s))
        remember_place(self, "help")
        self.topic = None
        self.drawn = None  # (topic, search) on the page now: the same again isn't drawn twice
        self.clips = []  # the Clip objects playing in the shown topic
        self.pictures = {}  # clips/<name>.png read once while the window is open
        # Back / Forward (user 2026-11-14): pages left behind as (topic, its top line shown); a search's own picks
        # aren't kept (typing would fill the list), only pages opened by a click / link
        self.back, self.forward = [], []
        self.auto_pick = None  # (the topic the search picked by itself: showing it isn't a step)

        left = ttk.Frame(self, padding=(8, 8, 4, 8))
        left.pack(side="left", fill="y")
        # Alt+arrows and the mouse's back / forward buttons (Tk 8.7+ gives them the numbers 8 / 9 everywhere). The
        # buttons BOTH ways: a real press on Windows reached only a plain <ButtonPress> (the user's mouse,
        # 2026-11-14: e.num 8 / 9, "<Button-8>" never fired), a generated one only "<Button-8>". Tk runs one
        # binding per tag for an event, so a press never steps twice.
        for seq, step in (("<Alt-Left>", -1), ("<Alt-Right>", 1), ("<Button-8>", -1), ("<Button-9>", 1)):
            self.bind(seq, lambda e, step=step: self.go(step) or "break")
        self.bind("<ButtonPress>", self.side_button, add="+")
        self.query = tk.StringVar()
        search = ttk.Entry(left, textvariable=self.query, width=30)
        search.pack(fill="x")
        ttk.Label(left, text=tr("help.search_type_words_all_of_them"), foreground=look.HINT,
                  font=look.font(8)).pack(anchor="w", pady=(2, 4))
        self.query.trace_add("write", lambda *_: self.fill_list())
        search.bind("<Down>", lambda e: (self.tree.focus_set(), self.pick_first()))
        search.bind("<Return>", lambda e: self.pick_first())
        tree_box = ttk.Frame(left)
        tree_box.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_box, show="tree", selectmode="browse")
        font = tkfont.nametofont(look.TK)  # (wide enough for the longest title, in any language, bold too)
        bold = tkfont.Font(font=font)
        bold.configure(weight="bold")
        self._bold = bold  # (kept: Tk forgets a font nobody holds)
        self.tree.column("#0", width=max(bold.measure(t["title"]) for t in TOPICS) + int(48 * s))
        sb = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")  # (packed first: a tight window never squeezes it out)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_pick())
        self.tree.tag_configure("title_some", font=bold)
        self.tree.tag_configure("title_all", font=bold, background=look.SEARCH_HIT)

        right = ttk.Frame(self, padding=(4, 8, 8, 8))
        right.pack(side="left", fill="both", expand=True)
        bottom = ttk.Frame(right)
        bottom.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Checkbutton(bottom, text=tr("help.show_a_tip_the_first_time"), variable=app.tips.on,
                        command=app.schedule_autosave).pack(side="left")
        ttk.Button(bottom, text=tr("help.show_all_tips_again"), command=self.reset_tips).pack(side="left", padx=(8, 0))
        self.reset_note = ttk.Label(bottom, text="", foreground=look.GOOD)
        self.reset_note.pack(side="left", padx=(6, 0))
        version = ttk.Label(bottom, text=tr("help.spiderweb", VERSION=FULL_VERSION), foreground=look.FAINT_TEXT, cursor="hand2",
                            font=look.font(9))  # (the hover's font from the start: it didn't change width on hover)
        version.pack(side="right")
        version.bind("<Button-1>", lambda e: self.open_topic("about"))
        version.bind("<Enter>", lambda e: version.config(font=look.font(9, "underline")))
        version.bind("<Leave>", lambda e: version.config(font=look.font(9)))
        # Back / Forward: two small coloured arrows at the top right, grey when there's nowhere to go (user
        # 2026-11-14: buttons were too big)
        steps = ttk.Frame(right)
        steps.pack(side="top", fill="x")
        # (◀ ▶: the user's pick of 6, 2026-11-14; the side panel's shut strip uses ◀ too)
        self.forward_button = self.step_arrow(steps, "▶", 1, tr("help.go_forward_tip"))
        self.back_button = self.step_arrow(steps, "◀", -1, tr("help.go_back_tip"))
        text_box = ttk.Frame(right)
        text_box.pack(fill="both", expand=True)
        bg = ttk.Style().lookup("TFrame", "background") or "SystemButtonFace"
        self.text = tk.Text(text_box, wrap="word", font=look.font(10), relief="flat", borderwidth=0,
                            highlightthickness=0, padx=10, pady=6, cursor="arrow", background=bg,
                            spacing2=2, spacing3=4, width=20)  # (small ask: it fills what's there)
        tsb = ttk.Scrollbar(text_box, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")  # (packed first: a tight window never squeezes it out)
        self.text.pack(side="left", fill="both", expand=True)
        self._banner_label, self._banner_job = None, None
        self.text.bind("<Configure>", self.banner_later, add="+")
        self.text.tag_configure("title", font=look.font(14, "bold"), spacing3=8)
        self.text.tag_configure("section", font=look.font(9), foreground=look.HINT)
        self.text.tag_configure("heading", font=look.font(12, "bold"), spacing1=6)
        self.text.tag_configure("hit", background=look.SEARCH_HIT)
        self.text.tag_configure("link", foreground=look.LINK)
        self.text.tag_configure("back_top", font=look.font(8))  # (the small link after a heading)
        self.text.tag_configure("small", font=look.font(8), foreground=look.HINT)  # ("^^ " lines: What's new dates)
        self.text.tag_configure("hover", underline=True)
        # (a long bullet line wraps under its text, not under the •)
        self.text.tag_configure("bullet", lmargin2=tkfont.Font(font=self.text.cget("font")).measure("•  "))
        self.text.config(state="disabled")
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<F1>", lambda e: "break")  # (already open: stays on the page being read)
        for key in ("<Control-f>", "<Control-F>"):  # (F: Caps Lock on, user 2026-10-29)
            self.bind(key, lambda e: (search.focus_set(), search.select_range(0, "end"), search.icursor("end"),
                                      "break")[-1])
        self.bind("<Key>", lambda e: self.type_to_search(e, search))
        # (also on the list, search box and page themselves: their own Left / Right keys would fold a section or
        # move the cursor first)
        for w in (self.tree, search, self.text):
            for seq, step in (("<Alt-Left>", -1), ("<Alt-Right>", 1)):
                w.bind(seq, lambda e, step=step: self.go(step) or "break")
        self.fill_list()
        self.sync_steps()
        search.focus_set()
        # (never narrower than the bottom row's tick, button and version beside the topic list: cut off in a wider
        # font or a longer translation otherwise)
        # the bottom row onto two lines when the window is too narrow for it (the note after "Show all tips again",
        # a wider font, a longer translation: cut off otherwise); never narrower than its widest piece
        def fit(*_):
            if self.winfo_exists() and right.winfo_width() > 1:
                row.fit(right.winfo_width() - int(12 * s))
                set_least(self, max(int(600 * s), left.winfo_reqwidth() + row.widest() + int(24 * s)), int(360 * s))
        row = WrapRow(bottom, changed=fit)
        right.bind("<Configure>", fit, add="+")

    def type_to_search(self, e, search):
        """Typing anywhere in the window types into the search box (Backspace too)."""
        if e.widget is search or e.state & 0x4 or e.state & 0x20000:  # (Ctrl / Alt: shortcuts)
            return None
        if e.keysym == "BackSpace":
            search.focus_set()
            search.delete(max(0, len(search.get()) - 1), "end")
            return "break"
        if not e.char or not e.char.isprintable():
            return None
        if e.char == " " and isinstance(e.widget, (ttk.Button, ttk.Checkbutton)):
            return None  # (Space presses a button that has the keyboard)
        search.focus_set()
        search.select_clear()
        search.insert("end", e.char)
        search.icursor("end")
        return "break"

    def matches(self):
        words = self.query.get().lower().split()
        return [t for t in TOPICS if all(w in topic_words(t) for w in words)]

    def title_rank(self, topic):
        """How well a page's title fits the search (user 2026-11-14: the best ones stand out in the list):
        2 = every word in its title, 1 = some, 0 = none (or no search)."""
        words = self.query.get().lower().split()
        n = sum(w in topic["title"].lower() for w in words)
        return 2 if words and n == len(words) else 1 if n else 0

    def fill_list(self):
        found = self.matches()
        rank = {t["id"]: self.title_rank(t) for t in found}
        self.tree.delete(*self.tree.get_children())
        for section in SECTIONS:
            topics = [t for t in found if t["section"] == section]
            if not topics:
                continue
            node = self.tree.insert("", "end", iid="section:" + section, text=SECTION_NAMES[section], open=True)
            # pages whose title has the words first (in their section), bold; every word = also the search colour
            for t in sorted(topics, key=lambda t: -rank[t["id"]]):
                self.tree.insert(node, "end", iid=t["id"], text=t["title"],
                                 tags=((), ("title_some",), ("title_all",))[rank[t["id"]]])
        best = max(rank.values(), default=0)
        if self.topic and self.tree.exists(self.topic) and rank[self.topic] >= best:
            self.tree.selection_set(self.topic)
            self.tree.see(self.topic)
            self.show(self.topic)  # (the highlighted words changed)
        elif found:
            self.pick_first()  # (a page whose title has the words, when there is one)
        else:
            self.show(None)

    def pick_first(self):
        kids = [c for s in self.tree.get_children() for c in self.tree.get_children(s)]
        ranks = {c: self.title_rank(BY_ID[c]) for c in kids}
        first = max(kids, key=lambda c: ranks[c], default=None)  # (the first of the best: max keeps the first)
        if first:
            self.auto_pick = first
            self.tree.selection_set(first)
            self.tree.see(first)

    def on_pick(self):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("section:"):  # a section: its first topic
            kids = self.tree.get_children(iid)
            if kids:
                self.tree.selection_set(kids[0])
            return
        auto, self.auto_pick = self.auto_pick == iid, None
        if not auto:
            self.step_from(iid)
        self.show(iid)

    def here(self):
        """The page shown now and its top line, for Back / Forward (None: nothing shown yet)."""
        if self.topic is None or self.drawn is None or self.drawn[0] != self.topic:
            return None
        return self.topic, self.text.index("@0,0")

    def step_from(self, topic_id):
        """Going to topic_id by a click: the page now goes on the Back list, Forward is forgotten."""
        now = self.here()
        if now and topic_id != now[0]:
            self.back.append(now)
            del self.back[:-100]
            self.forward.clear()
        self.sync_steps()

    def go(self, step):
        """Back (-1) or Forward (+1): that page again, scrolled where it was."""
        take, keep = (self.back, self.forward) if step < 0 else (self.forward, self.back)
        if not take:
            return
        now = self.here()
        if now:
            keep.append(now)
        topic_id, top = take.pop()
        self.open_topic(topic_id, step=False)
        self.text.yview(top)
        self.sync_steps()

    def side_button(self, e):
        """The mouse's back (8) / forward (9) button anywhere in the window: Back / Forward."""
        step = {8: -1, 9: 1}.get(e.num)
        if step:
            self.go(step)
            return "break"
        return None

    def step_arrow(self, row, arrow, step, tip):
        """One Back / Forward arrow: link-coloured text, a click goes there; grey (nothing to do) when it can't."""
        label = ttk.Label(row, text=arrow, font=look.font(12, "bold"), padding=(6, 0))
        label.pack(side="right")
        label.bind("<Button-1>", lambda e: self.go(step))
        Tooltip(label, tip)
        return label

    def sync_steps(self):
        for label, can in ((self.back_button, self.back), (self.forward_button, self.forward)):
            label.state(["!disabled" if can else "disabled"])
            label.config(foreground=look.LINK if can else look.FAINT_TEXT, cursor="hand2" if can else "")

    def open_topic(self, topic_id, step=True, spot=None):
        """Show that page (step: the page now goes on the Back list); spot = words of the part a link is about:
        the page opens scrolled there and that line flashes."""
        if step:
            self.step_from(topic_id)
        if self.query.get() and topic_id not in [t["id"] for t in self.matches()]:
            self.query.set("")
        self.topic = topic_id
        if self.tree.exists(topic_id):
            self.tree.selection_set(topic_id)
            self.tree.see(topic_id)
        self.show(topic_id)
        if spot:
            self.go_to_spot(spot)

    def find_spot(self, spot):
        """The line holding the spot words (a heading first, else the first paragraph with them; never the title,
        the section name or the "On this page" line); None if they aren't on the page."""
        t, first, at = self.text, None, "1.0"
        while True:
            at = t.search(spot, at, "end", nocase=True)
            if not at:
                break
            tags = t.tag_names(at)
            line = int(at.split(".")[0])
            if not ({"title", "section"} & set(tags) or any(x.startswith("head_link") for x in tags)):
                if "heading" in tags:
                    return line
                first = first or line
            at = f"{line + 1}.0"
        return first

    def go_to_spot(self, spot):
        """Scroll the page so the spot's line is near the top (the empty line above it showing) and pulse it."""
        line = self.find_spot(spot)
        if line is None:
            return
        t = self.text
        t.update_idletasks()
        t.yview(f"{max(1, line - 1)}.0")
        self.flash(line)

    FLASH_STEPS = (1.0, 0.75, 0.5, 0.25, 0.0, 0.0, 0.5, 1.0, 0.8, 0.6, 0.4, 0.25, 0.1, 0.0)  # two pulses, ~1.2 s

    def flash(self, line):
        """A short pulse on the line (user 2026-11-14: shows "here" after a link jumped down the page)."""
        t = self.text
        job = getattr(self, "_flash_job", None)
        if job:
            self.after_cancel(job)
        t.tag_remove("flash", "1.0", "end")
        t.tag_add("flash", f"{line}.0", f"{line}.end+1c")
        t.tag_lower("flash")  # (search hits and links stay readable on top)
        page = [c / 65535 for c in self.winfo_rgb(t.cget("background"))]
        strong = [c / 65535 for c in self.winfo_rgb(look.HELP_FLASH)]

        def step(n=0):
            self._flash_job = None
            if not t.winfo_exists():
                return
            if n >= len(self.FLASH_STEPS):
                t.tag_remove("flash", "1.0", "end")
                return
            k = self.FLASH_STEPS[n]
            t.tag_configure("flash", background="#%02x%02x%02x" % tuple(
                round(255 * (p + (s - p) * k)) for p, s in zip(page, strong)))
            self._flash_job = self.after(85, step, n + 1)
        step()

    def show(self, topic_id):
        """The topic's page (None: "Nothing found", the open topic kept for when the search finds it again)."""
        if (topic_id, self.query.get()) == self.drawn:
            return
        self.drawn = (topic_id, self.query.get())
        if topic_id is not None:
            self.topic = topic_id
        t = self.text
        t.config(state="normal")
        self.stop_clip()
        for w in t.winfo_children():  # (deleting the text leaves them to Python, with their pictures)
            w.destroy()
        t.delete("1.0", "end")
        if getattr(self, "_flash_job", None):  # (a pulse from the page before stops)
            self.after_cancel(self._flash_job)
            self._flash_job = None
        if topic_id is None:
            t.insert("end", tr("help.nothing_found_try_fewer_or_other"), "section")
        else:
            topic = BY_ID[topic_id]
            t.insert("end", SECTION_NAMES[topic["section"]] + "\n", "section")
            t.insert("end", topic["title"] + "\n", "title")
            hidden = self.found_by(topic)
            if hidden:
                t.insert("end", tr("help.found_by") + ", ".join(hidden) + "\n\n", "section")
            top = t.index("end-1c")  # (where the topic links go)
            if topic_id == "about":
                self.about_banner()
            page = topic["page"]
            # (the page's own-named clip goes at the top, unless the page places it itself: not shown twice)
            if f"[clip:{topic_id}]" not in page and self.add_clip(topic_id):
                t.insert("end", "\n\n")
            if topic_id == "whats_new":
                page = with_build_date(page)
            links = 0
            for n, part in enumerate(re.split(r"\[clip:([^\]]*)\]\n?", page)):
                if n % 2:  # (the odd parts are the clip names)
                    if self.add_clip(part):
                        t.insert("end", "\n")
                else:
                    links = self.put_text(part, links)
            heads = []
            for i in range(1, int(t.index("end").split(".")[0])):
                if t.get(f"{i}.0") == "•":
                    t.tag_add("bullet", f"{i}.0", f"{i}.end")
                elif t.get(f"{i}.0", f"{i}.3") == "## ":  # ("## Name" line = a heading)
                    t.delete(f"{i}.0", f"{i}.3")
                    t.tag_add("heading", f"{i}.0", f"{i}.end")
                    heads.append(i)
                elif t.get(f"{i}.0", f"{i}.3") == "^^ ":  # ("^^ text" line = small grey text)
                    t.delete(f"{i}.0", f"{i}.3")
                    t.tag_add("small", f"{i}.0", f"{i}.end")
            self.topic_links(top, heads)
            for w in set(self.query.get().lower().split()):
                start = "1.0"
                while True:
                    start = t.search(w, start, "end", nocase=True)
                    if not start:
                        break
                    end = f"{start}+{len(w)}c"
                    t.tag_add("hit", start, end)
                    start = end
            if topic_id == "about":
                self.about_buttons()
            self.see_also(SEE.get(topic_id, []))
        t.config(state="disabled")

    def found_by(self, topic):
        """The search words found only in the topic's hidden search words or its tip (not on its page): the
        words holding them, so the page shows why it was found and they get highlighted."""
        shown = (topic["title"] + " " + topic["text"]).lower()
        hidden = re.findall(r"[\w'-]+", (topic.get("words", "") + " " + topic["tip"]).lower())
        out = []
        for w in dict.fromkeys(self.query.get().lower().split()):
            if w not in shown:
                out += [h for h in dict.fromkeys(hidden) if w in h and h not in out]
        return out

    def put_text(self, part, links):
        """A piece of a page at the end of the text, its "[page:<id>|words]" marks as links that open that page
        (an unknown page: the words as plain text). links = how many were put before (each gets its own tag, so
        only the one under the mouse is underlined); returns the new count."""
        t, at = self.text, 0
        for m in PAGE_LINK.finditer(part):
            t.insert("end", part[at:m.start()])
            words, tid, spot = link_words(m), m.group(1), m.group(2)
            if tid in BY_ID:
                tag = f"page_link:{links}"
                links += 1
                t.insert("end", words, ("link", tag))
                self.bind_link(tag, lambda tid=tid, spot=spot: self.open_topic(tid, spot=spot))
            else:
                t.insert("end", words)
            at = m.end()
        t.insert("end", part[at:])
        return links

    def topic_links(self, top, heads):
        """One line of links at the page's top (at index top) to its headings (their line numbers), when it has
        TOPIC_LINKS or more: a click scrolls the page to that heading."""
        t = self.text
        if len(heads) < TOPIC_LINKS:
            return
        names = [t.get(f"{i}.0", f"{i}.end") for i in heads]
        for n, i in enumerate(heads):  # (marks move along with the text put in above them)
            t.mark_set(f"head:{n}", f"{i}.0")
            t.mark_gravity(f"head:{n}", "left")
            # a small "Back to top" link after each heading (user 2026-11-03), on pages with the links at the top
            t.insert(f"{i}.end", "   ")
            t.insert(f"{i}.end", tr("help.back_to_top"), ("back_top", "link", f"back_top:{n}"))
            self.bind_link(f"back_top:{n}", lambda: t.yview("1.0"))  # (own tag each: only it underlined)
        t.mark_set("links", top)
        t.mark_gravity("links", "right")  # (each piece goes after the one before)
        t.insert("links", tr("help.on_this_page"), "section")
        for n, name in enumerate(names):
            if n:
                t.insert("links", "  ·  ", "section")
            tag = f"head_link:{n}"
            t.insert("links", name, ("link", tag))
            self.bind_link(tag, lambda mark=f"head:{n}": t.yview(mark))
        t.insert("links", "\n\n")

    def see_also(self, ids):
        """The clickable "See also" line: each related topic's title opens it."""
        t = self.text
        if not ids:
            return
        t.insert("end", tr("help.see_also"), "section")
        for n, tid in enumerate(ids):
            if n:
                t.insert("end", "  ·  ", "section")
            tag = "go:" + tid
            t.insert("end", BY_ID[tid]["title"], ("link", tag))
            self.bind_link(tag, lambda tid=tid: self.open_topic(tid))

    def bind_link(self, tag, command):
        """Text under tag works as a link: a hand pointer and underline under the mouse, a click = command()."""
        t = self.text
        t.tag_bind(tag, "<Button-1>", lambda e: command())
        t.tag_bind(tag, "<Enter>",
                   lambda e: (t.config(cursor="hand2"), t.tag_add("hover", *t.tag_ranges(tag))))
        t.tag_bind(tag, "<Leave>", lambda e: (t.config(cursor="arrow"), t.tag_remove("hover", "1.0", "end")))

    def about_banner(self):
        """The About page's picture (half its file's size, growing with the screen scaling up to the whole file), if
        it's there. Never wider than the page: a narrower window shrinks it, a wider one gives it back up to that
        size (fit_banner)."""
        from notes.picture import pixels
        try:
            self._banner_pixels = pixels(BANNER, self)
        except (tk.TclError, OSError, ValueError):
            return
        w = self._banner_pixels[0].shape[1]
        self._banner_most = min(w, round(w / 2 * self.scale))
        self._banner = None  # (the picture shown, kept in self, or Tk forgets it)
        self._banner_label = tk.Label(self.text, borderwidth=0)
        self.fit_banner()
        self.embed(self._banner_label)
        self.text.insert("end", "\n\n")

    def banner_later(self, e=None):
        """The page's width changed: the About picture fitted once the window rests for a moment."""
        if self._banner_label is None:
            return
        if self._banner_job:
            self.after_cancel(self._banner_job)
        self._banner_job = self.after(80, self.banner_now)

    def banner_now(self):
        self._banner_job = None
        self.fit_banner()

    def fit_banner(self):
        """The About picture made as wide as the page has room for (at most its own size)."""
        label = getattr(self, "_banner_label", None)
        if label is None or not label.winfo_exists():
            return
        from files import speed
        from notes.picture import resized
        rgb, alpha = self._banner_pixels
        h, w = rgb.shape[:2]
        self.text.update_idletasks()
        room = self.text.winfo_width() - 2 * int(self.text.cget("padx")) - 4
        most = self._banner_most
        width = most if room < 50 else max(50, min(most, room))  # (not shown yet: its own size)
        if self._banner is not None and self._banner.size[0] == width:
            return
        height = max(1, round(h * width / w))
        under = [c // 257 for c in self.winfo_rgb(self.text.cget("background"))]
        photo = speed.Photo(self, width, height)
        photo.put(resized(rgb, alpha, width, height, under))
        label.config(image=photo.photo)
        self._banner = photo

    def about_buttons(self):
        """The About page's website link and buttons: the website, the license, Spiderweb's folder."""
        t = self.text
        at = t.search(WEBSITE, "1.0", "end")
        if at:
            t.tag_add("web", at, f"{at}+{len(WEBSITE)}c")
            t.tag_add("link", at, f"{at}+{len(WEBSITE)}c")
            t.tag_bind("web", "<Button-1>", lambda e: webbrowser.open(WEBSITE))
            t.tag_bind("web", "<Enter>", lambda e: (t.config(cursor="hand2"), t.tag_add("hover", *t.tag_ranges("web"))))
            t.tag_bind("web", "<Leave>", lambda e: (t.config(cursor="arrow"), t.tag_remove("hover", "1.0", "end")))
        row = ttk.Frame(self.text)
        ttk.Button(row, text=tr("help.website"), takefocus=False,
                   command=lambda: webbrowser.open(WEBSITE)).pack(side="left", padx=(0, 8))
        if os.path.exists(LICENSE):
            ttk.Button(row, text=tr("help.license"), takefocus=False,
                       command=lambda: open_path(LICENSE, text=True)).pack(side="left")
        ttk.Button(row, text=tr("help.open_spiderweb_s_folder"), takefocus=False,
                   command=lambda: open_path(HERE)).pack(side="left", padx=(8, 0))
        self.text.insert("end", "\n\n")
        self.embed(row)

    def add_clip(self, name):
        """Put clips/<name>.gif (playing) or clips/<name>.png (a still picture) at the end of the text. False if
        there's neither."""
        path = os.path.join(CLIPS, name + ".gif")
        picture = os.path.join(CLIPS, name + ".png")
        if not os.path.exists(path) and os.path.exists(picture):
            image = self.pictures.get(name)
            if image is None:
                try:  # (kept in self.pictures, or Tk forgets it)
                    image = self.pictures[name] = tk.PhotoImage(file=picture)
                except tk.TclError:
                    return False
            label = tk.Label(self.text, image=image, borderwidth=0)
            self.embed(label)
            return True
        try:
            clip = Clip(self.text, path) if os.path.exists(path) and gif_frames(path) else None
        except (OSError, tk.TclError):
            clip = None
        if clip:
            self.clips.append(clip)
            self.embed(clip.label)
        return bool(clip)

    def embed(self, widget):
        """Put a widget (clip, picture, buttons) at the end of the text. The wheel over it still scrolls the text."""
        self.text.window_create("end", window=widget)
        for w in [widget] + widget.winfo_children():
            w.bind("<MouseWheel>", lambda e: self.text.event_generate("<MouseWheel>", delta=e.delta) or "break")

    def stop_clip(self):
        for clip in self.clips:
            clip.stop()
        self.clips = []

    def reset_tips(self):
        self.app.tips.reset()
        self.app.tips.on.set(True)
        self.reset_note.config(text=tr("help.done_every_tip_shows_again_the"))


def open_help(app, topic_id=None):
    """The Help window (one of it), at that topic."""
    win = getattr(app, "help_window", None)
    if win is None or not win.winfo_exists():
        win = app.help_window = HelpWindow(app)
    win.deiconify()
    win.lift()
    win.focus_force()
    win.open_topic(topic_id or "welcome")
