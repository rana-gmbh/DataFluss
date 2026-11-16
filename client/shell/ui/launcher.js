// Start screen: lists the databases on this computer and creates, imports, opens and removes them.
// Every action is a command of the shell (src-tauri/src/main.rs), which only this window may call.
const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

window.addEventListener("error", (e) => invoke("report_error", { message: `${e.message} (${e.filename}:${e.lineno})` }));
window.addEventListener("unhandledrejection", (e) => invoke("report_error", { message: `Unhandled: ${e.reason}` }));

const $ = (selector, root = document) => root.querySelector(selector);
const { t, translate } = window.I18N; // i18n.js: the start screen in the system's (or the chosen) language
// The same start screen on macOS and Windows; only the names of the computer and its tools differ.
const WINDOWS = navigator.userAgent.includes("Windows");
const FILE_MANAGER = WINDOWS ? "Explorer" : "Finder";

// Elements with data-win get their Windows wording (in the page and in templates when they are used).
function localize(root) {
  if (!WINDOWS) return;
  for (const el of root.querySelectorAll("[data-win]")) el.textContent = el.dataset.win;
  for (const el of root.querySelectorAll("[data-win-label]")) el.setAttribute("aria-label", el.dataset.winLabel);
}
// The Mac's or the PC's wording of a text, translated.
const forComputer = (mac, pc, vars) => t(WINDOWS ? pc : mac, vars);
localize(document);
translate(document);
const list = $("#databases");
const sheet = $("#sheet");
let databases = [];
let selected = null;
let working = false;

const COLORS = ["#2553eb", "#0f766e", "#7c3aed", "#c2410c", "#be185d", "#4d7c0f", "#0369a1", "#a16207"];
const colorOf = (id) => COLORS[[...id].reduce((sum, c) => sum + c.charCodeAt(0), 0) % COLORS.length];
const initial = (name) => (name.trim()[0] || "?").toUpperCase();

function when(seconds) {
  if (!seconds) return t("noch nie geöffnet");
  const days = Math.floor((Date.now() / 1000 - seconds) / 86400);
  if (days < 1) return t("heute geöffnet");
  if (days < 2) return t("gestern geöffnet");
  if (days < 30) return t("vor {days} Tagen geöffnet", { days });
  return t("zuletzt {date}", { date: new Date(seconds * 1000).toLocaleDateString(window.I18N.locale) });
}

// A database of its own says when its regular backup (archive/plan.py) is missing or overdue.
const BACKUP_DAYS = { day: 1, week: 7 };

function backupWarning(backup) {
  if (!backup) return "";
  if (!backup.every) return t("Keine Sicherung eingerichtet");
  if (backup.state === "failed") return t("Letzte Sicherung fehlgeschlagen");
  if (!backup.ok_at) return "";
  const days = (Date.now() - Date.parse(backup.ok_at)) / 86400000;
  return days > BACKUP_DAYS[backup.every] + 1 ? t("Sicherung seit {days} Tagen überfällig", { days: Math.floor(days) }) : "";
}

function size(bytes) {
  return bytes > 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}

// ------------------------------------------------------------ list

async function refresh() {
  try {
    databases = await invoke("list_databases");
  } catch (error) {
    databases = [];
    console.error(error);
  }
  render();
}

