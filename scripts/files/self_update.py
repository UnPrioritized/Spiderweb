"""Updating Spiderweb from the update popup (window/updates.py). A release's file is used only when its signed list
(files/signing.py) checks out: signed with our key, holding that release's own version (newer than this one), and the
file's SHA-256 matching. Anything else = refused, the half download deleted, the popup's download page still there.

The folder version (Spiderweb.exe + data/<version>/): the checked Spiderweb-<version>.zip is saved in data/ and
unpacked there under a name of its own (data/<version>.part-<random>/), renamed data/<version>/ only once whole (its
files' sizes checked against its files.txt), its starter saved beside the running one as <exe>.new. Nothing the
running program uses is touched until then. On closing, ONE switch: the starter swapped (Windows lets a running exe be
renamed: <exe> -> <exe>.old, .new -> <exe>) and data/version.txt names the new version; Spiderweb closes as usual,
then the new one starts with --updated (it waits for this one's lock, spiderweb.py). Closing cancelled / failing =
undo() (old starter + version.txt back). At a later start, once the new one is up (CLEAN_AFTER_S), clean_leftovers()
deletes <exe>.old and the old data/<version>/ folders: only the files each one's own files.txt lists, then folders
left empty (anything else put there stays).
From the scripts the checked source zip is only saved in Downloads and shown: running scripts are never overwritten.
ONE download at a time (_busy); each writes its own "<target>.<random>.part" (never two writers on one file)."""

import glob
import hashlib
import http.client
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request

from files import folder_zip, signing, system
from files.about import BUNDLE, HERE, VERSION
from files.folder_zip import EXE, Refused, listed, remove, remove_listed  # noqa: F401 (tests + updates.py use them here)
from files.update_check import TIMEOUT, version_tuple

ZIP = "Spiderweb-source.zip"
FOLDER_ZIP = "Spiderweb-%s.zip"  # (the folder version's download: Spiderweb/Spiderweb.exe + Spiderweb/data/<version>/)
DATA = os.path.join(HERE, "data")  # (the folder version's data/: version folders + version.txt)
VERSION_TXT = os.path.join(DATA, "version.txt")
CLEAN_AFTER_S = 30  # old version folders deleted this long after a start (a start that fails never deletes them)
STAGE = re.compile(r"[0-9A-Za-z._-]+\.part-[0-9a-f]{8}")  # (data/<version>.part-<random>: an unpacking's own name)
SMALL_MOST = 64 * 1024  # the list and its signature (a few hundred bytes)
BIG_MOST = 1 << 30  # the exe / zip (~90 MB)
UPDATED = "--updated"  # the new exe's start argument
OPEN = urllib.request.urlopen  # (tests put a pretend GitHub here)
STALE_PART_S = 3600  # a Downloads part file older than this at start = a dropped download (another folder's
_busy = threading.Lock()  # Spiderweb may be downloading a younger one right now)


def exe_mode():
    """True = running as the exe (it can swap itself); False = from the scripts (the zip is only downloaded)."""
    from files.about import FROZEN
    return FROZEN and sys.platform == "win32"


def exe_path():
    """The running exe (tests point it at a throwaway file)."""
    return sys.executable


def wanted(version=None):
    """The release file this Spiderweb takes: the folder version's zip, or the source zip."""
    return FOLDER_ZIP % version if exe_mode() else ZIP


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


def possible(release):
    """None when this release can be updated from here, else why not (an "updates.fail.*" key end): no PyNaCl, no
    public key, a release without signed files, or a folder Spiderweb can't write in."""
    if not signing.PUBLIC_KEY or not signing.have_nacl():
        return "no_check"
    assets = release.get("assets") or {}
    if any(n not in assets for n in (signing.LIST_NAME, signing.SIG_NAME, wanted(release.get("version")))):
        return "unsigned"
    if exe_mode():
        if not (_writable(os.path.dirname(exe_path())) and _writable(DATA)):
            return "not_writable"
    elif not _writable(downloads_folder()):
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
    """Where the checked file goes: data/Spiderweb-<version>.zip (unpacked, then deleted), or
    Downloads/Spiderweb-<version>-source.zip."""
    if exe_mode():
        return os.path.join(DATA, FOLDER_ZIP % version)
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
    name = wanted(version)
    if name not in hashes or name not in assets:
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
    if exe_mode():  # (unpacked straight from the checked part file, then it goes)
        try:
            if progress:
                progress(-1, 0)  # (the popup says "Unpacking…")
            return unpack(part, version, cancel)
        finally:
            try:
                os.remove(part)
            except OSError:
                pass
    try:
        os.replace(part, dest)
    except OSError:
        try:
            os.remove(part)
        except OSError:
            pass
        raise Refused("disk") from None
    return dest


