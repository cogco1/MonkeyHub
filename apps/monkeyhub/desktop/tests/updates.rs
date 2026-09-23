use monkeyhub_desktop::updates::{await_trial_decision, target_directory, TrialReady};
use monkeyhub_desktop::ExpectedIdentity;
use std::{
    fs,
    io::{Cursor, Read, Write},
    net::TcpListener,
    path::PathBuf,
    thread,
};
use uuid::Uuid;

struct Installation {
    root: PathBuf,
    old: PathBuf,
    next: PathBuf,
}
impl Installation {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("hub-update-test-{}", Uuid::new_v4()));
        let old = root.join(format!("{}-desktop", "a".repeat(12)));
        let next = root.join(format!("{}-desktop", "b".repeat(12)));
        for (path, commit) in [(&old, "a"), (&next, "b")] {
            for relative in [
                "MonkeyHub.exe",
                "_runtime/python/python.exe",
                "apps/monkeyhub/run.py",
                "apps/monkeyhub/web/dist/index.html",
            ] {
                let file = path.join(relative);
                fs::create_dir_all(file.parent().unwrap()).unwrap();
                fs::write(file, b"fixture").unwrap();
            }
            fs::write(path.join("source-version.txt"), commit.repeat(40)).unwrap();
        }
        Self { root, old, next }
    }
}
impl Drop for Installation {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[test]
fn update_can_only_select_an_exact_complete_sibling_bundle() {
    let fixture = Installation::new();
    assert_eq!(
        target_directory(&fixture.old, &"b".repeat(40)).unwrap(),
        fs::canonicalize(&fixture.next).unwrap()
    );
    for value in [
        "../../arbitrary.exe".into(),
        "b".repeat(12),
        "B".repeat(40),
        "c".repeat(40),
    ] {
        assert!(target_directory(&fixture.old, &value).is_err());
    }
    fs::write(fixture.next.join("source-version.txt"), "c".repeat(40)).unwrap();
    assert!(target_directory(&fixture.old, &"b".repeat(40)).is_err());
    fs::write(fixture.next.join("source-version.txt"), "b".repeat(40)).unwrap();
    fs::remove_file(fixture.next.join("apps/monkeyhub/run.py")).unwrap();
    assert!(target_directory(&fixture.old, &"b".repeat(40)).is_err());
    assert_eq!(
        fs::read(fixture.old.join("MonkeyHub.exe")).unwrap(),
        b"fixture"
    );
}

#[test]
fn trial_stops_on_helper_loss_stop_or_unknown_input_and_only_commit_opens_ui() {
    for input in ["", "stop\n", "ready\n", "commit-other\n"] {
        assert!(!await_trial_decision(Cursor::new(input)));
    }
    assert!(await_trial_decision(Cursor::new("commit\n")));
}

#[test]
fn trial_handshake_must_match_spawned_desktop_and_independent_hub_health() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let identity = ExpectedIdentity {
        process_id: 123,
        parent_process_id: 456,
        instance_id: Uuid::new_v4().to_string(),
        source_revision: "b".repeat(40),
        port: listener.local_addr().unwrap().port(),
    };
    let ready = TrialReady::for_identity(&identity);
    assert!(ready.verify(999, &identity.source_revision).is_err());
    assert!(ready.verify(456, &"a".repeat(40)).is_err());
    let body =
        serde_json::json!({"status":"ok", "service":"monkeyhub-api", "serverVersion":"0.1.0",
        "processId":identity.process_id, "parentProcessId":identity.parent_process_id,
        "managedInstanceId":identity.instance_id, "sourceRevision":identity.source_revision})
        .to_string();
    let server = thread::spawn(move || {
        let (mut socket, _) = listener.accept().unwrap();
        let mut request = Vec::new();
        while !request.windows(4).any(|chunk| chunk == b"\r\n\r\n") {
            let mut chunk = [0u8; 2048];
            let count = socket.read(&mut chunk).unwrap();
            assert!(count > 0);
            request.extend_from_slice(&chunk[..count]);
        }
        write!(
            socket,
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        )
        .unwrap();
    });
    assert_eq!(
        ready
            .verify(456, &identity.source_revision)
            .unwrap()
            .process_id,
        123
    );
    server.join().unwrap();
}
