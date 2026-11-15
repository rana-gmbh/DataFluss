//! One Django sidecar per open database. Requests go over its stdin, answers come back over its
//! stdout (see client/sidecar/sidecar.py for the frames). No port is opened.
//!
//! Cookies are kept here, not in the WebView: WKWebView keeps no cookies for custom schemes, and
//! page scripts never see the session this way.

use std::collections::{BTreeMap, HashMap};
use std::fs::File;
use std::io::{BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};
use tauri::http::{Request, Response};
use tokio::sync::{mpsc, oneshot, watch};

/// What the sidecar sees as the site. Django runs with its production settings for https.
pub const SIDECAR_ORIGIN: &str = "https://localhost";

/// The address of one database in the WebView: its own origin, so sign-ins and stored settings stay apart.
/// WebView2 serves custom schemes as `<http|https>://<scheme>.<host>`; https makes it a secure context,
/// like `app://` in WKWebView (database windows are built with `use_https_scheme(true)`).
pub fn webview_origin(id: &str) -> String {
    if cfg!(windows) {
        format!("https://app.{id}")
    } else {
        format!("app://{id}")
    }
}

/// The request headers as the sidecar gets them. Only this database's own windows reach a sidecar
/// (the scheme handler checks the window), so the shell vouches for the origin. That matters because
/// WebKit sends neither Origin nor Referer with an XMLHttpRequest on a custom scheme, and htmx saves
/// with one, so Django's CSRF check would refuse every inline edit. The CSRF token is still checked.
pub fn sidecar_headers<'a>(headers: impl Iterator<Item = (&'a str, &'a str)>, own: &str) -> Vec<[String; 2]> {
    let mut out: Vec<[String; 2]> = headers
        // No compression: WKWebView does not decode Content-Encoding for custom schemes, and a pipe gains nothing.
        .filter(|(name, _)| !name.eq_ignore_ascii_case("cookie") && !name.eq_ignore_ascii_case("accept-encoding"))
        .map(|(name, value)| {
            let is_origin = name.eq_ignore_ascii_case("origin") || name.eq_ignore_ascii_case("referer");
            let value = if is_origin && value.starts_with(own) { value.replacen(own, SIDECAR_ORIGIN, 1) } else { value.to_string() };
            [name.to_ascii_lowercase(), value]
        })
        .collect();
    if !out.iter().any(|[name, _]| name == "origin" || name == "referer") {
        out.push(["origin".into(), SIDECAR_ORIGIN.into()]);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn xhr_without_origin_gets_the_sidecar_origin() {
        let headers = sidecar_headers([("accept", "*/*"), ("x-csrftoken", "t")].into_iter(), "app://abc");
        assert!(headers.contains(&["origin".into(), SIDECAR_ORIGIN.into()]));
        assert!(headers.contains(&["x-csrftoken".into(), "t".into()]));
    }

    #[test]
    fn own_referer_is_translated_and_no_origin_added() {
        let headers = sidecar_headers([("referer", "app://abc/personen/")].into_iter(), "app://abc");
        assert_eq!(headers, vec![["referer".to_string(), "https://localhost/personen/".to_string()]]);
    }

    #[test]
    fn foreign_origin_is_kept_so_django_refuses_it() {
        let headers = sidecar_headers([("origin", "app://other")].into_iter(), "app://abc");
        assert_eq!(headers, vec![["origin".to_string(), "app://other".to_string()]]);
    }

    #[test]
    fn cookies_and_compression_are_dropped() {
        let headers = sidecar_headers([("cookie", "a=b"), ("Accept-Encoding", "gzip"), ("origin", "app://abc")].into_iter(), "app://abc");
        assert_eq!(headers, vec![["origin".to_string(), SIDECAR_ORIGIN.to_string()]]);
    }
}

type Reply = (Value, Vec<u8>);