function render() {
  list.replaceChildren();
  $("#empty").hidden = databases.length > 0;
  list.hidden = databases.length === 0;
  $("#count").textContent = databases.length ? forComputer("{n} auf diesem Mac", "{n} auf diesem PC", { n: databases.length }) : "";
  if (selected && !databases.some((d) => d.id === selected)) selected = null;
  for (const db of databases) {
    const item = document.createElement("li");
    item.className = "db";
    item.role = "option";
    item.dataset.id = db.id;
    item.setAttribute("aria-selected", String(db.id === selected));
    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.style.background = colorOf(db.id);
    avatar.textContent = initial(db.name);
    const text = document.createElement("div");
    text.className = "db-text";
    const name = document.createElement("div");
    name.className = "db-name";
    name.textContent = db.name;
    const meta = document.createElement("div");
    meta.className = "db-meta";
    if (db.open) {
      const dot = document.createElement("span");
      dot.className = "dot";
      dot.title = t("Geöffnet");
      meta.append(dot);
    }
    const badge = document.createElement("span");
    badge.className = db.kind === "demo" ? "badge demo" : db.kind === "connected" ? "badge server" : "badge";
    badge.textContent = db.kind === "demo" ? "Demo" : db.kind === "connected" ? "Server" : t("Lokal");
    if (db.kind === "connected" && db.server) badge.title = db.server;
    meta.append(badge);
    const details = document.createElement("span");
    const host = db.kind === "connected" && db.server ? db.server.replace(/^https?:\/\//, "") : "";
    details.textContent = [db.school && db.school !== db.name ? db.school : "", host, when(db.last_opened), size(db.size)].filter(Boolean).join(" · ");
    meta.append(details);
    const warning = backupWarning(db.backup);
    if (warning) {
      const flag = document.createElement("span");
      flag.className = "backup-flag";
      flag.textContent = warning;
      flag.title = t("Die regelmäßige Sicherung lässt sich in der Datenbank unter Verwaltung → Sicherung einrichten.");
      meta.append(flag);
    }
    text.append(name, meta);
    const open = document.createElement("button");
    open.className = "btn open-btn";
    open.textContent = db.open ? t("Anzeigen") : t("Öffnen");
    open.addEventListener("click", (e) => { e.stopPropagation(); openDatabase(db.id); });
    const more = document.createElement("button");
    more.className = "icon-btn";
    more.setAttribute("aria-label", t("Mehr zu {name}", { name: db.name }));
    more.innerHTML = '<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/></svg>';
    more.addEventListener("click", (e) => { e.stopPropagation(); select(db.id); showMenu(db, more.getBoundingClientRect()); });
    item.append(avatar, text, open, more);
    item.addEventListener("click", () => select(db.id));
    item.addEventListener("dblclick", () => openDatabase(db.id));
    item.addEventListener("contextmenu", (e) => { e.preventDefault(); select(db.id); showMenu(db, { left: e.clientX, bottom: e.clientY }); });
    list.append(item);
  }
}

function select(id) {
  selected = id;
  for (const item of list.children) item.setAttribute("aria-selected", String(item.dataset.id === id));
  list.focus();
}

list.addEventListener("keydown", (e) => {
  const index = databases.findIndex((d) => d.id === selected);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const next = databases[Math.max(0, Math.min(databases.length - 1, index + (e.key === "ArrowDown" ? 1 : -1)))];
    if (next) select(next.id);
  } else if (e.key === "Enter" && selected) {
    openDatabase(selected);
  } else if (((e.key === "Backspace" || e.key === "Delete") && e.metaKey || e.key === "Delete" && WINDOWS) && selected) {
    confirmRemove(databases[index]);
  }
});

// ------------------------------------------------------------ menu

const menu = $("#menu");

function showMenu(db, at) {
  menu.replaceChildren();
  const add = (label, action, danger = false) => {
    const button = document.createElement("button");
    button.textContent = t(label);
    button.role = "menuitem";
    if (danger) button.className = "danger";
    button.addEventListener("click", () => { hideMenu(); action(); });
    menu.append(button);
  };
  add(db.open ? "Anzeigen" : "Öffnen", () => openDatabase(db.id));
  add(t("Im {app} zeigen", { app: FILE_MANAGER }), () => invoke("reveal_database", { id: db.id }));
  if (db.kind === "demo") add("Anmeldedaten zeigen …", () => showLogins(db.id));
  menu.append(document.createElement("hr"));
  add("In den Papierkorb legen …", () => confirmRemove(db), true);
  menu.hidden = false;
  const box = menu.getBoundingClientRect();
  menu.style.left = `${Math.min(at.left, innerWidth - box.width - 8)}px`;
  menu.style.top = `${Math.min(at.bottom + 4, innerHeight - box.height - 8)}px`;
  menu.querySelector("button").focus();
}

