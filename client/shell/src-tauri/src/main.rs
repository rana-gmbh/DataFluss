//! DataFluss, the desktop client of the Schulverwaltung.
//!
//! The start screen (window "launcher", files in ../ui) lists the databases on this computer and
//! creates or imports new ones. Each open database gets its own window "db-<id>" showing
//! `app://<id>/`, answered by that database's Django sidecar. See docs/PLAN.md §14.
//!
//! Only the start screen may call the shell's commands; every command checks the window it came
//! from. Database windows reach the shell only through a few fixed addresses under `/__client/`.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod databases;
#[cfg(target_os = "macos")]
mod js_dialogs;
mod legacy;
#[cfg(target_os = "macos")]
mod pdf_save;
mod sidecar;
#[cfg(target_os = "macos")]
mod sparkle;
#[cfg(windows)]
mod winsparkle;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};
use tauri::http::{Request, Response};
use tauri::menu::{MenuBuilder, MenuItemBuilder, SubmenuBuilder};
use tauri::webview::{DownloadEvent, NewWindowResponse, PageLoadEvent};
use tauri::{AppHandle, Emitter, Manager, RunEvent, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

use databases::{Keys, Node};
use sidecar::{plain, webview_origin, Sidecar};

const LAUNCHER: &str = "launcher";

#[derive(Default)]
struct AppState {
    open: Mutex<HashMap<String, Arc<Sidecar>>>,
    /// The PDF each window shows last (window label -> file name, bytes), for "Als PDF sichern".
    /// Only in memory, so a printout with passwords never lands on the disk unless someone saves it.
    pdfs: Mutex<HashMap<String, (String, Vec<u8>)>>,
    /// The database window used last, for Print, Save as PDF and Reload. While a menu is open no
    /// window counts as focused, so asking at that moment finds none.
    last_focused: Mutex<Option<String>>,
}

fn current_window(app: &AppHandle) -> Option<WebviewWindow> {
    let label = state(app).last_focused.lock().unwrap().clone()?;
    app.get_webview_window(&label)
}

fn state(app: &AppHandle) -> tauri::State<'_, AppState> {
    app.state::<AppState>()
}

fn window_label(id: &str) -> String {
    format!("db-{id}")
}

/// Every command checks that the start screen called it, not a database page.
fn from_launcher(window: &WebviewWindow) -> Result<(), String> {
    if window.label() == LAUNCHER {
        Ok(())
    } else {
        Err("Nicht erlaubt.".into())
    }
}

fn sidecar_program(app: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(path) = std::env::var("SCHULE_SIDECAR") {
        return Ok(PathBuf::from(path)); // For development: a sidecar outside the bundle.
    }
    let name = if cfg!(windows) { "schule-sidecar.exe" } else { "schule-sidecar" };
    Ok(app.path().resource_dir().map_err(|e| e.to_string())?.join("sidecar").join(name))
}

/// Starts the sidecar of one database. `task` is set only for a database that is being created.
fn start_sidecar(app: &AppHandle, node: &Node, task: Option<&str>) -> Result<Arc<Sidecar>, String> {
    let dir = databases::folder(app, &node.id)?;
    let keys = Keys::load(&node.id, task.is_some(), node.media_encrypted)?;
    let mut env = vec![
        ("SQLCIPHER_KEY", keys.sqlcipher),
        ("VAULT_KEY", keys.vault),
        ("SECRET_KEY", keys.django),
        ("SERVER_ID", format!("client-{}", node.id)),
        ("SCHULE_CLIENT_DB_NAME", node.name.clone()),
        ("DATAFLUSS_MODULE_PATH", databases::modules_root(app)?.to_string_lossy().into_owned()),
    ];
    // Dates and times in the computer's own zone (a server sets TIME_ZONE itself).
    if let Ok(zone) = iana_time_zone::get_timezone() {
        env.push(("TIME_ZONE", zone));
    }
    if node.media_encrypted {
        env.push(("MEDIA_KEY", keys.media));
    }
    if let Some(task) = task {
        env.push(("SIDECAR_TASK", task.to_string()));
    }
    if node.kind == "demo" {
        env.push(("ALLOW_DEMO_DATA", "1".into())); // Only ever invented people in a demo database.
    }
    if node.kind == "connected" && task.is_none() {
        env.push(("SYNC_TOKEN", databases::fetch(&node.id, "sync")?));
    }
    let wipe_app = app.clone();
    let wipe_id = node.id.clone();
    let on_wipe = Box::new(move || {
        let app = wipe_app.clone();
        let id = wipe_id.clone();
        let _ = wipe_app.run_on_main_thread(move || wipe_database(&app, &id));
    });
    Sidecar::start(&sidecar_program(app)?, &node.id, &dir, env, on_wipe)
}

/// The server revoked this computer's connection, so its copy of the school's data goes away for good.
fn wipe_database(app: &AppHandle, id: &str) {
    let name = databases::folder(app, id).ok().and_then(|dir| databases::read(&dir)).map(|n| n.name).unwrap_or_default();
    let main = window_label(id);
    let prefix = format!("{main}:");
    for (label, window) in app.webview_windows() {
        if label == main || label.starts_with(&prefix) {
            let _ = window.destroy();
        }
    }
    close_database(app, id);
    if let Ok(dir) = databases::folder(app, id) {
        let _ = std::fs::remove_dir_all(dir);
    }
    Keys::forget(id);
    refresh_menu(app);
    show_launcher(app, None);
    let _ = app.emit_to(LAUNCHER, "database-wiped", json!({"name": name}));
}

fn progress_to_launcher(app: &AppHandle, id: &str) -> impl Fn(&Value) {
    let app = app.clone();
    let id = id.to_string();
    move |step: &Value| {
        let _ = app.emit_to(LAUNCHER, "task-progress", json!({"id": id, "step": step}));
    }
}

// ---------------------------------------------------------------- windows

fn show_launcher(app: &AppHandle, action: Option<&str>) {
    if let Some(window) = app.get_webview_window(LAUNCHER) {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        if let Some(action) = action {
            let _ = app.emit_to(LAUNCHER, "launcher-action", action);
        }
        let _ = app.emit_to(LAUNCHER, "databases-changed", ());
    }
}

