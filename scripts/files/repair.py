"""Repairing the folder version's program files (data/<version>/; user 2026-11-26). Three ways in:
- the starter found names / sizes wrong (checksum.txt) or the list damaged and the user said Repair: boot.pyc gets
  --spiderweb-repair -> run()
- Spiderweb couldn't start because a program file won't load (looks_damaged): offer() asks, then starts the repair
- Help -> About "Check program files": damaged() = every file's SHA-256 against checksum.txt (a few seconds)
run() needs only Python, PyNaCl and these few files: it downloads THIS version's zip from its release page (signed
list checked, SHA-256 checked), compares every file with the zip's own checksum.txt and puts back only those that
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
# Given to the Spiderweb started after a repair that worked: the starter skips its file check and its
# "closed unexpectedly" question for that start, and if Spiderweb still can't start nothing offers another repair,
# only the download page (no question loops; user 2026-11-27). boot.py takes it out of sys.argv: sys.spiderweb_repaired.
REPAIRED = "--spiderweb-repaired"
# Given with REPAIRED when the repair found every file fine: if Spiderweb still can't start, the files aren't the cause
# ("repair.fine_still": the crash log + the problem page, never the download page).
FINE = "--spiderweb-files-fine"
ISSUES = WEBSITE + "/issues"
FROM = os.environ.get("SPIDERWEB_REPAIR_FROM") or "%s/releases/download/v%s/" % (WEBSITE, VERSION)
ZIP = "Spiderweb-%s.zip" % VERSION
OLD_MARK = ".repair-old-"
REPAIRED_LIST = "repaired-files.txt"  # (in user/: what the last repair put back; native/starter reads it)
ENGLISH = {  # (used when the language files themselves are damaged)
    "repair.ask": "Spiderweb couldn't start: some of its program files seem to be damaged.\n\nRepair them now? "
                  "Spiderweb downloads this version again and puts back only the damaged files. Your own files "
                  "stay as they are.\n\nNo = Spiderweb stays closed.",
    "repair.title": "Repairing Spiderweb {version}",
    "repair.checking": "Checking the program files…",
    "repair.downloading": "Downloading Spiderweb {version}… {done} of {size} MB",
    "repair.done": "{n} program files were repaired. Spiderweb starts now.",
    "repair.done_one": "1 program file was repaired. Spiderweb starts now.",
    "repair.none": "All program files were fine: nothing was changed. Spiderweb starts now.",
    "repair.failed": "Spiderweb couldn't repair its program files.\n\n{why}\n\nOpen the download page to get Spiderweb "
                     "again? Unpack the download over this Spiderweb folder: your settings, autosave and exports (the "
                     "user and output folders) stay as they are.\n\nYou can also try the repair again later.",
    "repair.still": "Spiderweb still can't start after the repair.\n\nOpen the download page to get Spiderweb again? "
                    "Unpack the download over this Spiderweb folder: your settings, autosave and exports (the user and "
                    "output folders) stay as they are.",
    "repair.fine_still": "Spiderweb still can't start, but its program files are all fine, so something else stops it: "
                         "for example a graphics or sound driver, or a security program.\n\nSpiderweb keeps a crash log "
                         "in its folder (spiderweb-crash.log, or user\\crash-now.log). Open the page for reporting "
                         "problems? Please add that log to your report.",
    "repair.fail.disk": "Spiderweb couldn't write its files (the disk may be full, the folder read-only, or another "
                        "program is using a file). Some files may already have been put back; the others are as they "
                        "were.",
    "repair.fail.bad_signature": "The download didn't pass the safety check, so it wasn't used and nothing was "
                                 "changed. Someone may have changed the files on GitHub: please report it.",
    "repair.fail.bad_file": "The downloaded file doesn't match its safety signature, so it wasn't used. Please report "
                            "it.",
    "repair.fail.wrong_version": "The download's safety signature is for another version, so it wasn't used and "
                                 "nothing was changed. Please report it.",
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
    """Spiderweb couldn't start because of exc: True when it looked like damaged files and the user was asked (a
    repair started on Yes; this program should end with boot's "said already" code, so the starter adds nothing).
    Right after a repair (sys.spiderweb_repaired): never another repair, the download page offered instead; after one
    that found every file fine (sys.spiderweb_files_fine): the files aren't the cause, the problem page."""
    if not FROZEN or sys.platform != "win32" or not looks_damaged(exc):
        return False
    try:
        if getattr(sys, "spiderweb_repaired", False):
            fine = getattr(sys, "spiderweb_files_fine", False)
            if ask(t("repair.fine_still" if fine else "repair.still")):
                import webbrowser
                webbrowser.open(ISSUES if fine else WEBSITE + "/releases/latest")
        elif ask(t("repair.ask")):
            start_repair(sys.argv[1:])
        return True
    except Exception:  # noqa: BLE001
        return False


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def damaged(folder=None, progress=None, cancel=None):
    """Places (from checksum.txt) of files missing, of another size or another SHA-256. None = the list is missing or
    damaged (a line that doesn't read, cut short: never a check that quietly skips files)."""
    folder = folder or BUNDLE
    files = folder_zip.listed(folder, strict=True)
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
    put_back = []
    try:
        return _put_back(data, inner, progress, put_back)
    finally:
        _remember_put_back(put_back)