function hideMenu() { menu.hidden = true; }
document.addEventListener("click", (e) => { if (!menu.contains(e.target)) hideMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideMenu(); });

// ------------------------------------------------------------ sheets

function openSheet(title, { template, html, actions }) {
  if (sheet.open) sheet.close(); // showModal() refuses a dialog that is still open.
  $("#sheet-title").textContent = typeof title === "string" ? t(title) : title.name; // a database's own name stays as it is
  const body = $("#sheet-body");
  body.replaceChildren();
  if (template) {
    const content = $(template).content.cloneNode(true);
    localize(content);
    translate(content);
    body.append(content);
  }
  if (html) body.append(html);
  $("#sheet-error").hidden = true;
  $("#sheet-busy").hidden = true;
  const footer = $("#sheet-actions");
  footer.replaceChildren();
  for (const { label, kind = "", run, submit } of actions) {
    const button = document.createElement("button");
    button.className = `btn ${kind}`;
    button.textContent = t(label);
    button.type = submit ? "submit" : "button";
    if (!submit) button.addEventListener("click", run || closeSheet);
    else sheet.onsubmit = (e) => { e.preventDefault(); run(); };
    footer.append(button);
  }
  if (!actions.some((a) => a.submit)) sheet.onsubmit = (e) => e.preventDefault();
  sheet.showModal();
  const first = body.querySelector("input:not([type=checkbox]), button")
    || footer.querySelector("button[type=submit]:not(.danger)") || footer.querySelector("button");
  if (first) first.focus();
}

function closeSheet() { if (!working) sheet.close(); }
sheet.addEventListener("cancel", (e) => { if (working) e.preventDefault(); });

function showError(message) {
  const box = $("#sheet-error");
  box.textContent = t(message);
  box.hidden = false;
}

async function busy(text, task) {
  working = true;
  $("#sheet-error").hidden = true;
  $("#busy-text").textContent = t(text);
  $("#busy-bar").hidden = true;
  $("#sheet-busy").hidden = false;
  for (const el of sheet.querySelectorAll("input, button")) el.disabled = true;
  try {
    return await task();
  } finally {
    working = false;
    $("#sheet-busy").hidden = true;
    for (const el of sheet.querySelectorAll("input, button")) el.disabled = false;
  }
}

listen("task-progress", ({ payload }) => {
  const { text, done, total } = payload.step || {};
  if (text) $("#busy-text").textContent = `${t(text)} …`;
  const bar = $("#busy-bar");
  if (total) {
    bar.hidden = false;
    bar.firstElementChild.style.width = `${Math.round((100 * (done || 0)) / total)}%`;
  } else {
    bar.hidden = true;
  }
});

const field = (name) => sheet.querySelector(`[name="${name}"]`);

// ------------------------------------------------------------ new database (wizard)

let offered = null; // optional modules this installation has: [{id, title}]

async function offeredModules() {
  if (offered === null) {
    try { offered = await invoke("available_modules"); } catch { offered = []; }
  }
  return offered;
}

let chosenModules = null;
let chosenStart = { without: [], templates: [] }; // the card "Leer" and the module templates picked
let templates = null; // module templates this installation has (tables/blueprints.py): [{id, title, requires, …}]

async function offeredTemplates() {
  if (templates === null) {
    try { templates = await invoke("available_templates"); } catch { templates = []; }
  }
  return templates;
}

// Template names in the start screen's language, so a card never mixes two languages.
const language = () => window.I18N.language;
const pick = (base, names) => (names && names[language()]) || base || "";

function renderTemplates(box, start) {
  box.querySelectorAll("label").forEach((label) => label.remove());
  const usable = (templates || []).filter((t) => !(t.requires || []).includes("schulverwaltung") || start === "schulverwaltung");
  for (const template of usable) {
    const label = document.createElement("label");
    label.className = "check";
    const input = Object.assign(document.createElement("input"), { type: "checkbox", name: "template", value: template.id });
    const text = document.createElement("span");
    text.append(Object.assign(document.createElement("strong"), { textContent: pick(template.title, template.titles) }));
    const description = pick(template.description, template.descriptions);
    if (description) {
      text.append(document.createElement("br"), Object.assign(document.createElement("span"), { className: "muted small", textContent: description }));
    }
    label.append(input, text);
    box.append(label);
  }
  box.hidden = usable.length === 0;
}

