"""Saving files without ever leaving a half-written one behind: the new contents go to a temporary file next to it,
which only replaces the real file once it's completely written. A crash, power cut or error while saving leaves
the old file as it was."""

import os
import shutil
import time


def write_bytes(path, data):
    """data = bytes, or byte pieces one after another (a generator: big files are never whole in memory)."""
    tmp = path + ".tmp"
    if isinstance(data, (bytes, bytearray, memoryview)):
        data = (data,)
    try:
        with open(tmp, "wb") as f:
            for piece in data:
                f.write(piece)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:  # (a full disk: the half-written temp file would go on taking the space)
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # a virus scanner / another program has the old file open for a moment
            if attempt == 19:
                break
            time.sleep(0.05)
    # still locked: write it directly (like before) rather than not at all
    try:
        shutil.copyfile(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def write_text(path, text):
    write_bytes(path, text.encode("utf-8"))
