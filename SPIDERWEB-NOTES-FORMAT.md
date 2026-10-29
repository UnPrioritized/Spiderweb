# Spiderweb Notes format

**Status: version 1** (since Spiderweb 1.7.0). It only grows from here (new block types, new fields at the end of
a block); it never changes in a way that breaks a reader.

Free to use: any program, free or paid, may read or write this format. No need to ask.

## What it is

When you copy in Spiderweb (Ctrl+C, in the piano roll or the track list) with Settings → General → "Ctrl+C also
copies as Spiderweb Notes" ticked, it puts two things on the clipboard:

- **Text** (starts with `SPIDERWEB1`): Spiderweb's own shape recipes, for pasting back into Spiderweb or sharing in
  chat. That text changes with every Spiderweb update, so other programs should not read it.
- **Spiderweb Notes** (this format): the finished music, in a small binary layout that stays the same. It holds
  the notes, and with them everything else Spiderweb's MIDI export writes for that stretch: tempo, time and key
  signatures, controllers, pitch bends, program changes, track names and colours.

Spiderweb only writes this format, it never reads it. Other programs may write it too. The same bytes work as a
file with no change (a file version may come later); the `SPWNOTES` tag at the start is what tells it apart.

## On the Windows clipboard

- The format is registered with `RegisterClipboardFormatW(L"Spiderweb Notes")` (Windows matches the name without
  caring about capitals). Read it with `OpenClipboard`, `GetClipboardData`, `GlobalLock`; copy the bytes out and
  `CloseClipboard` quickly.
- The memory block's size (`GlobalSize`) can be bigger than the data: stop at the End block, and never read past
  `GlobalSize` either (no End block before it = damaged).
- Copies can be big (about 1-2 bytes per note, and millions of notes are normal). Check `GlobalSize` against your
  own limit before reading; 32-bit programs may not manage very big ones.
- Spiderweb puts the data on the clipboard when you copy, so it's still there after Spiderweb closes.
- Not on Linux yet.

## Basics

- **Byte order:** there are no multi-byte fixed numbers; everything is a single byte or a varint.
- **Varint:** a whole number, 7 bits per byte, lowest bits first; a byte's top bit (0x80) set means another byte
  follows. 0..127 = 1 byte, 128..16383 = 2 bytes, and so on (same as LEB128 / Protocol Buffers). Values fit in 64
  bits unsigned; a varint longer than 10 bytes is damaged data. Spiderweb never writes any varint above 2^53 - 1
  (record heads included: a copy spanning 2^47 ticks or more gets no Spiderweb Notes), so JavaScript can use plain
  Numbers. **JavaScript:** build a varint with `value += (byte & 0x7F) * 2 ** shift` and split a record head with
  `Math.floor(head / 64)` and `head % 64`, never `<<`, `|` or `>>`: those work on 32 bits, and a gap of 2^25 ticks
  (under 5 hours at 960 PPQ) would come out negative.
- **Text:** UTF-8, given as a varint byte count, then the bytes (no zero at the end).
- **Ticks:** time and length, at the PPQ given in the Info block. Tick 0 is the start of the copy (see Info).
  Empty space is kept as real ticks, so the layout pastes exactly as made. Where tick 0 lands (cursor, bar line) is
  up to the program pasting it. **Nothing comes before tick 0:** a writer with something earlier (a controller,
  program change or bend set up before the first note) puts it on tick 0, keeping its order, or leaves it out.
- **Keys:** 0..255 (60 = middle C). Keys above 127 are past normal MIDI: warn, refuse or drop them as you like.
  In a 31-EDO project the keys are the piano roll's rows as they are (row 0 = C0, 31 rows an octave); nothing in
  the data says so, as with Spiderweb's MIDI export.
- **Velocity:** 1..127. **Length:** 1 tick or more.
- **Channel:** 0..15 (0 = channel 1, 9 = channel 10).
- **Same as the MIDI export:** every tick here is the tick Spiderweb's MIDI export would write (Keep timing and
  catch-ups already worked in), counted from the copy's start.

## Layout

