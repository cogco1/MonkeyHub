//! The desktop host owns one root process and its stdin, never project state.
use serde::Deserialize;
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, ExitStatus, Stdio},
    sync::{Arc, Mutex},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use url::Url;
use uuid::Uuid;

pub mod updates;

pub const SOURCE_REVISION: &str = env!("ARCHFLOW_SOURCE_REVISION");

#[derive(Clone, Debug)]
pub struct LaunchConfig {
    pub source_root: PathBuf,
    pub python: PathBuf,
    pub runtime_root: PathBuf,
    pub port: u16,
    pub startup_timeout: Duration,
    pub source_revision: String,
}

impl LaunchConfig {
    pub fn from_args(args: impl IntoIterator<Item = std::ffi::OsString>) -> Result<Self, String> {
        let mut source = None;
        let mut python = None;
        let mut runtime = None;
        let mut port = 0;
        let mut timeout = 60;
        let mut args = args.into_iter();
        while let Some(arg) = args.next() {
            let value = args
                .next()
                .ok_or_else(|| format!("{} requires a value", arg.to_string_lossy()))?;
            match arg.to_str() {
                Some("--source-root") => source = Some(PathBuf::from(value)),
                Some("--python") => python = Some(PathBuf::from(value)),
                Some("--runtime-root") => runtime = Some(PathBuf::from(value)),
                Some("--port") => {
                    port = value
                        .to_string_lossy()
                        .parse::<u16>()
                        .map_err(|_| "--port must be 0..65535")?
                }
                Some("--startup-timeout-seconds") => {
                    timeout = value
                        .to_string_lossy()
                        .parse::<u64>()
                        .map_err(|_| "invalid startup timeout")?;
                    if !(1..=600).contains(&timeout) {
                        return Err("startup timeout must be 1..600 seconds".into());
                    }
                }
                _ => return Err(format!("Unknown option: {}", arg.to_string_lossy())),
            }
        }
        // Release packages and explicit development roots use the same tree.
        let source_root = absolute_existing(
            source.unwrap_or(
                env::current_exe()
                    .map_err(|e| e.to_string())?
                    .parent()
                    .ok_or("EXE has no parent directory")?
                    .to_owned(),
            ),
            "source root",
        )?;
        let python = absolute_existing(
            python.unwrap_or_else(|| source_root.join("_runtime/python/python.exe")),
            "Python executable",
        )?;
        let runtime_root = match runtime {
            Some(path) => path,
            None => PathBuf::from(
                env::var_os("LOCALAPPDATA")
                    .ok_or("Set --runtime-root to an absolute nonproject directory")?,
            )
            .join("MonkeyHub"),
        };
        if !runtime_root.is_absolute() {
            return Err("runtime root must be absolute".into());
        }
        for relative in [
            "apps/monkeyhub/run.py",
            "apps/monkeyhub/web/dist/index.html",
        ] {
            if !source_root.join(relative).is_file() {
                return Err(format!(
                    "Incomplete application: missing {}",
                    source_root.join(relative).display()
                ));
            }
        }
        let source_revision = read_source_revision(&source_root)?;
        verify_source_revision(&source_revision, SOURCE_REVISION)?;
        // Resolve existing parent junctions before creating any directory.
        let runtime_root = resolve_directory_location(&runtime_root)?;
        if runtime_root.starts_with(&source_root) {
            return Err("runtime root must be outside installed application assets".into());
        }
        for ancestor in runtime_root.ancestors() {
            if ancestor.join("project.json").is_file() {
                return Err("runtime root must be outside a P036 project".into());
            }
        }
        fs::create_dir_all(&runtime_root)
            .map_err(|e| format!("Cannot create runtime directory: {e}"))?;
        if port == 0 {
            port = available_port()?;
        }
        Ok(Self {
            source_root,
            python,
            runtime_root,
            port,
            startup_timeout: Duration::from_secs(timeout),
            source_revision,
        })
    }

    pub fn url(&self) -> Url {
        Url::parse(&format!("http://127.0.0.1:{}/", self.port)).unwrap()
    }
}

