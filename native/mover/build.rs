// The icon / version info / manifest (.res from build_release.py) linked in when given.
// Windows' own DLLs (WinHTTP, BCrypt...) come from System32 only, never from the folder the mover sits in (it is
// run from Downloads: a planted winhttp.dll there ran inside it; hunt 2026-11-26).
fn main() {
    println!("cargo:rustc-link-arg=/DEPENDENTLOADFLAG:0x800");
    if let Ok(res) = std::env::var("SPIDERWEB_RES") {
        println!("cargo:rustc-link-arg={}", res);
    }
    for v in ["SPIDERWEB_RES", "SPIDERWEB_VERSION", "SPIDERWEB_PUBLIC_KEY", "SPIDERWEB_TEXTS"] {
        println!("cargo:rerun-if-env-changed={v}");
    }
}
