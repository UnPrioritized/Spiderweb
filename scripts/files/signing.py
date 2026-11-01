"""Signed updates. Each release carries Spiderweb-update.txt (the version + a SHA-256 of each download) and
Spiderweb-update.sig (its Ed25519 signature, base64). Only the public key is here: the private key is kept by the
author, never in the program or on GitHub, so files uploaded by anyone else can't pass check().
PyNaCl is optional (have_nacl() False = nothing can be checked; the caller falls back to the release page)."""
import base64
import hashlib

PUBLIC_KEY = "CLFuvA5REHg94cjGGYIZU1+V6wIeEP0rRYUjFfva1yQ="  # base64 Ed25519 public key, written once by dev/signing/keys.py new
LIST_NAME = "Spiderweb-update.txt"
SIG_NAME = "Spiderweb-update.sig"
HEADER = "spiderweb-update 1"


def have_nacl():
    try:
        import nacl.signing  # noqa: F401
        return True
    except ImportError:
        return False


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def make_list(version, hashes):
    """The bytes that get signed. hashes: {file name: sha256 hex}."""
    lines = [HEADER, "version " + version]
    lines += ["sha256 %s %s" % (hashes[name], name) for name in sorted(hashes)]
    return ("\n".join(lines) + "\n").encode("ascii")


class BadSignature(ValueError):
    """Not signed by our key (or no key here): someone else's file."""


class Unreadable(ValueError):
    """Signed by us, but in a list format this version doesn't know (a newer one): not an alarm."""


def read_list(data):
    """(version, {file name: sha256 hex}) from a list's bytes; Unreadable when it isn't one this version reads.
    Lines of kinds it doesn't know are SKIPPED (still signed): later versions may add some without old copies
    refusing the list. Never change what "version" / "sha256" lines mean: add new kinds instead."""
    try:
        lines = data.decode("ascii").split("\n")
    except UnicodeDecodeError:
        raise Unreadable("not ascii") from None
    if not lines or lines[0] != HEADER:
        raise Unreadable("not an update list this version reads")
    version, hashes = None, {}
    for line in lines[1:]:
        parts = line.split(" ", 2)
        if parts[0] == "version" and len(parts) == 2 and version is None:
            version = parts[1]
        elif parts[0] == "sha256" and len(parts) == 3 and len(parts[1]) == 64:
            hashes[parts[2]] = parts[1].lower()
    if version is None:
        raise Unreadable("no version line")
    return version, hashes


def check(list_bytes, sig_text, public_key=None):
    """(version, hashes) when sig_text is a valid signature of list_bytes by the public key. BadSignature if it
    isn't; Unreadable when it is but the list can't be read here."""
    from nacl.signing import VerifyKey
    from nacl.exceptions import BadSignatureError
    key = public_key or PUBLIC_KEY
    if not key:
        raise BadSignature("no public key in this version")
    try:
        sig = base64.b64decode(sig_text.strip(), validate=True)
        VerifyKey(base64.b64decode(key)).verify(list_bytes, sig)
    except (BadSignatureError, ValueError, TypeError) as e:
        raise BadSignature("bad signature") from e
    return read_list(list_bytes)