fn build_launcher(app: &AppHandle) -> tauri::Result<()> {
    WebviewWindowBuilder::new(app, LAUNCHER, WebviewUrl::App("index.html".into()))
        .title("DataFluss")
        .inner_size(900.0, 640.0)
        .min_inner_size(760.0, 560.0)
        .disable_drag_drop_handler()
        .on_navigation(|url| url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost"))
        .build()?;
    Ok(())
}

fn open_window(app: &AppHandle, node: &Node) -> Result<(), String> {
    let label = window_label(&node.id);
    if let Some(window) = app.get_webview_window(&label) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        return Ok(());
    }
    let url = format!("{}/", webview_origin(&node.id)).parse().map_err(|_| "Ungültige Adresse".to_string())?;
    let window = database_window(WebviewWindowBuilder::new(app, &label, WebviewUrl::CustomProtocol(url)), app, &node.id, &label)
        .title(&node.name)
        .inner_size(1360.0, 860.0)
        .min_inner_size(1000.0, 640.0)
        .visible(false) // Shown once the first page is there, so there is no white flash.
        .on_page_load(|window, payload| {
            if payload.event() == PageLoadEvent::Finished && !window.is_visible().unwrap_or(true) {
                let _ = window.show();
                let _ = window.set_focus();
            }
        })
        .build()
        .map_err(|e| e.to_string())?;
    extend_webkit(&window);
    Ok(())
}

/// Extra windows of a database (printouts and other target="_blank" pages) are labelled db-<id>:<n>.
static NEXT_CHILD: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

fn database_of_label(label: &str) -> Option<&str> {
    let rest = label.strip_prefix("db-")?;
    Some(rest.split_once(':').map(|(id, _)| id).unwrap_or(rest))
}

/// What every window of a database shares: its pages stay inside the app, other sites open in the
/// default browser, downloads land in Downloads, and target="_blank" opens another window of the app.
fn database_window<'a>(builder: WebviewWindowBuilder<'a, tauri::Wry, AppHandle>, app: &AppHandle, id: &str, own_label: &str) -> WebviewWindowBuilder<'a, tauri::Wry, AppHandle> {
    let origin = webview_origin(id);
    let opener = app.clone();
    let downloads = app.clone();
    let windows = app.clone();
    let db = id.to_string();
    let opener_label = own_label.to_string();
    // WebView2 drops the contents of files from form posts to app://; this sends them as bytes.
    #[cfg(windows)]
    let builder = builder.initialization_script(include_str!("upload_bytes.js"));
    builder
        // WebView2 then serves app:// as https://app.<id>, a secure context like app:// in WebKit.
        .use_https_scheme(true)
        // Tauri's file drop handler takes every drag for itself, so the page never got a drop and
        // dragging fields into a mask did nothing. Without it WebKit handles drags, files included.
        .disable_drag_drop_handler()
        .on_navigation(move |url| {
            if url.as_str().starts_with(&origin) || url.as_str() == "about:blank" {
                return true;
            }
            // Einstellungen → Aktualisierung of a local database: the app's own update check (Sparkle).
            if url.scheme() == "datafluss" && url.host_str() == Some("aktualisierung") {
                check_for_updates();
                return false;
            }
            if matches!(url.scheme(), "http" | "https" | "mailto") {
                let _ = opener.opener().open_url(url.as_str(), None::<&str>);
            }
            false
        })
        .on_download(move |_webview, event| {
            match event {
                DownloadEvent::Requested { destination, .. } => {
                    if let Ok(folder) = downloads.path().download_dir() {
                        let name = destination.file_name().map(|n| n.to_owned()).unwrap_or_else(|| "Download".into());
                        *destination = unique_path(folder.join(name));
                    }
                }
                DownloadEvent::Finished { path: Some(path), success: true, .. } => {
                    let _ = downloads.opener().reveal_item_in_dir(path);
                }
                _ => {}
            }
            true
        })
        // Printing is a form with target="_blank" whose answer is a PDF. WebKit sends the request,
        // form data included, into the window made here, which shows the PDF and prints with ⌘P.
        .on_new_window(move |url, features| {
            if !url.as_str().starts_with(&webview_origin(&db)) {
                if matches!(url.scheme(), "http" | "https" | "mailto") {
                    let _ = windows.opener().open_url(url.as_str(), None::<&str>);
                }
                return NewWindowResponse::Deny;
            }
            let label = format!("{}:{}", window_label(&db), NEXT_CHILD.fetch_add(1, std::sync::atomic::Ordering::Relaxed));
            let blank = WebviewUrl::External("about:blank".parse().expect("valid url"));
            let opener = opener_label.clone();
            let built = database_window(WebviewWindowBuilder::new(&windows, &label, blank), &windows, &db, &label)
                .window_features(features)
                .title("Druckansicht")
                // WebKit sends the request of a new window through the window that opened it, so the
                // PDF it shows is filed under the opener until this window has loaded and takes it.
                .on_page_load(move |window, payload| {
                    if payload.event() == PageLoadEvent::Finished {
                        let app = window.app_handle();
                        let shared = state(app);
                        let mut pdfs = shared.pdfs.lock().unwrap();
                        if !pdfs.contains_key(window.label()) {
                            if let Some(pdf) = pdfs.remove(&opener) {
                                pdfs.insert(window.label().to_string(), pdf);
                            }
                        }
                    }
                })
                .inner_size(900.0, 1000.0)
                .on_document_title_changed(|window, title| {
                    if !title.trim().is_empty() {
                        let _ = window.set_title(&title);
                    }
                })
                .build();
            match built {
                Ok(window) => {
                    extend_webkit(&window);
                    NewWindowResponse::Create { window }
                }
                Err(error) => {
                    eprintln!("[shell] new window: {error}");
                    NewWindowResponse::Deny
                }
            }
        })
}

