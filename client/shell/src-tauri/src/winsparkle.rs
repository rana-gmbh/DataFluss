//! Updates on Windows through WinSparkle (https://winsparkle.org), the Windows twin of Sparkle.
//!
//! The release script puts WinSparkle.dll next to DataFluss.exe (tauri.windows.conf.json). It is
//! loaded here at start, so a build without it (`tauri dev`) simply has no updater. The feed is
//! `windows/appcast.xml` in rana-gmbh/DataFluss and every update must be signed with the same EdDSA
//! key as the macOS updates (SUPublicEDKey in Info.plist). WinSparkle shows its own windows, in
//! German, and runs the downloaded installer with `/P /UPDATE /R` (see client/release-windows.ps1):
//! passive, keeping the shortcuts, starting DataFluss again afterwards.

use std::ffi::{c_char, c_int, CString};
use std::sync::OnceLock;

use libloading::Library;
use tauri::AppHandle;

/// For trying an update locally, feed and key can be replaced when building:
/// DATAFLUSS_UPDATE_FEED=http://localhost:8000/appcast.xml DATAFLUSS_UPDATE_KEY=<public key>.
const FEED: &str = match option_env!("DATAFLUSS_UPDATE_FEED") {
    Some(feed) => feed,
    None => "https://raw.githubusercontent.com/rana-gmbh/DataFluss/main/windows/appcast.xml",
};
const PUBLIC_KEY: &str = match option_env!("DATAFLUSS_UPDATE_KEY") {
    Some(key) => key,
    None => "VCaae7N3kGR9hjuBaPMzl1XUXjN8MPSeVBa7zlLQAjA=",
};
const DAY: c_int = 86400;

struct WinSparkle {
    check_update_with_ui: unsafe extern "C" fn(),
    cleanup: unsafe extern "C" fn(),
    /// Kept as long as the app, so the functions above stay valid.
    _library: Library,
}

static UPDATER: OnceLock<WinSparkle> = OnceLock::new();
static APP: OnceLock<AppHandle> = OnceLock::new();

/// WinSparkle asks before it runs the installer; DataFluss has nothing that could stop it.
extern "C" fn can_shutdown() -> c_int {
    1
}

/// Called from WinSparkle's thread once the installer runs. Quitting stops the sidecars too
/// (RunEvent::Exit), so the installer can replace their files.
extern "C" fn shutdown_request() {
    if let Some(app) = APP.get() {
        app.exit(0);
    }
}

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}

/// Call once at start. WinSparkle then checks for updates once a day on its own.
pub fn start(app: &AppHandle) {
    let Some(dll) = std::env::current_exe().ok().and_then(|exe| Some(exe.parent()?.join("WinSparkle.dll"))) else { return };
    if !dll.exists() {
        return;
    }
    let _ = APP.set(app.clone());
    // The full path, so Windows never picks up a WinSparkle.dll from anywhere else.
    match unsafe { load(&dll, &app.package_info().version.to_string()) } {
        Ok(updater) => {
            let _ = UPDATER.set(updater);
        }
        Err(error) => eprintln!("[shell] WinSparkle could not be loaded: {error}"),
    }
}

unsafe fn load(dll: &std::path::Path, version: &str) -> Result<WinSparkle, libloading::Error> {
    let library = Library::new(dll)?;
    let set_lang = *library.get::<unsafe extern "C" fn(*const c_char)>(b"win_sparkle_set_lang\0")?;
    let set_appcast_url = *library.get::<unsafe extern "C" fn(*const c_char)>(b"win_sparkle_set_appcast_url\0")?;
    let set_public_key = *library.get::<unsafe extern "C" fn(*const c_char) -> c_int>(b"win_sparkle_set_eddsa_public_key\0")?;
    let set_app_details = *library.get::<unsafe extern "C" fn(*const u16, *const u16, *const u16)>(b"win_sparkle_set_app_details\0")?;
    let set_automatic = *library.get::<unsafe extern "C" fn(c_int)>(b"win_sparkle_set_automatic_check_for_updates\0")?;
    let set_interval = *library.get::<unsafe extern "C" fn(c_int)>(b"win_sparkle_set_update_check_interval\0")?;
    let last_check = *library.get::<unsafe extern "C" fn() -> i64>(b"win_sparkle_get_last_check_time\0")?;
    let set_can_shutdown = *library.get::<unsafe extern "C" fn(extern "C" fn() -> c_int)>(b"win_sparkle_set_can_shutdown_callback\0")?;
    let set_shutdown = *library.get::<unsafe extern "C" fn(extern "C" fn())>(b"win_sparkle_set_shutdown_request_callback\0")?;
    let init = *library.get::<unsafe extern "C" fn()>(b"win_sparkle_init\0")?;
    let check_update_with_ui = *library.get::<unsafe extern "C" fn()>(b"win_sparkle_check_update_with_ui\0")?;
    let cleanup = *library.get::<unsafe extern "C" fn()>(b"win_sparkle_cleanup\0")?;

    set_lang(c"de".as_ptr());
    let feed = CString::new(FEED).unwrap();
    set_appcast_url(feed.as_ptr());
    let key = CString::new(PUBLIC_KEY).unwrap();
    if set_public_key(key.as_ptr()) != 1 {
        eprintln!("[shell] WinSparkle refused the public key");
    }
    // Settings go to HKCU\Software\Rana GmbH\DataFluss\WinSparkle.
    set_app_details(wide("Rana GmbH").as_ptr(), wide("DataFluss").as_ptr(), wide(version).as_ptr());
    // Like Sparkle on the Mac (SUEnableAutomaticChecks): check daily without asking first. Only
    // before the first check, so a choice made later in WinSparkle's window stays.
    if last_check() == -1 {
        set_automatic(1);
        set_interval(DAY);
    }
    set_can_shutdown(can_shutdown);
    set_shutdown(shutdown_request);
    init();
    Ok(WinSparkle { check_update_with_ui, cleanup, _library: library })
}

/// Whether the menu should offer "Nach Updates suchen …".
pub fn available() -> bool {
    UPDATER.get().is_some()
}

/// The menu command: WinSparkle shows its own window with the result.
pub fn check_now() {
    if let Some(updater) = UPDATER.get() {
        unsafe { (updater.check_update_with_ui)() };
    }
}

/// At exit: stops WinSparkle's threads.
pub fn stop() {
    if let Some(updater) = UPDATER.get() {
        unsafe { (updater.cleanup)() };
    }
}
