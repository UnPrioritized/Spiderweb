"""Builds Spiderweb's compiled loops (cargo build --release) and puts the file where Spiderweb looks for it:
scripts/loops/x64/spiderweb_loops.dll (Windows), scripts/loops/linux-x64/libspiderweb_loops.so (Linux),
scripts/loops/mac/libspiderweb_loops.dylib (Mac). Needs Rust (https://rustup.rs); no other downloads.

Rust keeps source file paths inside the built file (for its error messages); plain builds would carry the
builder's home folder (and so their user name). This swaps them for short neutral names:
home folder -> "home", cargo's folder -> "cargo", the folder above the spiderweb repo -> "src".
Run: python build_release.py [extra cargo arguments]
Linux release builds: SW_ZIG_TARGET=x86_64-unknown-linux-gnu.2.17 builds with cargo-zigbuild against that old
glibc, so the .so loads on old Linux too (dev/harness/linux_build_loops.sh sets it and checks the result).
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts")


def forms(path):
    """The path as written with backslashes and with forward slashes (rustc may see either)."""
    path = os.path.normpath(path)
    out = [path]
    if os.sep == "\\":
        out.append(path.replace("\\", "/"))
    return out


def built_and_place(triple=None):
    """(the built file in target/[triple/]release, where Spiderweb looks for it) for this system."""
    if sys.platform == "win32":
        name, sub, out = "spiderweb_loops.dll", "x64", "spiderweb_loops.dll"
    elif sys.platform == "darwin":
        name, sub, out = "libspiderweb_loops.dylib", "mac", "libspiderweb_loops.dylib"
    else:
        name, sub, out = "libspiderweb_loops.so", "linux-x64", "libspiderweb_loops.so"
    built = os.path.join(HERE, "target", *([triple] if triple else []), "release", name)
    return built, os.path.join(SCRIPTS, "loops", sub, out)


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
    zig = os.environ.get("SW_ZIG_TARGET", "")
    if zig:
        cmd = ["cargo", "zigbuild", "--release", "--target", zig] + sys.argv[1:]
    else:
        cmd = ["cargo", "build", "--release"] + sys.argv[1:]
    code = subprocess.call(cmd, cwd=HERE, env=env)
    if code:
        return code
    built, place = built_and_place(zig.rsplit(".", 2)[0] if zig.count(".") >= 2 else (zig or None))
    os.makedirs(os.path.dirname(place), exist_ok=True)
    tmp = place + ".tmp"
    shutil.copyfile(built, tmp)
    os.replace(tmp, place)  # (fails while a running Spiderweb has the file loaded: close it first)
    print("Copied to", os.path.relpath(place, os.path.dirname(SCRIPTS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