fn unique_path(path: PathBuf) -> PathBuf {
    if !path.exists() {
        return path;
    }
    let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("Download").to_string();
    let ext = path.extension().and_then(|s| s.to_str()).map(|e| format!(".{e}")).unwrap_or_default();
    (2..1000).map(|n| path.with_file_name(format!("{stem} ({n}){ext}"))).find(|p| !p.exists()).unwrap_or(path)
}

/// Opens a database: starts its sidecar if needed, then shows its window.
async fn open_database(app: &AppHandle, id: &str) -> Result<(), String> {
    let dir = databases::folder(app, id)?;
    let mut node = databases::read(&dir).ok_or("Diese Datenbank gibt es nicht mehr.")?;
    for attempt in 0..2 {
        let existing = state(app).open.lock().unwrap().get(id).cloned();
        let sidecar = match existing {
            Some(sidecar) => sidecar,
            None => {
                let sidecar = start_sidecar(app, &node, None)?;
                state(app).open.lock().unwrap().insert(id.to_string(), sidecar.clone());
                sidecar
            }
        };
        match sidecar.wait_ready(Duration::from_secs(120)).await {
            Ok(()) => break,
            Err(error) => {
                // A module fetched from the server (e.g. after an update of the app) loads on a new start.
                let fetched = attempt == 0 && sidecar.exit_code() == Some(sidecar::MODULE_FETCHED);
                sidecar.stop();
                state(app).open.lock().unwrap().remove(id);
                if !fetched {
                    return Err(error);
                }
                eprintln!("[shell] {id}: module fetched from the server, starting again");
            }
        }
    }
    node.last_opened = Some(databases::now());
    let _ = databases::write(&dir, &node);
    let app2 = app.clone();
    let node2 = node.clone();
    app.run_on_main_thread(move || {
        if open_window(&app2, &node2).is_ok() {
            if let Some(launcher) = app2.get_webview_window(LAUNCHER) {
                let _ = launcher.hide();
            }
        }
        refresh_menu(&app2);
    })
    .map_err(|e| e.to_string())?;
    Ok(())
}

fn close_database(app: &AppHandle, id: &str) {
    if let Some(sidecar) = state(app).open.lock().unwrap().remove(id) {
        sidecar.stop();
    }
}

// ---------------------------------------------------------------- menu

fn refresh_menu(app: &AppHandle) {
    if let Ok(menu) = build_menu(app) {
        let _ = app.set_menu(menu);
    }
}

fn build_menu(app: &AppHandle) -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
    let open: Vec<Node> = {
        let ids: Vec<String> = state(app).open.lock().unwrap().keys().cloned().collect();
        let mut nodes: Vec<Node> = databases::list(app).unwrap_or_default().into_iter().filter(|n| ids.contains(&n.id)).collect();
        nodes.sort_by(|a, b| a.name.cmp(&b.name));
        nodes
    };
    #[cfg(target_os = "macos")]
    let mut window = SubmenuBuilder::new(app, "Fenster").minimize().maximize().separator();
    // Windows labels these items in English unless told otherwise.
    #[cfg(not(target_os = "macos"))]
    let mut window = SubmenuBuilder::new(app, "Fenster").minimize_with_text("Minimieren").maximize_with_text("Maximieren").separator();
    for (index, node) in open.iter().enumerate() {
        let mut item = MenuItemBuilder::with_id(format!("db:{}", node.id), &node.name);
        if index < 9 {
            item = item.accelerator(format!("CmdOrCtrl+{}", index + 1));
        }
        window = window.item(&item.build(app)?);
    }
    let window = window.build()?;
    /// The file menu's items both platforms share.
    fn databases<'a>(app: &AppHandle, menu: SubmenuBuilder<'a, tauri::Wry, AppHandle>) -> tauri::Result<SubmenuBuilder<'a, tauri::Wry, AppHandle>> {
        Ok(menu
            .item(&MenuItemBuilder::with_id("launcher", "Alle Datenbanken …").accelerator("CmdOrCtrl+Shift+O").build(app)?)
            .item(&MenuItemBuilder::with_id("new", "Neue Datenbank …").accelerator("CmdOrCtrl+N").build(app)?)
            .item(&MenuItemBuilder::with_id("import", "Sicherung importieren …").build(app)?)
            .separator()
            .item(&MenuItemBuilder::with_id("save-pdf", "Als PDF sichern").accelerator("CmdOrCtrl+S").build(app)?)
            .item(&MenuItemBuilder::with_id("print", "Drucken …").accelerator("CmdOrCtrl+P").build(app)?)
            .separator())
    }

    #[cfg(target_os = "macos")]
    {
        let app_menu = SubmenuBuilder::new(app, "DataFluss").about(None);
        let app_menu = if sparkle::available() {
            app_menu.item(&MenuItemBuilder::with_id("updates", "Nach Updates suchen …").build(app)?)
        } else {
            app_menu
        };
        let app_menu = app_menu
            .separator()
            .services()
            .separator()
            .hide()
            .hide_others()
            .show_all()
            .separator()
            .quit()
            .build()?;
        let file = databases(app, SubmenuBuilder::new(app, "Ablage"))?.close_window().build()?;
        let edit = SubmenuBuilder::new(app, "Bearbeiten").undo().redo().separator().cut().copy().paste().select_all().build()?;
        let view = SubmenuBuilder::new(app, "Darstellung")
            .item(&MenuItemBuilder::with_id("back", "Zurück").accelerator("CmdOrCtrl+[").build(app)?)
            .item(&MenuItemBuilder::with_id("forward", "Vorwärts").accelerator("CmdOrCtrl+]").build(app)?)
            .item(&MenuItemBuilder::with_id("reload", "Neu laden").accelerator("CmdOrCtrl+R").build(app)?)
            .separator()
            .fullscreen()
            .build()?;
        MenuBuilder::new(app).items(&[&app_menu, &file, &edit, &view, &window]).build()
    }
    // Windows: the menu bar of each window, with the names and keys Windows programs use. No Edit
    // menu: WebView2 handles Ctrl+C/V/X/Z/A and its context menu itself, and menu items for them
    // would only send messages to the window, which the web view does not get.
    #[cfg(not(target_os = "macos"))]
    {
        let file = databases(app, SubmenuBuilder::new(app, "Datei"))?
            .item(&MenuItemBuilder::with_id("close", "Fenster schließen").accelerator("Ctrl+W").build(app)?)
            .item(&MenuItemBuilder::with_id("quit", "Beenden").build(app)?)
            .build()?;
        let view = SubmenuBuilder::new(app, "Ansicht")
            .item(&MenuItemBuilder::with_id("back", "Zurück").accelerator("Alt+Left").build(app)?)
            .item(&MenuItemBuilder::with_id("forward", "Vorwärts").accelerator("Alt+Right").build(app)?)
            .item(&MenuItemBuilder::with_id("reload", "Neu laden").accelerator("F5").build(app)?)
            .separator()
            .item(&MenuItemBuilder::with_id("fullscreen", "Vollbild").accelerator("F11").build(app)?)
            .build()?;
        let help = SubmenuBuilder::new(app, "Hilfe");
        #[cfg(windows)]
        let help = if winsparkle::available() {
            help.item(&MenuItemBuilder::with_id("updates", "Nach Updates suchen …").build(app)?).separator()
        } else {
            help
        };
        let help = help
            .item(&MenuItemBuilder::with_id("welcome", "Erste Schritte …").build(app)?)
            .item(&MenuItemBuilder::with_id("tour", "Rundgang durch diese Datenbank").build(app)?)
            .separator();
        let help = help.about_with_text("Über DataFluss", None).build()?;
        MenuBuilder::new(app).items(&[&file, &view, &window, &help]).build()
    }
}

