"""Every help text in one place: the tip that pops up the first time you use something, the Help window (F1, a
searchable list of all of them), the tool buttons' tooltips and the drawer's help all come from here.

A topic: id, section, title, tip (a few lines: the popup and the tool button's tooltip), text (the whole story:
the Help window and, for drawer tools, the drawer's side panel) and optional search words. The texts themselves are in the language
files (lang/en.json: help.<id>.title / tip / text / words; a text is a list of lines, an empty line = a new paragraph);
this file lists the topics and how they're linked. The About page also gets the picture and the License / folder
buttons (help.py).

Clips (short GIFs, clips/<name>.gif next to Spiderweb) show in the Help window only: clips/<topic id>.gif at the top
of its topic, and "[clip:<name>]" on a line of its own in a text puts clips/<name>.gif right there (name them
<topic id>-<what it shows>). A still picture works the same way as clips/<name>.png (the GIF wins when both are
there). A clip that isn't there is just left out."""

import re

from files.about import VERSION, WEBSITE
from files.lang import texts, tr

SECTIONS = ["Getting started", "Tools", "Shapes and settings", "Editing", "Sound and MIDI", "Custom shape drawer",
            "Reference"]  # (their shown names: help.section.* in the language files)
SECTION_NAMES = {s: tr("help.section." + "_".join(re.findall(r"[a-z0-9]+", s.lower()))) for s in SECTIONS}

# (topic id, section), in the order they're listed; their texts are help.<id>.title / tip / text / words in the language
# files (the About page's text has {VERSION} and {WEBSITE} places)
TOPIC_LIST = [
    ("about", "Getting started"),
    ("welcome", "Getting started"),
    ("whats_new", "Getting started"),
    ("select", "Tools"),
    ("line", "Tools"),
    ("poly", "Tools"),
    ("free", "Tools"),
    ("curve", "Tools"),
    ("arc", "Tools"),
    ("custom", "Tools"),
    ("box", "Tools"),
    ("funnel", "Tools"),
    ("text", "Tools"),
    ("hz_bass", "Tools"),
    ("slice", "Tools"),
    ("live", "Shapes and settings"),
    ("turn_live", "Shapes and settings"),
    ("fill", "Shapes and settings"),
    ("custom_edit", "Shapes and settings"),
    ("tumours", "Shapes and settings"),
    ("claw", "Shapes and settings"),
    ("strum", "Shapes and settings"),
    ("chop", "Shapes and settings"),
    ("glue", "Shapes and settings"),
    ("straighten", "Shapes and settings"),
    ("curves_pen", "Editing"),
    ("symmetric", "Editing"),
    ("join", "Editing"),
    ("funnel_curves", "Editing"),
    ("funnel_links", "Editing"),
    ("formulas", "Editing"),
    ("undo", "Editing"),
    ("history", "Editing"),
    ("selecting", "Editing"),
    ("sharing", "Editing"),
    ("numbers", "Editing"),
    ("view", "Editing"),
    ("velocity", "Sound and MIDI"),
    ("playback", "Sound and MIDI"),
    ("channels", "Sound and MIDI"),
    ("files", "Sound and MIDI"),
    ("domino", "Sound and MIDI"),
    ("drawer", "Custom shape drawer"),
    ("drawer_select", "Custom shape drawer"),
    ("drawer_erase", "Custom shape drawer"),
    ("drawer_line", "Custom shape drawer"),
    ("drawer_poly", "Custom shape drawer"),
    ("drawer_free", "Custom shape drawer"),
    ("drawer_curve", "Custom shape drawer"),
    ("drawer_arc", "Custom shape drawer"),
    ("drawer_square", "Custom shape drawer"),
    ("drawer_circle", "Custom shape drawer"),
    ("drawer_areas", "Custom shape drawer"),
    ("shortcuts", "Reference"),
]
TOPICS = []
for _id, _section in TOPIC_LIST:
    _t = dict(id=_id, section=_section, title=tr(f"help.{_id}.title"), tip=tr(f"help.{_id}.tip"),
              text=tr(f"help.{_id}.text", VERSION=VERSION, WEBSITE=WEBSITE))
    if f"help.{_id}.words" in texts():
        _t["words"] = tr(f"help.{_id}.words")
    TOPICS.append(_t)
