//! One-shot handoff between sibling, exact-version desktop installations.
//! The Hub owns admission, staging and shortcut activation; this code owns only
//! its children and never replaces an installed file or terminates a process.
use crate::{
    fetch_json, hide_console, read_source_revision, DiagnosticLog, ExpectedIdentity, LaunchConfig,
};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{BufRead, BufReader, Read, Write},
    net::{Ipv4Addr, SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

pub fn requested_target(identity: &ExpectedIdentity) -> Result<Option<String>, String> {
    identity.check().map_err(|e| format!("{e:?}"))?;
    let response =
        fetch_json(identity.port, "/api/updates/restart", 8192).map_err(|e| format!("{e:?}"))?;
    match response.get("targetCommit") {
        Some(serde_json::Value::Null) => Ok(None),
        Some(serde_json::Value::String(commit)) if valid_commit(commit) => Ok(Some(commit.clone())),
        _ => Err("Invalid update restart response".into()),
    }
}

fn valid_commit(value: &str) -> bool {
    value.len() == 40
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// No request can supply an executable or an arbitrary path. Junctions cannot
/// redirect a staged sibling outside the installation's versions directory.
pub fn target_directory(source: &Path, commit: &str) -> Result<PathBuf, String> {
    if !valid_commit(commit) {
        return Err("Update target must be a full lowercase Git commit".into());
    }
    let source = fs::canonicalize(source).map_err(|e| e.to_string())?;
    let current = read_source_revision(&source)?;
    if source.file_name().and_then(|v| v.to_str()) != Some(&format!("{}-desktop", &current[..12])) {
        return Err("Updates require a versioned desktop installation".into());
    }
    let versions = source
        .parent()
        .ok_or("Installation has no versions directory")?;
    let name = format!("{}-desktop", &commit[..12]);
    let target =
        fs::canonicalize(versions.join(&name)).map_err(|e| format!("Update is not staged: {e}"))?;
    if target.parent() != Some(versions)
        || target.file_name().and_then(|v| v.to_str()) != Some(&name)
    {
        return Err("Update target must remain inside its exact sibling directory".into());
    }
    if read_source_revision(&target)? != commit {
        return Err("Staged update source revision does not match the request".into());
    }
    for relative in [
        "MonkeyHub.exe",
        "_runtime/python/python.exe",
        "apps/monkeyhub/run.py",
        "apps/monkeyhub/web/dist/index.html",
    ] {
        if !target.join(relative).is_file() {
            return Err(format!("Staged update is incomplete: {relative}"));
        }
    }
    Ok(target)
}

pub fn launch_helper(config: &LaunchConfig, commit: &str) -> Result<(), String> {
    target_directory(&config.source_root, commit)?;
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    if executable
        .parent()
        .and_then(|p| fs::canonicalize(p).ok())
        .as_ref()
        != Some(&config.source_root)
    {
        return Err("A checkout host cannot restart an installed update".into());
    }
    let mut command = Command::new(executable);
    hide_console(&mut command);
    command
        .args(["--complete-update", commit, "--runtime-root"])
        .arg(&config.runtime_root)
        .args([
            "--wait-for-desktop",
            &std::process::id().to_string(),
            "--startup-timeout-seconds",
            &config.startup_timeout.as_secs().to_string(),
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("Cannot start update handoff: {e}"))?;
    Ok(())
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TrialReady {
    event: String,
    port: u16,
    process_id: u32,
    parent_process_id: u32,
    managed_instance_id: String,
    source_revision: String,
}

impl TrialReady {
    pub fn for_identity(identity: &ExpectedIdentity) -> Self {
        Self {
            event: "update-ready".into(),
            port: identity.port,
            process_id: identity.process_id,
            parent_process_id: identity.parent_process_id,
            managed_instance_id: identity.instance_id.clone(),
            source_revision: identity.source_revision.clone(),
        }
    }

    pub fn verify(&self, desktop_pid: u32, commit: &str) -> Result<ExpectedIdentity, String> {
        if self.event != "update-ready"
            || self.parent_process_id != desktop_pid
            || self.source_revision != commit
            || self.process_id == 0
            || self.port == 0
            || uuid::Uuid::parse_str(&self.managed_instance_id).is_err()
        {
            return Err("Trial desktop did not identify its exact owned Hub".into());
        }
        let identity = ExpectedIdentity {
            process_id: self.process_id,
            parent_process_id: desktop_pid,
            instance_id: self.managed_instance_id.clone(),
            source_revision: commit.into(),
            port: self.port,
        };
        identity
            .check()
            .map_err(|e| format!("Trial Hub health failed: {e:?}"))?;
        Ok(identity)
    }
}

/// The pipe is private to the launching helper. EOF before commit is an orderly
/// stop, including when the helper crashes. No deadline forcibly kills a child.
pub fn await_trial_decision(mut reader: impl BufRead) -> bool {
    let mut line = String::new();
    matches!(reader.read_line(&mut line), Ok(n) if n > 0) && line.trim() == "commit"
}

fn post_update(
    identity: &ExpectedIdentity,
    path: &str,
    payload: serde_json::Value,
) -> Result<(), String> {
    identity.check().map_err(|e| format!("{e:?}"))?;
    let payload = payload.to_string();
    let deadline = Instant::now() + Duration::from_secs(75);
    let mut socket = TcpStream::connect_timeout(
        &SocketAddr::from((Ipv4Addr::LOCALHOST, identity.port)),
        Duration::from_secs(2),
    )
    .map_err(|e| e.to_string())?;
    socket
        .set_write_timeout(Some(Duration::from_secs(2)))
        .map_err(|e| e.to_string())?;
    write!(socket, "POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nOrigin: http://127.0.0.1:{}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", identity.port, identity.port, payload.len(), payload).map_err(|e| e.to_string())?;
    let mut response = Vec::new();
    loop {
        socket
            .set_read_timeout(Some(
                deadline
                    .checked_duration_since(Instant::now())
                    .ok_or("Update activation timed out")?,
            ))
            .map_err(|e| e.to_string())?;
        let mut chunk = [0u8; 4096];
        let count = socket.read(&mut chunk).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        if response.len() + count > 128 * 1024 {
            return Err("Oversized update activation response".into());
        }
        response.extend_from_slice(&chunk[..count]);
    }
    let status = std::str::from_utf8(&response)
        .map_err(|e| e.to_string())?
        .lines()
        .next()
        .unwrap_or("");
    if status.split_whitespace().nth(1) != Some("200") {
        return Err(format!("Update activation returned {status}"));
    }
    Ok(())
}

pub fn reject_restart(identity: &ExpectedIdentity, target: &str) -> Result<(), String> {
    post_update(
        identity,
        "/api/updates/rollback",
        serde_json::json!({"fromCommit": identity.source_revision, "targetCommit": target}),
    )
}

fn trial_command(directory: &Path, config: &LaunchConfig) -> Command {
    let mut command = Command::new(directory.join("MonkeyHub.exe"));
    hide_console(&mut command);
    command
        .arg("--runtime-root")
        .arg(&config.runtime_root)
        .args([
            "--startup-timeout-seconds",
            &config.startup_timeout.as_secs().to_string(),
            "--update-trial",
        ])
        .current_dir(directory)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    command
}

fn wait_trial(
    child: &mut Child,
    timeout: Duration,
    commit: &str,
) -> Result<ExpectedIdentity, String> {
    let stdout = child
        .stdout
        .take()
        .ok_or("Trial desktop has no ready pipe")?;
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let mut bytes = Vec::new();
        let result = BufReader::new(stdout)
            .take(8193)
            .read_until(b'\n', &mut bytes)
            .map_err(|e| e.to_string())
            .and_then(|_| {
                if bytes.len() > 8192 {
                    return Err("Oversized trial handshake".into());
                }
                serde_json::from_slice::<TrialReady>(&bytes).map_err(|e| e.to_string())
            });
        let _ = sender.send(result);
    });
    let ready = receiver
        .recv_timeout(timeout)
        .map_err(|_| "Trial desktop did not become ready before its startup deadline")??;
    if child.try_wait().map_err(|e| e.to_string())?.is_some() {
        return Err("Trial desktop exited before activation".into());
    }
    ready.verify(child.id(), commit)
}

fn stop_trial(child: &mut Child) -> Result<(), String> {
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(b"stop\n");
        let _ = stdin.flush();
    }
    child
        .wait()
        .map_err(|e| format!("Cannot observe trial desktop shutdown: {e}"))?;
    Ok(())
}

/// Called only after the previous Hub exited and released its runtime lease.
pub fn complete_handoff(
    config: &LaunchConfig,
    commit: &str,
    old_desktop: u32,
    log: &DiagnosticLog,
) -> Result<(), String> {
    wait_for_desktop_exit(old_desktop)?;
    log.state(
        "updating",
        &format!("Starting exact staged desktop {commit}"),
    );
    let trial = target_directory(&config.source_root, commit).and_then(|target| {
        trial_command(&target, config)
            .spawn()
            .map_err(|e| e.to_string())
    });
    let result = match trial {
        Ok(mut child) => {
            let activation = wait_trial(
                &mut child,
                config.startup_timeout + Duration::from_secs(5),
                commit,
            )
            .and_then(|identity| {
                let payload = serde_json::json!({"fromCommit": config.source_revision});
                // Completion is idempotent, including a lost HTTP response.
                post_update(&identity, "/api/updates/complete", payload.clone())
                    .or_else(|_| post_update(&identity, "/api/updates/complete", payload))
            });
            match activation {
                Ok(()) => {
                    let release = child
                        .stdin
                        .take()
                        .ok_or_else(|| "Trial control pipe is closed".to_owned())
                        .and_then(|mut pipe| {
                            pipe.write_all(b"commit\n")
                                .and_then(|_| pipe.flush())
                                .map_err(|e| e.to_string())
                        });
                    if release.is_ok() {
                        log.state(
                            "updated",
                            "New desktop verified and application entry activated",
                        );
                        return Ok(());
                    }
                    stop_trial(&mut child)?;
                    release
                }
                Err(error) => {
                    stop_trial(&mut child)?;
                    Err(error)
                }
            }
        }
        Err(error) => Err(format!("Cannot start staged desktop: {error}")),
    };
    let detail = result.unwrap_err();
    log.state("update-failed", &detail);
    // The failed trial has fully exited; the original bundle remains untouched.
    let mut rollback = trial_command(&config.source_root, config)
        .spawn()
        .map_err(|e| format!("{detail}; cannot reopen previous desktop: {e}"))?;
    let restored = wait_trial(
        &mut rollback,
        config.startup_timeout + Duration::from_secs(5),
        &config.source_revision,
    )
    .and_then(|identity| {
        reject_restart(&identity, commit).or_else(|_| reject_restart(&identity, commit))
    });
    match restored {
        Ok(()) => {
            if let Some(mut pipe) = rollback.stdin.take() {
                pipe.write_all(b"commit\n")
                    .and_then(|_| pipe.flush())
                    .map_err(|e| e.to_string())?;
            } else {
                return Err("Previous desktop control pipe is closed".into());
            }
            log.state(
                "rolled-back",
                "Previous desktop verified and its application entry restored",
            );
            Ok(())
        }
        Err(error) => {
            stop_trial(&mut rollback)?;
            Err(format!("{detail}; previous version recovery failed: {error}. Installed files were preserved."))
        }
    }
}

#[cfg(windows)]
fn wait_for_desktop_exit(pid: u32) -> Result<(), String> {
    use std::ffi::c_void;
    #[link(name = "kernel32")]
    extern "system" {
        fn OpenProcess(access: u32, inherit: i32, pid: u32) -> *mut c_void;
        fn WaitForSingleObject(handle: *mut c_void, milliseconds: u32) -> u32;
        fn CloseHandle(handle: *mut c_void) -> i32;
        fn GetLastError() -> u32;
    }
    if pid == 0 || pid == std::process::id() {
        return Err("Invalid previous desktop PID".into());
    }
    unsafe {
        let handle = OpenProcess(0x00100000, 0, pid); // SYNCHRONIZE only, never terminate access.
        if handle.is_null() {
            return if GetLastError() == 87 {
                Ok(())
            } else {
                Err("Cannot wait for previous desktop exit".into())
            };
        }
        let result = WaitForSingleObject(handle, 60000);
        CloseHandle(handle);
        if result == 0 {
            Ok(())
        } else {
            Err("Previous desktop has not finished closing; update was not started".into())
        }
    }
}

#[cfg(not(windows))]
fn wait_for_desktop_exit(_pid: u32) -> Result<(), String> {
    Err("Desktop updates currently require Windows".into())
}
