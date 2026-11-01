"""Updating Spiderweb from the update popup (window/updates.py). A release's file is used only when its signed list
(files/signing.py) checks out: signed with our key, holding that release's own version (newer than this one), and the
file's SHA-256 matching. Anything else = refused, the half download deleted, the popup's download page still there.

The exe swaps itself (Windows lets a running exe be renamed): the checked download is saved beside it as
Spiderweb.exe.new, Spiderweb.exe -> Spiderweb.exe.old, .new -> Spiderweb.exe, Spiderweb closes as usual, then the new
one starts with --updated (it waits for this one's lock, spiderweb.py) and clean_leftovers() deletes .old.
From the scripts the checked source zip is only saved in Downloads and shown: running scripts are never overwritten."""

import hashlib
import os
import subprocess
import sys
import threading
import urllib.request

from files import signing
from files.about import HERE, VERSION
from files.update_check import TIMEOUT, version_tuple

EXE = "Spiderweb.exe"
ZIP = "Spiderweb-source.zip"
SMALL_MOST = 64 * 1024  # the list and its signature (a few hundred bytes)
BIG_MOST = 1 << 30  # the exe / zip (~90 MB)
UPDATED = "--updated"  # the new exe's start argument
OPEN = urllib.request.urlopen  # (tests put a pretend GitHub here)


class Refused(Exception):
    """Why the update can't go on: the end of an "updates.fail.*" text key."""


def exe_mode():
    """True = running as the exe (it can swap itself); False = from the scripts (the zip is only downloaded)."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def exe_path():
    """The running exe (tests point it at a throwaway file)."""
    return sys.executable


def wanted():
    return EXE if exe_mode() else ZIP


def downloads_folder():
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    return d if os.path.isdir(d) else HERE


def _writable(folder):
    probe = os.path.join(folder, ".spiderweb-write-check")
    try:
        with open(probe, "wb"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


def possible(release):
    """None when this release can be updated from here, else why not (an "updates.fail.*" key end): no PyNaCl, no
    public key, a release without signed files, or a folder Spiderweb can't write in."""
    if not signing.PUBLIC_KEY or not signing.have_nacl():
        return "no_check"
    assets = release.get("assets") or {}
    if any(n not in assets for n in (signing.LIST_NAME, signing.SIG_NAME, wanted())):
        return "unsigned"
    if not _writable(os.path.dirname(exe_path()) if exe_mode() else downloads_folder()):
        return "not_writable"
    return None


def _fetch(url, most, path=None, progress=None, cancel=None):
    """Download url: its bytes (path None), or into path, returning its SHA-256. Bigger than most / cancelled =
    Refused (the part file deleted)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Spiderweb/" + VERSION})
    h = hashlib.sha256()
    got = bytearray()
    out = open(path, "wb") if path else None
    try:
        with OPEN(req, timeout=TIMEOUT * 4) as r:
            size = int(r.headers.get("Content-Length") or 0)
            if size > most:
                raise Refused("too_big")
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise Refused("cancelled")
                block = r.read(1 << 16)
                if not block:
                    break
                done += len(block)
                if done > most:
                    raise Refused("too_big")
                h.update(block)
                if out:
                    out.write(block)
                else:
                    got += block
                if progress:
                    progress(done, size)
    except BaseException:
        if out:
            out.close()
            out = None
            try:
                os.remove(path)
            except OSError:
                pass
        raise
    finally:
        if out:
            out.close()
    return h.hexdigest() if path else bytes(got)


def download(release, progress=None, cancel=None):
    """Runs on a thread. The checked file's path (exe: Spiderweb.exe.new beside the running one; scripts:
    Downloads/Spiderweb-<version>-source.zip). Raises Refused, or OSError (no connection, disk)."""
    assets = release.get("assets") or {}
    why = possible(release)
    if why:
        raise Refused(why)
    listed = _fetch(assets[signing.LIST_NAME]["url"], SMALL_MOST, cancel=cancel)
    sig = _fetch(assets[signing.SIG_NAME]["url"], SMALL_MOST, cancel=cancel)
    try:
        version, hashes = signing.check(listed, sig.decode("ascii", "replace"))
    except (ValueError, UnicodeDecodeError):
        raise Refused("bad_signature")
    mine, theirs = version_tuple(VERSION), version_tuple(version)
    if theirs is None or theirs != version_tuple(release.get("version", "")) or theirs <= mine:
        raise Refused("wrong_version")  # (a real older list put on a newer release: never goes backwards)
    name = wanted()
    if name not in hashes:
        raise Refused("unsigned")
    if exe_mode():
        dest = os.path.join(os.path.dirname(exe_path()), EXE + ".new")
    else:
        dest = os.path.join(downloads_folder(), "Spiderweb-%s-source.zip" % version)
    part = dest + ".part"
    got = _fetch(assets[name]["url"], BIG_MOST, part, progress, cancel)
    if got != hashes[name]:
        os.remove(part)
        raise Refused("bad_file")
    os.replace(part, dest)
    return dest


def swap_in(new_path, exe=None):
    """Puts the checked exe in the running one's place (it becomes .old). Returns undo() for when closing is
    cancelled. Raises OSError (nothing changed then)."""
    exe = exe or exe_path()
    old = exe + ".old"
    if os.path.exists(old):
        os.remove(old)  # (a past update's, if its cleanup couldn't)
    os.replace(exe, old)
    try:
        os.replace(new_path, exe)
    except OSError:
        os.replace(old, exe)
        raise

    def undo():
        os.replace(exe, new_path)
        os.replace(old, exe)
        try:
            os.remove(new_path)
        except OSError:
            pass
    return undo


def start_new(exe=None):
    """Starts the new exe as a program of its own (not sharing this one's unpacked files), told to wait for the
    lock this one still holds."""
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    exe = exe or exe_path()
    subprocess.Popen([exe, UPDATED], env=env, cwd=os.path.dirname(exe), close_fds=True)


def clean_leftovers():
    """At an exe's start: deletes .old / .new / .new.part from an update (the old program may still be ending:
    tried for a while on a thread)."""
    if not exe_mode():
        return
    exe = exe_path()
    for p in (exe + ".new", exe + ".new.part"):  # (once, now: a download started later uses these names)
        try:
            os.remove(p)
        except OSError:
            pass
    old = exe + ".old"

    def work():
        import time
        for _ in range(60):
            try:
                os.remove(old)
            except OSError:
                pass
            if not os.path.exists(old):
                return
            time.sleep(1)
    threading.Thread(target=work, daemon=True).start()