pub struct Sidecar {
    pub id: String,
    pub log: PathBuf,
    stdin: Mutex<ChildStdin>,
    child: Mutex<Child>,
    pending: Mutex<HashMap<u64, oneshot::Sender<Reply>>>,
    next_id: AtomicU64,
    ready: watch::Receiver<bool>,
    events: tokio::sync::Mutex<mpsc::UnboundedReceiver<Value>>,
    cookies: Mutex<BTreeMap<String, String>>,
}

fn write_frame(out: &mut impl Write, header: &Value, body: &[u8]) -> std::io::Result<()> {
    let raw = serde_json::to_vec(header)?;
    let mut frame = Vec::with_capacity(8 + raw.len() + body.len());
    frame.extend_from_slice(&(raw.len() as u32).to_be_bytes());
    frame.extend_from_slice(&raw);
    frame.extend_from_slice(&(body.len() as u32).to_be_bytes());
    frame.extend_from_slice(body);
    out.write_all(&frame)?;
    out.flush()
}

fn read_part(input: &mut impl Read) -> std::io::Result<Vec<u8>> {
    let mut len = [0u8; 4];
    input.read_exact(&mut len)?;
    let mut data = vec![0u8; u32::from_be_bytes(len) as usize];
    input.read_exact(&mut data)?;
    Ok(data)
}

/// Keeps a cookie from a Set-Cookie header, or forgets it when Django deletes it.
fn store_cookie(jar: &mut BTreeMap<String, String>, raw: &str) {
    let mut parts = raw.split(';');
    let Some((name, value)) = parts.next().and_then(|p| p.split_once('=')) else { return };
    let (name, value) = (name.trim().to_string(), value.trim().trim_matches('"').to_string());
    let expired = parts.any(|attr| {
        let attr = attr.trim().to_ascii_lowercase();
        attr == "max-age=0" || attr.starts_with("max-age=-")
    });
    if expired || value.is_empty() {
        jar.remove(&name);
    } else {
        jar.insert(name, value);
    }
}

pub fn plain(status: u16, text: &str) -> Response<Vec<u8>> {
    Response::builder()
        .status(status)
        .header("content-type", "text/plain; charset=utf-8")
        .body(text.as_bytes().to_vec())
        .unwrap()
}

/// The last lines of the sidecar's log, for an error message on the start screen.
pub fn log_tail(path: &Path, lines: usize) -> String {
    let text = std::fs::read_to_string(path).unwrap_or_default();
    let all: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    all[all.len().saturating_sub(lines)..].join("\n")
}

