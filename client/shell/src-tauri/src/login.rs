//! A remembered sign-in per database: "Anmeldedaten merken" on the sign-in page (core/views.py,
//! login_view, which shows it only in the client). Username and password lie in the Keychain or the
//! Windows Credential Manager as "<id>:login", beside the database's keys, never in a file, in the
//! WebView's storage or in the database. A second factor is never kept: its page still asks.
//!
//! The shell sees the form post on its way to the sidecar (main.rs) and keeps or forgets the sign-in
//! once Django answers "X-DataFluss-Login: ok". When the database opens again, the shell signs in itself
//! (Sidecar::sign_in) before the window shows; if Django says "failed", the sign-in is forgotten and the
//! window shows the sign-in page with a note.

use serde::{Deserialize, Serialize};

use crate::databases::{self, SERVICE};

/// The name of the entry beside the keys, in databases::KEY_NAMES so that a wipe removes it too.
pub const ENTRY: &str = "login";
pub const PATH: &str = "/anmelden/";
/// Django's answer to a sign-in in the client: "ok", "failed" or "locked".
pub const RESULT_HEADER: &str = "x-datafluss-login";
/// The shell tells the sign-in page whether a sign-in is kept ("1") or was just forgotten ("forgotten").
pub const REMEMBERED_HEADER: &str = "x-datafluss-remembered";
/// Headers only the shell may set. Whatever a page sends under this prefix is dropped.
pub const SHELL_PREFIX: &str = "x-datafluss-";

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Login {
    pub username: String,
    pub password: String,
}

fn entry(id: &str) -> Option<keyring::Entry> {
    keyring::Entry::new(SERVICE, &format!("{id}:{ENTRY}")).ok()
}

/// The kept sign-in, if there is one. Not from the earlier Schulverwaltung client, which had none.
pub fn load(id: &str) -> Option<Login> {
    let raw = entry(id)?.get_password().ok()?;
    serde_json::from_str(&raw).ok()
}

pub fn is_kept(id: &str) -> bool {
    load(id).is_some()
}

pub fn save(id: &str, login: &Login) -> Result<(), String> {
    databases::store(id, ENTRY, &serde_json::to_string(login).map_err(|e| e.to_string())?)
}

pub fn forget(id: &str) {
    if let Some(entry) = entry(id) {
        let _ = entry.delete_credential();
    }
}

/// Username, password and the box of a sign-in form post (application/x-www-form-urlencoded).
/// Every other field, the CSRF token among them, is left alone.
pub fn from_form(body: &[u8]) -> (Option<Login>, bool) {
    let (mut username, mut password, mut remember) = (None, None, false);
    for (name, value) in form_urlencoded::parse(body) {
        match name.as_ref() {
            "username" => username = Some(value.trim().to_string()),
            "password" => password = Some(value.into_owned()),
            "remember" => remember = value == "1",
            _ => {}
        }
    }
    let login = match (username, password) {
        (Some(username), Some(password)) if !username.is_empty() && !password.is_empty() => Some(Login { username, password }),
        _ => None,
    };
    (login, remember)
}

/// The form body the shell posts when it signs in by itself.
pub fn form(login: &Login, csrf_token: &str) -> String {
    form_urlencoded::Serializer::new(String::new())
        .append_pair("csrfmiddlewaretoken", csrf_token)
        .append_pair("username", &login.username)
        .append_pair("password", &login.password)
        .finish()
}

/// The CSRF token of the sign-in form, as Django writes it into the page.
pub fn csrf_token(page: &str) -> Option<String> {
    let marker = "name=\"csrfmiddlewaretoken\" value=\"";
    let start = page.find(marker)? + marker.len();
    let token = &page[start..start + page[start..].find('"')?];
    (!token.is_empty() && token.bytes().all(|b| b.is_ascii_alphanumeric())).then(|| token.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn form_post_with_the_box() {
        let (login, remember) = from_form(b"csrfmiddlewaretoken=abc&next=%2F&username=+sek+&password=p%26w%3Dx+y&remember=1");
        assert_eq!(login, Some(Login { username: "sek".into(), password: "p&w=x y".into() }));
        assert!(remember);
    }

    #[test]
    fn form_post_without_the_box() {
        let (login, remember) = from_form(b"username=sek&password=secret");
        assert!(login.is_some());
        assert!(!remember);
    }

    #[test]
    fn empty_fields_are_no_login() {
        assert_eq!(from_form(b"username=sek&password=&remember=1").0, None);
        assert_eq!(from_form(b"code=123456").0, None);
    }

    #[test]
    fn form_round_trip() {
        let login = Login { username: "it".into(), password: "ä&=+ %".into() };
        let (parsed, remember) = from_form(form(&login, "tok").as_bytes());
        assert_eq!(parsed, Some(login));
        assert!(!remember);
    }

    #[test]
    fn token_from_the_page() {
        let page = r#"<form><input type="hidden" name="csrfmiddlewaretoken" value="Ab12Cd"><input name="username">"#;
        assert_eq!(csrf_token(page).as_deref(), Some("Ab12Cd"));
        assert_eq!(csrf_token("<form></form>"), None);
        assert_eq!(csrf_token(r#"name="csrfmiddlewaretoken" value="a<b""#), None);
    }
}
