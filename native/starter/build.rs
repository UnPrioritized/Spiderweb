// The icon, version info and manifest (a .res made by rc.exe in build_release.py), linked in when given.
// Windows' own DLLs come from System32 only, never from the folder the exe sits in (hunt 2026-11-26: a planted
// copy beside it would run inside it).
fn main() {
    println!("cargo:rustc-link-arg=/DEPENDENTLOADFLAG:0x800");
    if let Ok(res) = std::env::var("SPIDERWEB_RES") {
        println!("cargo:rustc-link-arg={}", res);
    }
    println!("cargo:rerun-if-env-changed=SPIDERWEB_RES");
}