async function startCreate() {
  const modules = await offeredModules();
  await offeredTemplates();
  openSheet("Neue Datenbank", {
    template: "#t-start",
    actions: [
      { label: "Abbrechen" },
      { label: "Weiter", kind: "primary", submit: true, run: afterStart },
    ],
  });
  const box = $("#module-choices");
  for (const { id, title } of modules) {
    const label = document.createElement("label");
    label.className = "check";
    const input = Object.assign(document.createElement("input"), { type: "checkbox", name: "module", value: id, checked: false });
    const text = document.createElement("span");
    text.append(Object.assign(document.createElement("strong"), { textContent: title }));
    label.append(input, text);
    box.append(label);
  }
  box.hidden = modules.length === 0;
  const templateBox = $("#template-choices");
  const start = () => sheet.querySelector('[name="start"]:checked').value;
  renderTemplates(templateBox, start());
  // Sample data comes from Schulverwaltung's invented people or from a template; "Leer" without one has none.
  const demo = field("demo");
  const showDemo = () => {
    const none = start() === "leer" && templateBox.hidden;
    demo.closest("label").hidden = none;
    if (none) demo.checked = false;
  };
  sheet.querySelectorAll('[name="start"]').forEach((radio) => radio.addEventListener("change", () => {
    box.hidden = start() !== "schulverwaltung" || modules.length === 0; // optional modules build on Schulverwaltung
    renderTemplates(templateBox, start());
    showDemo();
  }));
}

function afterStart() {
  const start = sheet.querySelector('[name="start"]:checked').value;
  chosenModules = start === "schulverwaltung" ? [...sheet.querySelectorAll('[name="module"]:checked')].map((input) => input.value) : [];
  chosenStart = {
    without: start === "leer" ? ["schulverwaltung"] : [],
    templates: [...sheet.querySelectorAll('[name="template"]:checked')].map((input) => input.value),
  };
  if (field("demo").checked) {
    openSheet("Neue Datenbank mit Beispieldaten", {
      template: "#t-demo-name",
      actions: [
        { label: "Zurück", run: startCreate },
        { label: "Anlegen", kind: "primary", submit: true, run: () => createDemo(field("name").value.trim(), chosenModules, chosenStart) },
      ],
    });
    return;
  }
  openSheet("Neue Datenbank", {
    template: "#t-create",
    actions: [
      { label: "Zurück", run: startCreate },
      { label: "Anlegen", kind: "primary", submit: true, run: create },
    ],
  });
  if (chosenStart.without.length) { // not necessarily a school
    const school = field("school");
    school.parentElement.firstChild.textContent = t("Name (Schule, Verein, Firma …)");
    school.placeholder = t("z. B. Bibliothek am Markt");
    sheet.querySelector("p.muted").textContent = t("Noch ohne Tabellen, mit dem ersten IT-Konto. Beim ersten Anmelden wird die Zwei-Faktor-Anmeldung eingerichtet.");
  }
}

async function create() {
  const school = field("school").value.trim();
  const name = field("name").value.trim() || school;
  const username = field("username").value.trim();
  const password = field("password").value;
  if (!school || !username) return showError(chosenStart.without.length ? "Bitte einen Namen und einen Benutzernamen angeben." : "Bitte den Namen der Schule und einen Benutzernamen angeben.");
  if (password.length < 12) return showError("Das Passwort braucht mindestens 12 Zeichen.");
  if (password !== field("password2").value) return showError("Die beiden Passwörter stimmen nicht überein.");
  try {
    const id = await busy("Datenbank wird angelegt …", () => invoke("create_database", { name, school, username, password, modules: chosenModules, ...chosenStart }));
    sheet.close();
    await openDatabase(id);
  } catch (error) {
    showError(String(error));
  }
}