```
"SPWNOTES"      8 bytes, ASCII (the name tag)
version         1 byte, = 1
blocks...       one after another, until an End block
```

**Version** stays 1 for as long as a version 1 reader can read everything that matters. Any other value is a layout
you can't read: refuse it.

Each block:

```
type            varint
size            varint, bytes in the body (not in the End block)
body            size bytes
```

Block rules:

- **A block type you don't know: skip it by its size.** That's how the format grows without old readers breaking.
- **A block you know may be longer than described here:** read the fields you know, then skip to the end of the
  block by its size. New fields are only ever added at the end. (Not the blocks made of records: their body is
  records to the end, so they never get new fields.)
- **Order:** Info comes first and must be there (once; never inside a Zipped block). Tracks, Track names and Track
  colours come before any block that names a track (Notes, Events). Blocks of the same type may come more than once; the timed ones (Notes, Tempo, Events,
  Time signatures, Key signatures) each start fresh (below).

| Type | Name | Body |
|---|---|---|
| 0 | End | none, and no size. Stop reading here. |
| 1 | Info | PPQ (varint, 1 or more), number of notes (varint), ticks from the bar line to tick 0 (varint), length of the copy in ticks (varint) |
| 2 | Tracks | track count (varint), then one channel byte (0..15) per track, track 0 first |
| 3 | Notes | note records (below), until the body ends |
| 4 | Track colours | entry count (varint), then per entry: track (varint), red, green, blue (1 byte each) |
| 5 | Zipped | unzipped size (varint), then zlib data (RFC 1950, not raw deflate or gzip) |
| 6 | Track names | name count (varint), then per name: track (varint), text |
| 7 | Channel colours | 16 entries, channel 0 first: red, green, blue (1 byte each) |
| 16 | Tempo | records: gap (varint), microseconds per quarter note (varint, 1 or more) |
| 17 | Events | records: gap (varint), track (varint), a MIDI channel message (below) |
| 19 | Time signatures | records: gap (varint), top (1 byte, 1..255), bottom (1 byte: 1, 2, 4 ... 128) |
| 20 | Key signatures | records: gap (varint), sharps (1 byte, -7..7 as a signed byte: 0xF9 = 7 flats), minor (1 byte, 0 / 1) |

Type 18 is kept for other events (system exclusive, text and marker events), not defined yet. Types 8..15 and
21..999 are kept for this document (ask in the Spiderweb repo). Types 1000 and up are free for any program's own use
and will never be given a meaning here.

Spiderweb writes: Info, Tracks, Track names (only when a track has a name), Track colours (only when there are some),
Channel colours, Tempo, Time signatures, Key signatures (only when there are some), Notes, Events (only when there
are some), End. Big copies come as several Notes / Events blocks of up to a million records each, and each of those
may be inside a Zipped block.

### Timed records

Notes, Tempo, Events, Time signatures and Key signatures are lists of records in time order: a record's start is
never before the previous record's in the same block. **gap** = ticks since the previous record (a block's first
record: since tick 0). Records on the same tick come in no fixed order, except inside an Events block (there, keep
their order: a bank select comes before its program change). Each block starts fresh: time 0, and every "same as
before" value back to its start. Records from different blocks are not in order with each other: sort them if you
need to. **On the same tick, put Tempo, Time signature, Key signature and Events records before the notes** (as a
MIDI file does), so a note hears the program change, controller or bend set on its own tick.

### Info

- **PPQ:** ticks per quarter note.
- **Number of notes:** the total over all Notes blocks, every note counted one by one. It's there so you can warn
  or get memory ready before reading. Never trust it on its own: stop where the notes really end, and refuse a
  count above your own limit.
- **Ticks from the bar line:** how far tick 0 is past the bar line before it (0 = tick 0 is on a bar line). Paste
  at a bar line plus this much and the notes keep their place in the bar; or ignore it.
