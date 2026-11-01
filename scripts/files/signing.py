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


def read_list(data):
    """(version, {file name: sha256 hex}) from a list's bytes; ValueError when it isn't one."""
    lines = data.decode("ascii").split("\n")
    if not lines or lines[0] != HEADER or len(lines) < 3 or not lines[1].startswith("version "):
        raise ValueError("not an update list")
    version = lines[1][len("version "):]
    hashes = {}
    for line in lines[2:]:
        if not line:
            continue
        parts = line.split(" ", 2)
        if len(parts) != 3 or parts[0] != "sha256" or len(parts[1]) != 64:
            raise ValueError("bad line in update list")
        hashes[parts[2]] = parts[1].lower()
    return version, hashes


def check(list_bytes, sig_text, public_key=None):
    """(version, hashes) when sig_text is a valid signature of list_bytes by the public key; ValueError if not."""
    from nacl.signing import VerifyKey
    from nacl.exceptions import BadSignatureError
    key = public_key or PUBLIC_KEY
    if not key:
        raise ValueError("no public key in this version")
    try:
        sig = base64.b64decode(sig_text.strip(), validate=True)
        VerifyKey(base64.b64decode(key)).verify(list_bytes, sig)
    except (BadSignatureError, ValueError, TypeError) as e:
        raise ValueError("bad signature") from e
    return read_list(list_bytes)