impl Sidecar {
    /// `on_wipe` runs when the server revoked a connected database and the sidecar asks to remove it.
    pub fn start(program: &Path, id: &str, db_dir: &Path, env: Vec<(&str, String)>, on_wipe: Box<dyn Fn() + Send>) -> Result<Arc<Self>, String> {
        // One log per database, the previous start kept beside it.
        let log = db_dir.join("sidecar.log");
        if log.exists() {
            let _ = std::fs::rename(&log, db_dir.join("sidecar.1.log"));
        }
        let stderr = File::create(&log).map_err(|e| format!("{}: {e}", log.display()))?;
        let mut command = Command::new(program);
        command.env("SCHULE_DB_DIR", db_dir).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(stderr);
        #[cfg(windows)]
        {
            // The sidecar is a console program; without this flag Windows opens a console window for it.
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        for (key, value) in env {
            command.env(key, value);
        }
        let mut child = command.spawn().map_err(|e| format!("{}: {e}", program.display()))?;
        let stdin = child.stdin.take().expect("piped stdin");
        let stdout = child.stdout.take().expect("piped stdout");
        let (ready_tx, ready_rx) = watch::channel(false);
        let (events_tx, events_rx) = mpsc::unbounded_channel();
        let sidecar = Arc::new(Sidecar {
            id: id.to_string(),
            log,
            stdin: Mutex::new(stdin),
            child: Mutex::new(child),
            pending: Mutex::new(HashMap::new()),
            next_id: AtomicU64::new(1),
            ready: ready_rx,
            events: tokio::sync::Mutex::new(events_rx),
            cookies: Mutex::new(BTreeMap::new()),
        });
        let reader = sidecar.clone();
        std::thread::spawn(move || {
            let mut input = BufReader::new(stdout);
            loop {
                let (Ok(header), Ok(body)) = (read_part(&mut input), read_part(&mut input)) else { break };
                let Ok(header) = serde_json::from_slice::<Value>(&header) else { break };
                if header.get("wipe").is_some() {
                    eprintln!("[shell] {} was revoked by its server", reader.id);
                    on_wipe();
                } else if header.get("ready").is_some() {
                    eprintln!("[shell] {} ready: {header}", reader.id);
                    let _ = ready_tx.send(true);
                } else if header.get("task").is_some() || header.get("progress").is_some() {
                    let _ = events_tx.send(header);
                } else if let Some(waiting) = reader.pending.lock().unwrap().remove(&header["id"].as_u64().unwrap_or(0)) {
                    let _ = waiting.send((header, body));
                }
            }
            eprintln!("[shell] {} stopped", reader.id);
            reader.pending.lock().unwrap().clear(); // Every waiting request now gets an error page.
            drop(ready_tx);
            drop(events_tx);
        });
        Ok(sidecar)
    }

    /// Sends the one control frame of a new database and waits for its answer.
    pub async fn run_task(&self, task: &str, data: &Value, progress: impl Fn(&Value)) -> Result<Value, String> {
        let body = serde_json::to_vec(data).map_err(|e| e.to_string())?;
        write_frame(&mut *self.stdin.lock().unwrap(), &json!({"control": task}), &body).map_err(|_| self.failure())?;
        let mut events = self.events.lock().await;
        while let Some(event) = events.recv().await {
            if let Some(step) = event.get("progress") {
                progress(step);
            } else if event["task"] == "done" {
                return Ok(event);
            } else {
                return Err(event["error"].as_str().unwrap_or("Das hat nicht geklappt.").to_string());
            }
        }
        Err(self.failure())
    }

    pub async fn wait_ready(&self, timeout: Duration) -> Result<(), String> {
        let mut ready = self.ready.clone();
        let wait = async {
            while !*ready.borrow() {
                if ready.changed().await.is_err() {
                    return Err(self.failure());
                }
            }
            Ok(())
        };
        tokio::time::timeout(timeout, wait).await.map_err(|_| "Die Datenbank antwortet nicht.".to_string())?
    }

    fn failure(&self) -> String {
        let tail = log_tail(&self.log, 6);
        if tail.is_empty() {
            "Die Datenbank konnte nicht geöffnet werden.".into()
        } else {
            format!("Die Datenbank konnte nicht geöffnet werden.\n\n{tail}")
        }
    }

    pub async fn forward(&self, request: Request<Vec<u8>>) -> Response<Vec<u8>> {
        if self.wait_ready(Duration::from_secs(120)).await.is_err() {
            return plain(503, "Die Datenbank ist nicht erreichbar.");
        }
        let headers = sidecar_headers(
            request.headers().iter().map(|(name, value)| (name.as_str(), value.to_str().unwrap_or_default())),
            &webview_origin(&self.id),
        );
        let header_value = |name: &str| request.headers().get(name).and_then(|v| v.to_str().ok()).unwrap_or_default();
        let is_navigation = header_value("accept").contains("text/html")
            && header_value("hx-request").is_empty()
            && header_value("x-requested-with").is_empty();

        let (mut method, mut path, mut query) =
            (request.method().as_str().to_string(), request.uri().path().to_string(), request.uri().query().unwrap_or("").to_string());
        let mut body = request.body().clone();
        // WebKit does not follow redirects from a scheme handler. A page gets a refresh to the new address,
        // so the address and the back button stay right; HTMX and fetch get the result, as a browser would.
        for _ in 0..5 {
            let Some((status, response_headers, content)) = self.exchange(&method, &path, &query, &headers, &body).await else {
                return plain(503, "Die Datenbank ist nicht mehr erreichbar.");
            };
            let location = response_headers.iter().find(|(n, _)| n.eq_ignore_ascii_case("location")).map(|(_, v)| v.clone());
            match location {
                Some(target) if (300..400).contains(&status) && is_navigation => {
                    let target = target.replacen(SIDECAR_ORIGIN, "", 1).replace('"', "%22");
                    // The empty icon keeps WebView2 from asking for /favicon.ico for this page. That request
                    // ran beside the next page's, both without a cookie yet, so Django's 404 page set a
                    // second CSRF cookie over the login page's, and the first sign-in failed.
                    let page = format!("<!doctype html><meta charset=utf-8><link rel=icon href=\"data:,\"><meta http-equiv=refresh content=\"0;url={target}\">");
                    return Response::builder()
                        .status(200)
                        .header("content-type", "text/html; charset=utf-8")
                        .header("cache-control", "no-store")
                        .body(page.into_bytes())
                        .unwrap();
                }
                Some(target) if (300..400).contains(&status) => {
                    let target = target.replacen(SIDECAR_ORIGIN, "", 1);
                    let (next_path, next_query) = target.split_once('?').unwrap_or((&target, ""));
                    (method, path, query, body) = ("GET".into(), next_path.to_string(), next_query.to_string(), Vec::new());
                }
                _ => {
                    let mut response = Response::builder().status(status);
                    for (name, value) in response_headers {
                        response = response.header(name, value);
                    }
                    return response.body(content).unwrap_or_else(|_| plain(500, "Ungültige Antwort"));
                }
            }
        }
        plain(508, "Zu viele Weiterleitungen.")
    }

    /// One request to the sidecar. Cookies go in from the jar and come back into it.
    async fn exchange(&self, method: &str, path: &str, query: &str, headers: &[[String; 2]], body: &[u8]) -> Option<(u16, Vec<(String, String)>, Vec<u8>)> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let mut headers = headers.to_vec();
        {
            let jar = self.cookies.lock().unwrap();
            if !jar.is_empty() {
                headers.push(["cookie".into(), jar.iter().map(|(k, v)| format!("{k}={v}")).collect::<Vec<_>>().join("; ")]);
            }
        }
        let header = json!({"id": id, "method": method, "path": path, "query": query, "headers": headers});
        let (tx, rx) = oneshot::channel();
        self.pending.lock().unwrap().insert(id, tx);
        if write_frame(&mut *self.stdin.lock().unwrap(), &header, body).is_err() {
            self.pending.lock().unwrap().remove(&id);
            return None;
        }
        // A printout of many hundred pages takes a minute; nothing should take five.
        let Ok(answer) = tokio::time::timeout(Duration::from_secs(300), rx).await else {
            self.pending.lock().unwrap().remove(&id);
            eprintln!("[shell] {} gave no answer to {method} {path}", self.id);
            return None;
        };
        let (reply, content) = answer.ok()?;
        let mut kept = Vec::new();
        for pair in reply["headers"].as_array().into_iter().flatten() {
            let (Some(name), Some(value)) = (pair[0].as_str(), pair[1].as_str()) else { continue };
            if name.eq_ignore_ascii_case("set-cookie") {
                store_cookie(&mut self.cookies.lock().unwrap(), value);
            } else {
                kept.push((name.to_string(), value.to_string()));
            }
        }
        Some((reply["status"].as_u64().unwrap_or(500) as u16, kept, content))
    }

    pub fn stop(&self) {
        let mut child = self.child.lock().unwrap();
        let _ = child.kill();
        let _ = child.wait();
    }
}
