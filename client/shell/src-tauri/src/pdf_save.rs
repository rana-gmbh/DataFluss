//! The download button of WebKit's PDF viewer (in a printout window) on macOS.
//!
//! WebKit does not send that button through the download delegate. It asks the web view's UI
//! delegate to `_webView:saveDataToFile:suggestedFilename:mimeType:originatingURL:`, which wry's
//! delegate does not have, so the click did nothing. This adds the method to the delegate's class and
//! assigns the delegate again, because WebKit checks the delegate's methods only when it is assigned.
//!
//! The method is private WebKit API, which is fine for an app signed with a Developer ID but not for
//! the Mac App Store.

use std::ffi::c_void;
use std::sync::OnceLock;

use objc2::runtime::{AnyObject, Imp, Sel};
use objc2::{msg_send, sel};
use objc2_foundation::{NSData, NSString};

/// Where the saved file goes: set once at start, used from WebKit's callback.
pub static SAVE: OnceLock<Box<dyn Fn(&str, &[u8]) + Send + Sync>> = OnceLock::new();

unsafe extern "C-unwind" fn save_data_to_file(
    _this: *mut AnyObject,
    _cmd: Sel,
    _webview: *mut AnyObject,
    data: *mut NSData,
    suggested_filename: *mut NSString,
    _mime_type: *mut NSString,
    _originating_url: *mut AnyObject,
) {
    if data.is_null() {
        return;
    }
    let bytes = unsafe { (*data).to_vec() };
    let name = if suggested_filename.is_null() { "Bericht.pdf".to_string() } else { unsafe { (*suggested_filename).to_string() } };
    if let Some(save) = SAVE.get() {
        save(&name, &bytes);
    }
}

/// Call with the WKWebView of a window, on the main thread.
pub fn install(webview: *mut c_void) {
    if webview.is_null() {
        return;
    }
    unsafe {
        let webview = webview as *mut AnyObject;
        let delegate: *mut AnyObject = msg_send![webview, UIDelegate];
        if delegate.is_null() {
            return;
        }
        let class = (*delegate).class();
        let imp: Imp = std::mem::transmute(
            save_data_to_file
                as unsafe extern "C-unwind" fn(*mut AnyObject, Sel, *mut AnyObject, *mut NSData, *mut NSString, *mut NSString, *mut AnyObject),
        );
        // Adding fails harmlessly when an earlier window already added it to the shared class.
        objc2::ffi::class_addMethod(
            class as *const _ as *mut _,
            sel!(_webView:saveDataToFile:suggestedFilename:mimeType:originatingURL:),
            imp,
            c"v@:@@@@@".as_ptr(),
        );
        let _: () = msg_send![webview, setUIDelegate: delegate];
        let answers: bool = msg_send![delegate, respondsToSelector: sel!(_webView:saveDataToFile:suggestedFilename:mimeType:originatingURL:)];
        if answers {
            eprintln!("[shell] PDF download button connected");
        } else {
            eprintln!("[shell] the PDF viewer's download button stays without effect: delegate refused the method");
        }
    }
}
