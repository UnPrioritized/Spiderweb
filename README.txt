Spiderweb 1.0.0
===============

Draw lines, curves and shapes on a piano roll and turn them into MIDI notes
(made for black MIDI, styled after Domino).

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
- Domino can't open MIDI files with a PPQ of 32767 or higher. Spiderweb warns
  you (the PPQ turns red) but still writes them.
- Everything you do is saved on its own (autosave.json). Each time Spiderweb
  starts, the previous session is also kept as autosave-backup.json; open it
  with Open... if something went wrong.
- If Spiderweb runs into an error, the details go to errors.log in its
  folder. Please include that file when you report a problem.

Running from source
-------------------
Needs Windows, Python 3 (python.org; Tkinter comes with it) and NumPy
(fast maths for millions of notes). Double-click Spiderweb.bat. If NumPy
isn't installed yet, Spiderweb offers to install it for you; or type
  pip install numpy
in a command prompt.
Playback uses Windows' built-in MIDI output.

Building the .exe yourself
--------------------------
Install PyInstaller once (pip install pyinstaller), then double-click
build.bat. The result is in the dist folder.