function startConnect(url = "") {
  openSheet("Mit Server verbinden", {
    template: "#t-connect",
    actions: [
      { label: "Abbrechen" },
      { label: "Verbinden", kind: "primary", submit: true, run: connect },
    ],
  });
  if (url) {
    field("url").value = url;
    field("username").focus();
  }
}

async function connect() {
  const url = field("url").value.trim();
  const username = field("username").value.trim();
  const password = field("password").value;
  if (!url || !username || !password) return showError("Bitte Adresse, Benutzername und Passwort angeben.");
  try {
    const id = await busy("Verbindung zum Server …", () => invoke("connect_server", {
      name: field("name").value.trim(), url, username, password, code: field("code").value.trim(),
    }));
    sheet.close();
    await openDatabase(id);
  } catch (error) {
    showError(String(error));
    if (/Code/.test(String(error))) field("code").focus();
  }
}

let backupPath = "";

function startImport() {
  backupPath = "";
  openSheet("Sicherung importieren", {
    template: "#t-import",
    actions: [
      { label: "Abbrechen" },
      { label: "Importieren", kind: "primary", submit: true, run: importBackup },
    ],
  });
  $("#pick").addEventListener("click", async () => {
    const path = await invoke("pick_backup");
    if (!path) return;
    backupPath = path;
    const file = path.split(/[\\/]/).pop();
    $("#picked").textContent = file;
    $("#picked").title = path;
    if (!field("name").value) field("name").value = file.replace(/\.svbackup$/, "").replace(/[-_]+/g, " ");
    field("password").focus();
  });
}

async function importBackup() {
  const name = field("name").value.trim();
  const password = field("password").value;
  if (!backupPath) return showError("Bitte zuerst die Sicherungsdatei wählen.");
  if (!password) return showError("Bitte das Passwort der Sicherung eingeben.");
  if (!name) return showError("Bitte der Datenbank einen Namen geben.");
  try {
    const id = await busy("Sicherung wird geprüft …", () =>
      invoke("import_backup", { name, path: backupPath, password, keepSecrets: field("keep_secrets").checked }));
    sheet.close();
    await openDatabase(id);
  } catch (error) {
    showError(String(error));
  }
}

async function startDemo() {
  openSheet("Demo anlegen", {
    html: Object.assign(document.createElement("p"), {
      className: "muted",
      textContent: t("Die Schulverwaltung mit rund 600 erfundenen Personen, Geräten und Zugängen, zum Ausprobieren. Ein Konto für jede Rolle, ohne Zwei-Faktor-Anmeldung. Keine echten Daten eintragen."),
    }),
    actions: [
      { label: "Abbrechen" },
      { label: "Demo anlegen", kind: "primary", submit: true, run: createDemo },
    ],
  });
}

async function createDemo(name = null, modules = null, start = { without: [], templates: [] }) {
  try {
    const result = await busy("Demo wird angelegt …", () => invoke("create_demo", { name, modules, ...start }));
    await refresh();
    showLoginsSheet(result.id, result.logins);
  } catch (error) {
    showError(String(error));
  }
}

async function showLogins(id) {
  try {
    showLoginsSheet(id, await invoke("demo_logins", { id }));
  } catch (error) {
    openSheet("Anmeldedaten", { html: document.createTextNode(String(error)), actions: [{ label: "Schließen" }] });
  }
}

const ROLES = { it: "IT", sekretariat: "Sekretariat", leitung: "Schulleitung", daz: "DaZ-Lehrkraft", kiga: "Kindergarten", lehrer: "Lehrkraft" };

