#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

use monkeyarch_desktop::{
    is_status_url, DiagnosticLog, ExpectedIdentity, HealthError, LaunchConfig, OwnedRuntime,
    SOURCE_REVISION,
};
use serde::Serialize;
use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use tauri::{
    webview::{NewWindowFeatures, NewWindowResponse, PageLoadEvent},
    Manager, RunEvent, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent,
};
use uuid::Uuid;

#[derive(Clone, Serialize)]
struct Status {
    title: String,
    detail: String,
    log: String,
}
struct Shared {
    shutdown: AtomicBool,
    finished: AtomicBool,
    identity: Mutex<Option<ExpectedIdentity>>,
    status: Mutex<Status>,
}

fn paint_status(window: &WebviewWindow, shared: &Shared) {
    if window.url().is_ok_and(|url| is_status_url(&url)) {
        let status = shared.status.lock().unwrap().clone();
        if let Ok(json) = serde_json::to_string(&status) {
            let _ = window.eval(&format!(
                "window.renderDesktopStatus && window.renderDesktopStatus({json})"
            ));
        }
    }
}

fn show_status(
    window: &WebviewWindow,
    shared: &Shared,
    log: &DiagnosticLog,
    state: &str,
    title: &str,
    detail: &str,
) {
    log.state(state, detail);
    if matches!(state, "stopping" | "failed") {
        for (label, child) in window.app_handle().webview_windows() {
            if label != "main" {
                let _ = child.close();
            }
        }
    }
    *shared.identity.lock().unwrap() = None;
    *shared.status.lock().unwrap() = Status {
        title: title.into(),
        detail: detail.into(),
        log: log.path.display().to_string(),
    };
    let _ = window.set_title(&format!("MonkeyArch · {title}"));
    if window.url().is_ok_and(|url| is_status_url(&url)) {
        paint_status(window, shared);
    } else {
        #[cfg(windows)]
        let url = "http://tauri.localhost/index.html";
        #[cfg(not(windows))]
        let url = "tauri://localhost/index.html";
        if let Err(error) = window.navigate(url.parse().unwrap()) {
            log.write(&format!("event=window-error detail={error}"));
        }
    }
}

fn open_owned_page(
    app: &tauri::AppHandle,
    url: url::Url,
    features: NewWindowFeatures,
    shared: Arc<Shared>,
    log: DiagnosticLog,
) -> NewWindowResponse<tauri::Wry> {
    let identity = shared.identity.lock().unwrap().clone();
    if shared.shutdown.load(Ordering::SeqCst)
        || !identity.is_some_and(|identity| identity.allows_owned_page(&url))
    {
        log.write(
            "event=popup-blocked detail=Page is not on a currently owned healthy runtime origin",
        );
        return NewWindowResponse::Deny;
    }
    let navigation_shared = shared.clone();
    let popup_app = app.clone();
    let popup_shared = shared.clone();
    let popup_log = log.clone();
    let label = format!("page-{}", Uuid::new_v4());
    match WebviewWindowBuilder::new(app, label, WebviewUrl::External(url))
        .window_features(features) // Reuse the opener's WebView2 environment and cache.
        .title("MonkeyArch · 项目视图")
        .inner_size(1200.0, 800.0)
        .devtools(false)
        .disable_drag_drop_handler()
        .on_navigation(move |url| {
            let identity = navigation_shared.identity.lock().unwrap().clone();
            !navigation_shared.shutdown.load(Ordering::SeqCst)
                && identity.is_some_and(|identity| identity.allows_owned_page(url))
        })
        .on_new_window(move |url, features| {
            open_owned_page(
                &popup_app,
                url,
                features,
                popup_shared.clone(),
                popup_log.clone(),
            )
        })
        .build()
    {
        Ok(window) => NewWindowResponse::Create { window },
        Err(error) => {
            log.write(&format!("event=popup-error detail={error}"));
            NewWindowResponse::Deny
        }
    }
}

