use std::{env, path::Path, process::Command};

fn main() {
    println!("cargo:rerun-if-env-changed=ARCHFLOW_SOURCE_REVISION");
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let revision = env::var("ARCHFLOW_SOURCE_REVISION").unwrap_or_else(|_| {
        let output = Command::new("git").arg("-C").arg(&root)
            .args(["rev-parse", "HEAD"]).output().expect("git or ARCHFLOW_SOURCE_REVISION is required");
        assert!(output.status.success(), "cannot resolve source revision");
        for name in ["HEAD", "refs/heads"] {
            let path = Command::new("git").arg("-C").arg(&root)
                .args(["rev-parse", "--path-format=absolute", "--git-path", name]).output().unwrap();
            println!("cargo:rerun-if-changed={}", String::from_utf8_lossy(&path.stdout).trim());
        }
        String::from_utf8(output.stdout).unwrap().trim().to_owned()
    });
    assert!(revision.len() == 40 && revision.bytes().all(|b| b.is_ascii_hexdigit()),
        "ARCHFLOW_SOURCE_REVISION must be a full Git commit");
    println!("cargo:rustc-env=ARCHFLOW_SOURCE_REVISION={}", revision.to_lowercase());
    tauri_build::build();
}
