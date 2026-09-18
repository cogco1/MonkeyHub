use monkeyhub_desktop::{
    available_port, fetch_health, hub_reports_worker_origin, is_status_url, verify_source_revision,
    DiagnosticLog, ExpectedIdentity, HealthError, HubHealth, LaunchConfig, OwnedRuntime,
    SOURCE_REVISION,
};
use std::{
    fs,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::PathBuf,
    process::Command,
    thread,
    time::{Duration, Instant},
};
use uuid::Uuid;

struct Fixture {
    root: PathBuf,
    config: LaunchConfig,
}
impl Fixture {
    fn new(options: serde_json::Value) -> Self {
        let root = std::env::temp_dir().join(format!("monkeyhub-desktop-test-{}", Uuid::new_v4()));
        let source = root.join("source");
        let runtime = root.join("runtime");
        fs::create_dir_all(source.join("apps/monkeyhub")).unwrap();
        fs::create_dir_all(&runtime).unwrap();
        fs::write(
            source.join("apps/monkeyhub/run.py"),
            include_str!("fake_hub.py"),
        )
        .unwrap();
        fs::write(source.join("source-version.txt"), "a".repeat(40)).unwrap();
        fs::write(source.join("fixture.json"), options.to_string()).unwrap();
        let python = std::env::var_os("MONKEYHUB_TEST_PYTHON").unwrap_or_else(|| "python".into());
        let output = Command::new(python)
            .args(["-c", "import sys; print(sys.executable)"])
            .output()
            .expect("Python is needed for the isolated process tests");
        assert!(output.status.success());
        let config = LaunchConfig {
            source_root: source,
            python: PathBuf::from(String::from_utf8(output.stdout).unwrap().trim()),
            runtime_root: runtime,
            port: available_port().unwrap(),
            startup_timeout: Duration::from_secs(10),
            source_revision: "a".repeat(40),
        };
        Self { root, config }
    }
    fn spawn(&self) -> OwnedRuntime {
        let instance = Uuid::new_v4();
        OwnedRuntime::spawn(
            &self.config,
            instance,
            DiagnosticLog::open(&self.config.runtime_root, &instance.to_string()).unwrap(),
        )
        .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn wait_ready(runtime: &mut OwnedRuntime) {
    let started = Instant::now();
    while started.elapsed() < Duration::from_secs(10) {
        assert!(
            runtime.try_wait().unwrap().is_none(),
            "root crashed before ready"
        );
        if runtime.identity.check().is_ok() {
            return;
        }
        thread::sleep(Duration::from_millis(40));
    }
    panic!("isolated root did not become ready");
}
fn wait_exit(runtime: &mut OwnedRuntime) -> std::process::ExitStatus {
    let started = Instant::now();
    while started.elapsed() < Duration::from_secs(10) {
        if let Some(status) = runtime.try_wait().unwrap() {
            return status;
        }
        thread::sleep(Duration::from_millis(40));
    }
    panic!("isolated root did not exit after stdin shutdown");
}

fn read_request_headers(stream: &mut TcpStream) {
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    let mut request = Vec::new();
    while !request.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
        let mut chunk = [0; 256];
        let count = stream.read(&mut chunk).unwrap();
        assert!(
            count > 0,
            "client closed before completing its request headers"
        );
        request.extend_from_slice(&chunk[..count]);
        assert!(request.len() <= 2048, "oversized test request headers");
    }
}

fn expected() -> ExpectedIdentity {
    ExpectedIdentity {
        process_id: 123,
        parent_process_id: 456,
        instance_id: Uuid::new_v4().to_string(),
        source_revision: "a".repeat(40),
        port: 18790,
    }
}
fn health(identity: &ExpectedIdentity) -> HubHealth {
    HubHealth {
        status: "ok".into(),
        service: "monkeyhub-api".into(),
        server_version: "0.1.0".into(),
        process_id: identity.process_id,
        parent_process_id: identity.parent_process_id,
        managed_instance_id: identity.instance_id.clone(),
        source_revision: identity.source_revision.clone(),
    }
}

#[test]
fn exact_identity_and_protocol_are_required() {
    let identity = expected();
    assert!(identity.verify(&health(&identity)).is_ok());
    for field in [
        "pid", "parent", "instance", "source", "protocol", "service", "status",
    ] {
        let mut response = health(&identity);
        match field {
            "pid" => response.process_id += 1,
            "parent" => response.parent_process_id += 1,
            "instance" => response.managed_instance_id = Uuid::new_v4().to_string(),
            "source" => response.source_revision = "b".repeat(40),
            "protocol" => response.server_version = "2.0.0".into(),
            "service" => response.service = "studio".into(),
            _ => response.status = "starting".into(),
        }
        assert!(
            identity.verify(&response).is_err(),
            "accepted mismatching {field}"
        );
    }
    assert!(verify_source_revision(&"a".repeat(40), &"b".repeat(40)).is_err());
}

#[test]
fn navigation_is_limited_to_the_exact_hub_origin() {
    let identity = expected();
    for url in [
        "http://127.0.0.1:18790/",
        "http://127.0.0.1:18790/?project=p",
        "http://127.0.0.1:18790/settings",
    ] {
        assert!(identity.allows_url(&url.parse().unwrap()));
    }
    for url in [
        "https://127.0.0.1:18790/",
        "http://127.0.0.1:18791/",
        "http://localhost:18790/",
        "http://127.0.0.1.evil:18790/",
        "http://user@127.0.0.1:18790/",
        "https://example.com/",
        "file:///C:/Windows/win.ini",
        "data:text/html,hello",
    ] {
        assert!(
            !identity.allows_url(&url.parse().unwrap()),
            "accepted {url}"
        );
    }
    assert!(is_status_url(
        &"http://tauri.localhost/index.html".parse().unwrap()
    ));
    assert!(!is_status_url(
        &"http://tauri.localhost:9999/index.html".parse().unwrap()
    ));
    assert!(!is_status_url(
        &"http://tauri.localhost/unrelated.html".parse().unwrap()
    ));
}

#[test]
fn owned_root_drains_and_foreign_root_remains_alive() {
    let fixture = Fixture::new(serde_json::json!({"drain_seconds": 0.5}));
    let foreign_fixture = Fixture::new(serde_json::json!({}));
    let mut owned = fixture.spawn();
    let mut foreign = foreign_fixture.spawn();
    wait_ready(&mut owned);
    wait_ready(&mut foreign);
    owned.request_stop();
    assert!(owned.try_wait().unwrap().is_none());
    assert!(wait_exit(&mut owned).success());
    assert_eq!(
        fs::read_to_string(fixture.config.runtime_root.join("operation-finished")).unwrap(),
        "retained"
    );
    assert!(
        foreign.identity.check().is_ok(),
        "stopping our root affected another instance"
    );
    foreign.request_stop();
    assert!(wait_exit(&mut foreign).success());
    // Reopen uses the same directory and retains prior completed output.
    let mut reopened = fixture.spawn();
    wait_ready(&mut reopened);
    assert!(fixture
        .config
        .runtime_root
        .join("operation-finished")
        .is_file());
    reopened.request_stop();
    assert!(wait_exit(&mut reopened).success());
}

#[test]
fn drop_closes_stdin_and_spawn_and_crash_errors_are_observable() {
    let fixture = Fixture::new(serde_json::json!({}));
    let mut runtime = fixture.spawn();
    wait_ready(&mut runtime);
    drop(runtime);
    let started = Instant::now();
    while !fixture
        .config
        .runtime_root
        .join("operation-finished")
        .exists()
        && started.elapsed() < Duration::from_secs(5)
    {
        thread::sleep(Duration::from_millis(30));
    }
    assert!(fixture
        .config
        .runtime_root
        .join("operation-finished")
        .exists());
    let crash = Fixture::new(serde_json::json!({"crash": true}));
    let crash_instance = Uuid::new_v4();
    let crash_log =
        DiagnosticLog::open(&crash.config.runtime_root, &crash_instance.to_string()).unwrap();
    let mut runtime =
        OwnedRuntime::spawn(&crash.config, crash_instance, crash_log.clone()).unwrap();
    assert_eq!(wait_exit(&mut runtime).code(), Some(17));
    let tail = crash_log.tail().unwrap();
    assert!(tail.contains("RuntimeError: fixture runtime directory is already in use"));
    assert!(!tail.contains("before-tail"));
    assert!(tail.len() <= 8192);
    assert!(tail.lines().count() <= 24);
    let mut config = fixture.config.clone();
    config.python = fixture.root.join("missing-python.exe");
    let instance = Uuid::new_v4();
    assert!(OwnedRuntime::spawn(
        &config,
        instance,
        DiagnosticLog::open(&config.runtime_root, &instance.to_string()).unwrap()
    )
    .err()
    .unwrap()
    .contains("Cannot start Hub"));
}

#[test]
fn occupied_port_is_not_adopted_or_stopped() {
    let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = thread::spawn(move || {
        for _ in 0..2 {
            let (mut stream, _) = listener.accept().unwrap();
            read_request_headers(&mut stream);
            let body = serde_json::json!({"status":"ok", "service":"monkeyhub-api", "serverVersion":"0.1.0", "processId":999, "parentProcessId":998, "managedInstanceId":"foreign", "sourceRevision":"a".repeat(40)}).to_string();
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            )
            .unwrap();
        }
    });
    let mut fixture = Fixture::new(serde_json::json!({}));
    fixture.config.port = port;
    let mut runtime = fixture.spawn();
    let result = runtime.identity.check();
    assert!(
        matches!(&result, Err(HealthError::Rejected(_))),
        "expected a valid foreign identity to be rejected; got {result:?}"
    );
    runtime.request_stop();
    assert!(!wait_exit(&mut runtime).success()); // Its own bind failed; no foreign-process termination.
    assert_eq!(fetch_health(port).unwrap().managed_instance_id, "foreign");
    server.join().unwrap();
}