fn check_for_updates() {
    #[cfg(target_os = "macos")]
    sparkle::check_now();
    #[cfg(windows)]
    winsparkle::check_now();
}

fn on_menu(app: &AppHandle, id: &str) {
    match id {
        "launcher" => show_launcher(app, None),
        "updates" => check_for_updates(),
        "new" => show_launcher(app, Some("create")),
        "welcome" => show_launcher(app, Some("welcome")),
        "tour" => {
            // The tour lives in the database's pages (static/js/tour.js); the start screen has none.
            if let Some(window) = current_window(app).filter(|w| w.label() != LAUNCHER) {
                let _ = window.eval("window.startTour && window.startTour()");
            }
        }
        "import" => show_launcher(app, Some("import")),
        "save-pdf" => {
            let Some(window) = current_window(app) else { return };
            let pdf = state(app).pdfs.lock().unwrap().get(window.label()).cloned();
            match pdf {
                Some((name, bytes)) => {
                    if let Err(error) = save_download(app, &name, &bytes) {
                        eprintln!("[shell] PDF not saved: {error}");
                    }
                }
                None => {
                    let _ = app.dialog().message("Dieses Fenster zeigt kein PDF. Ein PDF entsteht über „Drucken“ in der Datenbank.").title("Kein PDF").kind(tauri_plugin_dialog::MessageDialogKind::Info).show(|_| {});
                }
            }
        }
        "print" => {
            if let Some(window) = current_window(app) {
                if let Err(error) = window.print() {
                    eprintln!("[shell] print: {error}");
                }
            }
        }
        // Windows only: macOS has these as predefined items of its menu.
        "close" => {
            if let Some(window) = app.webview_windows().into_values().find(|w| w.is_focused().unwrap_or(false)) {
                let _ = window.close();
            }
        }
        "quit" => app.exit(0),
        "fullscreen" => {
            if let Some(window) = current_window(app) {
                let _ = window.set_fullscreen(!window.is_fullscreen().unwrap_or(false));
            }
        }
        "reload" | "back" | "forward" => {
            let script = match id {
                "back" => "history.back()",
                "forward" => "history.forward()",
                _ => "location.reload()",
            };
            if let Some(window) = current_window(app) {
                let _ = window.eval(script);
            }
        }
        other => {
            if let Some(db) = other.strip_prefix("db:") {
                if let Some(window) = app.get_webview_window(&window_label(db)) {
                    let _ = window.unminimize();
                    let _ = window.set_focus();
                }
            }
        }
    }
}

// ---------------------------------------------------------------- commands for the start screen

#[derive(Serialize)]
struct Entry {
    #[serde(flatten)]
    node: Node,
    open: bool,
    size: u64,
    /// The regular backup of a database of its own (archive/plan.py): interval and last results, or null.
    backup: Value,
}

/// What the start screen needs from a database's backup plan. The plan's password stays in its file.
fn backup_status(dir: &std::path::Path, node: &Node) -> Value {
    if node.kind != "local" {
        return Value::Null;
    }
    let read = |name: &str| std::fs::read(dir.join("backups").join(name)).ok().and_then(|raw| serde_json::from_slice::<Value>(&raw).ok());
    let plan = read("plan.json");
    let last = read("plan-last.json").unwrap_or(Value::Null);
    json!({
        "every": plan.as_ref().and_then(|p| p.get("every").cloned()).unwrap_or(Value::Null),
        "ok_at": last.get("ok_at").cloned().unwrap_or(Value::Null),
        "state": last.get("state").cloned().unwrap_or(Value::Null),
    })
}

/// Errors of the start screen's scripts, so they show up in the app's log.
#[tauri::command]
fn report_error(window: WebviewWindow, message: String) -> Result<(), String> {
    from_launcher(&window)?;
    eprintln!("[launcher] {}", message.chars().take(2000).collect::<String>());
    Ok(())
}

#[tauri::command]
fn list_databases(app: AppHandle, window: WebviewWindow) -> Result<Vec<Entry>, String> {
    from_launcher(&window)?;
    let open = state(&app).open.lock().unwrap().keys().cloned().collect::<Vec<_>>();
    let root = databases::root(&app)?;
    Ok(databases::list(&app)?
        .into_iter()
        .map(|node| {
            let dir = root.join(&node.id);
            Entry { open: open.contains(&node.id), size: databases::size_bytes(&dir), backup: backup_status(&dir, &node), node }
        })
        .collect())
}

