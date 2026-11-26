"""Repairing the folder version's program files (data/<version>/; user 2026-11-26). Three ways in:
- the starter found names / sizes wrong (files.txt) and the user said Repair: boot.pyc gets --spiderweb-repair -> run()
- Spiderweb couldn't start because a program file won't load (looks_damaged): offer() asks, then starts the repair
- Help -> About "Check program files": damaged() = every file's SHA-256 against files.txt (a few seconds)
run() needs only Python, PyNaCl and these few files: it downloads THIS version's zip from its release page (signed
list checked, SHA-256 checked), compares every file with the zip's own files.txt and puts back only those that
differ (a file Windows holds is renamed aside first: <name>.repair-old-<random>, deleted at a later start by
self_update.clean_old_versions). user/ and output/ are never touched. Then Spiderweb starts as usual."""

import hashlib
import os
import secrets
import subprocess
import sys
import zipimport

from files import folder_zip, signing
from files.about import BUNDLE, FROZEN, HERE, VERSION, WEBSITE

REPAIR = "--spiderweb-repair"  # (boot.py: run() instead of Spiderweb)
FROM = os.environ.get("SPIDERWEB_REPAIR_FROM") or "%s/releases/download/v%s/" % (WEBSITE, VERSION)
ZIP = "Spiderweb-%s.zip" % VERSION
OLD_MARK = ".repair-old-"
ENGLISH = {  # (used when the language files themselves are damaged)
    "repair.ask": "Spiderweb couldn't start: some of its program files seem to be damaged.\n\nRepair them now? "
                  "Spiderweb downloads this version again and puts back only the damaged files. Your own files "
                  "stay as they are.",
    "repair.title": "Repairing Spiderweb {version}",
    "repair.checking": "Checking the program files…",
    "repair.downloading": "Downloading Spiderweb {version}… {done} of {size} MB",
    "repair.done": "{n} program files were repaired. Spiderweb starts now.",
    "repair.done_one": "1 program file was repaired. Spiderweb starts now.",
    "repair.none": "All program files were fine: nothing was changed. Spiderweb starts now.",
    "repair.failed": "Spiderweb couldn't repair its program files.\n\n{why}\n\nOpen the download page to get Spiderweb "
                     "again? Spiderweb starts either way, with its files as they are. Your settings, autosave and "
                     "exports (the user and output folders) stay as they are.",
    "repair.files": "Checking and putting back files… {n} of {all}",
    "repair.cancel": "Cancel",
    "repair.other": "Something went wrong while repairing. Some files may already have been put back; the others are "
                    "as they were.",
}


def t(key, n=None, **places):
    """The text in the user's language; English (ENGLISH) when the language files can't be read."""
    try:
        from files.lang import tr, trn
        text = trn(key, n, n=n, **places) if n is not None else tr(key, **places)
        if text != key:
            return text
    except Exception:  # noqa: BLE001 (damaged language files / settings: English)
        pass
    text = ENGLISH.get(key + "_one" if n == 1 and key + "_one" in ENGLISH else key, key)
    for k, v in dict(places, n=n).items():
        text = text.replace("{%s}" % k, str(v))
    return text


def _box(text, kind):
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    return ctypes.windll.user32.MessageBoxW(None, text, t("repair.title", version=VERSION), kind | 0x10000)


def ask(text):
    return _box(text, 0x04 | 0x30) == 6


def tell(text, error=False):
    _box(text, 0x10 if error else 0x40)


def looks_damaged(exc):
    """An error at start that damaged program files cause: a module or DLL that won't load, unreadable .pyc."""
    if isinstance(exc, (ImportError, SyntaxError, EOFError, zipimport.ZipImportError)):
        return True
    if isinstance(exc, ValueError) and ("marshal" in str(exc) or "bad magic" in str(exc).lower()):
        return True
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) in (126, 127, 193, 1114, 1785)


def start_repair(args=()):
    """Spiderweb.exe started again in repair mode (a fresh program: nothing half loaded in the way); args = what this
    start was given (a project double-clicked), handed on to the Spiderweb started after the repair."""
    subprocess.Popen([sys.executable, REPAIR, *args], cwd=HERE, close_fds=True)


