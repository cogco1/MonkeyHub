//! #373: one desktop window per runtime root.
//!
//! A window launch takes a Windows named mutex derived from its runtime root before it
//! creates a window or starts Hub, and keeps it until the process exits. The owner listens
//! on a message-only window whose class name carries the same hash. A repeated launch on
//! that root asks the owner to bring its main window forward, says so in a native notice
//! and exits; it opens no window, log or Hub. Other roots have other names, so explicit
//! independent roots still run side by side. Hub's `hub.lock` remains the second guard.
use std::path::Path;

#[cfg(windows)]
pub use native::{attach, claim};

/// Non-Windows builds have no single-instance check.
#[cfg(not(windows))]
pub fn claim(_runtime_root: &Path, _trial: bool) -> Result<bool, String> {
    Ok(true)
}

#[cfg(not(windows))]
pub fn attach(_window: &tauri::WebviewWindow, _log: crate::DiagnosticLog) {}

/// One text per directory: no `\\?\` prefix, backslashes, no trailing separator, lower case.
#[cfg_attr(not(windows), allow(dead_code))]
fn normalise(path: &Path) -> String {
    let text = path.as_os_str().to_string_lossy().replace('/', "\\");
    let text = if let Some(share) = text.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{share}")
    } else if let Some(local) = text.strip_prefix(r"\\?\") {
        local.to_owned()
    } else {
        text
    };
    text.trim_end_matches('\\').to_lowercase()
}

#[cfg(windows)]
mod native {
    use super::normalise;
    use crate::DiagnosticLog;
    use std::{
        ffi::c_void,
        fs, io,
        path::Path,
        ptr,
        sync::{mpsc, Mutex, MutexGuard, OnceLock, PoisonError},
        thread,
        time::{Duration, Instant},
    };

    const SWITCHED: &str =
        "MonkeyHub 已在运行，已切换到打开的窗口。\nMonkeyHub is already running; switched to its window.";
    const STARTING: &str = "MonkeyHub 已在运行，可能仍在启动。请稍候并使用已打开的窗口。\nMonkeyHub is already running and may still be starting. Please wait and use its open window.";
    /// The update helper starts a trial only after the previous desktop has exited; this
    /// covers the moment in which Windows hands over the abandoned lock.
    const TRIAL_WAIT_MS: u32 = 10_000;
    /// How long a repeated launch looks for the owner's activation window while it starts.
    const FIND_OWNER: Duration = Duration::from_secs(3);
    /// How long the owner may take to bring its window forward before the notice appears.
    const ACTIVATE_TIMEOUT_MS: u32 = 5_000;

    type Handle = *mut c_void;
    type WindowProcedure = unsafe extern "system" fn(Handle, u32, usize, isize) -> isize;

    const HWND_MESSAGE: Handle = -3isize as Handle;
    const BCRYPT_SHA256_ALG_HANDLE: Handle = 0x41 as Handle;
    const WAIT_OBJECT_0: u32 = 0;
    const WAIT_ABANDONED: u32 = 0x80;
    const WAIT_TIMEOUT: u32 = 0x102;
    const ERROR_ACCESS_DENIED: i32 = 5;
    const SW_SHOW: i32 = 5;
    const SW_RESTORE: i32 = 9;
    const SMTO_ABORTIFHUNG: u32 = 0x2;
    const MB_OK: u32 = 0x0;
    const MB_ICONINFORMATION: u32 = 0x40;
    const MB_SETFOREGROUND: u32 = 0x10000;

    /// WNDCLASSEXW
    #[repr(C)]
    struct WindowClass {
        size: u32,
        style: u32,
        procedure: Option<WindowProcedure>,
        class_extra: i32,
        window_extra: i32,
        instance: Handle,
        icon: Handle,
        cursor: Handle,
        background: Handle,
        menu_name: *const u16,
        class_name: *const u16,
        small_icon: Handle,
    }

    /// MSG
    #[repr(C)]
    struct Message {
        window: Handle,
        message: u32,
        wparam: usize,
        lparam: isize,
        time: u32,
        point: [i32; 2],
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateMutexW(attributes: *const c_void, initial_owner: i32, name: *const u16) -> Handle;
        fn WaitForSingleObject(handle: Handle, milliseconds: u32) -> u32;
        fn CloseHandle(handle: Handle) -> i32;
        fn GetModuleHandleW(name: *const u16) -> Handle;
    }