/// Creates a database in a new folder and runs its first task. On any failure the folder and its keys go away again.
async fn create_with_task(app: &AppHandle, name: &str, kind: &str, task: &str, data: Value) -> Result<(Node, Value), String> {
    let name = name.trim();
    if name.is_empty() {
        return Err("Bitte der Datenbank einen Namen geben.".into());
    }
    // Two databases with the same name would be hard to tell apart in the list and the window menu.
    let taken: Vec<String> = databases::list(app)?.into_iter().map(|n| n.name.to_lowercase()).collect();
    let name = (1..1000)
        .map(|n| if n == 1 { name.to_string() } else { format!("{name} {n}") })
        .find(|candidate| !taken.contains(&candidate.to_lowercase()))
        .unwrap_or_else(|| name.to_string());
    let name = name.as_str();
    let id = databases::new_id();
    let dir = databases::folder(app, &id)?;
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let mut node = Node { id: id.clone(), name: name.to_string(), kind: kind.into(), server: String::new(), school: String::new(), created: databases::now(), last_opened: None, media_encrypted: true };
    let undo = |error: String| {
        Keys::forget(&id);
        let _ = std::fs::remove_dir_all(&dir); // Our own new folder, nothing else is in it.
        error
    };
    let sidecar = start_sidecar(app, &node, Some(task)).map_err(undo)?;
    match sidecar.run_task(task, &data, progress_to_launcher(app, &id)).await {
        Ok(result) => {
            node.school = result["school"].as_str().unwrap_or_default().to_string();
            if node.school.is_empty() {
                node.school = data["school"].as_str().unwrap_or_default().to_string();
            }
            databases::write(&dir, &node).map_err(|e| {
                sidecar.stop();
                undo(e)
            })?;
            state(app).open.lock().unwrap().insert(id.clone(), sidecar);
            Ok((node, result))
        }
        Err(error) => {
            sidecar.stop();
            Err(undo(error))
        }
    }
}

#[tauri::command]
async fn create_database(app: AppHandle, window: WebviewWindow, name: String, school: String, username: String, password: String, modules: Option<Vec<String>>, without: Option<Vec<String>>, templates: Option<Vec<String>>) -> Result<String, String> {
    from_launcher(&window)?;
    let data = json!({"school": school, "username": username, "password": password, "modules": modules, "without": without, "templates": templates});
    let (node, _) = create_with_task(&app, &name, "local", "create", data).await?;
    Ok(node.id)
}

/// A demo with the optional modules picked in the wizard; without a pick (the start screen's
/// "Mit erfundenen Daten ausprobieren"), with the basic modules only.
#[tauri::command]
async fn create_demo(app: AppHandle, window: WebviewWindow, name: Option<String>, modules: Option<Vec<String>>, without: Option<Vec<String>>, templates: Option<Vec<String>>) -> Result<Value, String> {
    from_launcher(&window)?;
    let name = name.filter(|n| !n.trim().is_empty()).unwrap_or_else(|| "Demo".into());
    let modules = modules.unwrap_or_default();
    let data = json!({"modules": modules, "without": without.unwrap_or_default(), "templates": templates.unwrap_or_default()});
    let (node, result) = create_with_task(&app, &name, "demo", "demo", data).await?;
    Ok(json!({"id": node.id, "logins": result["logins"]}))
}

/// The optional modules a new database can use: those built into the sidecar (modules.json, written by
/// sidecar.spec) and the bundles of this version in the modules folder. The sidecar checks bundles again.
#[tauri::command]
fn available_modules(app: AppHandle, window: WebviewWindow) -> Result<Vec<Value>, String> {
    from_launcher(&window)?;
    let mut found: Vec<Value> = Vec::new();
    let mut add = |id: &str, title: &str| {
        if !id.is_empty() && !found.iter().any(|m| m["id"] == id) {
            found.push(json!({"id": id, "title": title}));
        }
    };
    if let Some(dir) = sidecar_program(&app)?.parent() {
        for file in [dir.join("_internal").join("modules.json"), dir.join("modules.json")] {
            if let Ok(text) = std::fs::read_to_string(&file) {
                let parsed: Value = serde_json::from_str(&text).unwrap_or_default();
                for module in parsed["optional"].as_array().cloned().unwrap_or_default() {
                    add(module["id"].as_str().unwrap_or_default(), module["title"].as_str().unwrap_or_default());
                }
                break;
            }
        }
    }
    let version = app.package_info().version.to_string();
    if let Ok(entries) = std::fs::read_dir(databases::modules_root(&app)?) {
        for entry in entries.flatten() {
            let manifest = entry.path().join(&version).join("manifest.json");
            if let Ok(text) = std::fs::read_to_string(manifest) {
                let parsed: Value = serde_json::from_str(&text).unwrap_or_default();
                add(parsed["id"].as_str().unwrap_or_default(), parsed["title"].as_str().unwrap_or_default());
            }
        }
    }
    Ok(found)
}

/// The module templates a new database can start with (tables/blueprints.py): those of the build (modules.json,
/// written by sidecar.spec) and those in the bundles of this version. The wizard offers the database templates
/// ("database": true) as starting points of their own; the sidecar checks them again.
#[tauri::command]
fn available_templates(app: AppHandle, window: WebviewWindow) -> Result<Vec<Value>, String> {
    from_launcher(&window)?;
    let mut found: Vec<Value> = Vec::new();
    let mut add = |template: Value| {
        let id = template["id"].as_str().unwrap_or_default().to_string();
        if !id.is_empty() && !found.iter().any(|t| t["id"] == id.as_str()) {
            // has_demo: written by sidecar.spec for the build's templates; a bundle's file has its "demo" itself.
            let has_demo = template["has_demo"].as_bool().unwrap_or(false) || template["demo"].as_object().is_some_and(|demo| !demo.is_empty());
            found.push(json!({"id": id, "title": template["title"], "titles": template["titles"], "description": template["description"],
                              "descriptions": template["descriptions"], "requires": template["requires"], "database": template["database"],
                              "order": template["order"], "listed": template["listed"], "has_demo": has_demo}));
        }
    };
    if let Some(dir) = sidecar_program(&app)?.parent() {
        for file in [dir.join("_internal").join("modules.json"), dir.join("modules.json")] {
            if let Ok(text) = std::fs::read_to_string(&file) {
                let parsed: Value = serde_json::from_str(&text).unwrap_or_default();
                for template in parsed["templates"].as_array().cloned().unwrap_or_default() {
                    add(template);
                }
                break;
            }
        }
    }
    let version = app.package_info().version.to_string();
    if let Ok(entries) = std::fs::read_dir(databases::modules_root(&app)?) {
        for entry in entries.flatten() {
            let folder = entry.path().join(&version);
            let Ok(text) = std::fs::read_to_string(folder.join("manifest.json")) else { continue };
            let manifest: Value = serde_json::from_str(&text).unwrap_or_default();
            for name in manifest["templates"].as_array().cloned().unwrap_or_default() {
                let Some(name) = name.as_str() else { continue };
                if name.contains("..") {
                    continue;
                }
                if let Ok(text) = std::fs::read_to_string(folder.join(name)) {
                    add(serde_json::from_str(&text).unwrap_or_default());
                }
            }
        }
    }
    Ok(found)
}