for _t in TOPICS:  # "page" = the text with its clips (Help window); "text" = without (drawer's help, search)
    _t["page"] = _t["text"]
    _t["text"] = re.sub(r"\n*\[clip:[^\]]*\]\n*", "\n\n", _t["text"]).strip()
BY_ID = {t["id"]: t for t in TOPICS}
# tips that follow another one: "Got it" on the first shows the second (the first time only)
NEXT = {"welcome": "view"}
# related topics: the clickable "See also" line under a topic in the Help window
SEE = {
    "welcome": ["select", "view", "shortcuts"],
    "whats_new": ["about"],
    "select": ["selecting", "view", "shortcuts"],
    "line": ["poly", "tumours", "selecting"],
    "poly": ["line", "tumours"],
    "free": ["straighten", "tumours", "live"],
    "curve": ["curves_pen", "formulas", "symmetric", "tumours", "join"],
    "arc": ["curve", "tumours"],
    "custom": ["drawer", "fill", "custom_edit", "box"],
    "box": ["custom", "fill", "custom_edit", "formulas", "live"],
    "funnel": ["funnel_curves", "funnel_links", "formulas"],
    "text": ["fill", "custom_edit"],
    "hz_bass": ["fill", "custom_edit"],
    "slice": ["join", "selecting", "fill"],
    "live": ["turn_live", "fill", "curves_pen", "straighten", "drawer", "join"],
    "turn_live": ["live", "fill", "join", "channels"],
    "fill": ["custom", "drawer", "live", "turn_live", "hz_bass"],
    "custom_edit": ["custom", "selecting"],
    "tumours": ["line", "curve", "arc", "join", "view"],
    "claw": ["strum", "glue", "selecting", "fill", "undo", "playback"],
    "strum": ["claw", "glue", "selecting", "velocity", "undo"],
    "chop": ["glue", "claw", "strum", "selecting"],
    "glue": ["chop", "claw", "strum", "selecting", "fill"],
    "straighten": ["free", "live"],
    "curves_pen": ["curve", "symmetric", "funnel_curves"],
    "symmetric": ["curves_pen", "curve"],
    "join": ["curve", "tumours", "live", "turn_live", "curves_pen"],
    "funnel_curves": ["funnel", "curves_pen", "funnel_links", "formulas"],
    "funnel_links": ["funnel_curves", "formulas"],
    "formulas": ["curve", "curves_pen", "join", "funnel_curves", "velocity"],
    "selecting": ["select", "sharing", "shortcuts", "undo", "history"],
    "sharing": ["selecting", "drawer", "text"],
    "undo": ["history", "shortcuts"],
    "history": ["undo", "selecting", "shortcuts"],
    "numbers": ["shortcuts"],
    "view": ["playback", "shortcuts"],
    "velocity": ["playback", "selecting", "formulas"],
    "playback": ["velocity", "channels", "files"],
    "channels": ["files", "domino", "playback"],
    "files": ["channels", "domino", "sharing"],
    "domino": ["files", "channels"],
    "drawer": ["drawer_select", "drawer_areas", "custom", "fill", "sharing"],
    "drawer_select": ["drawer", "curves_pen", "symmetric"],
    "drawer_line": ["drawer", "drawer_poly"],
    "drawer_poly": ["drawer", "drawer_line"],
    "drawer_free": ["drawer", "drawer_select"],
    "drawer_curve": ["drawer", "curves_pen", "symmetric", "formulas"],
    "drawer_arc": ["drawer", "arc"],
    "drawer_square": ["drawer", "drawer_circle"],
    "drawer_circle": ["drawer", "drawer_square"],
    "drawer_erase": ["drawer", "drawer_select"],
    "drawer_areas": ["drawer", "drawer_select", "fill", "channels"],
    "shortcuts": ["selecting", "view", "numbers", "curves_pen"],
}
# the tip for each tool (Circle / Polygon share one)
TOOL_TOPICS = {"select": "select", "line": "line", "poly": "poly", "free": "free", "curve": "curve", "arc": "arc",
               "custom": "custom", "circle": "box", "polygon": "box", "funnel": "funnel",
               "text": "text", "hz": "hz_bass", "slice": "slice"}
DRAWER_TOOL_TOPICS = {"select": "drawer_select", "line": "drawer_line", "poly": "drawer_poly", "free": "drawer_free",
                      "curve": "drawer_curve", "arc": "drawer_arc", "square": "drawer_square",
                      "circle": "drawer_circle", "erase": "drawer_erase",
                      "areas": "drawer_areas"}