fn resolve_directory_location(path: &Path) -> Result<PathBuf, String> {
    if path
        .components()
        .any(|part| part == std::path::Component::ParentDir)
    {
        return Err("runtime root must be an absolute path without '..' components".into());
    }
    let mut existing = path;
    let mut missing = Vec::new();
    while !existing.exists() {
        missing.push(existing.file_name().ok_or("Cannot resolve runtime root")?);
        existing = existing
            .parent()
            .ok_or("Cannot resolve runtime root parent")?;
    }
    let mut resolved =
        fs::canonicalize(existing).map_err(|e| format!("Cannot resolve runtime root: {e}"))?;
    for part in missing.iter().rev() {
        resolved.push(part);
    }
    Ok(resolved)
}

fn absolute_existing(path: PathBuf, label: &str) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err(format!("{label} must be absolute"));
    }
    fs::canonicalize(&path).map_err(|e| format!("Cannot locate {label} {}: {e}", path.display()))
}

pub fn verify_source_revision(actual: &str, expected: &str) -> Result<(), String> {
    if actual != expected {
        return Err(format!("Source version mismatch: desktop {expected}, runtime {actual}. Use the matching application bundle."));
    }
    Ok(())
}

pub fn read_source_revision(root: &Path) -> Result<String, String> {
    let marker = root.join("source-version.txt");
    let value = if marker.exists() {
        fs::read_to_string(marker).map_err(|e| format!("Cannot read source version: {e}"))?
    } else {
        let mut cmd = Command::new("git");
        hide_console(&mut cmd);
        let output = cmd
            .arg("-C")
            .arg(root)
            .args(["rev-parse", "HEAD"])
            .output()
            .map_err(|e| format!("Cannot resolve source version: {e}"))?;
        if !output.status.success() {
            return Err(
                "Source version unavailable; use a complete bundle or an explicit source checkout"
                    .into(),
            );
        }
        String::from_utf8(output.stdout).map_err(|e| e.to_string())?
    };
    let value = value
        .trim_start_matches('\u{feff}')
        .trim()
        .to_ascii_lowercase();
    if value.len() != 40 || !value.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("Invalid source-version.txt; expected a full Git commit".into());
    }
    Ok(value)
}

pub fn available_port() -> Result<u16, String> {
    TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
        .and_then(|listener| listener.local_addr())
        .map(|address| address.port())
        .map_err(|e| format!("Cannot allocate a loopback port: {e}"))
}

fn hide_console(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW, child retains its stdin pipe.
    }
    #[cfg(not(windows))]
    let _ = command;
}

#[derive(Clone)]
pub struct DiagnosticLog {
    pub path: PathBuf,
    file: Arc<Mutex<File>>,
}