#[tauri::command]
async fn import_backup(app: AppHandle, window: WebviewWindow, name: String, path: String, password: String, keep_secrets: bool) -> Result<String, String> {
    from_launcher(&window)?;
    let data = json!({"path": path, "password": password, "keep_secrets": keep_secrets});
    let (node, _) = create_with_task(&app, &name, "local", "restore", data).await?;
    Ok(node.id)
}

/// Connects a new database to a server with the user's own login and second factor, and makes the first copy.
#[tauri::command]
async fn connect_server(app: AppHandle, window: WebviewWindow, name: String, url: String, username: String, password: String, code: String) -> Result<String, String> {
    from_launcher(&window)?;
    let (computer, user) = if cfg!(windows) { ("PC", "USERNAME") } else { ("Mac", "USER") };
    let device = std::env::var(user).map(|u| format!("{computer} von {u}")).unwrap_or_else(|_| computer.into());
    let data = json!({"url": url, "username": username, "password": password, "code": code, "name": device});
    let wanted = name.trim().to_string();
    let (mut node, result) = create_with_task(&app, if wanted.is_empty() { "Server" } else { &wanted }, "connected", "connect", data).await?;
    let token = result["token"].as_str().unwrap_or_default();
    let vault = result["vault_key"].as_str().unwrap_or_default();
    databases::store(&node.id, "sync", token)?;
    // From now on this database uses the server's vault key, so passwords and 2FA codes work offline.
    databases::store(&node.id, "vault", vault)?;
    node.server = result["url"].as_str().unwrap_or_default().to_string();
    node.school = result["school"].as_str().unwrap_or_default().to_string();
    if wanted.is_empty() && !node.school.is_empty() {
        node.name = node.school.clone();
    }
    databases::write(&databases::folder(&app, &node.id)?, &node)?;
    Ok(node.id)
}

#[tauri::command]
async fn pick_backup(app: AppHandle, window: WebviewWindow) -> Result<Option<String>, String> {
    from_launcher(&window)?;
    let (tx, rx) = tokio::sync::oneshot::channel();
    app.dialog()
        .file()
        .set_title("Sicherung auswählen")
        .add_filter("Sicherung der Schulverwaltung", &["svbackup"])
        .set_parent(&window)
        .pick_file(move |file| {
            let _ = tx.send(file.and_then(|f| f.into_path().ok()));
        });
    Ok(rx.await.ok().flatten().map(|p| p.to_string_lossy().into_owned()))
}

/// Whether the welcome of the start screen was seen on this computer (a file in the app data folder).
#[tauri::command]
fn welcome_seen(app: AppHandle, window: WebviewWindow) -> Result<bool, String> {
    from_launcher(&window)?;
    Ok(app.path().app_data_dir().map_err(|e| e.to_string())?.join("welcome-seen").exists())
}

#[tauri::command]
fn welcome_done(app: AppHandle, window: WebviewWindow) -> Result<(), String> {
    from_launcher(&window)?;
    let dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    std::fs::write(dir.join("welcome-seen"), b"").map_err(|e| e.to_string())
}

/// Copies a module bundle (.dfmodule) into the modules folder. Its signature is checked by the next
/// sidecar that starts, which loads it only if it holds (schule/bundles.py).
#[tauri::command]
async fn install_module(app: AppHandle, window: WebviewWindow) -> Result<Option<String>, String> {
    from_launcher(&window)?;
    let (tx, rx) = tokio::sync::oneshot::channel();
    app.dialog()
        .file()
        .set_title("Modul auswählen")
        .add_filter("DataFluss-Modul", &["dfmodule"])
        .set_parent(&window)
        .pick_file(move |file| {
            let _ = tx.send(file.and_then(|f| f.into_path().ok()));
        });
    let Some(path) = rx.await.ok().flatten() else { return Ok(None) };
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    if !name.ends_with(".dfmodule") {
        return Err("Das ist keine Moduldatei (.dfmodule).".into());
    }
    std::fs::copy(&path, databases::modules_root(&app)?.join(&name)).map_err(|e| e.to_string())?;
    Ok(Some(name))
}

#[tauri::command]
async fn open_db(app: AppHandle, window: WebviewWindow, id: String) -> Result<(), String> {
    from_launcher(&window)?;
    open_database(&app, &id).await
}

#[tauri::command]
fn reveal_database(app: AppHandle, window: WebviewWindow, id: String) -> Result<(), String> {
    from_launcher(&window)?;
    let dir = databases::folder(&app, &id)?;
    app.opener().reveal_item_in_dir(dir.join("db.sqlite")).map_err(|e| e.to_string())
}