    #[link(name = "user32")]
    extern "system" {
        fn RegisterWindowMessageW(name: *const u16) -> u32;
        fn RegisterClassExW(class: *const WindowClass) -> u16;
        fn CreateWindowExW(
            extended_style: u32,
            class_name: *const u16,
            window_name: *const u16,
            style: u32,
            x: i32,
            y: i32,
            width: i32,
            height: i32,
            parent: Handle,
            menu: Handle,
            instance: Handle,
            parameter: *const c_void,
        ) -> Handle;
        fn DefWindowProcW(window: Handle, message: u32, wparam: usize, lparam: isize) -> isize;
        fn GetMessageW(message: *mut Message, window: Handle, first: u32, last: u32) -> i32;
        fn DispatchMessageW(message: *const Message) -> isize;
        fn PostMessageW(window: Handle, message: u32, wparam: usize, lparam: isize) -> i32;
        fn SendMessageTimeoutW(
            window: Handle,
            message: u32,
            wparam: usize,
            lparam: isize,
            flags: u32,
            timeout: u32,
            result: *mut usize,
        ) -> isize;
        fn FindWindowExW(
            parent: Handle,
            after: Handle,
            class_name: *const u16,
            window_name: *const u16,
        ) -> Handle;
        fn GetWindowThreadProcessId(window: Handle, process_id: *mut u32) -> u32;
        fn AllowSetForegroundWindow(process_id: u32) -> i32;
        fn SetForegroundWindow(window: Handle) -> i32;
        fn IsWindow(window: Handle) -> i32;
        fn IsIconic(window: Handle) -> i32;
        fn IsWindowVisible(window: Handle) -> i32;
        fn ShowWindow(window: Handle, command: i32) -> i32;
        fn MessageBoxW(window: Handle, text: *const u16, caption: *const u16, flags: u32) -> i32;
    }

