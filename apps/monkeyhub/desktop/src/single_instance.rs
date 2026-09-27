//! #373: one desktop window per runtime root.
//!
//! Before it creates a window, a log or Hub, a window launch takes a Windows named mutex
//! derived from its runtime root and keeps it until the process exits. The owner answers on a
//! message-only window whose class name carries the same hash. A later launch on that root
//! asks it what it is doing:
//! - running: the owner brings its main window forward, and the launch says so and exits;
//! - restarting for an update: the launch says MonkeyHub opens by itself, and exits;
//! - closing, still building its window, or already gone: the launch waits for the mutex
//!   without a notice and then starts as the running desktop. Only if that takes longer than
//!   30 seconds does it say that MonkeyHub is still closing or starting.
//!
//! Other roots have other names, so explicit independent roots still run side by side.
//! Hub's `hub.lock` remains the second guard.

#[cfg(windows)]
pub use native::{attach, claim, restarting_for_update};

/// Non-Windows builds have no single-instance check.
#[cfg(not(windows))]
pub fn claim(_runtime_root: &std::path::Path, _trial: bool) -> Result<bool, String> {
    Ok(true)
}

#[cfg(not(windows))]
pub fn attach(
    _window: &tauri::WebviewWindow,
    _log: crate::DiagnosticLog,
    _closing: impl Fn() -> bool + Send + Sync + 'static,
) {
}

#[cfg(not(windows))]
pub fn restarting_for_update() {}

#[cfg(windows)]
mod native {
    use crate::DiagnosticLog;
    use std::{
        ffi::{c_char, c_void},
        fs, io,
        path::Path,
        ptr,
        sync::{
            atomic::{AtomicBool, Ordering},
            mpsc, Arc, Mutex, MutexGuard, OnceLock, PoisonError,
        },
        thread,
        time::{Duration, Instant},
    };

    const SWITCHED: &str =
        "MonkeyHub 已在运行，已切换到打开的窗口。\nMonkeyHub is already running; switched to its window.";
    const RUNNING: &str =
        "MonkeyHub 已在运行，请使用已打开的窗口。\nMonkeyHub is already running; please use its open window.";
    const RESTARTING: &str = "MonkeyHub 正在为更新重新启动，稍后会自动打开。\nMonkeyHub is restarting for an update and will open by itself shortly.";
    const BUSY: &str = "MonkeyHub 仍在关闭或启动，请稍后再打开。\nMonkeyHub is still closing or starting; please open it again in a moment.";

    /// The update helper starts a trial only after the previous desktop has exited; this
    /// covers the moment in which Windows hands over the abandoned lock.
    const TRIAL_WAIT_MS: u32 = 10_000;
    /// How long a later launch waits, without a notice, for a desktop that is closing or still
    /// building its window.
    const WAIT_FOR_OWNER: Duration = Duration::from_secs(30);
    /// How often a waiting launch asks the owner again.
    const ASK_EVERY_MS: u32 = 250;
    /// The owner answers at once; this bounds only a hung activation thread.
    const ANSWER_TIMEOUT_MS: u32 = 5_000;

    // The owner's answers to the activation message.
    /// Its main window is still being built.
    const NOT_READY: isize = 0;
    /// Its main window was brought forward and has the foreground.
    const FORWARD: isize = 1;
    /// Its main window was brought forward, but Windows kept the foreground elsewhere.
    const BEHIND: isize = 2;
    /// It is closing and releases the root when it exits.
    const CLOSING: isize = 3;
    /// It is closing so that the update helper can open the new version.
    const UPDATING: isize = 4;

    type Handle = *mut c_void;
    type WindowProcedure = unsafe extern "system" fn(Handle, u32, usize, isize) -> isize;

    const HWND_MESSAGE: Handle = -3isize as Handle;
    const DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2: Handle = -4isize as Handle;
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
        fn GetProcAddress(module: Handle, name: *const c_char) -> *const c_void;
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
        fn ShowWindowAsync(window: Handle, command: i32) -> i32;
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

