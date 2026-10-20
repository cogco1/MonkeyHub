//! 工作坊模式 (#668): the desktop reads the switch from the account's settings file and asks for
//! the same port on every launch.
use monkeyhub_desktop::{preferred_port, workshop_mode_in, WORKSHOP_PORT};
use std::{
    fs,
    net::{Ipv4Addr, TcpListener},
    path::PathBuf,
};
use uuid::Uuid;

struct Settings(PathBuf);
impl Settings {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("MonkeyHub workshop 测试 {}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        Self(root)
    }
    fn with(&self, bytes: &[u8]) -> PathBuf {
        let path = self.0.join("settings.json");
        fs::write(&path, bytes).unwrap();
        path
    }
}
impl Drop for Settings {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn only_a_saved_true_turns_workshop_mode_on() {
    let settings = Settings::new();
    assert!(!workshop_mode_in(&settings.0.join("settings.json")), "no file");
    assert!(workshop_mode_in(&settings.with(br#"{"language": "zh-CN", "workshopMode": true}"#)));
    assert!(workshop_mode_in(&settings.with(b"\xEF\xBB\xBF{\"workshopMode\": true}")), "a byte-order mark");
    for off in [
        &br#"{"language": "zh-CN"}"#[..],
        br#"{"workshopMode": false}"#,
        br#"{"workshopMode": "true"}"#,
        br#"{"workshopMode": 1}"#,
        br#"[{"workshopMode": true}]"#,
        b"{not json",
        b"",
    ] {
        assert!(!workshop_mode_in(&settings.with(off)), "{}", String::from_utf8_lossy(off));
    }
}

#[test]
fn the_preferred_port_is_used_while_it_is_free() {
    let held = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let port = held.local_addr().unwrap().port();
    let other = preferred_port(port).unwrap();
    assert_ne!(other, port, "a port in use is not taken");
    assert_ne!(other, 0);
    drop(held);
    assert_eq!(preferred_port(port).unwrap(), port);
    assert_eq!(WORKSHOP_PORT, 8790);
}
