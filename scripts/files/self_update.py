"""Updating Spiderweb from the update popup (window/updates.py). A release's file is used only when its signed list
(files/signing.py) checks out: signed with our key, holding that release's own version (newer than this one), and the
file's SHA-256 matching. Anything else = refused, the half download deleted, the popup's download page still there.

The exe swaps itself (Windows lets a running exe be renamed): the checked download is saved beside it as
<exe>.new, <exe> -> <exe>.old, .new -> <exe>, Spiderweb closes as usual, then the new one starts with --updated (it
waits for this one's lock, spiderweb.py) and clean_leftovers() deletes .old. Closing cancelled / failing = undo().
From the scripts the checked source zip is only saved in Downloads and shown: running scripts are never overwritten.
ONE download at a time (_busy); each writes its own "<target>.<random>.part" (never two writers on one file)."""

import glob
import hashlib
import http.client
import os
import secrets
import stat
import subprocess
import sys
import threading
import time
import urllib.request

from files import signing, system
from files.about import VERSION
from files.update_check import TIMEOUT, version_tuple

EXE = "Spiderweb.exe"
ZIP = "Spiderweb-source.zip"
SMALL_MOST = 64 * 1024  # the list and its signature (a few hundred bytes)
BIG_MOST = 1 << 30  # the exe / zip (~90 MB)
UPDATED = "--updated"  # the new exe's start argument
OPEN = urllib.request.urlopen  # (tests put a pretend GitHub here)
STALE_PART_S = 3600  # a Downloads part file older than this at start = a dropped download (another folder's
_busy = threading.Lock()  # Spiderweb may be downloading a younger one right now)


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
    return system.downloads_folder()