#[tauri::command]
fn demo_logins(app: AppHandle, window: WebviewWindow, id: String) -> Result<Vec<Value>, String> {
    from_launcher(&window)?;
    let dir = databases::folder(&app, &id)?;
    let node = databases::read(&dir).ok_or("Diese Datenbank gibt es nicht mehr.")?;
    if node.kind != "demo" {
        return Err("Nur Demo-Datenbanken haben Anmeldedaten in ihrem Ordner.".into());
    }
    let text = std::fs::read_to_string(dir.join(".secrets").join("demo-logins.txt")).unwrap_or_default();
    Ok(text.lines().filter_map(|l| l.split_once('\t')).map(|(u, p)| json!({"username": u, "password": p})).collect())
}

/// Moves the folder to the Trash. The keys stay in the Keychain, so putting the folder back restores it.
#[tauri::command]
fn remove_database(app: AppHandle, window: WebviewWindow, id: String) -> Result<(), String> {
    from_launcher(&window)?;
    let dir = databases::folder(&app, &id)?;
    if let Some(db_window) = app.get_webview_window(&window_label(&id)) {
        let _ = db_window.destroy();
    }
    close_database(&app, &id);
    trash::delete(&dir).map_err(|e| format!("Konnte nicht in den Papierkorb gelegt werden: {e}"))?;
    refresh_menu(&app);
    Ok(())
}

// ---------------------------------------------------------------- the app:// scheme

/// The few addresses a database page may ask the shell for: the list for the sidebar's switcher,
/// opening another database, and the start screen.
async fn client_endpoint(app: &AppHandle, current: &str, request: &Request<Vec<u8>>) -> Response<Vec<u8>> {
    let path = request.uri().path().trim_start_matches("/__client/");
    let json_response = |value: Value| {
        Response::builder().status(200).header("content-type", "application/json").header("cache-control", "no-store").body(value.to_string().into_bytes()).unwrap()
    };
    if path == "databases" {
        let open = state(app).open.lock().unwrap().keys().cloned().collect::<Vec<_>>();
        let items: Vec<Value> = databases::list(app)
            .unwrap_or_default()
            .into_iter()
            .map(|n| json!({"id": n.id, "name": n.name, "school": n.school, "kind": n.kind, "open": open.contains(&n.id)}))
            .collect();
        return json_response(json!({"current": current, "items": items}));
    }
    if request.method() != "POST" {
        return plain(405, "Nur POST");
    }
    if path == "launcher" {
        let app2 = app.clone();
        let _ = app.run_on_main_thread(move || show_launcher(&app2, None));
        return Response::builder().status(204).body(Vec::new()).unwrap();
    }
    if path == "pick-folder" {
        // The folder of the regular backup (archive/plan.py). The page gets the path, never a file.
        let (tx, rx) = tokio::sync::oneshot::channel();
        let mut dialog = app.dialog().file().set_title("Ordner für die Sicherungen");
        if let Some(window) = app.get_webview_window(&window_label(current)) {
            dialog = dialog.set_parent(&window);
        }
        dialog.pick_folder(move |folder| {
            let _ = tx.send(folder.and_then(|f| f.into_path().ok()));
        });
        let path = rx.await.ok().flatten().map(|p| p.to_string_lossy().into_owned());
        return json_response(json!({"path": path}));
    }
    if path == "connect" {
        // After a transfer to a server (archive/transfer.py): the start screen's connect sheet, with its address.
        let url = serde_json::from_slice::<Value>(request.body()).ok().and_then(|v| v.get("url").and_then(|u| u.as_str()).map(str::to_string)).unwrap_or_default();
        let app2 = app.clone();
        let _ = app.run_on_main_thread(move || {
            show_launcher(&app2, None);
            let _ = app2.emit_to(LAUNCHER, "launcher-connect", json!({"url": url}));
        });
        return Response::builder().status(204).body(Vec::new()).unwrap();
    }
    if let Some(id) = path.strip_prefix("open/") {
        return match open_database(app, id).await {
            Ok(()) => Response::builder().status(204).body(Vec::new()).unwrap(),
            Err(error) => plain(409, &error),
        };
    }
    plain(404, "Unbekannt")
}

/// The file name from a Content-Disposition header, as Django writes it (filename*=utf-8''… or filename="…").
fn attachment_name(header: &str) -> Option<String> {
    if !header.trim_start().to_ascii_lowercase().starts_with("attachment") {
        return None;
    }
    disposition_name(header)
}

fn disposition_name(header: &str) -> Option<String> {
    let mut plain_name = None;
    for part in header.split(';').map(str::trim) {
        if let Some(encoded) = part.strip_prefix("filename*=").and_then(|v| v.split_once("''").map(|(_, n)| n.to_string())) {
            let bytes: Vec<u8> = {
                let raw = encoded.as_bytes();
                let mut out = Vec::with_capacity(raw.len());
                let mut i = 0;
                while i < raw.len() {
                    if raw[i] == b'%' && i + 2 < raw.len() {
                        let digits = std::str::from_utf8(&raw[i + 1..i + 3]).unwrap_or("");
                        if let Ok(b) = u8::from_str_radix(digits, 16) {
                            out.push(b);
                            i += 3;
                            continue;
                        }
                    }
                    out.push(raw[i]);
                    i += 1;
                }
                out
            };
            return Some(String::from_utf8_lossy(&bytes).into_owned());
        }
        if let Some(name) = part.strip_prefix("filename=") {
            plain_name = Some(name.trim_matches('"').to_string());
        }
    }
    Some(plain_name.unwrap_or_else(|| "Download".into()))
}

/// WKWebView shows attachments of a custom scheme inline instead of saving them, and the page is then
/// stuck on a text file. So the shell saves them to Downloads, shows them in the Finder, and the page
/// gets "204 No Content", which leaves it where it was.
fn save_if_attachment(app: &AppHandle, response: Response<Vec<u8>>) -> Response<Vec<u8>> {
    let Some(name) = response.headers().get("content-disposition").and_then(|v| v.to_str().ok()).and_then(attachment_name) else {
        return response;
    };
    match save_download(app, &name, response.body()) {
        Ok(_) => Response::builder().status(204).body(Vec::new()).unwrap(),
        Err(error) => plain(500, &format!("Die Datei konnte nicht gespeichert werden: {error}")),
    }
}

