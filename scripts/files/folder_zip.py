"""The folder version's zip (Spiderweb-<version>.zip: Spiderweb/Spiderweb.exe + Spiderweb/data/<version>/ with its
checksum.txt) unpacked safely, and version folders deleted the way they came. Plain Python only (no other program
module): used by the updater (files/self_update.py) and by the mover (scripts/mover.py, its own small exe).
NEVER TOUCHES WHAT ISN'T SPIDERWEB'S: unpacking goes into a folder of its own, renamed only once whole; a version
folder is deleted only by its own checksum.txt (anything else put there stays)."""

import os
import secrets
import shutil
import stat

EXE = "Spiderweb.exe"
LIST = "checksum.txt"  # (each version folder's list of its files: size, SHA-256, place; build_folder.py)
UNPACKED_MOST = 2 << 30  # (what a release zip may unpack to: ~150 MB now)
TOP = "Spiderweb/"
TOP_FILES = ("LICENSE", "README.txt", "THIRD-PARTY-NOTICES.txt", "SPIDERWEB-NOTES-FORMAT.md")  # (beside the exe)


class Refused(Exception):
    """Why it can't go on: the end of an "updates.fail.*" text key (disk, bad_file, too_big, cancelled)."""


def remove(path):
    """Delete a file, read-only ones too. Raises OSError."""
    try:
        os.remove(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)


def inside(root, rel):
    """root/rel when it stays inside root (no "..", no drive or absolute part), else None."""
    rel = rel.replace("\\", "/")
    if not rel or rel.startswith("/") or ":" in rel or any(p in ("", ".", "..") for p in rel.split("/")):
        return None
    return os.path.join(root, *rel.split("/"))


def listed(folder, strict=False):
    """{place: (size, sha256)} from a version folder's checksum.txt; None when it has none (= not ours), or (strict)
    when it is damaged (see read_list)."""
    try:
        with open(os.path.join(folder, LIST), encoding="utf-8", newline="") as f:
            return read_list(f.read(), strict)
    except (OSError, UnicodeDecodeError):
        return None


def read_list(text, strict=False):
    """{place: (size, sha256)} from a checksum.txt's text ("<size>\\t<sha256>\\t<place>"; "<size>\\t<place>" too).
    Lines that don't read are skipped; strict = None instead (the list is damaged), also when it is empty or doesn't
    end with a line break (cut short). Strict is for checking files; deleting / unpacking take what reads."""
    out = {}
    if strict and not text.endswith("\n"):
        return None
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit() and parts[2]:
            out[parts[2].replace("\\", "/")] = (int(parts[0]), parts[1])
        elif len(parts) == 2 and parts[0].isdigit() and parts[1]:
            out[parts[1].replace("\\", "/")] = (int(parts[0]), "")
        elif strict:
            return None
    if strict and not out:
        return None
    return out


def remove_listed(folder):
    """Deletes a version folder the way it came: only the files its own checksum.txt lists (and the list itself), then
    the folders left empty, deepest first. Anything else someone put there stays, with its folders. True = all gone."""
    files = listed(folder)
    if files is None:
        return False
    for rel in files:
        p = inside(folder, rel)
        if p and os.path.isfile(p):
            try:
                remove(p)
            except OSError:
                pass
    try:
        remove(os.path.join(folder, LIST))
    except OSError:
        pass
    for d, _, _ in sorted(os.walk(folder), key=lambda w: -len(w[0])):
        try:
            os.rmdir(d)  # (only empty ones)
        except OSError:
            pass
    return not os.path.exists(folder)


def whole(folder):
    """Every file its checksum.txt lists is there with its size (no list = not whole)."""
    files = listed(folder)
    if not files:
        return False
    for rel, (size, _) in files.items():
        p = inside(folder, rel)
        try:
            if p is None or os.path.getsize(p) != size:
                return False
        except OSError:
            return False
    return True