function showLoginsSheet(id, logins) {
  const wrap = document.createElement("div");
  wrap.append(Object.assign(document.createElement("p"), { className: "muted", textContent: t("Mit diesen Konten anmelden. Sie stehen auch im Ordner der Datenbank.") }));
  const table = document.createElement("table");
  table.className = "logins";
  for (const { username, password } of logins) {
    const row = table.insertRow();
    row.insertCell().textContent = ROLES[username] ? t(ROLES[username]) : username;
    const user = document.createElement("code");
    user.textContent = username;
    row.insertCell().append(user);
    const pass = document.createElement("code");
    pass.textContent = password;
    row.insertCell().append(pass);
  }
  wrap.append(table);
  openSheet("Demo-Anmeldedaten", {
    html: wrap,
    actions: [
      { label: "Schließen" },
      { label: "Demo öffnen", kind: "primary", submit: true, run: () => { sheet.close(); openDatabase(id); } },
    ],
  });
}

function confirmRemove(db) {
  if (!db) return;
  const text = document.createElement("p");
  text.textContent = [t("„{name}“ wird mit allen Daten in den Papierkorb gelegt.", { name: db.name }), db.open ? t("Ihr Fenster wird geschlossen.") : "",
    t("Solange der Ordner im Papierkorb liegt, lässt er sich zurücklegen.")].filter(Boolean).join(" ");
  openSheet("In den Papierkorb legen?", {
    html: text,
    actions: [
      { label: "Abbrechen" },
      { label: "In den Papierkorb", kind: "danger", submit: true, run: async () => {
        try {
          await invoke("remove_database", { id: db.id });
          sheet.close();
          refresh();
        } catch (error) {
          showError(String(error));
        }
      } },
    ],
  });
}

// ------------------------------------------------------------ open

async function openDatabase(id) {
  const db = databases.find((d) => d.id === id);
  if (!sheet.open) {
    openSheet(db ? { name: db.name } : "Datenbank", { html: document.createElement("span"), actions: [] });
  }
  try {
    await busy("Datenbank wird geöffnet …", () => invoke("open_db", { id }));
    sheet.close();
    refresh();
  } catch (error) {
    if (!sheet.open) openSheet(db ? { name: db.name } : "Datenbank", { html: document.createElement("span"), actions: [] });
    $("#sheet-actions").replaceChildren(Object.assign(document.createElement("button"), { className: "btn", type: "button", textContent: t("Schließen"), onclick: closeSheet }));
    showError(String(error));
  }
}

// The language switcher (i18n.js): each language named in its own words; a change reloads the start screen.
const languageSelect = $("#language");
for (const [code, name] of window.I18N.languages) languageSelect.append(new Option(name, code, false, code === window.I18N.language));
languageSelect.addEventListener("change", () => window.I18N.setLanguage(languageSelect.value));

for (const button of document.querySelectorAll("[data-action]")) {
  button.addEventListener("click", () => {
    const action = button.dataset.action;
    if (action === "create") startCreate();
    if (action === "connect") startConnect();
    if (action === "import") startImport();
    if (action === "demo") startDemo();
    if (action === "module") installModule();
  });
}

async function installModule() {
  try {
    const name = await invoke("install_module");
    if (!name) return;
    const text = document.createElement("p");
    text.textContent = t("{name} liegt bereit. DataFluss prüft die Signatur beim nächsten Öffnen einer Datenbank und lädt das Modul nur, wenn sie stimmt und das Modul zu dieser Version passt.", { name });
    openSheet("Modul installiert", { html: text, actions: [{ label: "OK", kind: "primary" }] });
  } catch (error) {
    openSheet("Modul installieren", { html: document.createElement("span"), actions: [{ label: "Schließen" }] });
    showError(String(error));
  }
}

// ------------------------------------------------------------ first steps (welcome on the first start)