    fn sha256(data: &[u8]) -> Result<[u8; 32], String> {
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

    /// One text per directory: no `\\?\` prefix, backslashes, no trailing separator, lower case.
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

    /// The first 32 hex digits of the SHA-256 of the canonical runtime root.
    fn instance_key(runtime_root: &Path) -> Result<String, String> {
        let resolved = fs::canonicalize(runtime_root).unwrap_or_else(|_| runtime_root.into());
        Ok(sha256(normalise(&resolved).as_bytes())?[..16]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect())
    }

    fn mutex_name(key: &str) -> String {
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

    /// Per-monitor (v2) DPI awareness before this process's first window, so a notice is sharp
    /// at 125–150 % scaling; tao asks for the same awareness later. Looked up by name because
    /// Windows 10 before version 1703 lacks the function.
    fn per_monitor_dpi() {
        unsafe {
            let user32 = GetModuleHandleW(wide("user32.dll").as_ptr());
            if user32.is_null() {
                return;
            }
            let function = GetProcAddress(user32, c"SetProcessDpiAwarenessContext".as_ptr());
            if !function.is_null() {
                let set = std::mem::transmute::<*const c_void, unsafe extern "system" fn(Handle) -> i32>(
                    function,
                );
                set(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
            }
        }
    }

    /// A handle to the named lock, or None when another desktop's lock may not even be opened
    /// (it runs elevated).
    fn open(name: &str) -> Result<Option<Handle>, String> {
        let name = wide(name);
        let handle = unsafe { CreateMutexW(ptr::null(), 0, name.as_ptr()) };
        if !handle.is_null() {
            return Ok(Some(handle));
        }
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(ERROR_ACCESS_DENIED) {
            return Ok(None);
        }
        Err(format!("Cannot create the single-instance lock: {error}"))
    }

    /// True when the calling thread now owns the lock, including one abandoned by a desktop
    /// that exited. The lock stays owned, and its handle open, until this process exits, so
    /// take it on the thread that lives as long as the process.
    fn take(handle: Handle, wait_ms: u32) -> Result<bool, String> {
        match unsafe { WaitForSingleObject(handle, wait_ms) } {
            WAIT_OBJECT_0 | WAIT_ABANDONED => Ok(true),
            WAIT_TIMEOUT => Ok(false),
            _ => Err(format!(
                "Cannot take the single-instance lock: {}",
                io::Error::last_os_error()
            )),
        }
    }

    fn close(handle: Handle) {
        unsafe {
            CloseHandle(handle);
        }
    }

    /// `take` on a new handle, which is closed unless the lock was taken.
    fn acquire(name: &str, wait_ms: u32) -> Result<bool, String> {
        let Some(handle) = open(name)? else {
            return Ok(false);
        };
        let taken = take(handle, wait_ms);
        if !matches!(taken, Ok(true)) {
            close(handle);
        }
        taken
    }

    /// Ok(true): this process owns the runtime root. Ok(false): another desktop owns it and the
    /// person was told what it is doing, so this launch must exit. An update trial never asks
    /// or tells: it fails, and the update helper rolls back.
    pub fn claim(runtime_root: &Path, trial: bool) -> Result<bool, String> {
        per_monitor_dpi();
        let key = instance_key(runtime_root)?;
        let name = mutex_name(&key);
        if trial {
            if !acquire(&name, TRIAL_WAIT_MS)? {
                return Err(format!(
                    "Another MonkeyHub desktop is still using runtime directory {}; the update trial did not start.",
                    runtime_root.display()
                ));
            }
        } else if !acquire(&name, 0)? && !follow_owner(&key, &name, WAIT_FOR_OWNER, show_notice)? {
            return Ok(false);
        }
        listen(&key);
        Ok(true)
    }

    /// Another desktop owns the root. A running owner comes forward, and one restarting for an
    /// update is left to open the new version. An owner that is closing, still building its
    /// window or already gone is waited for without a notice, and then this launch owns the
    /// root; `notify` is called only when this launch gives up.
    fn follow_owner(
        key: &str,
        name: &str,
        patience: Duration,
        notify: impl FnOnce(&str),
    ) -> Result<bool, String> {
        let class = wide(&class_name(key));
        let lock = open(name)?;
        let deadline = Instant::now() + patience;
        let outcome = loop {
            match ask_owner(&class) {
                Some(FORWARD) => break Ok(Some(SWITCHED)),
                Some(BEHIND) => break Ok(Some(RUNNING)),
                Some(UPDATING) => break Ok(Some(RESTARTING)),
                // Closing, still building its window, gone, or not listening yet.
                _ => {}
            }
            // A desktop running elevated owns a lock that this launch may not wait for.
            let Some(handle) = lock else {
                break Ok(Some(RUNNING));
            };
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break Ok(Some(BUSY));
            }
            match take(handle, ASK_EVERY_MS.min(left.as_millis() as u32)) {
                Ok(true) => break Ok(None),
                Ok(false) => {}
                Err(error) => break Err(error),
            }
        };
        if !matches!(outcome, Ok(None)) {
            if let Some(handle) = lock {
                close(handle);
            }
        }
        match outcome? {
            // The lock is this thread's now, and its handle stays open.
            None => Ok(true),
            Some(text) => {
                notify(text);
                Ok(false)
            }
        }
    }

    /// The owner's answer, or None when its activation window is missing or does not answer.
    fn ask_owner(class: &[u16]) -> Option<isize> {
        unsafe {
            let owner = FindWindowExW(HWND_MESSAGE, ptr::null_mut(), class.as_ptr(), ptr::null());
            if owner.is_null() {
                return None;
            }
            // Let the owner take the foreground this launch was given.
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
                ANSWER_TIMEOUT_MS,
                &mut answer,
            );
            (delivered != 0).then_some(answer as isize)
        }
    }

    fn show_notice(text: &str) {
        let text = wide(text);
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

    struct Activation {
        /// The owner's main window, once built.
        window: isize,
        /// Whether the owner has begun closing.
        closing: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
        /// This process's activation window.
        listener: isize,
        /// Why the activation window could not start, logged once the log exists.
        failure: Option<String>,
        log: Option<DiagnosticLog>,
        /// The last answer logged, so a waiting launch's repeated questions add one line.
        logged: Option<(u32, isize)>,
    }

    static ACTIVATION: Mutex<Activation> = Mutex::new(Activation {
        window: 0,
        closing: None,
        listener: 0,
        failure: None,
        log: None,
        logged: None,
    });

    static RESTARTING_FOR_UPDATE: AtomicBool = AtomicBool::new(false);

    fn state() -> MutexGuard<'static, Activation> {
        ACTIVATION.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// This desktop now closes so that the update helper can open the new version; a later
    /// launch leaves the root to that helper instead of taking it. Call it before shutdown
    /// begins.
    pub fn restarting_for_update() {
        RESTARTING_FOR_UPDATE.store(true, Ordering::SeqCst);
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
            // Usually a millisecond; a later launch also keeps asking while it waits.
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
            return answer(wparam as u32);
        }
        DefWindowProcW(window, message, wparam, lparam)
    }

    /// Runs on the activation thread and makes no Tauri call. Its Win32 calls on the main
    /// window only post to the Tauri main thread, so a busy page cannot delay the answer.
    fn answer(requester: u32) -> isize {
        let (window, closing, log) = {
            let state = state();
            (state.window as Handle, state.closing.clone(), state.log.clone())
        };
        if window.is_null() {
            return NOT_READY;
        }
        let closing = closing.is_some_and(|closing| closing()) || unsafe { IsWindow(window) } == 0;
        let (answer, detail) = if !closing {
            bring_forward(window, requester)
        } else if RESTARTING_FOR_UPDATE.load(Ordering::SeqCst) {
            (UPDATING, "restarting".to_owned())
        } else {
            (CLOSING, "closing".to_owned())
        };
        if let Some(log) = log {
            let mut state = state();
            if state.logged != Some((requester, answer)) {
                state.logged = Some((requester, answer));
                drop(state);
                log.write(&format!(
                    "event=relaunch requester={requester} answer={detail}"
                ));
            }
        }
        answer
    }

    fn bring_forward(window: Handle, requester: u32) -> (isize, String) {
        unsafe {
            let minimized = IsIconic(window) != 0;
            if minimized {
                ShowWindowAsync(window, SW_RESTORE);
            } else if IsWindowVisible(window) == 0 {
                ShowWindowAsync(window, SW_SHOW);
            }
            let foreground = SetForegroundWindow(window) != 0;
            if foreground && requester != 0 {
                // The launch's notice may then appear above the window it brought forward.
                AllowSetForegroundWindow(requester);
            }
            (
                if foreground { FORWARD } else { BEHIND },
                format!("brought-forward minimized={minimized} foreground={foreground}"),
            )
        }
    }

    /// Hands the owner's main window to its activation window, with how to tell that this
    /// desktop has begun closing.
    pub fn attach(
        window: &tauri::WebviewWindow,
        log: DiagnosticLog,
        closing: impl Fn() -> bool + Send + Sync + 'static,
    ) {
        let main = match window.hwnd() {
            Ok(main) => main.0 as isize,
            Err(error) => {
                log.write(&format!(
                    "event=single-instance-warning detail=The main window has no native handle: {error}"
                ));
                return;
            }
        };
        let (listener, failure) = {
            let mut state = state();
            state.window = main;
            state.closing = Some(Arc::new(closing));
            state.log = Some(log.clone());
            (state.listener, state.failure.take())
        };
        if listener == 0 {
            let failure = failure.unwrap_or_else(|| "The activation window has not started".into());
            log.write(&format!(
                "event=single-instance-warning detail={failure}; a later launch cannot bring this window forward"
            ));
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::{path::PathBuf, process::Command};
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

        /// A thread that owns the lock until the returned sender is used or dropped, then ends
        /// while it owns it, as a desktop's main thread does.
        fn hold(name: &str) -> (thread::JoinHandle<()>, mpsc::Sender<()>) {
            let (held, owned) = mpsc::channel();
            let (release, released) = mpsc::channel::<()>();
            let name = name.to_owned();
            let owner = thread::spawn(move || {
                held.send(acquire(&name, 0).unwrap()).unwrap();
                let _ = released.recv();
            });
            assert!(owned.recv().unwrap(), "the test lock was already taken");
            (owner, release)
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
            let (owner, release) = hold(&name);
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

        /// The only test that starts this process's activation window; it needs no visible
        /// window, and its notices are recorded instead of shown.
        #[test]
        fn a_later_launch_follows_what_the_owner_answers() {
            let directory = Directory::new();
            let key = directory.key("Runtime");
            let class = wide(&class_name(&key));
            let log = DiagnosticLog::open(&directory.0, "owner").unwrap();
            listen(&key);
            assert_ne!(state().listener, 0, "{:?}", state().failure);
            assert_eq!(ask_owner(&wide(&class_name(&directory.key("Other")))), None);
            // Still building its main window.
            assert_eq!(ask_owner(&class), Some(NOT_READY));
            let closing = Arc::new(AtomicBool::new(true));
            {
                let flag = closing.clone();
                let mut state = state();
                state.window = 1; // not a window: this owner's main window is gone
                state.closing = Some(Arc::new(move || flag.load(Ordering::SeqCst)));
                state.log = Some(log.clone());
            }
            assert_eq!(ask_owner(&class), Some(CLOSING));
            closing.store(false, Ordering::SeqCst);
            assert_eq!(ask_owner(&class), Some(CLOSING), "a gone window is closing");
            closing.store(true, Ordering::SeqCst);

            // Closing: wait without a notice, and own the root once the owner has exited.
            let notices = Mutex::new(Vec::new());
            let record = |text: &str| notices.lock().unwrap().push(text.to_owned());
            let freed = mutex_name(&directory.key("Other"));
            let (owner, release) = hold(&freed);
            let releaser = thread::spawn(move || {
                thread::sleep(Duration::from_millis(600));
                release.send(()).unwrap();
            });
            let started = Instant::now();
            assert!(follow_owner(&key, &freed, Duration::from_secs(10), record).unwrap());
            assert!(started.elapsed() >= Duration::from_millis(500));
            releaser.join().unwrap();
            owner.join().unwrap();

            // Closing past the patience: give up with the still-closing notice.
            let busy = mutex_name(&directory.key(r"Runtime\Busy"));
            let (owner, release) = hold(&busy);
            let started = Instant::now();
            assert!(!follow_owner(&key, &busy, Duration::from_millis(800), record).unwrap());
            assert!(started.elapsed() >= Duration::from_millis(800));

            // Restarting for an update: leave the root to the update helper.
            restarting_for_update();
            assert!(!follow_owner(&key, &busy, Duration::from_secs(10), record).unwrap());
            RESTARTING_FOR_UPDATE.store(false, Ordering::SeqCst);
            release.send(()).unwrap();
            owner.join().unwrap();
            assert_eq!(*notices.lock().unwrap(), [BUSY, RESTARTING]);

            // One log line per answer, however often a waiting launch asks.
            let requester = std::process::id();
            let text = fs::read_to_string(&log.path).unwrap();
            let lines: Vec<_> = text.lines().map(|line| line.split_once(' ').unwrap().1).collect();
            assert_eq!(
                lines,
                [
                    format!("event=relaunch requester={requester} answer=closing"),
                    format!("event=relaunch requester={requester} answer=restarting"),
                ]
            );
            let mut state = state();
            (state.window, state.closing, state.log) = (0, None, None); // closes the log file
        }
    }
}
