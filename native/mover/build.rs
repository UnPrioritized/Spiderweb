// The icon / version info / manifest (.res from build_release.py) linked in when given.
fn main() {
    if let Ok(res) = std::env::var("SPIDERWEB_RES") {
        println!("cargo:rustc-link-arg={}", res);
    }
    for v in ["SPIDERWEB_RES", "SPIDERWEB_VERSION", "SPIDERWEB_PUBLIC_KEY", "SPIDERWEB_TEXTS"] {
        println!("cargo:rerun-if-env-changed={v}");
    }
}
