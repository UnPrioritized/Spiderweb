"""Every help text in one place: the tip that pops up the first time you use something, the Help window (F1, a
searchable list of all of them), the tool buttons' tooltips and the side panel's help all come from here.

A topic: id, section, title, tip (a few lines: the popup and the tool button's tooltip), text (the whole story:
the Help window and, for tools, the side panel) and optional search words.

Clips (short GIFs, clips/<name>.gif next to Spiderweb) show in the Help window only: clips/<topic id>.gif at the top
of its topic, and "[clip:<name>]" on a line of its own in a text puts clips/<name>.gif right there (name them
<topic id>-<what it shows>). A clip that isn't there is just left out."""

import re

from files.about import VERSION, WEBSITE

SECTIONS = ["Getting started", "Tools", "Shapes and settings", "Editing", "Sound and MIDI", "Custom shape drawer",
            "Reference"]

TOPICS = [
    # ------------------------------------------------------------ getting started
    # (the Help window adds the picture and the License / folder buttons to this one)
    dict(id="about", section="Getting started", title="About Spiderweb",
         tip="Version, license, and where Spiderweb keeps your files.",
         text=f"Version {VERSION}\n"
              "© 2026 Kanade Tachibana\n\n"
              "Free to use, share and change under the MIT License.\n\n"
              f"New versions, the source code and problem reports: {WEBSITE}\n\n"
              "All of the code was written by an AI model (Claude Opus 5.5, by Anthropic). Kanade Tachibana directed "
              "the project: decided what it should do, tested it and asked for changes.\n\n"
              "Spiderweb's folder holds your autosave (and autosave-backup), your shape library (shapes), the MIDI "
              "files you generate (output) and errors.log, where the details go if something goes wrong.",
         words="about version license copyright author credits folder errors log website github download update "
               "source bug report"),
    dict(id="welcome", section="Getting started", title="Welcome to Spiderweb",
         tip="Draw lines and shapes on the piano roll and they become notes.\n"
             "Pick a tool at the top; a short tip like this one explains each the first time.\n"
             "F1 or the Help button: every tip, searchable.",
         text="Draw lines, curves and shapes on the piano roll; every key they cross gets notes, like drawing notes "
              "by hand but in one stroke.\n\n"
              "1. Pick a tool at the top (Line, Polyline, Freehand, Curve, Arc, Custom shape, Funnel, Text) and draw.\n"
              "2. Select a shape to change it: drag its points, or change its settings in the panel on the right.\n"
              "3. Space plays it. Right-drag listens to the notes under the mouse.\n"
              "4. Set PPQ, BPM and the output file under Project, then Generate MIDI.\n\n"
              "Your work is saved on its own (autosave) and comes back next time. Save… keeps a copy as a project "
              "file.\n\n"
              "The first time you use a tool or feature, a small tip explains it. You can turn tips off or show "
              "them all again in this Help window (bottom).",
         words="start begin intro overview"),

    # ------------------------------------------------------------ tools
    dict(id="select", section="Tools", title="Select (V)",
         tip="Click a shape to select it, drag it to move, drag its squares to reshape.\n"
             "Drag empty space = scroll, click empty space = move the play line.\n"
             "Double right-click = to the last drawing tool and back.",
         text="Click a shape to select it; Ctrl+click selects more (Ctrl+A = all). Drag a shape to move it (all "
              "selected ones move together), drag its squares to reshape it.\n\n"
              "Drag empty space to scroll. Click empty space (no drag) to move the play line there.\n\n"
              "Right-click a shape = its menu (delete, duplicate, copy, flip, turn, add a point...). Right-click empty "
              "space = deselect. Double right-click switches between Select and the last drawing tool.\n\n"
              "Blue curve handles can be dragged with any tool.",
         words="move pick choose drag double right-click switch toggle last tool"),
    dict(id="line", section="Tools", title="Line (L)",
         tip="Drag from start to end, or click the start and then click the end.",
         text="Drag from the start to the end, or click the start and click the end (the line follows the mouse in "
              "between; right-click or Esc cancels).\n\n"
              "Each key the line crosses gets one note, starting where the line reaches that key, so a slope becomes "
              "a smooth staircase. Points snap to the grid (Snap at the top); hold Shift to place them freely.\n\n"
              "Last note (panel): the last note ends on the line's end, or starts exactly on it.",
         words="straight draw two points"),
    dict(id="poly", section="Tools", title="Polyline (P)",
         tip="Click point after point (or drag each piece). Double-click or right-click to finish.",
         text="Click point after point, or drag each piece. Double-click, right-click or Enter finishes it; Esc "
              "cancels.\n\n"
              "Selected polyline: drag its squares to move points; middle-click on it adds a point where it fits "
              "best.\n\n"
              "Last note (panel) works at every point: each piece is its own line.",
         words="lines points segments corners"),
    dict(id="free", section="Tools", title="Freehand (F)",
         tip="Drag to draw. Straighten (panel) turns it into perfect lines, curves or shapes.",
         text="Hold the mouse button and draw.\n\n"
              "Straighten (panel, 0-100) makes it perfect: 0 = as you drew it, higher = straighter lines and smoother "
              "curves. A stroke that ends where it started becomes a perfect circle, ellipse, square, rectangle or "
              "triangle. The higher, the simpler (a slightly oval loop becomes an ellipse, then a circle). The stroke as "
              "drawn is kept, so change it any time. New strokes use the last number you picked.",
         words="draw hand pencil smooth straighten shape recognition"),
    dict(id="curve", section="Tools", title="Curve (C)",
         tip="Drag from start to end (or click both), then bend it with its blue handles.\n"
             "Middle-click the curve to add an anchor.",
         text="Drag from the start to the end, or click both. Then shape it (see Curves: "
              "anchors and handles): drag the blue dots to bend it, middle-click the curve to add an anchor.\n\n"
              "Right-click it: Symmetric halves (one half follows the other), add an anchor, and the usual menu.",
         words="bezier bend pen"),
    dict(id="arc", section="Tools", title="Arc (A)",
         tip="Click the start, a point it passes through, and the end.\n"
             "Or drag from start to end, then move the mouse to bend it and click.",
         text="A piece of a perfect circle. Click its start, a point it passes through, then its end. Or drag from "
              "the start to the end, then move the mouse to bend it and click.\n\n"
              "It's round as the piano roll looks while you draw it. Put the end on the start for a whole circle.\n\n"
              "Selected: drag its round middle point (any tool) to bend it, its squares to move the ends.",
         words="circle round bend three points"),
    dict(id="custom", section="Tools", title="Custom shape (S)",
         tip="Pick a shape in the panel (or make one with Drawer…), then drag a box to place it.",
         text="Pick a shape in the panel, or draw your own with Drawer…. Then drag a box on the piano roll (or click two "
              "corners); the drawing stretches to fill it. Ctrl keeps its drawn proportions.\n\n"
              "Its inside can be filled (see Inside fill). Square, Circle and Triangle (Q / O / T) show next to "
              "Custom shape while it's picked: they make that shape straight away (Ctrl = perfect, as it looks).\n\n"
              "A placed shape keeps its own copy: changing the library shape later doesn't change it.",
         words="library drawer place box stamp"),
    dict(id="box", section="Tools", title="Square, Circle, Triangle (Q / O / T)",
         tip="Drag a box (or click two corners). Ctrl = a perfect square, circle or triangle as it looks.",
         text="Drag a box on the piano roll, or click two corners. Ctrl makes it perfect as it looks on screen (a square, "
              "a circle, an equilateral triangle).\n\n"
              "They're custom shapes, so their inside can be filled (see Inside fill) and they resize, turn and skew "
              "like any custom shape (see Custom shapes: resize, turn, skew). With Live shape on they're drawn into "
              "the live shape instead.",
         words="rectangle ellipse oval shapes"),
    dict(id="funnel", section="Tools", title="Funnel (N)",
         tip="Draw its line, then its wall (drag, or click twice each).\n"
             "Then middle-click on the line to start a curve there.",
         text="Draw the funnel's line, then its wall (drag each, or click twice). Either can come first; a wall "
              "earlier in time than the line's start makes a reverse funnel. Ctrl while drawing or dragging the wall "
              "keeps it centred on the line.\n\n"
              "Middle-click on the line (any tool) to start a curve there: curves go to both ends of the wall. "
              "Middle-click near a curve to add an anchor it goes through. The curves have anchors and handles "
              "(see Funnel curves).\n\n"
              "With a funnel selected, a line drawn to (or from) its wall is added to it (two-line funnels).\n\n"
              "Panel: the wall (notes end on it, or start on it), the inside (spam or long notes) and the gate. "
              "Tick \"Different start and wall gate\" to let the gate change from the start to the wall.",
         words="spam gate wall reverse"),
    dict(id="text", section="Tools", title="Text (X)",
         tip="Click on the piano roll and type. Esc = done. The letters become a custom shape.",
         text="Click on the piano roll and type (Enter = new line, Esc = done). The letters become a custom shape, so "
              "they can be filled, moved, resized, turned and skewed.\n\n"
              "Panel: font, size (font size, or rows = how many keys capital letters are), weight, italic, letter "
              "and line spacing, alignment, threshold (how much of a key must be inside to get a note) and grow.\n\n"
              "Click a text with the Text tool (or double-click it with Select) to retype it. Drag, Shift+arrows, "
              "double-click (a word) or Ctrl+A select letters; Ctrl+C / X / V copy, cut and paste them.",
         words="letters font type words"),

    # ------------------------------------------------------------ shapes and settings
    dict(id="live", section="Shapes and settings", title="Live shape (G)",
         tip="Lines, curves, arcs, freehand and boxes you draw now go into ONE custom shape.\n"
             "Close its outline to fill it. Right-click empty space to start the next one.",
         text="With Live shape on, Line, Polyline, Freehand, Curve, Arc, Square, Circle and Triangle draw into one "
              "custom shape (the selected one, or a new one) instead of making separate shapes. Outlines that meet "
              "end to end join up, so a closed drawing can be filled (see Inside fill). Points snap onto the "
              "drawing's ends; clicking a polyline's first point closes it; letting go of a freehand stroke near "
              "its start closes it. Right-click empty space (deselect) to start the next shape.\n\n"
              "While it's on, the selected shape's points show (purple squares): drag them with Select. Points in "
              "the same spot move together, and a point dropped on another one joins it.\n\n"
              "Select tool: click a stroke of the selected shape to pick it: Del deletes it, a curve shows its "
              "handles, a freehand stroke can be straightened. Right-click a stroke = its menu, also: save the "
              "drawing to the shape library.",
         words="combine join merge strokes outline"),
    dict(id="fill", section="Shapes and settings", title="Inside fill (custom shapes)",
         tip="Empty = just the outline. Fill = one long note per key inside. Spam = back-to-back notes of the gate.\n"
             "Outline spam = the outline chopped into notes of the gate.",
         text="How a custom shape (and text) is filled, in the panel:\n"
              "Empty: just the outline, like lines (upright parts get 1-tick notes).\n"
              "Fill: one note per stretch of each key inside.\n"
              "Spam: back-to-back notes of the gate; what doesn't fit a whole gate is dropped. Start: Auto = each "
              "key from its own left edge; Aligned = on the gate grid from the song's start (straight columns).\n"
              "Outline spam: the outline chopped into notes of the gate.\n\n"
              "Fill and Spam need a closed outline. With one gap (two red dots in the drawer), they close it with a "
              "straight line, shown dashed, and the buttons turn orange. With more gaps only Empty and Outline "
              "spam work. Holes (a shape inside a shape) stay empty.\n\n"
              "Spiderweb asks before making more than a million notes.",
         words="spam gate empty inside outline gap hole"),
    dict(id="custom_edit", section="Shapes and settings", title="Custom shapes: resize, turn, skew",
         tip="Drag a corner or side to resize, just outside a corner to turn,\n"
             "just outside a side's middle to skew. Drag inside to move it.",
         text="A selected custom shape (and text) shows a box. The mouse pointer changes to show what a drag "
              "will do.\n\n"
              "Move: drag anywhere inside it (Select tool). It moves in grid steps; Shift = free.\n"
              "[clip:custom_edit-move]\n\n"
              "Resize: drag a corner square. The opposite corner stays put; Ctrl keeps its proportions.\n"
              "[clip:custom_edit-resize]\n\n"
              "One side: drag a side (or its middle square) to move just that side.\n"
              "[clip:custom_edit-sides]\n\n"
              "Turn: drag from just outside a corner. It turns around its middle in 15° steps; Shift = any angle.\n"
              "[clip:custom_edit-turn]\n\n"
              "Skew: drag from just outside a side's middle. That side slides along itself in grid steps (Shift = "
              "free) while the opposite side stays.\n"
              "[clip:custom_edit-skew]\n\n"
              "Turning happens as the piano roll looks; zooming only one way afterwards slants it.",
         words="rotate scale stretch slant box"),
    dict(id="tumours", section="Shapes and settings", title="Tumours",
         tip="Bumps along the line: size, length, distance apart, which side.\nLength 0 = spikes (a zigzag).\n"
             "Bumps take the current zoom's shape: zoom first, then set them up.",
         text="Select a line, polyline, freehand stroke, curve or arc and tick Tumours in the panel: bumps along it. "
              "The line's points stay draggable; the line as drawn shows faint and dashed.\n\n"
              "Shape: triangle, square, circle or parabola.\n"
              "[clip:tumours-shape]\n\n"
              "Size: how far the bumps stick out, in keys.\n"
              "[clip:tumours-size]\n\n"
              "Length: how long each bump is along the line, in ticks. 0 = spikes (a zigzag).\n"
              "[clip:tumours-length]\n\n"
              "Distance: from the start of one bump to the start of the next, in ticks.\n"
              "[clip:tumours-distance]\n\n"
              "Side: alternating, left, right or random.\n"
              "[clip:tumours-side]\n\n"
              "Straight or Bent with the line: this only matters where the line curves under a bump (long bumps "
              "on a curve, arc or circle). Straight builds each bump on a straight shortcut from where it starts to "
              "where it ends, so it looks angular. Bent with the line makes it follow the curve underneath, so the "
              "whole thing stays round. On a straight line both look the same.\n"
              "[clip:tumours-bent]\n\n"
              "Lead in: the bumps grow from nothing over that many ticks from each end, so the line eases into "
              "them.\n"
              "[clip:tumours-lead-in]\n\n"
              "Range: only part of the line gets bumps. Fit: a whole number of bumps fits exactly (seamless on a "
              "full circle).\n\n"
              "The zoom matters: bumps are shaped as they look on screen when you change a tumour setting (a circle "
              "comes out round at that zoom). Zooming afterwards stretches them like the rest of the piano roll; "
              "changing any tumour setting again reshapes them for the zoom you're at now. So zoom to how you want "
              "to see them first, then set them up.",
         words="bumps spikes zigzag wave zoom round stretched"),
    dict(id="straighten", section="Shapes and settings", title="Straighten (freehand)",
         tip="0 = as drawn. Higher = straighter lines, smoother curves, perfect shapes.",
         text="Freehand strokes (and freehand strokes in a live shape: pick the stroke first) have a Straighten "
              "number in the panel, 0-100.\n\n"
              "0 = exactly as drawn. Higher = the stroke may stray further from your drawing to become simpler: "
              "wobbly parts become straight lines and smooth curves, sharp turns stay corners. A stroke that ends "
              "where it started becomes a perfect shape: circle or ellipse, square or rectangle, triangle (or another "
              "straight-sided shape). Turning it up makes it simpler (an oval → a circle, a rectangle → a square); a "
              "loop keeps its kind and its tilt.\n\n"
              "The drawing is kept underneath, so you can change the number any time. New freehand strokes use the "
              "last number you picked.",
         words="smooth perfect recognise shape freehand sensitivity"),
    dict(id="curves_pen", section="Editing", title="Curves: anchors and handles",
         tip="Drag a round anchor to move it, drag a handle dot to bend.\n"
             "Alt = sharp corner. Right-click an anchor to remove it.",
         text="Curves (the Curve tool, funnel curves, curve strokes in the drawer and in live shapes) work like "
              "a pen tool.\n\n"
              "Handles: drag the dot at the end of a blue handle line to steer the curve (its direction) and set how "
              "strongly it pulls (its length). On a smooth anchor both handles turn together. Handles can be dragged "
              "with any tool.\n"
              "[clip:curves_pen-drag-handles]\n\n"
              "Anchors: drag an anchor to move it; its handles come along. The curve's two ends are anchors too "
              "(the squares, Select tool).\n"
              "[clip:curves_pen-move-anchor]\n\n"
              "Adding and removing: middle-click the curve to add an anchor there (then drag it like any other). "
              "Right-click an anchor to remove it, or a handle dot to pull it back in (a corner).\n"
              "[clip:curves_pen-add-remove-anchor]\n\n"
              "Corners: Alt+drag a handle = move only that one (a sharp corner). Alt+drag an anchor = pull new "
              "handles out of it.",
         words="bezier pen anchor handle bend corner"),
    dict(id="symmetric", section="Editing", title="Symmetric halves",
         tip="Right-click a curve: one half follows the other, mirrored (an arch) or turned (an S).",
         text="Right-click a curve → Symmetric halves: one half follows the other. The curve gets an odd number of "
              "anchors with the middle one on the line of symmetry; editing one half rewrites the other.\n\n"
              "Mirrored (like an arch): the second half is the first one reflected.\n"
              "[clip:symmetric-arch]\n\n"
              "Turned half way round (like an S): the second half is the first one turned around the middle.\n"
              "[clip:symmetric-s]\n\n"
              "Off: the halves go their own way again.",
         words="mirror arch s-curve symmetry"),
    dict(id="funnel_curves", section="Editing", title="Funnel curves",
         tip="Middle-click the line: a new curve start. Middle-click near a curve: an anchor.\n"
             "Select tool: click a curve again to highlight it (Del, right-click = shapes, linking).",
         text="Middle-click on a funnel's line (any tool) to start curves there (a diamond); middle-click near a "
              "curve to add an anchor. The curves work like any curve: drag the anchors and handle dots (see Curves: "
              "anchors and handles). A start's two curves are linked, so they change together (see Linking funnel "
              "curves).\n"
              "[clip:funnel_curves-start-and-handles]\n\n"
              "Highlighting: with the funnel selected and the Select tool, click one of its lines or curves again to "
              "highlight it (purple). A curve comes with the curves linked to it (teal); Ctrl+click adds or removes "
              "just one.\n"
              "[clip:funnel_curves-highlight]\n\n"
              "With something highlighted:\n"
              "Del = delete it. Right-click = curve shapes, your own formulas, and linking.\n"
              "Ctrl+H / Ctrl+J = flip the curves (end to end / inside out). Ctrl+C / Ctrl+V = copy a curve's shape "
              "onto others. Esc = clear the highlight.",
         words="curve start anchor link mirror formula highlight"),
    dict(id="funnel_links", section="Editing", title="Linking funnel curves",
         tip="Linked curves change together (Ctrl while dragging = only this one).\n"
             "To link: Select tool, click a curve, Ctrl+click another, right-click → Link curves.",
         text="Linked curves change together: drag an anchor or handle on one and the others follow, and adding or "
              "removing an anchor does it on all of them. A new curve start's two curves come linked as mirror "
              "images of each other, so the funnel stays even on both sides.\n\n"
              "Changing just one: hold Ctrl while dragging (or middle-clicking to add an anchor). If that gives it a "
              "different number of anchors than the others, it stops following them until they match again or you "
              "link them anew.\n\n"
              "Linking curves yourself (any curves of one funnel, also on different lines):\n"
              "1. Select the funnel, then with the Select tool click one of its curves: it's highlighted (purple), "
              "with the curves already linked to it (teal).\n"
              "2. Ctrl+click more curves to add them (Ctrl+click a highlighted one to leave it out).\n"
              "3. Right-click the curve whose shape you want to keep → Link curves:\n"
              "   Auto: each curve takes whichever way is closer to how it looks now.\n"
              "   Mirrored / Same shape (the name depends on which way the curves open): the same curve, reflected "
              "across the line or the same way round.\n"
              "   Flipped: turned end to end, so what happens near the start on one happens near the wall on the other.\n"
              "   Unlink: the highlighted curves go their own way.\n"
              "The right-clicked curve keeps its shape, the others are reshaped to follow it. A curve can only be in "
              "one link group: linking it again takes it out of its old group.\n\n"
              "Ctrl while drawing or dragging the wall keeps it centred on the line, so mirrored curves stay exact "
              "mirrors on the piano roll.",
         words="link linked unlink mirror mirrored flipped same shape together follow group ctrl"),
    dict(id="formulas", section="Editing", title="Curve shapes and formulas",
         tip="Right-click highlighted funnel curves: preset shapes or your own formula (y of x).",
         text="Right-click highlighted funnel curves → Curve shape (presets) or Custom formula. A formula is y of x: "
              "x goes from 0 at the curve's start to 1 at the wall end, y is how far open the funnel is, stretched to "
              "fit (so x^2 and 5*x^2 give the same curve). Numbers, x, + - * / ^ ( ), pi, e, sin cos tan asin acos "
              "atan sqrt exp ln log abs min max. The result becomes anchors and handles, so it can still be dragged.",
         words="formula math preset expression"),
    dict(id="selecting", section="Editing", title="Selecting, copying, flipping, turning",
         tip="Ctrl+click = more shapes, Ctrl+A = all. Ctrl+C / V = copy / paste at the play line.\n"
             "Ctrl+H / J = flip, Ctrl+Left / Right = turn 90°.",
         text="Ctrl+click selects more shapes (also in the Shapes list), Ctrl+A selects all.\n"
              "Ctrl+C / Ctrl+V: copy / paste at the play line (snapped to the grid). Ctrl+D: duplicate. Del: delete.\n"
              "Ctrl+H / Ctrl+J: flip sideways / upside down (sideways also mirrors velocities).\n"
              "Ctrl+Left / Ctrl+Right: turn 90° as it looks on screen (the zoom decides how many beats a key "
              "becomes).\n"
              "Ctrl+Z / Ctrl+Y: undo / redo. While something is half drawn, Ctrl+Z just drops that.\n"
              "Settings in the panel (velocity, last note...) change every selected shape.",
         words="copy paste duplicate flip mirror rotate turn undo redo delete"),
    dict(id="numbers", section="Editing", title="Number boxes",
         tip="Drag a box's name sideways to change it, or press Up / Down in the box.\n"
             "Shift = big steps, Ctrl = small steps.",
         text="Every number box can be changed without typing: drag the name next to it sideways, or press Up / Down "
              "in it (the mouse wheel too, while you're typing in it). Shift = big steps, Ctrl = small steps. A name "
              "over two boxes (Velocity, Gate) changes both. Boxes also take math: 960*4, 1920/3...",
         words="scrub drag value arrow wheel math"),
    dict(id="view", section="Editing", title="Scrolling and zooming",
         tip="Wheel = scroll, Shift+wheel = sideways, middle-drag = scroll.\n"
             "Ctrl+wheel = zoom (Ctrl+Shift = time only, Alt = keys only).\n"
             "Fit view (top) = see everything.",
         text="Wheel = scroll up / down, Shift+wheel = sideways. Ctrl+wheel = zoom both ways, Ctrl+Shift+wheel = "
              "zoom time, Alt+wheel = zoom pitch. Middle-drag, or dragging empty space with Select, scrolls. Fit view "
              "shows everything. The zoom and scroll are remembered.\n\n"
              "Show lines / Show notes (top) hide the drawn lines or the notes. Keys outside a real 88-key piano are "
              "greyed on the keyboard.",
         words="zoom scroll pan fit view"),

    # ------------------------------------------------------------ sound and MIDI
    dict(id="velocity", section="Sound and MIDI", title="Velocity pane",
         tip="Draw velocities over the bars: Linear, Curve or Pencil.\n"
             "With a shape selected only its notes change.",
         text="The pane under the piano roll shows every note's velocity. Draw over the bars with Linear, Curve or "
              "Pencil. With a shape selected, only its notes change (the others fade); with nothing selected, every "
              "note under the drag.\n\n"
              "Linear / Curve: drag it, then drag its end squares to move the ends (Ctrl = level with the other end) "
              "or a curve's round handle to bend it. Ctrl while drawing = flat. Hold Shift to snap the ends to the "
              "grid and, with a shape selected, onto its first / last note. Enter or a click outside the pane = "
              "done.\n\n"
              "The velocities move and stretch with the shape. Typing a start / end velocity in the panel goes back "
              "to a straight ramp. Drag the bar above the pane to resize it; Velocity pane (top) hides it.",
         words="velocity loudness volume dynamics ramp"),
    dict(id="playback", section="Sound and MIDI", title="Playing and listening",
         tip="Space = play / stop. Right-drag = listen to the notes under the mouse.",
         text="Space (or Play) plays from the play line through the MIDI out picked under Project; the blue line "
              "follows and the view turns the page. Click or drag the bar numbers (or click empty space with Select) "
              "to move the play line. Playing stops a moment after the last note.\n\n"
              "Right-drag (any tool) = listen to the notes under the mouse, like scrubbing.",
         words="play sound listen midi out scrub audio"),
    dict(id="channels", section="Sound and MIDI", title="Channels",
         tip="As drawn = everything kept. Single = one channel, overlaps fixed.\n"
             "Multi = overlapping shapes on different channels (each its own track).",
         text="Under Project → Channels:\n"
              "As drawn: every note exactly as the shapes make it, one channel; notes on the same key may overlap.\n"
              "Single channel: where two notes on the same key overlap, the earlier one is cut where the later "
              "starts and the later one is stretched to where the earlier would have ended; notes starting on the "
              "same tick become one (the loudest, as long as the longest).\n"
              "Multi channel: shapes whose notes clash go on different channels, each on its own track (channel 10, "
              "drums, is skipped). Split: Same key = only notes on the same key at the same time clash; Any notes = "
              "any notes at the same time. Shapes that follow one after another share a channel.\n\n"
              "Every channel has its own note colour.",
         words="channel track overlap colour"),
    dict(id="files", section="Sound and MIDI", title="Saving and MIDI export",
         tip="Your work saves itself. Generate MIDI writes the .mid file set under Project.",
         text="Everything is saved on its own (autosave.json next to Spiderweb) and comes back next time; each time "
              "Spiderweb starts, the previous session is kept as autosave-backup.json. Save… / Open… keep and open "
              "project files (.json). Shapes are stored in beats, so changing PPQ doesn't move them.\n\n"
              "Generate MIDI writes the file set under Output file, with the PPQ, BPM and beats per bar from Project. "
              "PPQ 32767 or higher: many MIDI programs can't open the file (the box turns red).\n\n"
              "If something goes wrong, the details go to errors.log next to Spiderweb.",
         words="save open export generate midi file ppq bpm autosave backup"),

    # ------------------------------------------------------------ drawer
    dict(id="drawer", section="Custom shape drawer", title="The drawer",
         tip="Draw a shape for your library, Save it, then Use on the piano roll.\n"
             "Closed outlines can be filled.",
         text="The drawer (Custom shape → Drawer…) is where you draw shapes for your library. Its tools work like the "
              "piano roll's: Line, Polyline, Freehand, Curve (C), Arc, Square, Circle (O), Eraser, and Select. Points snap to "
              "the grid; Shift draws freely. Drag a stroke out, or click where it starts and click where it ends "
              "(right-click or Esc cancels).\n\n"
              "Save keeps it in the library (the spiderweb/shapes folder); Use on the piano roll picks it for the "
              "Custom shape tool. On the piano roll it stretches to the box you drag.\n\n"
              "Lines and curves that meet end to end count as one outline. Closed: Empty, Fill and Spam all work. One "
              "gap (two red dots): Fill and Spam close it with a straight line. More gaps: only Empty and Outline "
              "spam.\n\n"
              "Wheel = zoom, middle-drag = scroll, Reset view = back to the middle of the board.",
         words="library draw custom shape board"),
    dict(id="drawer_select", section="Custom shape drawer", title="Drawer: Select and editing",
         tip="Drag a point (small squares), a stroke, or empty space to scroll. Del = delete the stroke.",
         text="Select: drag a point (small squares; points in the same spot move together, so outlines stay closed), "
              "an ellipse's corners, or a whole stroke (moves in grid squares, Shift = free). Drag empty space to "
              "scroll. Del deletes the selected stroke.\n\n"
              "Right-click a stroke = its menu (add point / anchor, symmetric curve halves, delete, copy, paste, "
              "flip, turn); empty space = deselect; while drawing a polyline = finish it.\n\n"
              "Ctrl+C / Ctrl+V = copy / paste (each paste lands one more grid square down and right), Ctrl+H / "
              "Ctrl+J = flip sideways / upside down, Ctrl+Left / Ctrl+Right = turn 90°. These work on the selected "
              "stroke, or the whole drawing when nothing is selected.",
         words="drawer edit move copy flip turn"),
    dict(id="drawer_line", section="Custom shape drawer", title="Drawer: Line",
         tip="Drag from start to end, or click both.",
         text="Drag from start to end, or click the start and click the end."),
    dict(id="drawer_poly", section="Custom shape drawer", title="Drawer: Polyline",
         tip="Click points (or drag each piece). Click the first point again to close it.",
         text="Click points, or drag each piece. Click the first point again to close it; double-click or "
              "right-click stops."),
    dict(id="drawer_free", section="Custom shape drawer", title="Drawer: Freehand",
         tip="Drag to draw. Let go near the start to close it.",
         text="Hold the mouse button and draw. Letting go near the start closes it."),
    dict(id="drawer_curve", section="Custom shape drawer", title="Drawer: Curve",
         tip="From start to end, then bend it with its blue handles. Middle-click it = new anchor.",
         text="Drag (or click) from start to end. The new curve is selected: bend it with its handles like any curve "
              "(see Curves: anchors and handles); middle-click near it adds an anchor."),
    dict(id="drawer_arc", section="Custom shape drawer", title="Drawer: Arc",
         tip="Click start, a point it passes through, end. Or drag start to end, then click to bend.",
         text="Click its start, a point it passes through, then its end (or drag from start to end, then click to "
              "set the bend): a piece of a perfect circle. End on the start = a whole circle. Select: drag its three "
              "points."),
    dict(id="drawer_square", section="Custom shape drawer", title="Drawer: Square",
         tip="Corner to corner. Ctrl = a perfect square.",
         text="Drag (or click) corner to corner; Ctrl makes it a perfect square."),
    dict(id="drawer_circle", section="Custom shape drawer", title="Drawer: Circle",
         tip="Corner to corner. Ctrl = a perfect circle.",
         text="Drag (or click) corner to corner of its box; Ctrl makes it a perfect circle."),
    dict(id="drawer_erase", section="Custom shape drawer", title="Drawer: Eraser",
         tip="Click a stroke to remove it. Ctrl+Z = undo.",
         text="Click a stroke to remove it. Ctrl+Z = undo."),

    # ------------------------------------------------------------ reference
    dict(id="shortcuts", section="Reference", title="Keyboard and mouse shortcuts",
         tip="Every key and mouse shortcut in one list.",
         text="Tools: V Select, L Line, P Polyline, F Freehand, C Curve, A Arc, S Custom shape, Q Square, O Circle, "
              "T Triangle, N Funnel, X Text. G = Live shape on / off.\n\n"
              "Drawing: Shift = don't snap. Ctrl = perfect (square, circle, triangle, proportions). Right-click or Esc "
              "= cancel what's half drawn. Enter / double-click = finish a polyline.\n\n"
              "Mouse: right-click a shape = its menu, empty space = deselect. Double right-click = Select ↔ last tool. "
              "Right-drag = listen. Middle-click = add a point / anchor / funnel curve start. Middle-drag = scroll.\n\n"
              "Wheel = scroll, Shift+wheel = sideways, Ctrl+wheel = zoom, Ctrl+Shift+wheel = zoom time, "
              "Alt+wheel = zoom pitch.\n\n"
              "Editing: Del delete, Ctrl+D duplicate, Ctrl+Z / Ctrl+Y undo / redo, Ctrl+A select all, Ctrl+C / "
              "Ctrl+V copy / paste, Ctrl+H / Ctrl+J flip, Ctrl+Left / Ctrl+Right turn 90°, Esc clear highlight / "
              "unpick a stroke.\n\n"
              "Playing: Space play / stop. Click or drag the bar numbers = move the play line.\n\n"
              "Curves: Alt+drag a handle = sharp corner, Alt+drag an anchor = new handles, Ctrl = change only this "
              "curve (linked funnel curves).\n\n"
              "Number boxes: drag the name, Up / Down, wheel; Shift = big steps, Ctrl = small.\n\n"
              "F1 = Help. Ctrl+S = save.",
         words="keys hotkeys keyboard mouse shortcuts"),
]

