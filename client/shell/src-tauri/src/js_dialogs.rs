//! JavaScript's alert(), confirm() and prompt() on macOS, and the question before leaving a page
//! with unsaved changes.
//!
//! WKWebView shows these only through its UI delegate, and wry's delegate leaves them out. Without
//! them confirm() answers "no" and prompt() nothing, at once: "+ Reiter" and "Veröffentlichen" in the
//! mask builder did nothing. This adds the methods to the delegate's class (the way pdf_save.rs does)
//! and shows each dialog as a sheet on the window.
//!
//! alert/confirm/prompt are public WKUIDelegate methods. The question before leaving is private
//! WebKit API (`_webView:runBeforeUnloadConfirmPanel…`), fine for a Developer ID app.

use std::ffi::c_void;

use block2::{Block, RcBlock};
use objc2::rc::Retained;
use objc2::runtime::{AnyObject, Bool, Imp, Sel};
use objc2::{class, msg_send, sel};
use objc2_foundation::{NSPoint, NSRect, NSSize, NSString};

const FIRST_BUTTON: isize = 1000; // NSAlertFirstButtonReturn

fn alert(message: *mut NSString, ok: &str, cancel: Option<&str>) -> Retained<AnyObject> {
    unsafe {
        let alert: Retained<AnyObject> = msg_send![class!(NSAlert), new];
        let text = if message.is_null() { NSString::new() } else { NSString::from_str(&(*message).to_string()) };
        let _: () = msg_send![&*alert, setMessageText: &*text];
        let _: *mut AnyObject = msg_send![&*alert, addButtonWithTitle: &*NSString::from_str(ok)];
        if let Some(cancel) = cancel {
            let button: *mut AnyObject = msg_send![&*alert, addButtonWithTitle: &*NSString::from_str(cancel)];
            let _: () = msg_send![button, setKeyEquivalent: &*NSString::from_str("\u{1b}")];
        }
        alert
    }
}

/// As a sheet on the web view's window; `done` gets the button's response. The alert stays alive
/// until then because `done`'s block owns it.
fn present(webview: *mut AnyObject, alert: Retained<AnyObject>, done: impl Fn(isize) + 'static) {
    unsafe {
        let window: *mut AnyObject = if webview.is_null() { std::ptr::null_mut() } else { msg_send![webview, window] };
        if window.is_null() {
            let response: isize = msg_send![&*alert, runModal];
            done(response);
            return;
        }
        let kept = alert.clone();
        let handler = RcBlock::new(move |response: isize| {
            let _ = &kept;
            done(response)
        });
        let _: () = msg_send![&*alert, beginSheetModalForWindow: window, completionHandler: &*handler];
    }
}

unsafe extern "C-unwind" fn run_alert(_this: *mut AnyObject, _cmd: Sel, webview: *mut AnyObject, message: *mut NSString, _frame: *mut AnyObject, handler: *mut Block<dyn Fn()>) {
    let Some(handler) = (unsafe { RcBlock::copy(handler) }) else { return };
    present(webview, alert(message, "OK", None), move |_| handler.call(()));
}

unsafe extern "C-unwind" fn run_confirm(_this: *mut AnyObject, _cmd: Sel, webview: *mut AnyObject, message: *mut NSString, _frame: *mut AnyObject, handler: *mut Block<dyn Fn(Bool)>) {
    let Some(handler) = (unsafe { RcBlock::copy(handler) }) else { return };
    present(webview, alert(message, "OK", Some("Abbrechen")), move |response| handler.call((Bool::new(response == FIRST_BUTTON),)));
}

unsafe extern "C-unwind" fn run_before_unload(_this: *mut AnyObject, _cmd: Sel, webview: *mut AnyObject, _message: *mut NSString, _frame: *mut AnyObject, handler: *mut Block<dyn Fn(Bool)>) {
    let Some(handler) = (unsafe { RcBlock::copy(handler) }) else { return };
    // Browsers ignore the page's own text here; this is the same question Safari asks.
    let question = NSString::from_str("Diese Seite verlassen? Nicht gespeicherte Änderungen gehen verloren.");
    let dialog = alert(Retained::as_ptr(&question) as *mut NSString, "Verlassen", Some("Bleiben"));
    present(webview, dialog, move |response| handler.call((Bool::new(response == FIRST_BUTTON),)));
}

unsafe extern "C-unwind" fn run_prompt(
    _this: *mut AnyObject,
    _cmd: Sel,
    webview: *mut AnyObject,
    message: *mut NSString,
    default_text: *mut NSString,
    _frame: *mut AnyObject,
    handler: *mut Block<dyn Fn(*mut NSString)>,
) {
    let Some(handler) = (unsafe { RcBlock::copy(handler) }) else { return };
    let dialog = alert(message, "OK", Some("Abbrechen"));
    unsafe {
        let default = if default_text.is_null() { NSString::new() } else { NSString::from_str(&(*default_text).to_string()) };
        let field: Retained<AnyObject> = msg_send![class!(NSTextField), textFieldWithString: &*default];
        let frame = NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(280.0, 24.0));
        let _: () = msg_send![&*field, setFrame: frame];
        let _: () = msg_send![&*dialog, setAccessoryView: &*field];
        let window: *mut AnyObject = msg_send![&*dialog, window];
        let _: () = msg_send![window, setInitialFirstResponder: &*field];
        present(webview, dialog, move |response| {
            if response == FIRST_BUTTON {
                let value: Retained<NSString> = msg_send![&*field, stringValue];
                handler.call((Retained::as_ptr(&value) as *mut NSString,));
            } else {
                handler.call((std::ptr::null_mut(),));
            }
        });
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
        let class = (*delegate).class() as *const _ as *mut _;
        type Panel<B> = unsafe extern "C-unwind" fn(*mut AnyObject, Sel, *mut AnyObject, *mut NSString, *mut AnyObject, *mut B);
        let methods: [(Sel, Imp, &std::ffi::CStr); 4] = [
            (sel!(webView:runJavaScriptAlertPanelWithMessage:initiatedByFrame:completionHandler:), std::mem::transmute(run_alert as Panel<Block<dyn Fn()>>), c"v@:@@@@?"),
            (sel!(webView:runJavaScriptConfirmPanelWithMessage:initiatedByFrame:completionHandler:), std::mem::transmute(run_confirm as Panel<Block<dyn Fn(Bool)>>), c"v@:@@@@?"),
            (sel!(_webView:runBeforeUnloadConfirmPanelWithMessage:initiatedByFrame:completionHandler:), std::mem::transmute(run_before_unload as Panel<Block<dyn Fn(Bool)>>), c"v@:@@@@?"),
            (
                sel!(webView:runJavaScriptTextInputPanelWithPrompt:defaultText:initiatedByFrame:completionHandler:),
                std::mem::transmute(
                    run_prompt as unsafe extern "C-unwind" fn(*mut AnyObject, Sel, *mut AnyObject, *mut NSString, *mut NSString, *mut AnyObject, *mut Block<dyn Fn(*mut NSString)>),
                ),
                c"v@:@@@@@?",
            ),
        ];
        // Adding fails harmlessly when an earlier window already added them to the shared class.
        for (selector, imp, types) in methods {
            objc2::ffi::class_addMethod(class, selector, imp, types.as_ptr());
        }
        // WebKit checks the delegate's methods only when it is assigned.
        let _: () = msg_send![webview, setUIDelegate: delegate];
    }
}
