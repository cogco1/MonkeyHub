//! #354: the desktop title row on Windows 11.
//!
//! The Hub page and the status page draw one 40 px row: the menus, a drag area and the
//! pictures of the three window buttons. The drag area is CSS `app-region: drag`, which
//! WebView2 already treats as the window caption (move loop, double-click, system menu).
//! This module owns what a page cannot do: a transparent child window over the three
//! buttons that answers `WM_NCHITTEST` with `HTMINBUTTON` / `HTMAXBUTTON` / `HTCLOSE`.
//! That answer is what Windows 11 needs to offer Snap Layouts. A click on a button becomes
//! `WM_SYSCOMMAND`, so Close still reaches the host's CloseRequested drain.
//!
//! Hover, press, maximized and active state go back to the page as [`ShellEvent`]s. The host
//! turns each change into one `eval` of [`ShellState::script`]. No window procedure here calls
//! into Tauri, and all Win32 lives in this file. The geometry constants below are the only
//! copy; the page reads them from [`bridge_script`].

use serde::Serialize;

/// Height of the title row, in CSS pixels.
pub const TITLE_BAR_HEIGHT: u32 = 40;
/// Width of one window button, in CSS pixels.
pub const CAPTION_BUTTON_WIDTH: u32 = 46;
/// Minimize, maximize or restore, and close.
pub const CAPTION_BUTTONS: u32 = 3;
/// Microsoft asks for a minimum width of at most 500 effective pixels so a window fits
/// the Snap Layouts zones; wider windows show the layouts but refuse to snap into them.
pub const MIN_WINDOW_WIDTH: f64 = 500.0;

/// One of the three window buttons the caption child answers for.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum CaptionButton {
    Minimize,
    Maximize,
    Close,
}

impl CaptionButton {
    /// The `WM_NCHITTEST` answer Windows expects for this button.
    pub const fn hit_code(self) -> u32 {
        match self {
            Self::Minimize => 8, // HTMINBUTTON
            Self::Maximize => 9, // HTMAXBUTTON
            Self::Close => 20,   // HTCLOSE
        }
    }

    /// The button a non-client mouse message names in its `WPARAM` hit code.
    pub const fn from_hit_code(code: usize) -> Option<Self> {
        match code {
            8 => Some(Self::Minimize),
            9 => Some(Self::Maximize),
            20 => Some(Self::Close),
            _ => None,
        }
    }
}

/// A rectangle in the main window's client area, in physical pixels.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
}

/// `css` CSS pixels at `dpi` (96 is 100 %), rounded to the nearest physical pixel.
pub fn physical(css: u32, dpi: u32) -> i32 {
    ((u64::from(css) * u64::from(dpi) + 48) / 96) as i32
}

/// Where the caption child sits: the top-right `3 × 46` by `40` CSS pixels of the client
/// area. When the window is restored, the child starts below Tauri's top resize strip
/// (`resize_strip` physical pixels, the same `SM_CYFRAME` Tauri uses), so the two never
/// overlap. A maximized window has no strip, so the buttons reach the screen's top edge.
pub fn caption_rect(client_width: i32, dpi: u32, maximized: bool, resize_strip: i32) -> Rect {
    let client_width = client_width.max(0);
    let width = physical(CAPTION_BUTTON_WIDTH * CAPTION_BUTTONS, dpi).min(client_width);
    let bottom = physical(TITLE_BAR_HEIGHT, dpi);
    let top = if maximized {
        0
    } else {
        resize_strip.clamp(0, bottom)
    };
    Rect {
        left: client_width - width,
        top,
        right: client_width,
        bottom,
    }
}

/// The button under `x`, a position in the caption child's own client area (0 is its left
/// edge, `width` its right edge). The split is measured from the right edge in CSS pixels,
/// the way the page lays out its 46 px buttons, so it matches the drawing at any scale.
pub fn button_at(x: i32, width: i32, dpi: u32) -> Option<CaptionButton> {
    if width <= 0 || x < 0 || x >= width {
        return None;
    }
    let from_right = i64::from(width - x) * 96;
    let button = i64::from(CAPTION_BUTTON_WIDTH) * i64::from(dpi.max(1));
    Some(if from_right <= button {
        CaptionButton::Close
    } else if from_right <= 2 * button {
        CaptionButton::Maximize
    } else {
        CaptionButton::Minimize
    })
}

/// The screen point in a mouse message's `LPARAM`. The halves are sign-extended like
/// `GET_X_LPARAM` / `GET_Y_LPARAM`, because monitors left of or above the primary one have
/// negative coordinates. Unsigned `LOWORD` / `HIWORD` would put them 65536 pixels away.
pub fn signed_point(lparam: isize) -> (i32, i32) {
    let x = (lparam & 0xFFFF) as u16 as i16;
    let y = ((lparam >> 16) & 0xFFFF) as u16 as i16;
    (i32::from(x), i32::from(y))
}