fn remember_pdf(app: &AppHandle, label: &str, response: &Response<Vec<u8>>) {
    let is_pdf = response.headers().get("content-type").and_then(|v| v.to_str().ok()).is_some_and(|t| t.starts_with("application/pdf"));
    if !is_pdf || response.status() != 200 {
        return;
    }
    let name = response.headers().get("content-disposition").and_then(|v| v.to_str().ok()).and_then(disposition_name).unwrap_or_else(|| "Bericht.pdf".into());
    state(app).pdfs.lock().unwrap().insert(label.to_string(), (name, response.body().clone()));
}

/// Saves a file to Downloads under a free name and shows it in the Finder.
fn save_download(app: &AppHandle, name: &str, bytes: &[u8]) -> Result<PathBuf, String> {
    // Only a plain file name, never a path.
    let name: String = name.chars().map(|c| if matches!(c, '/' | '\\' | ':' | '\0') { '_' } else { c }).collect();
    let name = if name.trim_matches('.').is_empty() { "Download".to_string() } else { name };
    let target = unique_path(app.path().download_dir().map_err(|e| e.to_string())?.join(&name));
    std::fs::write(&target, bytes).map_err(|e| e.to_string())?;
    let _ = app.opener().reveal_item_in_dir(&target);
    Ok(target)
}

/// What wry's WebKit delegate leaves out: alert/confirm/prompt (js_dialogs.rs) and the download
/// button of WebKit's PDF viewer (pdf_save.rs).
fn extend_webkit(window: &WebviewWindow) {
    #[cfg(target_os = "macos")]
    let _ = window.with_webview(|webview| {
        js_dialogs::install(webview.inner());
        pdf_save::install(webview.inner());
    });
    #[cfg(not(target_os = "macos"))]
    let _ = window;
}

fn database_of(request: &Request<Vec<u8>>) -> Option<String> {
    let host = request.uri().host()?;
    let id = host.strip_prefix("app.").unwrap_or(host); // Windows shows app://<id> as https://app.<id>
    databases::valid_id(id).then(|| id.to_string())
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        // Without its click handler: it sends every http(s) link with target="_blank" to the default
        // browser, and on Windows the database pages are https://app.<id>/ themselves, so the print
        // links of Unterricht und Noten never opened. database_window's handlers open outside links.
        .plugin(tauri_plugin_opener::Builder::new().open_js_links_on_click(false).build())
        .manage(AppState::default())
        .register_asynchronous_uri_scheme_protocol("app", |ctx, request, responder| {
            let app = ctx.app_handle().clone();
            // A page may only reach the database of its own window, never another one.
            let Some(id) = database_of(&request).filter(|id| database_of_label(ctx.webview_label()) == Some(id.as_str())) else {
                responder.respond(plain(403, "Nicht erlaubt."));
                return;
            };
            let label = ctx.webview_label().to_string();
            tauri::async_runtime::spawn(async move {
                if request.uri().path().starts_with("/__client/") {
                    responder.respond(client_endpoint(&app, &id, &request).await);
                    return;
                }
                let sidecar = state(&app).open.lock().unwrap().get(&id).cloned();
                match sidecar {
                    Some(sidecar) => {
                        let response = sidecar.forward(request).await;
                        remember_pdf(&app, &label, &response);
                        responder.respond(save_if_attachment(&app, response));
                    }
                    None => responder.respond(plain(503, "Diese Datenbank ist geschlossen.")),
                }
            });
        })
        .invoke_handler(tauri::generate_handler![
            report_error,
            list_databases,
            create_database,
            create_demo,
            import_backup,
            connect_server,
            pick_backup,
            install_module,
            available_modules,
            available_templates,
            welcome_seen,
            welcome_done,
            open_db,
            reveal_database,
            demo_logins,
            remove_database
        ])
        .on_menu_event(|app, event| on_menu(app, event.id().as_ref()))
        .on_window_event(|window, event| {
            let app = window.app_handle();
            let label = window.label().to_string();
            if matches!(event, WindowEvent::Focused(true)) && label.starts_with("db-") {
                *state(app).last_focused.lock().unwrap() = Some(label.clone());
            }
            if matches!(event, WindowEvent::Destroyed) {
                state(app).pdfs.lock().unwrap().remove(&label);
                let shared = state(app);
                let mut last = shared.last_focused.lock().unwrap();
                if last.as_deref() == Some(label.as_str()) {
                    *last = None;
                }
            }
            match event {
                // The start screen hides while databases are open; closing it with none open quits.
                WindowEvent::CloseRequested { api, .. } if label == LAUNCHER => {
                    if !state(app).open.lock().unwrap().is_empty() {
                        api.prevent_close();
                        let _ = window.hide();
                    }
                }
                // Only the main window of a database stops its sidecar, and it takes the extra windows with it.
                WindowEvent::Destroyed if label.starts_with("db-") && !label.contains(':') => {
                    if let Some(id) = database_of_label(&label) {
                        eprintln!("[shell] window of {id} closed");
                        let prefix = format!("{label}:");
                        for (other, window) in app.webview_windows() {
                            if other.starts_with(&prefix) {
                                let _ = window.destroy();
                            }
                        }
                        close_database(app, id);
                        refresh_menu(app);
                        if state(app).open.lock().unwrap().is_empty() {
                            show_launcher(app, None);
                        }
                    }
                }
                _ => {}
            }
        })
        .setup(|app| {
            #[cfg(target_os = "macos")]
            {
                let handle = app.handle().clone();
                let _ = pdf_save::SAVE.set(Box::new(move |name: &str, bytes: &[u8]| {
                    if let Err(error) = save_download(&handle, name, bytes) {
                        eprintln!("[shell] PDF not saved: {error}");
                    }
                }));
            }
            legacy::take_over(app.handle());
            #[cfg(target_os = "macos")]
            sparkle::start();
            #[cfg(windows)]
            winsparkle::start(app.handle());
            build_launcher(app.handle())?;
            refresh_menu(app.handle());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Tauri app");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            for (_, sidecar) in state(app).open.lock().unwrap().drain() {
                sidecar.stop();
            }
            #[cfg(windows)]
            winsparkle::stop();
        }
    });
}
