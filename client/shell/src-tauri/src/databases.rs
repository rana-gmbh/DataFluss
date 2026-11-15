//! The databases on this computer. Each one is a folder under `databases/` with a `node.json`,
//! so there is no central list that could get out of step with the folders.

use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use base64::Engine;
use serde::{Deserialize, Serialize};
use tauri::Manager;

pub const SERVICE: &str = "de.rana.datafluss";
/// Every Keychain entry a database can have, as "<id>:<name>".
pub const KEY_NAMES: [&str; 5] = ["sqlcipher", "vault", "django", "media", "sync"];
const NODE_FILE: &str = "node.json";

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Node {
    pub id: String,
    pub name: String,
    /// "local" for a school's own database, "demo" for invented people, "connected" for a copy of a server.
    pub kind: String,
    /// The server a connected database syncs with.
    #[serde(default)]
    pub server: String,
    #[serde(default)]
    pub school: String,
    #[serde(default)]
    pub created: u64,
    #[serde(default)]
    pub last_opened: Option<u64>,
    /// Photos and attachments are sealed with the media key. Off only for databases from the spike.
    #[serde(default)]
    pub media_encrypted: bool,
}

pub fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

pub fn root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let root = app.path().app_data_dir().map_err(|e| e.to_string())?.join("databases");
    std::fs::create_dir_all(&root).map_err(|e| e.to_string())?;
    Ok(root)
}

/// Module bundles (schule/bundles.py), shared by every database on this computer. Each sidecar
/// checks their signatures before it loads one.
pub fn modules_root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let root = app.path().app_data_dir().map_err(|e| e.to_string())?.join("modules");
    std::fs::create_dir_all(&root).map_err(|e| e.to_string())?;
    Ok(root)
}

/// Lower-case letters and digits only, because the id is the host name of the database's address.
pub fn valid_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 32 && id.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
}

pub fn new_id() -> String {
    hex(&random(5))
}

pub fn folder(app: &tauri::AppHandle, id: &str) -> Result<PathBuf, String> {
    if !valid_id(id) {
        return Err("Ungültige Datenbank.".into());
    }
    Ok(root(app)?.join(id))
}

pub fn read(dir: &Path) -> Option<Node> {
    let id = dir.file_name()?.to_str()?.to_string();
    if !valid_id(&id) || !dir.join("db.sqlite").is_file() {
        return None;
    }
    match std::fs::read(dir.join(NODE_FILE)) {
        Ok(raw) => serde_json::from_slice::<Node>(&raw).ok().map(|node| Node { id, ..node }),
        // The spike's demo database has no node.json. Its photos are not sealed.
        Err(_) => {
            let node = Node { id, name: "Demo".into(), kind: "demo".into(), server: String::new(), school: String::new(), created: now(), last_opened: None, media_encrypted: false };
            write(dir, &node).ok()?;
            Some(node)
        }
    }
}

/// Written to a temporary file first, so a crash never leaves half a node.json.
pub fn write(dir: &Path, node: &Node) -> Result<(), String> {
    let partial = dir.join(format!("{NODE_FILE}.part"));
    std::fs::write(&partial, serde_json::to_vec_pretty(node).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    std::fs::rename(&partial, dir.join(NODE_FILE)).map_err(|e| e.to_string())
}

pub fn list(app: &tauri::AppHandle) -> Result<Vec<Node>, String> {
    let mut nodes: Vec<Node> = std::fs::read_dir(root(app)?)
        .map_err(|e| e.to_string())?
        .filter_map(|entry| entry.ok())
        .filter(|entry| entry.path().is_dir())
        .filter_map(|entry| read(&entry.path()))
        .collect();
    nodes.sort_by(|a, b| b.last_opened.unwrap_or(b.created).cmp(&a.last_opened.unwrap_or(a.created)).then(a.name.cmp(&b.name)));
    Ok(nodes)
}

pub fn size_bytes(dir: &Path) -> u64 {
    fn walk(dir: &Path) -> u64 {
        std::fs::read_dir(dir)
            .map(|entries| {
                entries
                    .filter_map(|e| e.ok())
                    .map(|e| match e.metadata() {
                        Ok(m) if m.is_dir() => walk(&e.path()),
                        Ok(m) => m.len(),
                        Err(_) => 0,
                    })
                    .sum()
            })
            .unwrap_or(0)
    }
    walk(dir)
}

pub fn random(bytes: usize) -> Vec<u8> {
    let mut buffer = vec![0u8; bytes];
    getrandom::fill(&mut buffer).expect("system random source");
    buffer
}

pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn b64(bytes: &[u8]) -> String {
    base64::engine::general_purpose::STANDARD.encode(bytes)
}

/// The keys of one database, from the Keychain. New keys are made only for a new database, because a
/// lost key must never be replaced silently: the old data would be unreadable.
pub struct Keys {
    pub sqlcipher: String,
    pub vault: String,
    pub django: String,
    pub media: String,
}

impl Keys {
    pub fn load(id: &str, is_new: bool, media_encrypted: bool) -> Result<Keys, String> {
        Ok(Keys {
            sqlcipher: secret(id, "sqlcipher", is_new, || hex(&random(32)))?,
            vault: secret(id, "vault", is_new, || b64(&random(32)))?,
            django: secret(id, "django", is_new, || b64(&random(48)))?,
            // Databases from the spike keep their files unsealed and need no media key.
            media: if media_encrypted { secret(id, "media", is_new, || b64(&random(32)))? } else { String::new() },
        })
    }

    /// Only for a database that never got past its first start, or one its server revoked.
    pub fn forget(id: &str) {
        for name in KEY_NAMES {
            if let Ok(entry) = keyring::Entry::new(SERVICE, &format!("{id}:{name}")) {
                let _ = entry.delete_credential();
            }
        }
    }
}

/// Where the keys are kept, for error messages: the macOS Keychain or the Windows Credential Manager.
#[cfg(windows)]
const KEY_STORE: (&str, &str) = ("Anmeldeinformationsverwaltung", "in der Windows-Anmeldeinformationsverwaltung");
#[cfg(not(windows))]
const KEY_STORE: (&str, &str) = ("Schlüsselbund", "im Schlüsselbund");

fn secret(id: &str, name: &str, may_create: bool, make: impl Fn() -> String) -> Result<String, String> {
    let entry = keyring::Entry::new(SERVICE, &format!("{id}:{name}")).map_err(|e| e.to_string())?;
    match entry.get_password() {
        Ok(value) => Ok(value),
        Err(keyring::Error::NoEntry) if !may_create => match crate::legacy::old_key(id, name) {
            // A database of the earlier Schulverwaltung client still has its keys under the old name.
            Some(found) => found,
            None => Err(format!("Der Schlüssel „{name}“ dieser Datenbank fehlt {}. Ohne ihn lässt sie sich nicht öffnen.", KEY_STORE.1)),
        },
        Err(keyring::Error::NoEntry) => {
            let value = make();
            entry.set_password(&value).map_err(|e| e.to_string())?;
            Ok(value)
        }
        Err(e) => Err(format!("{}: {e}", KEY_STORE.0)),
    }
}

/// A value the shell keeps for a database besides its keys, such as the device token of a connected one.
pub fn store(id: &str, name: &str, value: &str) -> Result<(), String> {
    keyring::Entry::new(SERVICE, &format!("{id}:{name}")).and_then(|e| e.set_password(value)).map_err(|e| format!("{}: {e}", KEY_STORE.0))
}

pub fn fetch(id: &str, name: &str) -> Result<String, String> {
    secret(id, name, false, String::new)
}