/// A host condition the Hub page shows in the title row while it stays loaded. The native
/// title that used to carry it is no longer visible.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum HostStatus {
    Recovering,
}

/// Which title bar the main window uses.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TitleBar {
    /// Windows 11: no system title bar; the page draws the merged row.
    Merged,
    /// Windows 10 or `--native-title-bar`: today's system title bar and layout.
    Native,
}

/// A change the page must draw.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ShellEvent {
    /// The merged row is (still) in use; false returns the page to today's layout.
    TitleBar(bool),
    Hover(Option<CaptionButton>),
    Pressed(Option<CaptionButton>),
    Maximized(bool),
    Active(bool),
    /// The native caption was pressed (the move/size loop started). The page never sees
    /// that press, so the counter tells it to close an open menu.
    CaptionPressed,
    HostStatus(Option<HostStatus>),
    /// A page finished loading and needs the whole state again.
    PageLoaded,
}

/// What the page draws, delivered whole on every change.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ShellState {
    title_bar: bool,
    maximized: bool,
    active: bool,
    hover: Option<CaptionButton>,
    pressed: Option<CaptionButton>,
    host_status: Option<HostStatus>,
    caption_press: u32,
}

impl Default for ShellState {
    fn default() -> Self {
        Self {
            title_bar: true,
            maximized: false,
            active: true,
            hover: None,
            pressed: None,
            host_status: None,
            caption_press: 0,
        }
    }
}

impl ShellState {
    /// Applies `event`; true when the page has to be told.
    pub fn apply(&mut self, event: ShellEvent) -> bool {
        let before = self.clone();
        match event {
            ShellEvent::TitleBar(on) => self.title_bar = on,
            ShellEvent::Hover(button) => self.hover = button,
            ShellEvent::Pressed(button) => self.pressed = button,
            ShellEvent::Maximized(maximized) => self.maximized = maximized,
            ShellEvent::Active(active) => self.active = active,
            ShellEvent::CaptionPressed => self.caption_press = self.caption_press.wrapping_add(1),
            ShellEvent::HostStatus(status) => self.host_status = status,
            ShellEvent::PageLoaded => return true,
        }
        *self != before
    }

    /// The script that hands this state to the page. The bridge exists only on the verified
    /// Hub origin and the status page, so other documents ignore it.
    pub fn script(&self) -> String {
        format!(
            "window.__monkeyhubDesktopPush&&window.__monkeyhubDesktopPush({})",
            serde_json::to_string(self).expect("shell state serializes")
        )
    }
}

/// Defines `window.__monkeyhubDesktop` before any page script runs. It exists only in the
/// main window, only on the verified Hub origin (`port`) and the embedded status page, and
/// only in merged mode. A browser never has it, so the page keeps today's layout there.
pub fn bridge_script(port: u16) -> String {
    let initial = serde_json::to_string(&ShellState::default()).expect("shell state serializes");
    format!(
        r#"(() => {{
  const origin = location.origin;
  if (origin !== "http://127.0.0.1:{port}" && origin !== "http://tauri.localhost" && origin !== "tauri://localhost") return;
  const listeners = new Set();
  let state = Object.freeze({initial});
  const shell = Object.freeze({{
    version: 1,
    titleBarHeight: {TITLE_BAR_HEIGHT},
    captionButtonWidth: {CAPTION_BUTTON_WIDTH},
    captionButtons: {CAPTION_BUTTONS},
    get state() {{ return state; }},
    subscribe(listener) {{ listeners.add(listener); return () => {{ listeners.delete(listener); }}; }},
  }});
  Object.defineProperty(window, "__monkeyhubDesktop", {{ value: shell }});
  Object.defineProperty(window, "__monkeyhubDesktopPush", {{ value(next) {{
    if (!next || typeof next !== "object") return;
    state = Object.freeze({{ ...state, ...next }});
    for (const listener of [...listeners]) {{
      try {{ listener(state); }} catch (error) {{ console.error(error); }}
    }}
  }} }});
}})();"#
    )
}

/// Whether this Windows has Snap Layouts and rounded undecorated windows (build 22000+).
#[cfg(windows)]
pub fn merged_title_bar_supported() -> bool {
    native::windows_build() >= 22000
}

#[cfg(not(windows))]
pub fn merged_title_bar_supported() -> bool {
    false
}

