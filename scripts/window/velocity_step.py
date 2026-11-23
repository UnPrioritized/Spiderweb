"""The velocity pane's − / + buttons (QoL stage G, user 2026-11-23): every selected shape 1 quieter / louder, its
line's shape kept (each point moved by 1, stopping at 1 / 127; Ctrl+Z is the way back). Pasted notes keeping their
own velocities: each note. Note tool pages' velocity steps move along. Held = repeats, the whole hold = ONE undo
step."""

import copy
from tkinter import ttk

from files.lang import tr
from notes.custom import pack_notes, unpack_notes
from notes.envelope import velocity_env
from window.widgets import Tooltip

REPEAT_WAIT_MS = 350  # held this long, the button repeats ...
REPEAT_MS = 70  # ... every this long


def _moved(v, d):
    return float(max(1, min(127, round(v) + d)))


def step_shape(sh, d):
    """sh's velocities d (+1 / -1) louder. True when anything changed."""
    changed = False
    if sh.get("own_vel") and "notes" in sh:
        rows = unpack_notes(sh["notes"])
        if len(rows):
            new = rows.copy()
            new[:, 3] = (new[:, 3] + d).clip(1, 127)
            if (new[:, 3] != rows[:, 3]).any():
                sh["notes"] = pack_notes(new)
                changed = True
    else:
        env = velocity_env(sh)
        new = [[u, _moved(v, d)] for u, v in env]
        if new != [[u, float(v)] for u, v in env]:
            if sh.get("vel_env"):
                sh["vel_env"] = new
            sh["vel0"], sh["vel1"] = int(_moved(sh["vel0"], d)), int(_moved(sh["vel1"], d))
            changed = True
    for step in sh.get("fx") or ():  # (velocities drawn after note tool pages)
        if step.get("tool") == "vel":
            pts = [[u, _moved(v, d)] for u, v in step["pts"]]
            if pts != step["pts"]:
                step["pts"] = pts
                changed = True
    return changed


class VelocitySteps:
    """The two buttons in the velocity pane's bar; app.velocity_step(d) does one step."""

    def __init__(self, app, bar):
        self.app = app
        self.job = None
        self.held = None  # the undo step this hold made (None = none yet)
        self.buttons = []
        for text, d, tip in (("−", -1, "velocity.quieter_tip"), ("+", 1, "velocity.louder_tip")):
            b = ttk.Button(bar, text=text, width=2, takefocus=False)
            b.pack(side="left", padx=(0 if d < 0 else 1, 0))
            b.bind("<ButtonPress-1>", lambda e, d=d: self.press(d))
            b.bind("<ButtonRelease-1>", lambda e: self.release())
            b.bind("<Destroy>", lambda e: self.release(), add="+")
            Tooltip(b, tr(tip))
            self.buttons.append(b)

    def press(self, d):
        self.held = None
        self.step(d)
        self.job = self.app.after(REPEAT_WAIT_MS, lambda: self.again(d))

    def again(self, d):
        self.step(d)
        self.job = self.app.after(REPEAT_MS, lambda: self.again(d))

    def release(self):
        if self.job is not None:
            self.app.after_cancel(self.job)
            self.job = None
        self.held = None

    def step(self, d):
        app = self.app
        targets = [app.shapes[i] for i in sorted(app.sels) if i < len(app.shapes)
                   and "picture" not in app.shapes[i]]
        if not targets:
            app.status.config(text=tr("velocity.step_none"))
            return False
        app.commit_typing()
        if not any([step_shape(copy.deepcopy(sh), d) for sh in targets]):  # (tried on copies: no empty undo step)
            app.status.config(text=tr("velocity.step_end_low" if d < 0 else "velocity.step_end_high"))
            return False
        if self.held is None:  # (the hold's first step: one undo step for all of it)
            app.push_undo(name=tr("velocity.quieter" if d < 0 else "velocity.louder"))
            self.held = True
        for sh in targets:
            step_shape(sh, d)
        app.shapes_changed()
        app.sync_panel()
        return True