impl DiagnosticLog {
    pub fn open(root: &Path, instance: &str) -> Result<Self, String> {
        let directory = root.join("logs");
        fs::create_dir_all(&directory).map_err(|e| e.to_string())?;
        let path = directory.join(format!("desktop-{instance}.log"));
        let file = OpenOptions::new()
            .create_new(true)
            .append(true)
            .open(&path)
            .map_err(|e| format!("Cannot open {}: {e}", path.display()))?;
        Ok(Self {
            path,
            file: Arc::new(Mutex::new(file)),
        })
    }
    pub fn write(&self, line: &str) {
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis();
        if let Ok(mut file) = self.file.lock() {
            let _ = writeln!(file, "{timestamp} {}", line.replace(['\r', '\n'], " "));
        }
    }
    pub fn state(&self, state: &str, detail: &str) {
        self.write(&format!("event=state state={state} detail={detail}"));
    }
    pub fn tail(&self) -> std::io::Result<String> {
        // A separate reader leaves the inherited child output handle untouched.
        let mut file = File::open(&self.path)?;
        let start = file.metadata()?.len().saturating_sub(8192);
        file.seek(SeekFrom::Start(start))?;
        let mut bytes = Vec::new();
        file.take(8192).read_to_end(&mut bytes)?;
        let text = String::from_utf8_lossy(&bytes);
        let mut lines: Vec<_> = text.lines().rev().take(24).collect();
        lines.reverse();
        Ok(lines.join("\n"))
    }
    fn child_output(&self) -> Result<Stdio, String> {
        self.file
            .lock()
            .map_err(|e| e.to_string())?
            .try_clone()
            .map(Stdio::from)
            .map_err(|e| e.to_string())
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HubHealth {
    pub status: String,
    pub service: String,
    pub server_version: String,
    pub process_id: u32,
    pub parent_process_id: u32,
    pub managed_instance_id: String,
    pub source_revision: String,
}

#[derive(Clone, Debug)]
pub struct ExpectedIdentity {
    pub process_id: u32,
    pub parent_process_id: u32,
    pub instance_id: String,
    pub source_revision: String,
    pub port: u16,
}

impl ExpectedIdentity {
    pub fn verify(&self, health: &HubHealth) -> Result<(), String> {
        if health.status != "ok"
            || health.service != "monkeyhub-api"
            || health.server_version != "0.1.0"
        {
            return Err(
                "Hub health/protocol mismatch; expected monkeyhub-api 0.1.0 with status ok".into(),
            );
        }
        if health.process_id != self.process_id
            || health.parent_process_id != self.parent_process_id
            || health.managed_instance_id != self.instance_id
        {
            return Err(format!("Hub instance mismatch on port {}: expected owned PID {} / parent {} / instance {}, received PID {} / parent {} / instance {}", self.port, self.process_id, self.parent_process_id, self.instance_id, health.process_id, health.parent_process_id, health.managed_instance_id));
        }
        verify_source_revision(&health.source_revision, &self.source_revision)
    }
    pub fn check(&self) -> Result<(), HealthError> {
        let health = fetch_health(self.port)?;
        self.verify(&health).map_err(HealthError::Rejected)
    }
    pub fn allows_url(&self, url: &Url) -> bool {
        url.scheme() == "http"
            && url.host_str() == Some("127.0.0.1")
            && url.port() == Some(self.port)
            && url.username().is_empty()
            && url.password().is_none()
    }

    pub fn allows_owned_page(&self, url: &Url) -> bool {
        if !is_loopback_http(url) || self.check().is_err() {
            return false;
        }
        self.allows_url(url)
            || fetch_json(self.port, "/api/runtime", 4 * 1024 * 1024)
                .is_ok_and(|snapshot| hub_reports_worker_origin(&snapshot, url))
    }
}

fn is_loopback_http(url: &Url) -> bool {
    url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
        && url.username().is_empty()
        && url.password().is_none()
        && url.port_or_known_default().is_some()
}

/// Membership comes from the already identified Hub, never from a page's claim.
pub fn hub_reports_worker_origin(snapshot: &serde_json::Value, url: &Url) -> bool {
    is_loopback_http(url)
        && snapshot
            .get("workers")
            .and_then(|rows| rows.as_array())
            .is_some_and(|rows| {
                rows.iter().any(|row| {
                    row.get("healthy").and_then(|value| value.as_bool()) == Some(true)
                        && row
                            .get("processId")
                            .and_then(|value| value.as_u64())
                            .is_some_and(|pid| pid > 0)
                        && row
                            .get("url")
                            .and_then(|value| value.as_str())
                            .and_then(|value| Url::parse(value).ok())
                            .is_some_and(|worker| {
                                is_loopback_http(&worker) && worker.origin() == url.origin()
                            })
                })
            })
}

#[derive(Debug)]
pub enum HealthError {
    Unavailable(String),
    Rejected(String),
}
impl std::fmt::Display for HealthError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unavailable(message) | Self::Rejected(message) => write!(f, "{message}"),
        }
    }
}

// A fixed GET to a numeric loopback address; no proxy, DNS or redirects.
pub fn fetch_health(port: u16) -> Result<HubHealth, HealthError> {
    serde_json::from_value(fetch_json(port, "/api/health", 65536)?)
        .map_err(|e| HealthError::Rejected(format!("Invalid Hub health JSON: {e}")))
}