    #[link(name = "bcrypt")]
    extern "system" {
        fn BCryptHash(
            algorithm: Handle,
            secret: *const u8,
            secret_length: u32,
            input: *const u8,
            input_length: u32,
            output: *mut u8,
            output_length: u32,
        ) -> i32;
    }

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(Some(0)).collect()
    }

    pub(super) fn sha256(data: &[u8]) -> Result<[u8; 32], String> {
        let mut digest = [0u8; 32];
        let length = u32::try_from(data.len()).map_err(|_| "Input too long to hash")?;
        let status = unsafe {
            BCryptHash(
                BCRYPT_SHA256_ALG_HANDLE,
                ptr::null(),
                0,
                data.as_ptr(),
                length,
                digest.as_mut_ptr(),
                32,
            )
        };
        if status != 0 {
            return Err(format!("Windows SHA-256 failed with NTSTATUS {status:#x}"));
        }
        Ok(digest)
    }

    /// The first 32 hex digits of the SHA-256 of the canonical runtime root.
    pub(super) fn instance_key(runtime_root: &Path) -> Result<String, String> {
        let resolved = fs::canonicalize(runtime_root).unwrap_or_else(|_| runtime_root.into());
        Ok(sha256(normalise(&resolved).as_bytes())?[..16]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect())
    }

    pub(super) fn mutex_name(key: &str) -> String {
        format!(r"Local\MonkeyHub-{key}")
    }

    fn class_name(key: &str) -> String {
        format!("MonkeyHub.Activation.{key}")
    }

    fn activation_message() -> u32 {
        static MESSAGE: OnceLock<u32> = OnceLock::new();
        *MESSAGE.get_or_init(|| unsafe {
            RegisterWindowMessageW(wide("MonkeyHub.Activate").as_ptr())
        })
    }

    /// True when the calling thread now owns the named lock, including one abandoned by a
    /// desktop that exited. The handle stays open, and the lock owned, until this process
    /// exits, so call it on the thread that lives as long as the process.
    pub(super) fn acquire(name: &str, wait_ms: u32) -> Result<bool, String> {
        let name = wide(name);
        unsafe {
            let handle = CreateMutexW(ptr::null(), 0, name.as_ptr());
            if handle.is_null() {
                let error = io::Error::last_os_error();
                // A desktop running elevated owns a lock this process may not open.
                if error.raw_os_error() == Some(ERROR_ACCESS_DENIED) {
                    return Ok(false);
                }
                return Err(format!("Cannot create the single-instance lock: {error}"));
            }
            match WaitForSingleObject(handle, wait_ms) {
                WAIT_OBJECT_0 | WAIT_ABANDONED => Ok(true),
                WAIT_TIMEOUT => {
                    CloseHandle(handle);
                    Ok(false)
                }
                _ => {
                    let error = io::Error::last_os_error();
                    CloseHandle(handle);
                    Err(format!("Cannot take the single-instance lock: {error}"))
                }
            }
        }
    }

    /// Ok(true): this process owns the runtime root. Ok(false): another desktop owns it; it
    /// was asked to come forward and the person was told, so this launch must exit. An
    /// update trial never asks or tells: it fails, and the update helper rolls back.
    pub fn claim(runtime_root: &Path, trial: bool) -> Result<bool, String> {
        let key = instance_key(runtime_root)?;
        if acquire(&mutex_name(&key), if trial { TRIAL_WAIT_MS } else { 0 })? {
            listen(&key);
            return Ok(true);
        }
        if trial {
            return Err(format!(
                "Another MonkeyHub desktop is still using runtime directory {}; the update trial did not start.",
                runtime_root.display()
            ));
        }
        bring_owner_forward(&key);
        Ok(false)
    }

    struct Activation {
        /// The owner's main window, once built.
        window: isize,
        /// This process's activation window.
        listener: isize,
        /// A request arrived before the main window existed.
        pending: bool,
        /// Why the activation window could not start, logged once the log exists.
        failure: Option<String>,
        log: Option<DiagnosticLog>,
    }

    static ACTIVATION: Mutex<Activation> = Mutex::new(Activation {
        window: 0,
        listener: 0,
        pending: false,
        failure: None,
        log: None,
    });

    fn state() -> MutexGuard<'static, Activation> {
        ACTIVATION.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Starts the owner's message-only window on its own thread and message loop, so its
    /// window procedure never runs on the Tauri main thread.
    fn listen(key: &str) {
        let class = wide(&class_name(key));
        let (started, running) = mpsc::channel::<()>();
        let spawned = thread::Builder::new()
            .name("monkeyhub-activation".into())
            .spawn(move || unsafe {
                let module = GetModuleHandleW(ptr::null());
                let window_class = WindowClass {
                    size: std::mem::size_of::<WindowClass>() as u32,
                    style: 0,
                    procedure: Some(procedure),
                    class_extra: 0,
                    window_extra: 0,
                    instance: module,
                    icon: ptr::null_mut(),
                    cursor: ptr::null_mut(),
                    background: ptr::null_mut(),
                    menu_name: ptr::null(),
                    class_name: class.as_ptr(),
                    small_icon: ptr::null_mut(),
                };
                let listener = if RegisterClassExW(&window_class) == 0 {
                    ptr::null_mut()
                } else {
                    CreateWindowExW(
                        0,
                        class.as_ptr(),
                        ptr::null(),
                        0,
                        0,
                        0,
                        0,
                        0,
                        HWND_MESSAGE,
                        ptr::null_mut(),
                        module,
                        ptr::null(),
                    )
                };
                if listener.is_null() {
                    let error = io::Error::last_os_error();
                    state().failure = Some(format!("Cannot create the activation window: {error}"));
                    return;
                }
                state().listener = listener as isize;
                let _ = started.send(());
                let mut message: Message = std::mem::zeroed();
                while GetMessageW(&mut message, ptr::null_mut(), 0, 0) > 0 {
                    DispatchMessageW(&message);
                }
            });
        match spawned {
            // Usually a millisecond; a repeated launch also looks for a few seconds.
            Ok(_) => drop(running.recv_timeout(Duration::from_secs(2))),
            Err(error) => {
                state().failure = Some(format!("Cannot start the activation window: {error}"));
            }
        }
    }

    unsafe extern "system" fn procedure(
        window: Handle,
        message: u32,
        wparam: usize,
        lparam: isize,
    ) -> isize {
        let activate = activation_message();
        if activate != 0 && message == activate {
            return isize::from(bring_to_front(wparam as u32));
        }
        DefWindowProcW(window, message, wparam, lparam)
    }

    /// Runs on the activation thread. Win32 on the main window is safe from here: the Tauri
    /// main thread keeps pumping its own messages, and no Tauri call is made.
    fn bring_to_front(requester: u32) -> bool {
        let (window, log) = {
            let mut state = state();
            if state.window == 0 {
                state.pending = true;
                return false;
            }
            (state.window as Handle, state.log.clone())
        };
        unsafe {
            if IsWindow(window) == 0 {
                return false;
            }
            let minimized = IsIconic(window) != 0;
            if minimized {
                ShowWindow(window, SW_RESTORE);
            } else if IsWindowVisible(window) == 0 {
                ShowWindow(window, SW_SHOW);
            }
            let foreground = SetForegroundWindow(window) != 0;
            // The repeated launch's notice may then appear above the window it brought forward.
            if requester != 0 {
                AllowSetForegroundWindow(requester);
            }
            if let Some(log) = log {
                log.write(&format!(
                    "event=activate minimized={minimized} foreground={foreground}"
                ));
            }
        }
        true
    }

    /// Hands the owner's main window to its activation window. A request that arrived while
    /// the window was being built is answered now.
    pub fn attach(window: &tauri::WebviewWindow, log: DiagnosticLog) {
        let main = match window.hwnd() {
            Ok(main) => main.0 as isize,
            Err(error) => {
                log.write(&format!(
                    "event=single-instance-warning detail=The main window has no native handle: {error}"
                ));
                return;
            }
        };
        let (listener, pending, failure) = {
            let mut state = state();
            state.window = main;
            state.log = Some(log.clone());
            (
                state.listener,
                std::mem::take(&mut state.pending),
                state.failure.take(),
            )
        };
        if listener == 0 {
            let failure = failure.unwrap_or_else(|| "The activation window has not started".into());
            log.write(&format!(
                "event=single-instance-warning detail={failure}; a repeated launch cannot bring this window forward"
            ));
        } else if pending {
            unsafe {
                PostMessageW(listener as Handle, activation_message(), 0, 0);
            }
        }
    }

    /// A repeated launch: find the owner's activation window (it may still be starting),
    /// let it take the foreground, wait for it to come forward, then tell the person.
    fn bring_owner_forward(key: &str) {
        let class = wide(&class_name(key));
        let deadline = Instant::now() + FIND_OWNER;
        let owner = loop {
            let found =
                unsafe { FindWindowExW(HWND_MESSAGE, ptr::null_mut(), class.as_ptr(), ptr::null()) };
            if !found.is_null() || Instant::now() >= deadline {
                break found;
            }
            thread::sleep(Duration::from_millis(100));
        };
        let mut switched = false;
        if !owner.is_null() {
            unsafe {
                let mut process = 0;
                GetWindowThreadProcessId(owner, &mut process);
                if process != 0 {
                    AllowSetForegroundWindow(process);
                }
                let mut answer = 0usize;
                let delivered = SendMessageTimeoutW(
                    owner,
                    activation_message(),
                    std::process::id() as usize,
                    0,
                    SMTO_ABORTIFHUNG,
                    ACTIVATE_TIMEOUT_MS,
                    &mut answer,
                );
                switched = delivered != 0 && answer == 1;
            }
        }
        let text = wide(if switched { SWITCHED } else { STARTING });
        let caption = wide("MonkeyHub");
        unsafe {
            MessageBoxW(
                ptr::null_mut(),
                text.as_ptr(),
                caption.as_ptr(),
                MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND,
            );
        }
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::native::{acquire, instance_key, mutex_name, sha256};
    use super::normalise;
    use std::{
        fs,
        path::{Path, PathBuf},
        process::Command,
        sync::mpsc,
        thread,
        time::{Duration, Instant},
    };
    use uuid::Uuid;

    struct Directory(PathBuf);
    impl Directory {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "MonkeyHub single instance 测试 {}",
                Uuid::new_v4()
            ));
            fs::create_dir_all(root.join("Runtime")).unwrap();
            fs::create_dir_all(root.join("Other")).unwrap();
            Self(root)
        }
        fn key(&self, relative: &str) -> String {
            instance_key(&self.0.join(relative)).unwrap()
        }
    }
    impl Drop for Directory {
        fn drop(&mut self) {
            let _ = fs::remove_dir(self.0.join("Link")); // the junction only, not its target
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn hex(bytes: &[u8]) -> String {
        bytes.iter().map(|byte| format!("{byte:02x}")).collect()
    }

    #[test]
    fn sha256_matches_the_published_vectors() {
        for (input, digest) in [
            (
                &b""[..],
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ),
            (
                b"abc",
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            ),
            (
                b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
                "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
            ),
        ] {
            assert_eq!(hex(&sha256(input).unwrap()), digest);
        }
    }

    #[test]
    fn verbatim_prefixes_separators_and_case_are_normalised() {
        for spelling in [
            r"C:\Users\Asus\AppData\Local\MonkeyHub",
            r"c:\users\asus\appdata\local\monkeyhub\",
            r"\\?\C:\Users\Asus\AppData\Local\MonkeyHub",
            r"\\?\C:\Users\Asus\AppData\Local\MonkeyHub\\",
            "C:/Users/Asus/AppData/Local/MonkeyHub/",
        ] {
            assert_eq!(
                normalise(Path::new(spelling)),
                r"c:\users\asus\appdata\local\monkeyhub",
                "{spelling}"
            );
        }
        for spelling in [
            r"\\?\UNC\Server\Share\MonkeyHub",
            r"\\server\share\MonkeyHub\",
            "//Server/Share/MonkeyHub",
        ] {
            assert_eq!(
                normalise(Path::new(spelling)),
                r"\\server\share\monkeyhub",
                "{spelling}"
            );
        }
        assert_eq!(normalise(Path::new(r"\\?\D:\")), "d:");
    }

    #[test]
    fn the_same_directory_spelled_differently_has_one_lock() {
        let directory = Directory::new();
        let runtime = directory.0.join("Runtime");
        let text = runtime.to_str().unwrap();
        let key = directory.key("Runtime");
        assert_eq!(key.len(), 32);
        assert!(key
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)));
        assert_eq!(mutex_name(&key), format!(r"Local\MonkeyHub-{key}"));
        for spelling in [
            text.to_uppercase(),
            text.to_lowercase(),
            format!(r"{text}\"),
            text.replace('\\', "/"),
            format!(r"\\?\{text}"),
            fs::canonicalize(&runtime).unwrap().to_string_lossy().into_owned(),
        ] {
            assert_eq!(instance_key(Path::new(&spelling)).unwrap(), key, "{spelling}");
        }
        // A junction to the directory is the same directory.
        let link = directory.0.join("Link");
        let created = Command::new("cmd")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&link)
            .arg(&runtime)
            .output()
            .unwrap();
        assert!(created.status.success(), "{created:?}");
        assert_eq!(directory.key("Link"), key);
    }

    #[test]
    fn different_roots_have_different_locks() {
        let directory = Directory::new();
        let runtime = directory.key("Runtime");
        for other in ["Other", "", r"Runtime\Nested"] {
            assert_ne!(directory.key(other), runtime, "{other}");
        }
    }

    #[test]
    fn a_held_lock_is_refused_and_an_abandoned_one_is_taken_within_the_wait() {
        let directory = Directory::new();
        let name = mutex_name(&directory.key("Runtime"));
        let (held, owned) = mpsc::channel();
        let (release, released) = mpsc::channel::<()>();
        let owner_name = name.clone();
        let owner = thread::spawn(move || {
            held.send(acquire(&owner_name, 0).unwrap()).unwrap();
            released.recv().unwrap();
            // The thread ends while it owns the lock, as a desktop's main thread does.
        });
        assert!(owned.recv().unwrap());
        assert!(!acquire(&name, 0).unwrap(), "a held lock was taken");
        let started = Instant::now();
        assert!(!acquire(&name, 300).unwrap(), "a held lock was taken");
        assert!(started.elapsed() >= Duration::from_millis(250));
        let releaser = thread::spawn(move || {
            thread::sleep(Duration::from_millis(300));
            release.send(()).unwrap();
        });
        let started = Instant::now();
        assert!(
            acquire(&name, 10_000).unwrap(),
            "an abandoned lock was not taken"
        );
        assert!(started.elapsed() < Duration::from_secs(5));
        releaser.join().unwrap();
        owner.join().unwrap();
        let other = mutex_name(&directory.key("Other"));
        assert!(acquire(&other, 0).unwrap(), "another root shares the lock");
    }
}
