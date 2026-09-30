Spiderweb
=========

Spiderweb turns drawings into MIDI notes. Instead of placing notes one by
one, you draw on a piano roll (it looks and works much like Domino's) and
every key your drawing crosses gets a note: a slanted line becomes a smooth
staircase of notes, a filled circle becomes a round block of notes. It's made
for black MIDI.

What it can do:
- Draw lines, polylines, freehand strokes, curves, arcs, circles, polygons,
  stars, funnels, text, and shapes you draw yourself.
- Fill shapes with notes: one long note per key, or chopped into notes of
  any length (spam).
- Add bumps along lines (tumours), shape the velocities, and put
  overlapping shapes on different channels.
- Play it as you go, then write a .mid file, or copy the notes straight into
  Domino (and paste notes from Domino).

New versions, source code and problem reports:
https://github.com/UnPrioritized/Spiderweb

About this program
------------------
All of the code was written by an AI model (Claude Opus 5.5, by Anthropic).
Kanade Tachibana directed the project: decided what it should do, tested it
and asked for changes. Released under the MIT License (see LICENSE).

Ideas borrowed from osu! mapping: arcs through three points work like osu!'s
"perfect curve" sliders, and tumours are the idea of the Tumour Generator in
Mapping Tools by OliBomby (github.com/OliBomby/Mapping_Tools), written anew
here for the piano roll.

Running the .exe
----------------
Just run Spiderweb.exe. Nothing to install.

Windows may show "Windows protected your PC" (SmartScreen) the first time:
the .exe isn't signed (a signing certificate costs money every year), so
Windows doesn't know it yet. Click "More info", then "Run anyway". Some virus
scanners also wrongly flag programs packed with PyInstaller. If you'd rather
not run the .exe, run it from source instead (below): the code is all there
to read.

Your autosave, saved shapes and exported MIDI go in the same folder as the
.exe. To update to a new version, replace just Spiderweb.exe in that folder;
everything else stays.

Spiderweb can look on GitHub for a new version when it starts (it asks you
once how often: every start, once a day, week or month, or never; change it
later in Help > About). It only asks GitHub for the list of versions; nothing
about you or your work is sent, and nothing is downloaded by itself.

Getting started
---------------
Pick a tool at the top and draw on the piano roll: every key a line crosses
gets notes. The first time you use a tool, a short tip explains it.
Help (F1) has every tip in one searchable list, including all the keyboard
and mouse shortcuts.

The basics:
  Draw        drag a shape out, or click where it starts and where it ends
  Select (V)  click a shape, drag it or its points; right-click = its menu
  Space       play / stop;  right-drag = listen to the notes under the mouse
  Wheel       scroll;  Ctrl+wheel = zoom;  middle-drag = scroll
  Ctrl+Z / Y  undo / redo
  Generate MIDI (Project, on the right) writes the .mid file

Good to know
------------
- Copy to Domino (Ctrl+Shift+C) puts the notes on the clipboard: in Domino,
  double-click a bar line in a track to paste there.
  With Multi channel, each channel goes into its own track, from that
  track down. Use the same PPQ in both (the ticks are copied as
  they are).
  Paste from Domino (Ctrl+Shift+V) does the opposite: notes copied in
  Domino (Ctrl+C there) become one shape, starting at the play line.
  Only the notes come along (no controllers).
- Domino can't open MIDI files with a PPQ of 32767 or higher. Spiderweb warns
  you (the PPQ turns red) but still writes them.
- Keys: 256 (under Project) takes the piano roll up to key 255, for players
  that read 256-key MIDI (handy for tunings like 31edo). Most MIDI programs,
  Domino included, only read keys 0-127, and Spiderweb's own playback
  skips the keys above 127.
- Everything you do is saved on its own (autosave.json). Each time Spiderweb
  starts, the previous session is also kept as autosave-backup.json; open it
  with Open... if something went wrong.
- If Spiderweb runs into an error, the details go to errors.log in its
  folder. Please include that file when you report a problem (on the
  website's Issues page).

Running from source
-------------------
Needs Windows, Python 3.10 or newer (python.org; tested with 3.14; Tkinter
comes with it) and NumPy (fast maths for millions of notes).
Double-click Spiderweb.bat. If NumPy
isn't installed yet, Spiderweb offers to install it for you; or type
  pip install numpy
in a command prompt.
Playback uses Windows' built-in MIDI output.

Building the .exe yourself
--------------------------
Install PyInstaller once (pip install pyinstaller), then double-click
build.bat. The result is in the dist folder.