def retry(job, tries=25):
    """job() until it works (a file another program holds for a moment); True if it did."""
    import time
    for n in range(tries):
        try:
            job()
            return True
        except OSError:
            if n + 1 < tries:
                time.sleep(0.2)
    return False


def is_ours(data):
    """A data/ folder Spiderweb made: missing, empty, or holding version.txt / version folders with a checksum.txt."""
    try:
        names = os.listdir(data)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if not names or "version.txt" in names:
        return True
    return any(listed(os.path.join(data, n)) is not None for n in names)


def unpack(zip_path, version, data, new_exe, cancel=None, keep=None, top_to=None, progress=None):
    """The checked zip's data/<version>/ into data/<version>.part-<random>/, sizes checked against its checksum.txt,
    renamed data/<version>/ (hunt 2026-11-26: a data/<version> already there and whole is KEPT; one not whole is set
    aside under an unpacking's name, never deleted in place, never `keep`); its
    Spiderweb.exe saved as new_exe; top_to = a folder for README + licences (None: left as they are). progress(done,
    all) in files. Raises Refused; nothing left behind then (the part folder and new_exe deleted)."""
    import zipfile
    inner = "%sdata/%s/" % (TOP, version)
    stage = os.path.join(data, "%s.part-%s" % (version, secrets.token_hex(4)))
    final = os.path.join(data, version)
    try:
        with zipfile.ZipFile(zip_path) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if sum(i.file_size for i in infos) > UNPACKED_MOST:
                raise Refused("too_big")
            names = {i.filename for i in infos}
            if TOP + EXE not in names or inner + LIST not in names:
                raise Refused("bad_file")
            tops = []
            for n, i in enumerate(infos):
                if cancel is not None and cancel.is_set():
                    raise Refused("cancelled")
                if i.filename == TOP + EXE:
                    dest = new_exe
                elif i.filename.startswith(inner):
                    dest = inside(stage, i.filename[len(inner):])
                elif top_to and i.filename[len(TOP):] in TOP_FILES and i.filename.startswith(TOP):
                    tops.append(i)
                    continue
                else:
                    continue
                if dest is None:
                    raise Refused("bad_file")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with z.open(i) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                if progress:
                    progress(n + 1, len(infos))
            for rel, (size, _) in (listed(stage) or {}).items():
                p = inside(stage, rel)
                if p is None or not os.path.isfile(p) or os.path.getsize(p) != size:
                    raise Refused("bad_file")
            if os.path.isdir(final) and whole(final):  # (already there and whole: kept, never deleted + replaced;
                drop(stage)                              # it may be in use by a Spiderweb started from it)
            else:
                if os.path.isdir(final):  # (an earlier try's, not whole: set aside whole under an unpacking's
                    if os.path.normcase(final) == os.path.normcase(keep or ""):  # own name, cleaned later;
                        raise Refused("disk")                                     # never deleted in place)
                    if not retry(lambda: os.rename(final, "%s.part-%s" % (final, secrets.token_hex(4)))):
                        raise Refused("disk")
                if not retry(lambda: os.rename(stage, final)):  # (an antivirus / cloud folder may hold it a moment)
                    raise Refused("disk")
            for i in tops:  # (after the folder is in place: a failing readme never stops it)
                try:
                    with z.open(i) as src, open(os.path.join(top_to, i.filename[len(TOP):]), "wb") as out:
                        shutil.copyfileobj(src, out)
                except OSError:
                    pass
    except Refused:
        drop(stage, new_exe)
        raise
    except (OSError, zipfile.BadZipFile, EOFError):
        drop(stage, new_exe)
        raise Refused("disk") from None
    return new_exe


def drop(stage, new_exe=None):
    shutil.rmtree(stage, ignore_errors=True)  # (an unpacking's own folder: only what it wrote)
    if new_exe:
        try:
            remove(new_exe)
        except OSError:
            pass
