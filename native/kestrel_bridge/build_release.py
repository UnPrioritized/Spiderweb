"""Builds the connector (cargo build --release) with the builder's folder paths taken out of the file.

Rust keeps source file paths inside the built file (for its error messages); plain builds would carry the
builder's home folder (and so their user name). This swaps them for short neutral names:
home folder -> "home", cargo's folder -> "cargo", the folder above the spiderweb repo -> "src".
Run: python build_release.py [extra cargo arguments]
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def forms(path):
    """The path as written with backslashes and with forward slashes (rustc may see either)."""
    path = os.path.normpath(path)
    out = [path]
    if os.sep == "\\":
        out.append(path.replace("\\", "/"))
    return out


def main():
    home = os.path.expanduser("~")
    cargo = os.environ.get("CARGO_HOME") or os.path.join(home, ".cargo")
    above_repo = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
    # rustc applies the LAST matching remap: broad first, more exact after.
    flags = []
    for path, name in ((home, "home"), (above_repo, "src"), (cargo, "cargo")):
        for p in forms(path):
            flags.append("--remap-path-prefix=%s=%s" % (p, name))
    old = os.environ.get("CARGO_ENCODED_RUSTFLAGS", "")
    env = dict(os.environ)
    env["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(([old] if old else []) + flags)
    env.pop("RUSTFLAGS", None)
    cmd = ["cargo", "build", "--release"] + sys.argv[1:]
    return subprocess.call(cmd, cwd=HERE, env=env)


if __name__ == "__main__":
    sys.exit(main())