#[test]
fn popup_requires_a_current_healthy_worker_origin() {
    let snapshot = serde_json::json!({"workers": [
        {"url": "http://127.0.0.1:18791/", "healthy": true, "processId": 123},
        {"url": "http://127.0.0.1:18792/", "healthy": false, "processId": 124}
    ]});
    assert!(hub_reports_worker_origin(
        &snapshot,
        &"http://127.0.0.1:18791/?compare=a".parse().unwrap()
    ));
    for url in [
        "http://127.0.0.1:18792/",
        "http://127.0.0.1:18793/",
        "https://127.0.0.1:18791/",
        "http://user@127.0.0.1:18791/",
        "https://example.com/",
    ] {
        assert!(!hub_reports_worker_origin(&snapshot, &url.parse().unwrap()));
    }
    assert!(!hub_reports_worker_origin(
        &serde_json::json!({"workers": []}),
        &"http://127.0.0.1:18791/".parse().unwrap()
    ));
}

#[test]
fn invalid_runtime_destination_does_not_modify_source_or_project() {
    let fixture = Fixture::new(serde_json::json!({}));
    for web in ["apps/monkeyhub/web/dist"] {
        fs::create_dir_all(fixture.config.source_root.join(web)).unwrap();
        fs::write(
            fixture.config.source_root.join(web).join("index.html"),
            "test",
        )
        .unwrap();
    }
    fs::write(
        fixture.config.source_root.join("source-version.txt"),
        SOURCE_REVISION,
    )
    .unwrap();
    let source_destination = fixture.config.source_root.join("must-not-create/cache");
    let project_root = fixture.root.join("project");
    fs::create_dir(&project_root).unwrap();
    fs::write(project_root.join("project.json"), "{}").unwrap();
    for destination in [
        &source_destination,
        &project_root.join("must-not-create/cache"),
    ] {
        let args = [
            "--source-root".into(),
            fixture.config.source_root.clone().into_os_string(),
            "--python".into(),
            fixture.config.python.clone().into_os_string(),
            "--runtime-root".into(),
            destination.clone().into_os_string(),
        ];
        assert!(LaunchConfig::from_args(args).is_err());
        assert!(
            !destination.parent().unwrap().exists(),
            "created an invalid cache directory before rejection"
        );
    }
}

#[test]
fn a_partial_health_response_cannot_hold_startup_forever() {
    let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        read_request_headers(&mut stream);
        let _ = stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{");
        for _ in 0..5 {
            thread::sleep(Duration::from_millis(250));
            if stream.write_all(b" ").is_err() {
                break;
            }
        }
    });
    let started = Instant::now();
    assert!(matches!(
        fetch_health(port),
        Err(HealthError::Unavailable(_))
    ));
    assert!(started.elapsed() < Duration::from_secs(2));
    server.join().unwrap();
}