fn fetch_json(port: u16, path: &str, limit: usize) -> Result<serde_json::Value, HealthError> {
    let unavailable = |e: std::io::Error| HealthError::Unavailable(e.to_string());
    let timeout = Duration::from_millis(700);
    let deadline = Instant::now() + timeout;
    let address = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let mut socket = TcpStream::connect_timeout(&address, timeout).map_err(unavailable)?;
    socket
        .set_read_timeout(Some(timeout))
        .map_err(unavailable)?;
    socket
        .set_write_timeout(Some(timeout))
        .map_err(unavailable)?;
    write!(socket, "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\nAccept: application/json\r\n\r\n").map_err(unavailable)?;
    let mut response = Vec::new();
    loop {
        let remaining = deadline
            .checked_duration_since(Instant::now())
            .ok_or_else(|| HealthError::Unavailable("Hub HTTP response timed out".into()))?;
        socket
            .set_read_timeout(Some(remaining))
            .map_err(unavailable)?;
        let mut chunk = [0u8; 4096];
        let count = socket.read(&mut chunk).map_err(unavailable)?;
        if count == 0 {
            break;
        }
        if response.len() + count > limit {
            return Err(HealthError::Rejected("Oversized Hub HTTP response".into()));
        }
        response.extend_from_slice(&chunk[..count]);
    }
    let split = response
        .windows(4)
        .position(|bytes| bytes == b"\r\n\r\n")
        .ok_or_else(|| HealthError::Rejected("Malformed Hub health response".into()))?;
    let headers = std::str::from_utf8(&response[..split])
        .map_err(|e| HealthError::Rejected(e.to_string()))?;
    let status = headers.lines().next().unwrap_or("");
    if status.split_whitespace().nth(1) != Some("200") {
        return Err(HealthError::Rejected(format!(
            "Hub health returned {status}"
        )));
    }
    serde_json::from_slice(&response[split + 4..])
        .map_err(|e| HealthError::Rejected(format!("Invalid Hub health JSON: {e}")))
}

pub struct OwnedRuntime {
    child: Child,
    pub identity: ExpectedIdentity,
    log: DiagnosticLog,
}

impl OwnedRuntime {
    pub fn spawn(
        config: &LaunchConfig,
        instance: Uuid,
        log: DiagnosticLog,
    ) -> Result<Self, String> {
        let mut command = Command::new(&config.python);
        hide_console(&mut command);
        command
            .arg("-u")
            .arg(config.source_root.join("apps/monkeyhub/run.py"))
            .arg("--runtime-root")
            .arg(&config.runtime_root)
            .arg("--port")
            .arg(config.port.to_string())
            .args([
                "--managed-stdin",
                "--managed-instance-id",
                &instance.to_string(),
                "--no-browser",
            ])
            .current_dir(&config.source_root)
            .stdin(Stdio::piped())
            .stdout(log.child_output()?)
            .stderr(log.child_output()?);
        let child = command
            .spawn()
            .map_err(|e| format!("Cannot start Hub with {}: {e}", config.python.display()))?;
        let identity = ExpectedIdentity {
            process_id: child.id(),
            parent_process_id: std::process::id(),
            instance_id: instance.to_string(),
            source_revision: config.source_revision.clone(),
            port: config.port,
        };
        log.write(&format!(
            "event=start pid={} url={} instance={} source={}",
            child.id(),
            config.url(),
            instance,
            config.source_revision
        ));
        Ok(Self {
            child,
            identity,
            log,
        })
    }
    pub fn try_wait(&mut self) -> Result<Option<ExitStatus>, String> {
        self.child.try_wait().map_err(|e| e.to_string())
    }
    pub fn request_stop(&mut self) {
        // Taking and closing the sole writer also delivers EOF after a failed write.
        if let Some(mut stdin) = self.child.stdin.take() {
            self.log
                .write(&format!("event=stop-request pid={}", self.child.id()));
            let _ = stdin.write_all(b"stop\n");
            let _ = stdin.flush();
        }
    }
}

impl Drop for OwnedRuntime {
    fn drop(&mut self) {
        self.request_stop();
    }
}

pub fn is_status_url(url: &Url) -> bool {
    let origin = (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
        || (url.scheme() == "http"
            && url.host_str() == Some("tauri.localhost")
            && url.port().is_none());
    origin
        && matches!(url.path(), "/" | "/index.html")
        && url.username().is_empty()
        && url.password().is_none()
}