- **Length of the copy:** how long the copy is, from tick 0 (a time selection's width, so empty bars at its end
  count; else up to the last note's end). For a paste that replaces that much. 0 = not known: use the last note's
  end.

**Tick 0** is the start of what was copied: the left edge of a time selection (Spiderweb's ruler band, or a box in
its track list), otherwise the first note.

### Tracks

Each note says which track it's on; the Tracks block gives each track its usual channel (Spiderweb: the one its
track list shows, for a track on several channels the first). Notes play on their
track's channel unless the note says another one (channel flag, below): one track may hold notes on several
channels, like a MIDI file's track can. Several tracks may share a channel. How you use this is up to you: one of
your tracks per track, everything by channel, or your own way.

Spiderweb numbers the tracks from the copy's top row down, track 0 first, and lists every one of them, also empty
ones between: a copy of Spiderweb's tracks 3, 4 and 6 comes as tracks 0, 1 and 3, with an empty track 2, so the
gaps stay as they were. Keep or drop the empty ones as you like.

### Track names

Names the tracks were given in Spiderweb. A track without an entry has no name (use your own). An entry for a track
that isn't in the Tracks block: ignore it. A track named twice: the last one counts.

### Track colours and Channel colours

Suggested colours, free to ignore.

- **Channel colours:** the colours Spiderweb shows each channel's notes in (the user's palette), so a program can
  show the notes as they looked.
- **Track colours:** only for tracks made by Image to notes (a picture's colour k = its own track), so the
  picture keeps its exact colours. A track with an entry here uses it instead of its channel's colour. An entry for a
  track that isn't in the Tracks block: ignore it. A track listed twice: the last one counts.

### Zipped

The unzipped data holds more blocks in this same layout, until the data ends (an End block inside it ends only the
zipped part). A Zipped block never holds another Zipped block (readers may refuse it). Spiderweb zips a block only
when that makes it smaller. If the unzipped data isn't exactly the unzipped size, it's damaged.

### Notes

Notes may overlap, also on the same key and the same track, and two notes may be exactly the same. What you do with
those (keep, cut the earlier one, merge) is up to you: MIDI itself can't hold them cleanly (note on, note on, note
off ends both).

Each record:

```
head            varint: (gap << 6) | flags
key             1 byte
[count - 1]     varint, if flag 16
[length]        varint, if flag 1
[velocity]      1 byte, if flag 2
[track]         varint, if flag 4
[group]         varint, if flag 8
[channel]       1 byte, if flag 32
```

- **flags:** 1 = new length, 2 = new velocity, 4 = new track, 8 = new group, 16 = a column, 32 = new channel. A value
  that isn't given is the same as the previous record's.
- **Each Notes block starts fresh:** length, velocity, track and group start at 0 and the channel at 255, so a
  block's first record gives what it needs.
- **channel:** 0..15 = the note plays on that channel; 255 = on its track's channel (Tracks block). Like the other
  values it carries on to the next records, also across a change of track. Writers: when you change track with a
  channel still given, write 255 again if the next notes should be on their track's own channel.
- **Column (flag 16):** `count` notes in one record (at least 1, at most 256 - key), keys `key`, `key + 1`, ...
  `key + count - 1`, all with the same start, length, velocity, track, group and channel. Spiderweb's fills are
  mostly columns like this, which keeps the data small (about 1-2 bytes per note on real projects).