fn supervise(
    window: WebviewWindow,
    config: LaunchConfig,
    shared: Arc<Shared>,
    log: DiagnosticLog,
    instance: Uuid,
) {
    show_status(
        &window,
        &shared,
        &log,
        "starting",
        "正在启动",
        "正在启动并核对本实例的本地运行时。",
    );
    if shared.shutdown.load(Ordering::SeqCst) {
        shared.finished.store(true, Ordering::SeqCst);
        window.app_handle().exit(0);
        return;
    }
    let mut runtime = match OwnedRuntime::spawn(&config, instance, log.clone()) {
        Ok(runtime) => runtime,
        Err(error) => {
            show_status(&window, &shared, &log, "failed", "启动失败", &error);
            shared.finished.store(true, Ordering::SeqCst);
            if shared.shutdown.load(Ordering::SeqCst) {
                window.app_handle().exit(0);
            }
            return;
        }
    };
    let started = Instant::now();
    let mut ready = false;
    let mut failed = false;
    let mut stopping = false;
    let mut recovering = false;
    let mut outage: Option<Instant> = None;
    let mut last_health = Instant::now() - Duration::from_secs(1);
    loop {
        let quitting = shared.shutdown.load(Ordering::SeqCst);
        if quitting && !stopping {
            stopping = true;
            show_status(
                &window,
                &shared,
                &log,
                "stopping",
                "正在关闭",
                "正在等待已接收的操作完成并关闭本实例。项目数据仍由原项目目录保存。",
            );
            runtime.request_stop();
        }
        match runtime.try_wait() {
            Ok(Some(status)) => {
                log.write(&format!(
                    "event=exit pid={} code={status}",
                    runtime.identity.process_id
                ));
                if quitting {
                    log.state("stopped", "Owned Hub exited after shutdown");
                } else if !failed {
                    let output = match log.tail() {
                        Ok(tail) if !tail.is_empty() => format!("\n\n运行时最后输出：\n{tail}"),
                        Ok(_) => String::new(),
                        Err(error) => format!("\n\n无法读取日志末尾：{error}"),
                    };
                    show_status(
                        &window,
                        &shared,
                        &log,
                        "failed",
                        "运行时已退出",
                        &format!("本地运行时意外退出（{status}）。关闭应用后可重新打开。{output}"),
                    );
                }
                shared.finished.store(true, Ordering::SeqCst);
                if quitting || shared.shutdown.load(Ordering::SeqCst) {
                    window.app_handle().exit(0);
                }
                return;
            }
            Err(error) if !failed => {
                failed = true;
                show_status(&window, &shared, &log, "failed", "无法检查运行时", &error);
                runtime.request_stop();
            }
            _ => {}
        }
        if !stopping
            && !failed
            && last_health.elapsed() >= Duration::from_millis(if ready { 1000 } else { 200 })
        {
            last_health = Instant::now();
            match runtime.identity.check() {
                Ok(()) => {
                    if !ready {
                        if shared.shutdown.load(Ordering::SeqCst) {
                            continue;
                        }
                        *shared.identity.lock().unwrap() = Some(runtime.identity.clone());
                        if let Err(error) = window.navigate(config.url()) {
                            failed = true;
                            show_status(
                                &window,
                                &shared,
                                &log,
                                "failed",
                                "无法打开工作界面",
                                &error.to_string(),
                            );
                            runtime.request_stop();
                        } else {
                            log.state("ready", "Owned Hub identity and health verified");
                            let _ = window.set_title("MonkeyArch");
                            ready = true;
                        }
                    } else if recovering {
                        // The same root recovered; retain the loaded document and its drafts.
                        log.state(
                            "ready",
                            "Owned Hub health recovered without reloading the page",
                        );
                        let _ = window.set_title("MonkeyArch");
                        recovering = false;
                    }
                    outage = None;
                }
                Err(HealthError::Rejected(error)) => {
                    failed = true;
                    show_status(
                        &window,
                        &shared,
                        &log,
                        "failed",
                        "运行时身份验证失败",
                        &error,
                    );
                    runtime.request_stop();
                }
                Err(HealthError::Unavailable(error)) => {
                    if !ready && started.elapsed() >= config.startup_timeout {
                        failed = true;
                        show_status(&window, &shared, &log, "failed", "启动超时", &format!("运行时未在 {} 秒内就绪：{error}。本实例正在退出，请查看日志后重新打开。", config.startup_timeout.as_secs()));
                        runtime.request_stop();
                    } else if ready {
                        let since = outage.get_or_insert_with(Instant::now);
                        if since.elapsed() >= Duration::from_secs(3) && !recovering {
                            recovering = true;
                            log.state("recovering", "Owned Hub is alive but health is unavailable; preserving the current page");
                            let _ = window.set_title("MonkeyArch · 正在等待运行时响应");
                        }
                    }
                }
            }
        }
        thread::sleep(Duration::from_millis(100));
    }
}

