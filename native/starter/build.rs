// The icon, version info and manifest (a .res made by rc.exe in build_release.py), linked in when given.
fn main() {
    if let Ok(res) = std::env::var("SPIDERWEB_RES") {
        println!("cargo:rustc-link-arg={}", res);
    }
    println!("cargo:rerun-if-env-changed=SPIDERWEB_RES");
}