const WELCOME = [
  {
    title: "Willkommen bei DataFluss",
    text: [WINDOWS ? "DataFluss hält Daten in eigenen Datenbanken: auf diesem PC, verschlüsselt, und auf Wunsch im Abgleich mit einem Server, auch nach einer Zeit ohne Verbindung."
      : "DataFluss hält Daten in eigenen Datenbanken: auf diesem Mac, verschlüsselt, und auf Wunsch im Abgleich mit einem Server, auch nach einer Zeit ohne Verbindung.",
      "Eine Datenbank beginnt mit einem Ausgangspunkt wie der Schulverwaltung und lässt sich dann mit eigenen Feldern, Masken und Druckvorlagen anpassen."],
  },
  {
    title: "Vier Wege zum Anfang",
    list: [
      ["Mit erfundenen Daten ausprobieren", "der schnellste Weg, alles kennenzulernen"],
      ["Neue Datenbank", "für die eigenen Daten, mit einem Ausgangspunkt"],
      ["Mit Server verbinden", "eine Kopie, die sich mit dem Server Ihrer Organisation abgleicht"],
      ["Sicherung importieren", WINDOWS ? "von einem Server oder einem anderen PC" : "von einem Server oder einem anderen Mac"],
    ],
  },
  {
    title: "Sicher von Anfang an",
    text: [WINDOWS ? "Jede Datenbank ist verschlüsselt. Die Schlüssel liegen in der Anmeldeinformationsverwaltung dieses PCs, nie im Ordner der Datenbank."
      : "Jede Datenbank ist verschlüsselt. Die Schlüssel liegen im Schlüsselbund dieses Macs, nie im Ordner der Datenbank.",
      "Beim ersten Anmelden zeigt ein kurzer Rundgang, wo was ist. Diese Seiten und den Rundgang gibt es jederzeit wieder im Menü Hilfe."],
  },
];

function showWelcome(index = 0) {
  const step = WELCOME[index];
  const wrap = document.createElement("div");
  wrap.className = "welcome";
  for (const paragraph of step.text || []) wrap.append(Object.assign(document.createElement("p"), { textContent: t(paragraph) }));
  if (step.list) {
    const list = document.createElement("ul");
    for (const [title, detail] of step.list) {
      const item = document.createElement("li");
      item.append(Object.assign(document.createElement("strong"), { textContent: t(title) }), document.createTextNode(` – ${t(detail)}`));
      list.append(item);
    }
    wrap.append(list);
  }
  wrap.append(Object.assign(document.createElement("p"), { className: "muted small", textContent: t("{i} von {n}", { i: index + 1, n: WELCOME.length }) }));
  const done = () => { invoke("welcome_done").catch(() => {}); };
  const last = index === WELCOME.length - 1;
  openSheet(step.title, {
    html: wrap,
    actions: last
      ? [{ label: "Selbst anfangen", run: () => { done(); closeSheet(); } },
         { label: "Erst die Demo ansehen", kind: "primary", submit: true, run: () => { done(); startDemo(); } }]
      : [{ label: "Überspringen", run: () => { done(); closeSheet(); } },
         ...(index ? [{ label: "Zurück", run: () => showWelcome(index - 1) }] : []),
         { label: "Weiter", kind: "primary", submit: true, run: () => showWelcome(index + 1) }],
  });
}

invoke("welcome_seen").then((seen) => { if (!seen && !sheet.open) showWelcome(); }).catch(() => {});

listen("launcher-action", ({ payload }) => {
  if (sheet.open) return;
  if (payload === "create") startCreate();
  if (payload === "import") startImport();
  if (payload === "welcome") showWelcome();
});
listen("databases-changed", refresh);
// After a database went to a server (archive/transfer.py), the copy to work with is one connection away.
listen("launcher-connect", ({ payload }) => { if (!working) startConnect(payload.url || ""); });
listen("database-wiped", ({ payload }) => {
  refresh();
  const text = document.createElement("p");
  text.textContent = forComputer("Der Server hat die Verbindung von „{name}“ zu diesem Mac getrennt. Die Kopie der Daten wurde von diesem Mac gelöscht. Zum weiteren Arbeiten den Mac neu verbinden oder die Webversion benutzen.",
    "Der Server hat die Verbindung von „{name}“ zu diesem PC getrennt. Die Kopie der Daten wurde von diesem PC gelöscht. Zum weiteren Arbeiten den PC neu verbinden oder die Webversion benutzen.", { name: payload.name });
  openSheet("Verbindung getrennt", { html: text, actions: [{ label: "OK", kind: "primary", submit: true, run: closeSheet }] });
});
window.addEventListener("focus", refresh);
refresh();