def _remember_put_back(places):
    """user/repaired-files.txt = the places this repair put back (empty list = none): the starter names one that goes
    missing again (a virus scanner removing it; native/starter gone_again)."""
    try:
        from files.about import USER
        from files.safefile import write_text
        os.makedirs(USER, exist_ok=True)  # (a folder never started yet has no user/)
        write_text(os.path.join(USER, REPAIRED_LIST), "".join(p + "\n" for p in places))
    except Exception:  # noqa: BLE001 (only a hint later: never stops a repair)
        pass


def _put_back(data, inner, progress, put_back):
    import io
    import zipfile
    fixed = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        try:
            listing = z.read(inner + folder_zip.LIST)
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
                put_back.append(rel)
                fixed += 1
        here_list = os.path.join(BUNDLE, folder_zip.LIST)
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


def repair_lock_name(exe=None):
    """The lock a repair of this folder holds while it runs (native/starter repair_lock: same text): the starter then
    says "being repaired" instead of a second repair or a start on half-replaced files."""
    exe = exe or sys.executable
    return "Local\\SpiderwebRepair-" + hashlib.sha256(exe.lower().encode("utf-8")).hexdigest()[:32]


def _hold_lock():
    """Takes the repair lock for the rest of this program (Windows lets go when it ends). None elsewhere."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        k = ctypes.WinDLL("kernel32")
        k.CreateMutexW.restype = ctypes.c_void_p
        k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        return k.CreateMutexW(None, 0, repair_lock_name())
    except (AttributeError, OSError):
        return None


def run(args=()):
    """Repair mode (boot.py with --spiderweb-repair): repair with a small window (Cancel / X stop it while it is
    still downloading: nothing changed yet), a message. A repair that worked starts Spiderweb, given args (a project
    double-clicked) + REPAIRED; a failed or cancelled one leaves it closed (user 2026-11-27, option B: like other
    apps; the next start asks again, where "No" still starts it). Exit code always 0 (everything was said here; the
    starter speaks only when this never ran)."""
    import threading
    lock = _hold_lock()  # noqa: F841 (held until this program ends)
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
    _crash_dealt_with()  # (whatever happened: the user has answered that crash's question)
    if result.get("fail") == "cancelled":
        return 0  # (the user stopped it: nothing changed, Spiderweb stays closed)
    if "fail" in result:
        fail = result["fail"]
        why = t("repair.other") if fail == "other" else t("repair.fail." + fail)  # (a repair's own words if any)
        if why == "repair.fail." + fail:
            why = t("updates.fail." + fail)
        if ask(t("repair.failed", why=why)):
            import webbrowser
            webbrowser.open(WEBSITE + "/releases/latest")
        return 0  # (never half-started on files a repair couldn't fix; the next start asks again)
    n = result.get("fixed", 0)
    tell(t("repair.done", n) if n else t("repair.none"))
    marks = [REPAIRED] if n else [REPAIRED, FINE]  # (no file check, no new offer; FINE: files aren't the cause)
    subprocess.Popen([sys.executable, *marks, *args], cwd=HERE, close_fds=True)
    return 0