def _writable(folder):
    probe = os.path.join(folder, ".spiderweb-write-check")
    try:
        with open(probe, "wb"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


def remove(path):
    """Delete a file, read-only ones too (a read-only exe's .old could never be deleted). Raises OSError."""
    try:
        os.remove(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)


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
    Refused (the part file deleted); can't write the file = Refused("disk"); network trouble = OSError."""
    req = urllib.request.Request(url, headers={"User-Agent": "Spiderweb/" + VERSION})
    h = hashlib.sha256()
    got = bytearray()
    out = None
    try:
        if path:
            try:
                out = open(path, "wb")
            except OSError:
                raise Refused("disk") from None
        with OPEN(req, timeout=TIMEOUT * 4) as r:
            size = int(r.headers.get("Content-Length") or 0)
            if size > most:
                raise Refused("too_big")
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise Refused("cancelled")
                try:
                    block = r.read(1 << 16)
                except http.client.HTTPException as e:  # (cut off mid-file: a network problem)
                    raise ConnectionError(str(e)) from None
                if not block:
                    break
                done += len(block)
                if done > most:
                    raise Refused("too_big")
                h.update(block)
                if out:
                    try:
                        out.write(block)
                    except OSError:
                        raise Refused("disk") from None
                else:
                    got += block
                if progress:
                    progress(done, size)
        if cancel is not None and cancel.is_set():
            raise Refused("cancelled")
        if out:
            try:
                out.close()
            except OSError:
                raise Refused("disk") from None
            out = None
    except BaseException:
        if out:
            try:
                out.close()
            except OSError:
                pass
            out = None
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
        raise
    return h.hexdigest() if path else bytes(got)


def target(version):
    """Where the checked file goes: <exe>.new beside the running exe, or Downloads/Spiderweb-<version>-source.zip."""
    if exe_mode():
        return exe_path() + ".new"
    return os.path.join(downloads_folder(), "Spiderweb-%s-source.zip" % version)


def download(release, progress=None, cancel=None):
    """Runs on a thread. The checked file's path. Raises Refused, or OSError (no connection)."""
    if not _busy.acquire(blocking=False):
        raise Refused("busy")
    try:
        return _download(release, progress, cancel)
    finally:
        _busy.release()


def _download(release, progress, cancel):
    assets = release.get("assets") or {}
    why = possible(release)
    if why:
        raise Refused(why)
    listed = _fetch(assets[signing.LIST_NAME]["url"], SMALL_MOST, cancel=cancel)
    sig = _fetch(assets[signing.SIG_NAME]["url"], SMALL_MOST, cancel=cancel)
    try:
        version, hashes = signing.check(listed, sig.decode("ascii", "replace"))
    except signing.Unreadable:  # (ours, in a newer list format: calm, not the alarm)
        raise Refused("newer_list") from None
    except ValueError:
        raise Refused("bad_signature") from None
    mine, theirs = version_tuple(VERSION), version_tuple(version)
    if theirs is None or theirs != version_tuple(release.get("version", "")) or theirs <= mine:
        raise Refused("wrong_version")  # (a real older list put on a newer release: never goes backwards)
    name = wanted()
    if name not in hashes:
        raise Refused("unsigned")
    dest = target(version)
    part = "%s.%s.part" % (dest, secrets.token_hex(4))
    got = _fetch(assets[name]["url"], BIG_MOST, part, progress, cancel)
    if got != hashes[name]:
        try:
            os.remove(part)
        except OSError:
            pass
        raise Refused("bad_file")
    try:
        os.replace(part, dest)
    except OSError:
        try:
            os.remove(part)
        except OSError:
            pass
        raise Refused("disk") from None
    return dest


def _retry(job, tries=25):
    """job() until it works (a file another program holds for a moment: antivirus, cloud folders); True if it did."""
    for n in range(tries):
        try:
            job()
            return True
        except OSError:
            if n + 1 < tries:
                time.sleep(0.2)
    return False


def swap_in(new_path, exe=None):
    """Puts the checked exe in the running one's place (it becomes .old). Returns undo(): True when the old exe is
    back; False when it couldn't be (the folder still has an exe then: the new one). Raises OSError (nothing
    changed then)."""
    exe = exe or exe_path()
    old = exe + ".old"
    if os.path.exists(old):
        remove(old)  # (a past update's, if its cleanup couldn't)
    os.replace(exe, old)
    try:
        os.replace(new_path, exe)
    except OSError:
        os.replace(old, exe)
        raise

    def undo():
        if os.path.exists(exe) and not _retry(lambda: os.replace(exe, new_path)):
            return False  # (the new exe stays in place: checked, used at the next start)
        if _retry(lambda: os.replace(old, exe)):
            try:
                remove(new_path)
            except OSError:
                pass
            return True
        if os.path.exists(new_path):  # (never a folder without an exe)
            _retry(lambda: os.replace(new_path, exe))
        return False
    return undo


def new_ready(new_size, exe=None):
    """Just before closing: the swapped-in exe is still there, whole (an antivirus may take it away)."""
    exe = exe or exe_path()
    try:
        return os.path.getsize(exe) == new_size
    except OSError:
        return False


def start_new(exe=None):
    """Starts the new exe as a program of its own (not sharing this one's unpacked files), told to wait for the
    lock this one still holds. Raises OSError when Windows refuses (e.g. an antivirus)."""
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    exe = exe or exe_path()
    subprocess.Popen([exe, UPDATED], env=env, cwd=os.path.dirname(exe), close_fds=True)


def give_back(exe=None):
    """The new exe couldn't start after closing: the old one back in its place (True if it is)."""
    exe = exe or exe_path()
    old = exe + ".old"
    if not os.path.exists(old):
        return False
    if os.path.exists(exe) and not _retry(lambda: remove(exe)):
        return False
    return _retry(lambda: os.replace(old, exe))


def clean_leftovers():
    """At start: an update's <exe>.old (the old program may still be ending: retried for a while on a thread) and
    <exe>.new / part files (exe); a dropped source zip download's part files in Downloads (scripts)."""
    if exe_mode():
        exe = exe_path()
        for p in glob.glob(glob.escape(exe) + ".new*"):  # (once, now: a download started later uses these names)
            try:
                remove(p)
            except OSError:
                pass
        old = exe + ".old"

        def work():
            for _ in range(60):
                try:
                    remove(old)
                except OSError:
                    pass
                if not os.path.exists(old):
                    return
                time.sleep(1)
        threading.Thread(target=work, daemon=True).start()
        return

    def parts():  # (on a thread: finding Downloads may ask a small system program on Linux)
        try:
            found = glob.glob(os.path.join(glob.escape(downloads_folder()), "Spiderweb-*-source.zip.*.part"))
        except OSError:
            return
        for p in found:
            try:
                if time.time() - os.path.getmtime(p) > STALE_PART_S:
                    os.remove(p)
            except OSError:
                pass
    threading.Thread(target=parts, daemon=True).start()