/// The single entry point, called once on the main thread right after the main window is
/// built. Both modes lock the page zoom and give Alt+Space back its system menu, because
/// WebView2 swallows it while the page has focus (WebView2Feedback #3840). Merged mode also
/// creates the caption child and watches the window's size, DPI and activation. An error
/// means the caption child is missing; the caller then restores the system title bar.
/// `report` receives problems that only surface later, on the WebView's own callback.
#[cfg(windows)]
pub fn install(
    window: &tauri::WebviewWindow,
    title_bar: TitleBar,
    events: std::sync::mpsc::Sender<ShellEvent>,
    report: impl Fn(String) + Send + 'static,
) -> Result<(), String> {
    let parent = window.hwnd().map_err(|error| error.to_string())?.0 as isize;
    window
        .with_webview(move |webview| {
            if let Err(error) = native::configure_webview(&webview.controller(), parent) {
                report(format!(
                    "Cannot configure WebView zoom and Alt+Space: {error}"
                ));
            }
        })
        .map_err(|error| error.to_string())?;
    match title_bar {
        TitleBar::Merged => native::install_caption(parent, events),
        TitleBar::Native => Ok(()),
    }
}

#[cfg(not(windows))]
pub fn install(
    _window: &tauri::WebviewWindow,
    title_bar: TitleBar,
    _events: std::sync::mpsc::Sender<ShellEvent>,
    _report: impl Fn(String) + Send + 'static,
) -> Result<(), String> {
    match title_bar {
        TitleBar::Merged => Err("The merged title row is Windows-only".into()),
        TitleBar::Native => Ok(()),
    }
}

/// The page script for [`keep_native_layout`]: it runs right after the bridge, before any
/// page script, so the page's first frame is already today's layout.
pub const NATIVE_LAYOUT_SCRIPT: &str =
    "window.__monkeyhubDesktopPush&&window.__monkeyhubDesktopPush({titleBar:false})";

/// Used when [`install`] failed after the window was built with the bridge. The bridge
/// script cannot be withdrawn, so from now on every document is told at creation that the
/// system title bar is back. Without this, each new page would first draw the merged row,
/// with buttons nothing answers, until the host's `PageLoaded` push arrived.
#[cfg(windows)]
pub fn keep_native_layout(
    window: &tauri::WebviewWindow,
    report: impl Fn(String) + Send + 'static,
) -> Result<(), String> {
    window
        .with_webview(move |webview| {
            if let Err(error) =
                native::add_document_script(&webview.controller(), NATIVE_LAYOUT_SCRIPT)
            {
                report(format!(
                    "Cannot keep new pages on the system title bar layout: {error}"
                ));
            }
        })
        .map_err(|error| error.to_string())
}

#[cfg(not(windows))]
pub fn keep_native_layout(
    _window: &tauri::WebviewWindow,
    _report: impl Fn(String) + Send + 'static,
) -> Result<(), String> {
    Ok(())
}