for _t in TOPICS:  # "page" = the text with its clips (Help window); "text" = without (side panel, search)
    _t["page"] = _t["text"]
    _t["text"] = re.sub(r"\n*\[clip:[^\]]*\]\n*", "\n\n", _t["text"]).strip()
BY_ID = {t["id"]: t for t in TOPICS}
# tips that follow another one: "Got it" on the first shows the second (the first time only)
NEXT = {"welcome": "view"}
# related topics: the clickable "See also" line under a topic in the Help window
SEE = {
    "welcome": ["select", "view", "shortcuts"],
    "select": ["selecting", "view", "shortcuts"],
    "line": ["poly", "tumours", "selecting"],
    "poly": ["line", "tumours"],
    "free": ["straighten", "tumours", "live"],
    "curve": ["curves_pen", "symmetric", "tumours"],
    "arc": ["curve", "tumours"],
    "custom": ["drawer", "fill", "custom_edit", "box"],
    "box": ["custom", "fill", "custom_edit", "live"],
    "funnel": ["funnel_curves", "funnel_links", "formulas"],
    "text": ["fill", "custom_edit"],
    "live": ["fill", "curves_pen", "straighten", "drawer"],
    "fill": ["custom", "drawer", "live"],
    "custom_edit": ["custom", "selecting"],
    "tumours": ["line", "curve", "arc", "view"],
    "straighten": ["free", "live"],
    "curves_pen": ["curve", "symmetric", "funnel_curves"],
    "symmetric": ["curves_pen", "curve"],
    "funnel_curves": ["funnel", "curves_pen", "funnel_links", "formulas"],
    "funnel_links": ["funnel_curves", "formulas"],
    "formulas": ["funnel_curves", "funnel_links"],
    "selecting": ["select", "shortcuts"],
    "numbers": ["shortcuts"],
    "view": ["playback", "shortcuts"],
    "velocity": ["playback", "selecting"],
    "playback": ["velocity", "channels", "files"],
    "channels": ["files", "playback"],
    "files": ["channels"],
    "drawer": ["drawer_select", "custom", "fill"],
    "drawer_select": ["drawer", "curves_pen", "symmetric"],
    "drawer_line": ["drawer", "drawer_poly"],
    "drawer_poly": ["drawer", "drawer_line"],
    "drawer_free": ["drawer", "drawer_select"],
    "drawer_curve": ["drawer", "curves_pen", "symmetric"],
    "drawer_arc": ["drawer", "arc"],
    "drawer_square": ["drawer", "drawer_circle"],
    "drawer_circle": ["drawer", "drawer_square"],
    "drawer_erase": ["drawer", "drawer_select"],
    "shortcuts": ["selecting", "view", "numbers", "curves_pen"],
}
# the tip for each tool (Square / Circle / Triangle share one)
TOOL_TOPICS = {"select": "select", "line": "line", "poly": "poly", "free": "free", "curve": "curve", "arc": "arc",
               "custom": "custom", "square": "box", "circle": "box", "triangle": "box", "funnel": "funnel",
               "text": "text"}
DRAWER_TOOL_TOPICS = {"select": "drawer_select", "line": "drawer_line", "poly": "drawer_poly", "free": "drawer_free",
                      "curve": "drawer_curve", "arc": "drawer_arc", "square": "drawer_square",
                      "circle": "drawer_circle", "erase": "drawer_erase"}
