Spiderweb
=========

Spiderweb turns drawings into MIDI notes. Instead of placing notes one by
one, you draw on a piano roll (it looks and works much like Domino's) and
every key your drawing crosses gets a note: a slanted line becomes a smooth
staircase of notes, a filled circle becomes a round block of notes.

New versions, source code and problem reports:
https://github.com/UnPrioritized/Spiderweb


What it can do
--------------
- Draw: lines, polylines, freehand strokes, curves, arcs, circles, polygons,
  stars, funnels, text, and shapes you draw yourself.
- Fill: shapes with notes, one long note per key or chopped into notes of
  any length (spam). Inside fills can be smaller copies of the shape
  (Nested) or another shape repeated like wallpaper (Tiles).
- Shape the notes: add bumps along lines (tumours), shape the velocities,
  and put overlapping shapes on different channels.
- Hz bass: spam so fast it sounds like a tone, with a synth window (knobs,
  effects, layers) and a preview you can hear right away.
- Image to notes: turn an image into notes in up to 15 colours (16 with
  channel 10).
- Note tools: Claw machine, Strum and Chop pages that change the notes of a
  shape, Add between (shapes that step from one shape to another), slicing
  and merging.
- Share shapes with others as one line of text.
- Play it as you go, then write a .mid file, or copy the notes straight into
  Domino (and paste notes from Domino, or save a Domino song file).
- Settings: light or dark look, your own note colours, size, font and
  language.


Running the .exe
----------------
Just run Spiderweb.exe. Nothing to install.

"Windows protected your PC"? The .exe isn't signed (a signing certificate
costs money every year), so Windows doesn't know it yet. Click "More info",
then "Run anyway". Some virus scanners also wrongly flag programs packed with
PyInstaller. If you'd rather not run the .exe, run it from source instead
(below): the code is all there to read.

- Exported MIDI goes in the same folder as the .exe. Your autosave,
  settings, presets and saved shapes go in its "user" folder.
- To update, replace just Spiderweb.exe in that folder; everything else
  stays.
- Update check: Spiderweb can look on GitHub for a new version when it
  starts. It asks you once how often (every start, once a day, week or
  month, or never); change it later in Settings (the gear button), General
  tab. It only asks GitHub for the list of versions: nothing about you or
  your work is sent, and nothing is downloaded by itself.


Getting started
---------------
Pick a tool at the top and draw on the piano roll: every key a line crosses
gets notes. The first time you use a tool, a short tip explains it.
Help (F1) has every tip in one searchable list, including all the keyboard
and mouse shortcuts.

  Draw             drag a shape out, or click where it starts and where it
                   ends
  Select (V)       click a shape, drag it or its points; right-click = its
                   menu
  Space            play / stop
  Right-drag       listen to the notes under the mouse
  Wheel            scroll (Ctrl+wheel = zoom, middle-drag = scroll)
  Ctrl+Z / Ctrl+Y  undo / redo
  Generate MIDI    (Project, on the right) writes the .mid file


Good to know
------------
Copying to Domino
- Copy to Domino (Ctrl+Shift+C) puts the notes on the clipboard: in Domino,
  double-click a bar line in a track to paste there.
- With Multi channel, each channel goes into its own track, from that track
  down.
- Use the same PPQ in both: the ticks are copied as they are.
- Pasting works the other way too: notes copied in Domino (Ctrl+C there) and
  pasted in Spiderweb (Ctrl+V) become one shape, starting at the play line.
  Only the notes come along (no controllers).

PPQ and keys
- Domino can't open MIDI files with a PPQ of 32767 or higher. Spiderweb
  warns you (the PPQ turns red) but still writes them.
- Keys: 256 (under Project) takes the piano roll up to key 255, for players
  that read 256-key MIDI (handy for tunings like 31edo). Most MIDI programs,
  Domino included, only read keys 0-127, and Spiderweb's own playback skips
  the keys above 127.

Limits
- Shapes make their notes as a whole: you can't move a single note or change
  its gate on its own. Change the shape instead (its points, box, fill and
  note settings).
- Editing the notes in Domino and pasting them back works, but it can change
  which channels they are on. To keep the colours (channels), right-click
  the shape and pick Turn into plain notes instead: the notes stay on their
  channels, and you can cut them with the Slice tool.
- Domino can't hold notes longer than 65,535 ticks. Copy and Export to
  Domino cut them to that length and tell you.

Autosave and problems
- Everything you do is saved on its own (user/autosave.json). Each time
  Spiderweb starts, the previous session is also kept as
  autosave-backup.json: open it with Open... if something went wrong.
- If Spiderweb runs into an error, the details go to spiderweb-error.log
  next to it. If it closes by itself, spiderweb-crash.log there says where
  (it's written the next time Spiderweb starts). Please include these files
  when you report a problem (on the website's Issues page).


Running from source
-------------------
You need:
- Windows or Linux. A Linux user reports it works; copying notes to and
  from Domino works on Windows only.
- Python 3.10 or newer (python.org; tested with 3.14; Tkinter comes with
  it).
- NumPy (fast maths for millions of notes).

Then:
1. Double-click Spiderweb.bat.
2. If NumPy isn't installed yet, Spiderweb offers to install it for you. Or
   type this in a command prompt:
     pip install numpy

Two more packages are optional: Numba (much faster with lots of notes) and
Pillow (images shown faster; the Image tool opens JPG, WebP, BMP and TIFF
too, not just PNG and GIF). Spiderweb works without them, just slower. It
offers to install them once; to do it yourself later, type
  pip install numba pillow
The first start with Numba takes a second longer in the background while it
prepares its fast code (kept for the next starts).

Tested with Python 3.14.7, NumPy 2.5.3, Numba 0.68.0 and Pillow 12.3.0. If
something misbehaves with other versions, these are known to work:
  pip install numpy==2.5.3 numba==0.68.0 pillow==12.3.0

Playback goes to the MIDI out picked under Project: Windows' MIDI output,
Built-in BASSMIDI (Spiderweb's own synth, with a soundfont you pick; the
only choice on Linux), or OmniMIDI (KDMAPI) when OmniMIDI is installed. The
Hz bass window's Preview always uses the built-in synth (no MIDI device
needed).

Building the .exe yourself: install PyInstaller once (pip install
pyinstaller), then double-click build.bat. The result is in the dist folder.


About this program
------------------
All of the code was written by an AI model (Claude Opus 5.5, by Anthropic).
Kanade Tachibana directed the project: decided what it should do, tested it
and asked for changes. Released under the MIT License (see LICENSE).

Ideas borrowed from osu! mapping: arcs through three points work like osu!'s
"perfect curve" sliders, and tumours are the idea of the Tumour Generator in
Mapping Tools by OliBomby (github.com/OliBomby/Mapping_Tools), written anew
here for the piano roll.

The Hz bass preview and the Built-in BASSMIDI playback use BASS and BASSMIDI
by Un4seen Developments (un4seen.com; the DLLs and Linux .so files in
scripts/bass, the DLLs also inside the .exe). They are NOT covered by
Spiderweb's MIT License: they are free for non-commercial use only. Anyone
selling or otherwise making money from a copy of Spiderweb needs a BASS
licence from Un4seen.

The .exe also carries Python, Tcl/Tk, NumPy, Numba, llvmlite (with LLVM),
Pillow and PyInstaller's starter program. Their licences are in
THIRD-PARTY-NOTICES.txt.