#[cfg(windows)]
mod native {
    use super::{button_at, caption_rect, signed_point, CaptionButton, ShellEvent};
    use std::ptr::{null, null_mut};
    use std::sync::{mpsc::Sender, Mutex};
    use webview2_com::{
        AcceleratorKeyPressedEventHandler, AddScriptToExecuteOnDocumentCreatedCompletedHandler,
        DOMContentLoadedEventHandler,
        Microsoft::Web::WebView2::Win32::{
            ICoreWebView2Controller, ICoreWebView2Settings5, ICoreWebView2_2,
            COREWEBVIEW2_KEY_EVENT_KIND, COREWEBVIEW2_KEY_EVENT_KIND_SYSTEM_KEY_DOWN,
            COREWEBVIEW2_PHYSICAL_KEY_STATUS,
        },
    };
    use windows_core::{Interface, PCWSTR};
    use windows_sys::Wdk::System::SystemServices::RtlGetVersion;
    use windows_sys::Win32::Foundation::{
        GetLastError, ERROR_CLASS_ALREADY_EXISTS, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM,
    };
    use windows_sys::Win32::Graphics::Gdi::{GetStockObject, ScreenToClient, HBRUSH, NULL_BRUSH};
    use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows_sys::Win32::System::SystemInformation::OSVERSIONINFOW;
    use windows_sys::Win32::UI::HiDpi::{GetDpiForWindow, GetSystemMetricsForDpi};
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
        TrackMouseEvent, TME_LEAVE, TME_NONCLIENT, TRACKMOUSEEVENT, VK_SPACE,
    };
    use windows_sys::Win32::UI::Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        CreateWindowExW, DefWindowProcW, DestroyWindow, GetClientRect, GetForegroundWindow,
        IsIconic, IsZoomed, LoadCursorW, PostMessageW, RegisterClassExW, SetWindowPos, HTNOWHERE,
        HWND_TOP, IDC_ARROW, SC_CLOSE, SC_KEYMENU, SC_MAXIMIZE, SC_MINIMIZE, SC_RESTORE,
        SM_CYFRAME, SWP_NOACTIVATE, SWP_NOOWNERZORDER, SWP_SHOWWINDOW, WA_INACTIVE, WM_ACTIVATE,
        WM_DPICHANGED, WM_ENTERSIZEMOVE, WM_ERASEBKGND, WM_NCDESTROY, WM_NCHITTEST,
        WM_NCLBUTTONDBLCLK, WM_NCLBUTTONDOWN, WM_NCLBUTTONUP, WM_NCMOUSELEAVE, WM_NCMOUSEMOVE,
        WM_NCRBUTTONDBLCLK, WM_NCRBUTTONDOWN, WM_NCRBUTTONUP, WM_SIZE, WM_SYSCOMMAND, WNDCLASSEXW,
        WS_CHILD, WS_CLIPSIBLINGS, WS_VISIBLE,
    };

    /// Tests find the child by this class. It has no window name, so UI Automation does not
    /// announce an extra pane; the page's buttons are hidden from it too, and the system
    /// menu carries the same commands for keyboard and screen reader users.
    const CLASS_NAME: *const u16 = windows_sys::w!("MonkeyHubCaption");
    const SUBCLASS_ID: usize = 0x354;

    /// The one caption child of the one main window. Handles are kept as integers because
    /// raw `HWND`s are not `Send`.
    struct Caption {
        parent: isize,
        child: isize,
        hover: Option<CaptionButton>,
        pressed: Option<CaptionButton>,
        tracking: bool,
        events: Sender<ShellEvent>,
    }

    static CAPTION: Mutex<Option<Caption>> = Mutex::new(None);

    /// Runs `change` on the caption state. Nothing inside may call Win32, which could
    /// re-enter a window procedure while the lock is held.
    fn with_caption<T>(change: impl FnOnce(&mut Caption) -> T) -> Option<T> {
        CAPTION.lock().ok()?.as_mut().map(change)
    }

    pub fn windows_build() -> u32 {
        // SAFETY: RtlGetVersion fills a caller-owned, correctly sized structure.
        unsafe {
            let mut info: OSVERSIONINFOW = std::mem::zeroed();
            info.dwOSVersionInfoSize = std::mem::size_of::<OSVERSIONINFOW>() as u32;
            if RtlGetVersion(&mut info) == 0 {
                info.dwBuildNumber
            } else {
                0
            }
        }
    }

    pub fn install_caption(parent: isize, events: Sender<ShellEvent>) -> Result<(), String> {
        let parent_hwnd = parent as HWND;
        // SAFETY: called on the thread that owns `parent`; every pointer passed below is
        // either null or points at data that outlives the call.
        unsafe {
            let instance = GetModuleHandleW(null());
            let class = WNDCLASSEXW {
                cbSize: std::mem::size_of::<WNDCLASSEXW>() as u32,
                style: 0,
                lpfnWndProc: Some(caption_proc),
                cbClsExtra: 0,
                cbWndExtra: 0,
                hInstance: instance,
                hIcon: null_mut(),
                hCursor: LoadCursorW(null_mut(), IDC_ARROW),
                // Never painted: the page's drawing shows through.
                hbrBackground: GetStockObject(NULL_BRUSH) as HBRUSH,
                lpszMenuName: null(),
                lpszClassName: CLASS_NAME,
                hIconSm: null_mut(),
            };
            if RegisterClassExW(&class) == 0 && GetLastError() != ERROR_CLASS_ALREADY_EXISTS {
                return Err(format!(
                    "Cannot register the caption window class: {}",
                    std::io::Error::last_os_error()
                ));
            }
            // WS_CLIPSIBLINGS and HWND_TOP keep the child above the WebView's own windows;
            // it is the same arrangement Tauri uses for its resize borders.
            let child = CreateWindowExW(
                0,
                CLASS_NAME,
                null(),
                WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS,
                0,
                0,
                0,
                0,
                parent_hwnd,
                null_mut(),
                instance,
                null(),
            );
            if child.is_null() {
                return Err(format!(
                    "Cannot create the caption window: {}",
                    std::io::Error::last_os_error()
                ));
            }
            if let Ok(mut caption) = CAPTION.lock() {
                *caption = Some(Caption {
                    parent,
                    child: child as isize,
                    hover: None,
                    pressed: None,
                    tracking: false,
                    events,
                });
            }
            if SetWindowSubclass(parent_hwnd, Some(parent_proc), SUBCLASS_ID, 0) == 0 {
                let error = std::io::Error::last_os_error();
                if let Ok(mut caption) = CAPTION.lock() {
                    *caption = None;
                }
                DestroyWindow(child);
                return Err(format!("Cannot watch the main window: {error}"));
            }
            layout(parent_hwnd);
            send(ShellEvent::Maximized(IsZoomed(parent_hwnd) != 0));
            send(ShellEvent::Active(GetForegroundWindow() == parent_hwnd));
        }
        Ok(())
    }

    fn send(event: ShellEvent) {
        with_caption(|caption| {
            let _ = caption.events.send(event);
        });
    }

    /// Places the child over the page's caption buttons for the current size, DPI and
    /// maximized state. Must run on the window's thread.
    unsafe fn layout(parent: HWND) {
        let Some(child) = with_caption(|caption| caption.child) else {
            return;
        };
        if IsIconic(parent) != 0 {
            return;
        }
        let mut client = RECT {
            left: 0,
            top: 0,
            right: 0,
            bottom: 0,
        };
        if GetClientRect(parent, &mut client) == 0 {
            return;
        }
        let dpi = GetDpiForWindow(parent);
        let strip = GetSystemMetricsForDpi(SM_CYFRAME, dpi);
        let rect = caption_rect(
            client.right - client.left,
            dpi,
            IsZoomed(parent) != 0,
            strip,
        );
        SetWindowPos(
            child as HWND,
            HWND_TOP,
            rect.left,
            rect.top,
            rect.right - rect.left,
            rect.bottom - rect.top,
            SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_SHOWWINDOW,
        );
    }

    /// Watches the main window: size and DPI move the child; maximized, activation and the
    /// start of a caption drag are reported to the page. Everything passes through.
    unsafe extern "system" fn parent_proc(
        hwnd: HWND,
        msg: u32,
        wparam: WPARAM,
        lparam: LPARAM,
        _id: usize,
        _data: usize,
    ) -> LRESULT {
        match msg {
            WM_SIZE => {
                layout(hwnd);
                if IsIconic(hwnd) == 0 {
                    send(ShellEvent::Maximized(IsZoomed(hwnd) != 0));
                }
            }
            WM_DPICHANGED => {
                // tao resizes the window to the suggested rectangle first; lay out after it.
                let result = DefSubclassProc(hwnd, msg, wparam, lparam);
                layout(hwnd);
                return result;
            }
            WM_ACTIVATE => send(ShellEvent::Active((wparam & 0xFFFF) as u32 != WA_INACTIVE)),
            WM_ENTERSIZEMOVE => send(ShellEvent::CaptionPressed),
            WM_NCDESTROY => {
                RemoveWindowSubclass(hwnd, Some(parent_proc), SUBCLASS_ID);
                if let Ok(mut caption) = CAPTION.lock() {
                    *caption = None;
                }
            }
            _ => {}
        }
        DefSubclassProc(hwnd, msg, wparam, lparam)
    }

    unsafe extern "system" fn caption_proc(
        hwnd: HWND,
        msg: u32,
        wparam: WPARAM,
        lparam: LPARAM,
    ) -> LRESULT {
        match msg {
            WM_NCHITTEST => {
                let (x, y) = signed_point(lparam);
                let mut point = POINT { x, y };
                let mut client = RECT {
                    left: 0,
                    top: 0,
                    right: 0,
                    bottom: 0,
                };
                if ScreenToClient(hwnd, &mut point) != 0 && GetClientRect(hwnd, &mut client) != 0 {
                    let width = client.right - client.left;
                    let x = point.x.clamp(0, (width - 1).max(0));
                    if let Some(button) = button_at(x, width, GetDpiForWindow(hwnd)) {
                        return button.hit_code() as LRESULT;
                    }
                }
                return HTNOWHERE as LRESULT;
            }
            WM_NCMOUSEMOVE => {
                hover(hwnd, CaptionButton::from_hit_code(wparam));
                return 0;
            }
            WM_NCMOUSELEAVE => {
                leave();
                return 0;
            }
            // Handled here, not by DefWindowProc, which would track and draw classic buttons.
            WM_NCLBUTTONDOWN | WM_NCLBUTTONDBLCLK => {
                press(CaptionButton::from_hit_code(wparam));
                return 0;
            }
            WM_NCLBUTTONUP => {
                release(CaptionButton::from_hit_code(wparam));
                return 0;
            }
            // System caption buttons ignore the right button too; the system menu opens from
            // the drag area (right-click) and from Alt+Space.
            WM_NCRBUTTONDOWN | WM_NCRBUTTONUP | WM_NCRBUTTONDBLCLK => return 0,
            WM_ERASEBKGND => return 1,
            _ => {}
        }
        DefWindowProcW(hwnd, msg, wparam, lparam)
    }

    fn hover(hwnd: HWND, button: Option<CaptionButton>) {
        let track = with_caption(|caption| {
            if caption.hover != button {
                caption.hover = button;
                let _ = caption.events.send(ShellEvent::Hover(button));
            }
            !std::mem::replace(&mut caption.tracking, true)
        });
        if track == Some(true) {
            // Without TME_NONCLIENT a fast exit never reports WM_NCMOUSELEAVE.
            let mut request = TRACKMOUSEEVENT {
                cbSize: std::mem::size_of::<TRACKMOUSEEVENT>() as u32,
                dwFlags: TME_LEAVE | TME_NONCLIENT,
                hwndTrack: hwnd,
                dwHoverTime: 0,
            };
            // SAFETY: `request` is a valid, correctly sized structure for this call.
            unsafe { TrackMouseEvent(&mut request) };
        }
    }

    fn leave() {
        with_caption(|caption| {
            caption.tracking = false;
            if caption.hover.take().is_some() {
                let _ = caption.events.send(ShellEvent::Hover(None));
            }
            if caption.pressed.take().is_some() {
                let _ = caption.events.send(ShellEvent::Pressed(None));
            }
        });
    }

    fn press(button: Option<CaptionButton>) {
        with_caption(|caption| {
            if caption.pressed != button {
                caption.pressed = button;
                let _ = caption.events.send(ShellEvent::Pressed(button));
            }
            // Like any press on a system title bar, it closes an open page menu. The page
            // never sees this click, so it only learns about it this way.
            let _ = caption.events.send(ShellEvent::CaptionPressed);
        });
    }

    /// A press and release on the same button performs it, like a system caption button.
    fn release(button: Option<CaptionButton>) {
        let action = with_caption(|caption| {
            let pressed = caption.pressed.take();
            if pressed.is_some() {
                let _ = caption.events.send(ShellEvent::Pressed(None));
            }
            match (pressed, button) {
                (Some(pressed), Some(released)) if pressed == released => {
                    Some((caption.parent, released))
                }
                _ => None,
            }
        })
        .flatten();
        if let Some((parent, button)) = action {
            let parent = parent as HWND;
            // SAFETY: plain message calls on the main window handle this module owns.
            unsafe {
                let command = match button {
                    CaptionButton::Minimize => SC_MINIMIZE,
                    CaptionButton::Maximize if IsZoomed(parent) != 0 => SC_RESTORE,
                    CaptionButton::Maximize => SC_MAXIMIZE,
                    CaptionButton::Close => SC_CLOSE,
                };
                PostMessageW(parent, WM_SYSCOMMAND, command as WPARAM, 0);
            }
        }
    }

    /// Locks the page zoom, so the page's 40 × 46 px drawing always matches the native
    /// rectangle, and opens the system menu on Alt+Space while the page has focus.
    pub fn configure_webview(
        controller: &ICoreWebView2Controller,
        parent: isize,
    ) -> windows_core::Result<()> {
        let handler = AcceleratorKeyPressedEventHandler::create(Box::new(move |_, args| {
            let Some(args) = args else {
                return Ok(());
            };
            let mut kind = COREWEBVIEW2_KEY_EVENT_KIND::default();
            let mut key = 0u32;
            // SAFETY: out-parameters point at locals; `args` is valid for this callback.
            unsafe {
                args.KeyEventKind(&mut kind)?;
                args.VirtualKey(&mut key)?;
                if kind == COREWEBVIEW2_KEY_EVENT_KIND_SYSTEM_KEY_DOWN && key == u32::from(VK_SPACE)
                {
                    args.SetHandled(true)?;
                    let mut status = COREWEBVIEW2_PHYSICAL_KEY_STATUS::default();
                    args.PhysicalKeyStatus(&mut status)?;
                    if !status.WasKeyDown.as_bool() {
                        // The request the system itself makes for Alt+Space: it opens the
                        // window menu (Restore, Move, Size, Minimize, Maximize, Close) with
                        // each entry kept in step with the window's state.
                        PostMessageW(parent as HWND, WM_SYSCOMMAND, SC_KEYMENU as WPARAM, 0x20);
                    }
                }
            }
            Ok(())
        }));
        // The load event can come long after the first paint; DOMContentLoaded is earlier, so
        // a new page gets the current restore glyph and active state sooner. In the native
        // layout there is no caption state, and `send` does nothing.
        let content_loaded = DOMContentLoadedEventHandler::create(Box::new(|_, _| {
            send(ShellEvent::PageLoaded);
            Ok(())
        }));
        // SAFETY: COM calls on the live controller, on the thread `with_webview` runs on.
        unsafe {
            controller.SetZoomFactor(1.0)?;
            let webview = controller.CoreWebView2()?;
            let settings = webview.Settings()?;
            settings.SetIsZoomControlEnabled(false)?;
            if let Ok(settings) = settings.cast::<ICoreWebView2Settings5>() {
                settings.SetIsPinchZoomEnabled(false)?;
            }
            let mut token = 0i64;
            controller.add_AcceleratorKeyPressed(&handler, &mut token)?;
            if let Ok(webview) = webview.cast::<ICoreWebView2_2>() {
                webview.add_DOMContentLoaded(&content_loaded, &mut token)?;
            }
        }
        Ok(())
    }

    /// Runs `script` at the creation of every later document, before the page's own scripts.
    pub fn add_document_script(
        controller: &ICoreWebView2Controller,
        script: &str,
    ) -> windows_core::Result<()> {
        let script: Vec<u16> = script.encode_utf16().chain(Some(0)).collect();
        let done =
            AddScriptToExecuteOnDocumentCreatedCompletedHandler::create(Box::new(|_, _| Ok(())));
        // SAFETY: the UTF-16 buffer is null-terminated and outlives the call, which copies it.
        unsafe {
            controller
                .CoreWebView2()?
                .AddScriptToExecuteOnDocumentCreated(PCWSTR(script.as_ptr()), &done)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pack(x: i32, y: i32) -> isize {
        // MAKELPARAM with 16-bit screen coordinates, as Windows sends them.
        (((y as u32 & 0xFFFF) << 16) | (x as u32 & 0xFFFF)) as i32 as isize
    }

    #[test]
    fn physical_pixels_follow_the_monitor_scale() {
        assert_eq!(physical(40, 96), 40);
        assert_eq!(physical(40, 120), 50);
        assert_eq!(physical(40, 144), 60);
        assert_eq!(physical(138, 120), 173); // 172.5 rounds up
        assert_eq!(physical(138, 144), 207);
        assert_eq!(physical(138, 168), 242); // 241.5 rounds up
        assert_eq!(physical(138, 192), 276);
    }

    #[test]
    fn caption_rectangle_sits_top_right_below_the_resize_strip() {
        let restored = caption_rect(1360, 96, false, 4);
        assert_eq!(
            restored,
            Rect {
                left: 1222,
                top: 4,
                right: 1360,
                bottom: 40
            }
        );
        let maximized = caption_rect(1360, 96, true, 4);
        assert_eq!(
            maximized,
            Rect {
                left: 1222,
                top: 0,
                right: 1360,
                bottom: 40
            }
        );
        // 150 %: 207 × 60 physical pixels, strip of SM_CYFRAME at 144 dpi.
        assert_eq!(
            caption_rect(3840, 144, false, 6),
            Rect {
                left: 3633,
                top: 6,
                right: 3840,
                bottom: 60
            }
        );
        assert_eq!(
            caption_rect(3840, 144, true, 6),
            Rect {
                left: 3633,
                top: 0,
                right: 3840,
                bottom: 60
            }
        );
    }

    #[test]
    fn caption_rectangle_never_leaves_a_narrow_client_area() {
        assert_eq!(
            caption_rect(100, 96, false, 4),
            Rect {
                left: 0,
                top: 4,
                right: 100,
                bottom: 40
            }
        );
        assert_eq!(
            caption_rect(-5, 96, false, 4),
            Rect {
                left: 0,
                top: 4,
                right: 0,
                bottom: 40
            }
        );
        assert_eq!(caption_rect(500, 96, false, 80).top, 40);
    }

    #[test]
    fn buttons_split_from_the_right_edge_in_css_pixels() {
        let width = physical(138, 96);
        assert_eq!(button_at(0, width, 96), Some(CaptionButton::Minimize));
        assert_eq!(button_at(45, width, 96), Some(CaptionButton::Minimize));
        assert_eq!(button_at(46, width, 96), Some(CaptionButton::Maximize));
        assert_eq!(button_at(91, width, 96), Some(CaptionButton::Maximize));
        assert_eq!(button_at(92, width, 96), Some(CaptionButton::Close));
        assert_eq!(button_at(137, width, 96), Some(CaptionButton::Close));
        assert_eq!(button_at(138, width, 96), None);
        assert_eq!(button_at(-1, width, 96), None);
        // 150 %: 69 physical pixels per button.
        let width = physical(138, 144);
        assert_eq!(button_at(68, width, 144), Some(CaptionButton::Minimize));
        assert_eq!(button_at(69, width, 144), Some(CaptionButton::Maximize));
        assert_eq!(button_at(137, width, 144), Some(CaptionButton::Maximize));
        assert_eq!(button_at(138, width, 144), Some(CaptionButton::Close));
        // 125 %: 57.5 px buttons; the boundary stays within one pixel of the drawing.
        let width = physical(138, 120);
        assert_eq!(
            button_at(width - 57, width, 120),
            Some(CaptionButton::Close)
        );
        assert_eq!(
            button_at(width - 59, width, 120),
            Some(CaptionButton::Maximize)
        );
        assert_eq!(
            button_at(width - 115, width, 120),
            Some(CaptionButton::Maximize)
        );
        assert_eq!(
            button_at(width - 116, width, 120),
            Some(CaptionButton::Minimize)
        );
    }

    #[test]
    fn hit_codes_round_trip_through_non_client_messages() {
        for button in [
            CaptionButton::Minimize,
            CaptionButton::Maximize,
            CaptionButton::Close,
        ] {
            assert_eq!(
                CaptionButton::from_hit_code(button.hit_code() as usize),
                Some(button)
            );
        }
        assert_eq!(CaptionButton::Minimize.hit_code(), 8);
        assert_eq!(CaptionButton::Maximize.hit_code(), 9);
        assert_eq!(CaptionButton::Close.hit_code(), 20);
        assert_eq!(CaptionButton::from_hit_code(2), None); // HTCAPTION
    }

    #[test]
    fn screen_points_left_of_and_above_the_primary_monitor_stay_negative() {
        assert_eq!(signed_point(pack(-1500, 20)), (-1500, 20));
        assert_eq!(signed_point(pack(-2560, -1)), (-2560, -1));
        assert_eq!(signed_point(pack(3839, 2159)), (3839, 2159));
        assert_eq!(signed_point(pack(-1, -32768)), (-1, -32768));
        // A maximized window on a 2560 px monitor left of the primary (150 %): its caption
        // child spans screen x -207..0, and ScreenToClient subtracts that left edge.
        let width = physical(138, 144);
        let left = -width;
        let (x, _) = signed_point(pack(-35, 30));
        assert_eq!(button_at(x - left, width, 144), Some(CaptionButton::Close));
        let (x, _) = signed_point(pack(-100, 30));
        assert_eq!(
            button_at(x - left, width, 144),
            Some(CaptionButton::Maximize)
        );
        let (x, _) = signed_point(pack(-200, 30));
        assert_eq!(
            button_at(x - left, width, 144),
            Some(CaptionButton::Minimize)
        );
    }

    #[test]
    fn state_changes_are_reported_once() {
        let mut state = ShellState::default();
        assert!(!state.apply(ShellEvent::Maximized(false)));
        assert!(state.apply(ShellEvent::Maximized(true)));
        assert!(!state.apply(ShellEvent::Maximized(true)));
        assert!(state.apply(ShellEvent::Hover(Some(CaptionButton::Close))));
        assert!(!state.apply(ShellEvent::Hover(Some(CaptionButton::Close))));
        assert!(state.apply(ShellEvent::CaptionPressed));
        assert!(state.apply(ShellEvent::CaptionPressed));
        assert!(state.apply(ShellEvent::PageLoaded));
        assert!(state.apply(ShellEvent::HostStatus(Some(HostStatus::Recovering))));
        assert!(state.apply(ShellEvent::TitleBar(false)));
    }

    #[test]
    fn state_script_carries_the_page_contract() {
        let mut state = ShellState::default();
        state.apply(ShellEvent::Hover(Some(CaptionButton::Maximize)));
        state.apply(ShellEvent::HostStatus(Some(HostStatus::Recovering)));
        state.apply(ShellEvent::CaptionPressed);
        assert_eq!(
            state.script(),
            "window.__monkeyhubDesktopPush&&window.__monkeyhubDesktopPush(\
             {\"titleBar\":true,\"maximized\":false,\"active\":true,\"hover\":\"maximize\",\
             \"pressed\":null,\"hostStatus\":\"recovering\",\"captionPress\":1})"
        );
    }

    #[test]
    fn fallback_script_only_turns_the_merged_row_off() {
        assert_eq!(
            NATIVE_LAYOUT_SCRIPT,
            "window.__monkeyhubDesktopPush&&window.__monkeyhubDesktopPush({titleBar:false})"
        );
        // It goes through the same guarded push as ShellState::script, so documents without
        // the bridge (frames, other origins) ignore it.
        assert!(ShellState::default()
            .script()
            .starts_with("window.__monkeyhubDesktopPush&&window.__monkeyhubDesktopPush("));
    }

    #[test]
    fn bridge_is_limited_to_the_hub_origin_and_the_status_page() {
        let script = bridge_script(49152);
        assert!(script.contains(r#"origin !== "http://127.0.0.1:49152""#));
        assert!(script.contains(r#"origin !== "http://tauri.localhost""#));
        assert!(script.contains("titleBarHeight: 40"));
        assert!(script.contains("captionButtonWidth: 46"));
        assert!(script.contains("captionButtons: 3"));
        assert!(script.contains(r#""titleBar":true"#));
        assert!(!script.contains("__TAURI"));
    }
}
