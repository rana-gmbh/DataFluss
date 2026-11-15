//! The client was called Schulverwaltung, with the app id de.rana.schulverwaltung. DataFluss
//! (de.rana.datafluss) takes its databases over.
//!
//! At start only the databases folder moves; the Keychain is not touched, so the start never waits
//! for a question. The keys of a database are copied from the old Keychain service when that database
//! opens (databases::secret asks `old_key`). macOS asks once per key whether DataFluss may read it,
//! because another app stored it; "Deny" stops at that key and the database stays closed. The old
//! entries stay where they are, because deleting them would ask again.

use std::path::Path;

use tauri::Manager;

use crate::databases::SERVICE;

const OLD_ID: &str = "de.rana.schulverwaltung";

pub fn take_over(app: &tauri::AppHandle) {
    let Ok(new_dir) = app.path().app_data_dir() else { return };
    let Some(old_dir) = new_dir.parent().map(|parent| parent.join(OLD_ID)) else { return };
    let (old, new) = (old_dir.join("databases"), new_dir.join("databases"));
    if !old.is_dir() || has_databases(&new) {
        return;
    }
    if std::fs::create_dir_all(&new_dir).is_err() || (new.exists() && std::fs::remove_dir(&new).is_err()) {
        return;
    }
    match std::fs::rename(&old, &new) {
        Ok(()) => eprintln!("[shell] took over the databases of {OLD_ID}"),
        Err(error) => eprintln!("[shell] databases not taken over: {error}"),
    }
}

/// A key the earlier client stored: copied to DataFluss's own service and returned. None when there is
/// none, an error when macOS did not hand it over (for example after "Deny").
pub fn old_key(id: &str, name: &str) -> Option<Result<String, String>> {
    let account = format!("{id}:{name}");
    let value = match keyring::Entry::new(OLD_ID, &account).and_then(|e| e.get_password()) {
        Ok(value) => value,
        Err(keyring::Error::NoEntry) => return None,
        Err(error) => {
            eprintln!("[shell] key {account} of {OLD_ID} not handed over: {error}");
            return Some(Err(format!(
                "macOS hat den Schlüssel „{name}“ dieser Datenbank aus der früheren Schulverwaltung nicht freigegeben. \
                 Beim nächsten Öffnen fragt macOS wieder: dann das Anmeldepasswort des Macs eingeben und „Immer erlauben“ wählen."
            )));
        }
    };
    if let Err(error) = keyring::Entry::new(SERVICE, &account).and_then(|e| e.set_password(&value)) {
        eprintln!("[shell] key {account} not copied: {error}"); // Still usable now; asked again next time.
    }
    Some(Ok(value))
}

/// An empty folder, which an earlier start of DataFluss may have made, does not count.
fn has_databases(dir: &Path) -> bool {
    std::fs::read_dir(dir).map(|mut entries| entries.next().is_some()).unwrap_or(false)
}