def unpack(zip_path, version, cancel=None):
    """The checked zip's data/<version>/ beside the running one (files/folder_zip.py: own folder until whole); its
    Spiderweb.exe saved as <exe>.new, README / licences beside the exe updated. Returns <exe>.new. Raises Refused
    (nothing left behind)."""
    new_exe = folder_zip.unpack(zip_path, version, DATA, exe_path() + ".new", cancel, keep=BUNDLE,
                                top_to=os.path.dirname(exe_path()))
    UNPACKED[new_exe] = version
    return new_exe


UNPACKED = {}  # <exe>.new -> the version unpacked with it (swap_in switches version.txt to it)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _put_version(text):
    """data/version.txt = text (None: removed). Raises OSError."""
    from files.safefile import write_text
    if text is None:
        if os.path.exists(VERSION_TXT):
            remove(VERSION_TXT)
    else:
        write_text(VERSION_TXT, text)


_was_version = [None]  # (version.txt as it was before the switch: give_back puts it back)


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
    """THE SWITCH: puts the checked starter in the running one's place (it becomes .old) and, for an unpacked
    folder version, data/version.txt names the new version. Returns undo(): True when the old starter and
    version.txt are back; False when they couldn't be (the folder then has the new starter + version: checked, used
    at the next start). Raises OSError (nothing changed then). Untested under WINE (system.WINE)."""
    exe = exe or exe_path()
    old = exe + ".old"
    version = UNPACKED.get(new_path)
    was = _read(VERSION_TXT) if version else None
    if os.path.exists(old):
        remove(old)  # (a past update's, if its cleanup couldn't)
    os.replace(exe, old)
    try:
        os.replace(new_path, exe)
    except OSError:
        os.replace(old, exe)
        raise
    if version:
        try:
            _put_version(version + "\n")
        except OSError:  # (the starter back, nothing switched)
            os.replace(exe, new_path)
            os.replace(old, exe)
            raise
        _was_version[0] = was

    def undo():
        if version and not _retry(lambda: _put_version(was)):
            return False  # (the new version stays named: whole and checked, used at the next start)
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
    if not _retry(lambda: os.replace(old, exe)):
        return False
    if exe_mode() and _was_version[0] is not None:  # (the old version named again: its folder is untouched)
        return _retry(lambda: _put_version(_was_version[0]))
    return True


def clean_leftovers():
    """At start: an update's <exe>.old (the old program may still be ending: retried for a while on a thread),
    <exe>.new / part files and, CLEAN_AFTER_S later, older data/<version> folders (exe: clean_old_versions); a
    dropped source zip download's part files in Downloads (scripts)."""
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
                    break
                time.sleep(1)
        threading.Thread(target=work, daemon=True).start()
        threading.Thread(target=lambda: (time.sleep(CLEAN_AFTER_S), clean_old_versions()), daemon=True,
                         name="old versions").start()
        return
    _source_parts()


def clean_old_versions(now=None):
    """(Folder version, CLEAN_AFTER_S after a start: this version is up) In data/: version folders OLDER than this
    one (never this run's, never a newer one an update just unpacked) deleted by their own files.txt only; an
    unpacking's own data/<version>.part-<random>/ folder and part files older than STALE_PART_S. How many folders."""
    if not exe_mode() or not os.path.isdir(DATA):
        return 0
    now = now or time.time()
    mine = version_tuple(VERSION)
    gone = 0
    for name in os.listdir(DATA):
        p = os.path.join(DATA, name)
        try:
            if os.path.isdir(p):
                if STAGE.fullmatch(name):
                    if now - os.path.getmtime(p) > STALE_PART_S:
                        folder_zip.drop(p)
                    continue
                v = version_tuple(name)
                if (v is None or mine is None or v >= mine
                        or os.path.normcase(p) == os.path.normcase(BUNDLE or "")):
                    continue
                if remove_listed(p):
                    gone += 1
            elif re.fullmatch(r"Spiderweb-[0-9A-Za-z._-]+\.zip\.[0-9a-f]{8}\.part", name):
                if now - os.path.getmtime(p) > STALE_PART_S:
                    remove(p)
        except OSError:
            continue
    if BUNDLE:  # (files a repair renamed aside while Windows held them: free now)
        for d, _, names in os.walk(BUNDLE):
            for n in names:
                if ".repair-old-" in n:
                    try:
                        remove(os.path.join(d, n))
                    except OSError:
                        pass
    return gone


def _source_parts():
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
