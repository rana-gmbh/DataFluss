//! Updates on macOS through Sparkle (https://sparkle-project.org).
//!
//! The release script puts Sparkle.framework into the app's Frameworks folder (tauri.macos.conf.json)
//! and signs it. It is loaded here at start, so the shell needs no link-time setup and a build without
//! the framework (`tauri dev`) simply has no updater. The feed and the public key that checks each
//! update come from Info.plist: SUFeedURL, SUPublicEDKey.

use std::ptr;
use std::sync::atomic::{AtomicPtr, Ordering};

use objc2::rc::{Allocated, Retained};
use objc2::runtime::{AnyClass, AnyObject, Bool};
use objc2::msg_send;
use objc2_foundation::NSString;

/// The SPUStandardUpdaterController, alive as long as the app.
static UPDATER: AtomicPtr<AnyObject> = AtomicPtr::new(ptr::null_mut());

/// Call once on the main thread. Sparkle then checks for updates on its own schedule.
pub fn start() {
    let Some(framework) = std::env::current_exe().ok().and_then(|exe| Some(exe.parent()?.parent()?.join("Frameworks/Sparkle.framework"))) else {
        return;
    };
    if !framework.exists() {
        return;
    }
    unsafe {
        let Some(bundle_class) = AnyClass::get(c"NSBundle") else { return };
        let path = NSString::from_str(&framework.to_string_lossy());
        let bundle: *mut AnyObject = msg_send![bundle_class, bundleWithPath: &*path];
        if bundle.is_null() {
            return;
        }
        let loaded: Bool = msg_send![bundle, load];
        let Some(controller) = AnyClass::get(c"SPUStandardUpdaterController").filter(|_| loaded.as_bool()) else {
            eprintln!("[shell] Sparkle could not be loaded");
            return;
        };
        let allocated: Allocated<AnyObject> = msg_send![controller, alloc];
        let none: *mut AnyObject = ptr::null_mut();
        let updater: Option<Retained<AnyObject>> =
            msg_send![allocated, initWithStartingUpdater: Bool::YES, updaterDelegate: none, userDriverDelegate: none];
        if let Some(updater) = updater {
            UPDATER.store(Retained::into_raw(updater), Ordering::SeqCst);
        }
    }
}

/// Whether the menu should offer "Nach Updates suchen …".
pub fn available() -> bool {
    !UPDATER.load(Ordering::SeqCst).is_null()
}

/// The menu command: Sparkle shows its own window with the result.
pub fn check_now() {
    let updater = UPDATER.load(Ordering::SeqCst);
    if updater.is_null() {
        return;
    }
    unsafe {
        let sender: *mut AnyObject = ptr::null_mut();
        let _: () = msg_send![updater, checkForUpdates: sender];
    }
}
