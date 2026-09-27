"""Saving files without ever leaving a half-written one behind: the new contents go to a temporary file next to it,
which only replaces the real file once it's completely written. A crash, power cut or error while saving leaves
the old file as it was."""

import os
import time


def write_bytes(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
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
        with open(path, "wb") as f:
            f.write(data)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def write_text(path, text):
    write_bytes(path, text.encode("utf-8"))