def offer(exc):
    """Spiderweb couldn't start because of exc: True when it looked like damaged files and the user said Repair
    (the repair started; this program should end)."""
    if not FROZEN or sys.platform != "win32" or not looks_damaged(exc):
        return False
    try:
        if ask(t("repair.ask")):
            start_repair(sys.argv[1:])
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def damaged(folder=None, progress=None, cancel=None):
    """Places (from files.txt) of files missing, of another size or another SHA-256. None = no files.txt."""
    folder = folder or BUNDLE
    files = folder_zip.listed(folder)
    if files is None:
        return None
    bad = []
    for n, (rel, (size, sha)) in enumerate(sorted(files.items())):
        if cancel is not None and cancel.is_set():
            break
        p = folder_zip.inside(folder, rel)
        try:
            if p is None or os.path.getsize(p) != size or (sha and _sha(p) != sha):
                bad.append(rel)
        except OSError:
            bad.append(rel)
        if progress:
            progress(n + 1, len(files))
    return bad


class NoConnection(Exception):
    """The download failed or was cut off (told apart from disk trouble: hunt 2026-11-26)."""


def _fetch(url, most, progress=None, cancel=None):
    """url's bytes. NoConnection = network trouble / a refused or cut-short download; Refused("cancelled" / "too_big")."""
    import http.client
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Spiderweb-repair/" + VERSION})
    got = bytearray()
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            size = int(r.headers.get("Content-Length") or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise folder_zip.Refused("cancelled")
                block = r.read(1 << 16)
                if not block:
                    break
                got += block
                if len(got) > most:
                    raise folder_zip.Refused("too_big")
                if progress:
                    progress(len(got), size)
            if size and len(got) != size:
                raise NoConnection("cut short")
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as e:
        raise NoConnection(str(e)) from None
    return bytes(got)


def _put(path, data):
    """Writes path (a file Windows holds is renamed aside first). Raises OSError."""
    tmp = "%s.%s.new" % (path, secrets.token_hex(4))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        os.replace(tmp, path)
    except PermissionError:
        try:
            os.replace(path, path + OLD_MARK + secrets.token_hex(4))
            os.replace(tmp, path)
        except OSError:
            os.remove(tmp)
            raise


def repair(progress=None, cancel=None, putting_back=None):
    """Downloads, checks and puts back what differs. progress(done, size) while downloading, progress(n, all, True)
    while checking / putting back files (cancel is no longer looked at then: putting_back() is called first).
    Returns how many files were put back; raises Refused / NoConnection / OSError (disk)."""
    import io
    import zipfile
    if not signing.have_nacl():
        raise folder_zip.Refused("no_check")
    listed = _fetch(FROM + signing.LIST_NAME, 64 * 1024, cancel=cancel)
    sig = _fetch(FROM + signing.SIG_NAME, 64 * 1024, cancel=cancel)
    try:
        version, hashes = signing.check(listed, sig.decode("ascii", "replace"))
    except signing.Unreadable:
        raise folder_zip.Refused("newer_list") from None
    except ValueError:
        raise folder_zip.Refused("bad_signature") from None
    if version != VERSION or ZIP not in hashes:
        raise folder_zip.Refused("wrong_version")
    data = _fetch(FROM + ZIP, 1 << 30, progress, cancel)
    if hashlib.sha256(data).hexdigest() != hashes[ZIP]:
        raise folder_zip.Refused("bad_file")
    if putting_back:
        putting_back()
    inner = "%sdata/%s/" % (folder_zip.TOP, VERSION)
    fixed = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        try:
            listing = z.read(inner + "files.txt")
        except KeyError:
            raise folder_zip.Refused("bad_file") from None
        wanted = folder_zip.read_list(listing.decode("utf-8", "replace"))
        for n, (rel, (size, sha)) in enumerate(sorted(wanted.items())):
            if progress:
                progress(n + 1, len(wanted), True)
            p = folder_zip.inside(BUNDLE, rel)
            if p is None:
                raise folder_zip.Refused("bad_file")
            try:
                same = os.path.getsize(p) == size and _sha(p) == sha
            except OSError:
                same = False
            if not same:
                _put(p, z.read(inner + rel))
                fixed += 1
        here_list = os.path.join(BUNDLE, "files.txt")
        try:
            same = open(here_list, "rb").read() == listing
        except OSError:
            same = False
        if not same:
            _put(here_list, listing)
            fixed += 1
    return fixed


def _crash_dealt_with():
    """A start that crashed (user/crash-now.log without its closing line) is dealt with by the repair: the closing
    line added, so the starter doesn't ask again and Spiderweb doesn't say "it crashed" (the dump still goes into
    spiderweb-crash.log at the next start, as a dump in a run that closed)."""
    import time
    try:
        from files.errors import CLOSED, CRASH_NOW
        with open(CRASH_NOW, encoding="utf-8", errors="replace") as f:
            closed = CLOSED in f.read()
        if not closed:
            with open(CRASH_NOW, "a", encoding="utf-8") as f:
                f.write("%s%s (repaired)\n" % (CLOSED, time.strftime("%H:%M:%S")))
    except Exception:  # noqa: BLE001 (no log, or errors.py damaged: nothing to deal with)
        pass


def run(args=()):
    """Repair mode (boot.py with --spiderweb-repair): repair with a small window (Cancel / X stop it while it is
    still downloading: nothing changed yet), a message, then Spiderweb starts, given args (a project double-clicked)
    AS IT WAS ASKED FOR, whatever happened: a failed or cancelled repair never leaves Spiderweb closed (hunt
    2026-11-26). Exit code always 0 (everything was said here; the starter speaks only when this never ran)."""
    import threading
    result = {}
    progress = [0, 0, False]
    cancel = threading.Event()
    late = threading.Event()  # (putting files back: too late to stop)

    def work():
        try:
            result["fixed"] = repair(lambda d, s, files=False: progress.__setitem__(slice(None), [d, s, files]),
                                     cancel, late.set)
        except folder_zip.Refused as e:
            result["fail"] = str(e)
        except NoConnection:
            result["fail"] = "no_connection"
        except OSError:  # (writing the files back: a read-only folder, a full disk)
            result["fail"] = "disk"
        except Exception:  # noqa: BLE001
            result["fail"] = "other"
            try:
                from files import errors
                errors.write_log(*sys.exc_info(), "Repair")
            except Exception:  # noqa: BLE001
                pass
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    try:  # (a small window while it works; without Tk (damaged too) it simply works)
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
        root.title(t("repair.title", version=VERSION))
        root.resizable(False, False)
        frame = ttk.Frame(root, padding=16)
        frame.pack()
        say = ttk.Label(frame, text=t("repair.checking"), width=52)
        say.pack(fill="x")
        bar = ttk.Progressbar(frame, maximum=1000, length=380)
        bar.pack(fill="x", pady=(10, 10))

        def stop():
            if not late.is_set():
                cancel.set()
                button.state(["disabled"])
        button = ttk.Button(frame, text=t("repair.cancel"), command=stop)
        button.pack(side="right")
        root.protocol("WM_DELETE_WINDOW", stop)  # (X = Cancel while downloading; once files go back it finishes)

        def poll():
            done, size, files = progress
            if late.is_set():
                button.state(["disabled"])
            if files:
                bar.configure(value=done * 1000 // size if size else 0)
                say.configure(text=t("repair.files", n=done, all=size))
            elif done:
                bar.configure(value=done * 1000 // size if size else 0)
                say.configure(text=t("repair.downloading", version=VERSION, done="%.1f" % (done / 1048576),
                                     size="%.1f" % (size / 1048576)))
            if worker.is_alive():
                root.after(100, poll)
            else:
                root.quit()  # (ends this wait itself, whatever other windows there are)
        root.after(100, poll)
        root.mainloop()
        root.destroy()
    except Exception:  # noqa: BLE001
        pass
    worker.join()
    if result.get("fail") == "cancelled":
        pass  # (nothing changed: Spiderweb as it was)
    elif "fail" in result:
        why = t("repair.other") if result["fail"] == "other" else t("updates.fail." + result["fail"])
        if ask(t("repair.failed", why=why)):
            import webbrowser
            webbrowser.open(WEBSITE + "/releases/latest")
    else:
        n = result.get("fixed", 0)
        _crash_dealt_with()
        tell(t("repair.done", n) if n else t("repair.none"))
    subprocess.Popen([sys.executable, *args], cwd=HERE, close_fds=True)  # (Spiderweb as usual: the starter checks
    return 0                                                              # again, "No = start anyway" still works)