- **group** = which shape the note came from (0, 1, 2...). Use it to keep each shape's notes together, or ignore it.
- **Note numbers:** every note has a number, its place in reading order: 0, 1, 2... A column counts as `count`
  notes, and the numbers go on across Notes blocks. A note you drop (length 0, a key you can't use...) still takes
  its number. Nothing uses them yet. Later, extra data per note may come in its
  own block, pointing at notes by number, so old readers still get every note and simply skip the extras.

### Tempo

The tempo from each record's tick on, as in a MIDI file's tempo event (500000 = 120 BPM). Spiderweb always writes a
record at tick 0 with the tempo in force there, then each change inside the copy; gliding tempos come as the many
small steps its MIDI export writes. No Tempo block: the program's own tempo (or 120 BPM). The tick 0 record says
what's in force, not that the tempo changes there: skip it when it matches your own.

### Time signatures and Key signatures

Like a MIDI file's: 6/8 = top 6, bottom 8. Spiderweb writes the time signature in force at tick 0 (on tick 0),
then each change inside the copy, each on a bar line (key signatures: the same, only when the song has any). The
tick 0 record is the signature in force, NOT a bar starting there (tick 0 may be mid-bar, see Info): pasting into a
song, put it at tick 0 minus Info's ticks from the bar line, or skip it when it matches your own.

### Events

Controllers (CC), pitch bends, program changes and pressure, each on a track:

```
gap             varint
track           varint
status          1 byte: 0xA0..0xEF (the low 4 bits = the channel)
data            2 bytes, or 1 for 0xC0..0xDF (program change, channel pressure)
```

Exactly the bytes a MIDI file holds (no running status). Note on / note off (0x80..0x9F) never come here: notes are
in the Notes blocks. Spiderweb writes the events its MIDI export would write for the copied notes:

- Spiderweb's own sound events (Hz bass pitch bends and the controllers that set them up): always.
- Events that came with pasted music (another song's controllers, program changes): only when the copy is a time
  selection. Shapes picked one by one bring their notes alone (like Spiderweb's Copy to Domino).

## Damaged data

A writer must keep every rule above. A reader meeting data that breaks one may do this:

| What's wrong | What to do |
|---|---|
| Velocity 0, or above 127 | use 1 / 127 |
| Length 0 | drop the note |
| A column past key 255 (key + count above 256) | damaged: stop, keep the notes read so far |
| A track number not in the Tracks block | use channel 1 (0) and warn |
| A channel byte above 15 (not 255 in a note), or PPQ 0 | damaged |
| Tempo 0; a time signature top 0 or bottom that isn't a power of 2; key signature sharps outside -7..7 or minor not 0 / 1 | drop that record |
| An Events status below 0xA0 or above 0xEF, or a data byte above 127 | damaged: stop that block |
| A record cut off by the end of its block (any record block) | damaged: stop that block, keep the records before it |
| Tracks / Track names / Track colours ending before its count | keep the entries read, warn |
| A name that isn't UTF-8 | drop that name |
| Channel colours shorter than 48 bytes | ignore the block |
| Info with fewer than 4 fields | damaged (a 3-field Info from an older writer: length 0) |
| A second Info block | ignore it |
| Zipped data that doesn't unzip, or not to the unzipped size | damaged |
| A varint longer than 10 bytes | damaged |
| A block running past the end of the data, or no End block | damaged: keep the whole blocks before it |
| Info's number of notes above your limit | refuse, or read up to your limit and warn |

"Damaged: stop" means the data can't be trusted past that point; it's up to you whether to paste what you have or
nothing.

## Example

A track named "Lead" on channel 1: three notes in a column (keys 60, 61, 62) at tick 0, length 240, velocity 100,
then key 64 at tick 480, same everything. PPQ 960, 120 BPM, 4/4, tick 0 on a bar line, and a volume of 100 at tick
0. The notes are numbered 0, 1, 2 (the column) and 3.

```
53 50 57 4E 4F 54 45 53     "SPWNOTES"
01                          version 1
01 06  C0 07  04  00  D0 05 Info: PPQ 960, 4 notes, on the bar line, 720 ticks long
02 02  01  00               Tracks: 1 track, channel 1
06 07  01  00 04 4C 65 61 64
                            Track names: 1 name, track 0 = "Lead"
10 04  00 A0 C2 1E          Tempo: tick 0, 500000 (120 BPM)
13 03  00 04 04             Time signatures: tick 0, 4/4
03 0C                       Notes, 12 bytes:
   1F 3C 02 F0 01 64 00 00     gap 0, flags 1+2+4+8+16; key 60, column of 3, length 240, velocity 100, track 0,
                               group 0
   80 F0 01 40                 gap 480, no flags; key 64
11 05  00 00 B0 07 64       Events: tick 0, track 0, volume (CC 7) 100 on channel 1
00                          End
```

(Channel colours left out to keep it short; Spiderweb always writes them.)

## Reading it (Python)

A short reader for data that keeps the rules: it raises an error on cut-off data and doesn't check the "Damaged
data" cases (a real reader should).

```python
import zlib


def varint(data, pos):
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, pos


def text(data, pos):
    size, pos = varint(data, pos)
    return data[pos:pos + size].decode("utf-8"), pos + size


def read_notes(body, out):
    pos = time = length = velocity = track = group = 0  # (each Notes block starts fresh)
    channel = 255
    while pos < len(body):
        head, pos = varint(body, pos)
        time += head >> 6
        key = body[pos]
        pos += 1
        count = 1
        if head & 16:
            count, pos = varint(body, pos)
            count += 1
        if head & 1:
            length, pos = varint(body, pos)
        if head & 2:
            velocity = body[pos]
            pos += 1
        if head & 4:
            track, pos = varint(body, pos)
        if head & 8:
            group, pos = varint(body, pos)
        if head & 32:
            channel = body[pos]
            pos += 1
        for i in range(count):
            out["notes"].append((time, length, key + i, velocity, track, group,
                                 channel if channel != 255 else out["channels"][track]))


def read_timed(body, kind, out):
    pos = time = 0  # (each block starts fresh)
    while pos < len(body):
        gap, pos = varint(body, pos)
        time += gap
        if kind == 16:
            value, pos = varint(body, pos)
            out["tempo"].append((time, value))
        elif kind == 17:
            track, pos = varint(body, pos)
            size = 2 if body[pos] & 0xE0 == 0xC0 else 3
            out["events"].append((time, track, bytes(body[pos:pos + size])))
            pos += size
        elif kind == 19:
            out["time_signatures"].append((time, body[pos], body[pos + 1]))
            pos += 2
        else:
            sharps = body[pos] - 256 if body[pos] > 127 else body[pos]
            out["key_signatures"].append((time, sharps, body[pos + 1]))
            pos += 2


def read_blocks(data, out):
    pos = 0
    while pos < len(data):
        kind, pos = varint(data, pos)
        if kind == 0:  # End (inside a Zipped block: ends only the zipped part)
            return
        size, pos = varint(data, pos)
        body = data[pos:pos + size]
        pos += size  # (skips whatever is left of the block: unknown types, new fields)
        if kind == 1:
            out["ppq"], p = varint(body, 0)
            out["count"], p = varint(body, p)
            out["from_bar"], p = varint(body, p)
            out["length"], _ = varint(body, p)
        elif kind == 2:
            count, p = varint(body, 0)
            out["channels"] = list(body[p:p + count])
        elif kind == 3:
            read_notes(body, out)
        elif kind == 4:
            count, p = varint(body, 0)
            for _ in range(count):
                track, p = varint(body, p)
                out["colours"][track] = tuple(body[p:p + 3])
                p += 3
        elif kind == 5:
            unzipped, p = varint(body, 0)
            read_blocks(zlib.decompress(body[p:], bufsize=unzipped), out)
        elif kind == 6:
            count, p = varint(body, 0)
            for _ in range(count):
                track, p = varint(body, p)
                out["names"][track], p = text(body, p)
        elif kind == 7:
            out["channel_colours"] = [tuple(body[3 * c:3 * c + 3]) for c in range(16)]
        elif kind in (16, 17, 19, 20):
            read_timed(body, kind, out)


def read_spiderweb_notes(data):
    """-> {"ppq", "count", "from_bar", "length", "channels" (per track), "names" {track: text},
    "colours" {track: (r, g, b)}, "channel_colours" [(r, g, b)] * 16,
    "notes" [(start, length, key, velocity, track, group, channel)] in note-number order,
    "tempo" [(tick, microseconds)], "events" [(tick, track, MIDI bytes)],
    "time_signatures" [(tick, top, bottom)], "key_signatures" [(tick, sharps, minor)]}"""
    if data[:8] != b"SPWNOTES" or data[8] != 1:
        raise ValueError("not Spiderweb Notes version 1")
    out = {"ppq": 960, "count": 0, "from_bar": 0, "length": 0, "channels": [], "names": {}, "colours": {},
           "channel_colours": [], "notes": [], "tempo": [], "events": [], "time_signatures": [],
           "key_signatures": []}
    read_blocks(data[9:], out)
    return out
```
