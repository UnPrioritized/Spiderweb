"""The Hz bass window's Sketch tool (S; user, 2026-10-28): a note drawn and shaped as a pitch line by hand, its
points and pieces dragged, a wobble on a piece. Everything it makes is the note's own bend line (tone["bend"], see
hz_bend.py), so the Bend tool, Copy / Paste bend and presets go on working with it. Until its own mouse is made it
works like the Bend tool (hz_bend.BEND_TOOLS)."""


class HzSketch:
    def sketching(self):
        return self.tool.get() == "sketch"