fn run() -> Result<(), String> {
    if std::env::args_os()
        .skip(1)
        .eq([std::ffi::OsString::from("--version")])
    {
        println!(
            "{}",
            serde_json::json!({"version": env!("CARGO_PKG_VERSION"), "sourceRevision": SOURCE_REVISION})
        );
        return Ok(());
    }
    let config = LaunchConfig::from_args(std::env::args_os().skip(1))?;
    let instance = Uuid::new_v4();
    let log = DiagnosticLog::open(&config.runtime_root, &instance.to_string())?;
    let data_directory = config.runtime_root.join("cache/desktop-webview");
    std::fs::create_dir_all(&data_directory)
        .map_err(|e| format!("Cannot prepare WebView cache: {e}"))?;
    let shared = Arc::new(Shared {
        shutdown: AtomicBool::new(false),
        finished: AtomicBool::new(false),
        identity: Mutex::new(None),
        status: Mutex::new(Status {
            title: "正在启动".into(),
            detail: "正在准备本地工作环境。".into(),
            log: log.path.display().to_string(),
        }),
    });
    let setup_shared = shared.clone();
    let setup_log = log.clone();
    let app = tauri::Builder::default()
        .setup(move |app| {
            let navigation_shared = setup_shared.clone();
            let page_shared = setup_shared.clone();
            let page_log = setup_log.clone();
            let navigation_log = setup_log.clone();
            let popup_app = app.handle().clone();
            let popup_shared = setup_shared.clone();
            let popup_log = setup_log.clone();
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("MonkeyArch").inner_size(1360.0, 900.0).min_inner_size(900.0, 600.0)
                .data_directory(data_directory).devtools(false).disable_drag_drop_handler()
                .on_navigation(move |url| {
                    if is_status_url(url) { return true; }
                    let identity = navigation_shared.identity.lock().unwrap().clone();
                    let allowed = !navigation_shared.shutdown.load(Ordering::SeqCst)
                        && identity.is_some_and(|identity| identity.allows_url(url) && identity.check().is_ok());
                    if !allowed { navigation_log.write("event=navigation-blocked detail=Outside verified Hub origin or Hub no longer healthy"); }
                    allowed
                })
                .on_new_window(move |url, features| open_owned_page(&popup_app, url, features, popup_shared.clone(), popup_log.clone()))
                .on_page_load(move |window, payload| {
                    if payload.event() == PageLoadEvent::Finished {
                        page_log.write(&format!("event=page-loaded url={}", payload.url()));
                        paint_status(&window, &page_shared);
                    }
                })
                .build()?;
            let close_shared = setup_shared.clone();
            let close_app = app.handle().clone();
            window.on_window_event(move |event| {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    close_shared.shutdown.store(true, Ordering::SeqCst);
                    if close_shared.finished.load(Ordering::SeqCst) { close_app.exit(0); }
                }
            });
            thread::spawn(move || supervise(window, config, setup_shared, setup_log, instance));
            Ok(())
        })
        .build(tauri::generate_context!()).map_err(|error| {
            log.state("failed", &format!("Cannot create desktop window/WebView2: {error}"));
            format!("无法创建桌面窗口。请确认 Microsoft Edge WebView2 Runtime 已安装。\n{error}\n日志：{}", log.path.display())
        })?;
    app.run(move |_, event| {
        if let RunEvent::ExitRequested { api, .. } = event {
            if !shared.finished.load(Ordering::SeqCst) {
                api.prevent_exit();
            }
            shared.shutdown.store(true, Ordering::SeqCst);
        }
    });
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        #[cfg(windows)]
        unsafe {
            // A visible fallback also covers a missing WebView2 runtime.
            #[link(name = "user32")]
            extern "system" {
                fn MessageBoxW(
                    hwnd: *mut std::ffi::c_void,
                    text: *const u16,
                    caption: *const u16,
                    flags: u32,
                ) -> i32;
            }
            let message: Vec<u16> = error.encode_utf16().chain(Some(0)).collect();
            let title: Vec<u16> = "MonkeyArch · 启动失败"
                .encode_utf16()
                .chain(Some(0))
                .collect();
            MessageBoxW(std::ptr::null_mut(), message.as_ptr(), title.as_ptr(), 0x10);
        }
        std::process::exit(1);
    }
}
