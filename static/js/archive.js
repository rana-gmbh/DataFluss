(function () {
  const source = document.getElementById("backup-job");
  if (!source) return;
  const $ = (id) => document.getElementById(id);
  const BASE = "/einstellungen/sicherung/";
  const CHUNK = 4 * 1024 * 1024;
  const RETRIES = 5;
  const FINISHED = { backup: gettext("Die Sicherung ist fertig. Bitte laden Sie sie herunter und bewahren Sie sie an einem anderen Ort auf."), restore: gettext("Die Sicherung wurde eingespielt.") };
  const csrf = App.csrf;
  let job = JSON.parse(source.textContent);
  let uploading = false;

  function render() {
    const running = job?.state === "running";
    $("b-job").classList.toggle("hidden", !running);
    if (running) {
      const steps = job.total ? ` (${interpolate(gettext("%(done)s von %(total)s"), { done: job.done, total: job.total }, true)})` : "";
      $("b-job-text").textContent = `… ${job.step}${steps}`;
      $("b-job-bar").classList.toggle("hidden", !job.total);
      if (job.total) $("b-job-fill").style.width = `${Math.round((job.done / job.total) * 100)}%`;
    }
    const result = $("b-result");
    const show = job?.state === "done" || job?.state === "failed";
    result.classList.toggle("hidden", !show);
    if (show) {
      result.className = `rounded-lg px-3 py-2 text-[13px] mb-4 max-w-3xl ${job.state === "done" ? "badge-ok" : "badge-bad"}`;
      result.textContent = job.message || FINISHED[job.kind];
    }
    document.querySelectorAll("[data-while-idle]").forEach((el) => { el.disabled = running; });
    if ($("b-upload")) {
      $("b-upload").disabled = running || uploading;
      $("b-upload-button").classList.toggle("opacity-50", running || uploading);
    }
  }

  async function poll() {
    let res = null;
    try {
      res = await fetch(`${BASE}status/`, { headers: { "X-Requested-With": "fetch" } });
    } catch (e) { /* The next try decides. */ }
    // An import replaces the sessions too, so everyone arrives at the sign-in page.
    if (res?.status === 401) {
      location.href = "/anmelden/?eingespielt=1";
      return;
    }
    if (res?.ok) {
      const previous = job?.state;
      job = await res.json();
      if (previous === "running" && job.state && job.state !== "running") {
        location.reload();
        return;
      }
      render();
    }
    if (job?.state === "running") setTimeout(poll, 1500);
  }

  function started(body) {
    job = body;
    render();
    setTimeout(poll, 1000);
  }

  const on = (id, event, handler) => { const el = $(id); if (el) el.addEventListener(event, handler); };

  // The regular backup of a database in the desktop client (archive/plan.py).
  on("b-plan", "submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const res = await App.post(`${BASE}plan/`, { folder: form.folder.value, every: form.every.value, keep: form.keep.value, password: form.password.value, repeat: form.repeat.value });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      App.toast(body.error || gettext("Das hat nicht geklappt."), { ms: 6000 });
      return;
    }
    location.reload();
  });

  on("b-plan-run", "click", async () => {
    const res = await App.post(`${BASE}plan/jetzt/`, {});
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      App.toast(body.error || gettext("Das hat nicht geklappt."), { ms: 6000 });
      return;
    }
    started(body);
  });

  // The shell of the desktop client opens its folder dialog (client/shell/src-tauri/src/main.rs).
  on("b-plan-pick", "click", async () => {
    const res = await fetch("/__client/pick-folder", { method: "POST" }).catch(() => null);
    if (!res?.ok) return;
    const { path } = await res.json();
    if (path) $("b-plan-folder").value = path;
  });

  async function postForm(url, data) {
    const res = await App.post(url, data);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) App.toast(body.error || gettext("Das hat nicht geklappt."), { ms: 8000 });
    return res.ok ? body : null;
  }

  // Moving this database to a server (archive/transfer.py): sign in there, confirm if it holds data, then go.
  on("b-transfer", "submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const button = form.querySelector(".btn-primary");
    const fields = Object.fromEntries(new FormData(form));
    const answer = await whileBusy(button, gettext("Anmeldung am Server …"), () => postForm(`${BASE}server/pruefen/`, fields));
    if (!answer) return;
    if (answer.has_data) {
      const h = answer.holdings;
      const text = interpolate(gettext("Auf dem Server liegen schon Daten: %(people)s Personen, %(assets)s Geräte, %(users)s Benutzerkonten. Sie werden durch diese Datenbank ersetzt. Vorher legt der Server eine Sicherung davon an. Wirklich ersetzen?"), h, true);
      if (!confirm(text)) return;
    }
    const job = await postForm(`${BASE}server/uebertragen/`, { url: answer.url, ticket: answer.ticket, backup_password: fields.backup_password, replace: answer.has_data ? "1" : "" });
    if (!job) return;
    form.reset();
    started(job);
  });

  on("b-connect", "click", (e) => {
    fetch("/__client/connect", { method: "POST", body: JSON.stringify({ url: e.currentTarget.dataset.url }) }).catch(() => {});
  });

  on("b-from-server", "submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const button = form.querySelector(".btn-primary");
    const job = await whileBusy(button, gettext("Anmeldung am Server …"), () => postForm(`${BASE}vom-server/`, Object.fromEntries(new FormData(form))));
    if (!job) return;
    form.reset();
    started(job);
  });

  on("b-create", "submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const res = await App.post(`${BASE}erstellen/`, { password: form.password.value, repeat: form.repeat.value });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      App.toast(body.error || gettext("Das hat nicht geklappt."), { ms: 6000 });
      return;
    }
    form.reset();
    started(body);
  });

  async function send(data) {
    const body = new FormData();
    for (const [key, value] of Object.entries(data)) body.append(key, value);
    for (let attempt = 1; ; attempt++) {
      let res = null;
      try {
        res = await fetch(`${BASE}hochladen/`, { method: "POST", headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" }, body });
      } catch (e) { /* Counts as a failed attempt. */ }
      if (res?.ok) return res.json();
      if (res?.status === 400) throw new Error((await res.json().catch(() => ({}))).error);
      if (attempt >= RETRIES) throw new Error();
      await new Promise((done) => setTimeout(done, attempt * 1000));
    }
  }

  async function upload(file) {
    const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
    const text = $("b-upload-text");
    uploading = true;
    render();
    text.classList.remove("hidden");
    try {
      let offset = 0;
      while (offset < file.size) {
        text.textContent = `… ${interpolate(gettext("%(name)s wird hochgeladen: %(percent)s"), { name: file.name, percent: `${Math.floor((offset / file.size) * 100)} %` }, true)}`;
        // The server names the next position, so a repeated piece does not count twice.
        ({ offset } = await send({ id, offset, total: file.size, chunk: file.slice(offset, offset + CHUNK) }));
      }
      await send({ id, offset, total: file.size, name: file.name });
      location.reload();
    } catch (e) {
      text.textContent = e.message || gettext("Das Hochladen hat nicht geklappt. Bitte erneut versuchen.");
      uploading = false;
      $("b-upload").value = "";
      render();
    }
  }

  on("b-upload", "change", (e) => {
    if (e.target.files[0]) upload(e.target.files[0]);
  });

  const dialog = $("b-dialog");
  let chosen = null;

  function showError(id, text) {
    $(id).textContent = text || gettext("Das hat nicht geklappt.");
    $(id).classList.remove("hidden");
  }

  document.addEventListener("click", (e) => {
    const button = e.target.closest("[data-restore]");
    if (button) {
      chosen = button.dataset.restore;
      $("b-dialog-name").textContent = chosen;
      $("b-check").reset();
      $("b-confirm").reset();
      $("b-check").classList.remove("hidden");
      $("b-confirm").classList.add("hidden");
      $("b-check-error").classList.add("hidden");
      $("b-confirm-error").classList.add("hidden");
      dialog.showModal();
    }
    if (e.target.closest("[data-dialog-close]")) dialog.close();
  });

  // The password hardening takes a few seconds on purpose.
  async function whileBusy(button, label, work) {
    const text = button.textContent;
    button.disabled = true;
    button.textContent = label;
    try {
      return await work();
    } finally {
      button.disabled = false;
      button.textContent = text;
    }
  }

  $("b-check").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("b-check-error").classList.add("hidden");
    const button = e.target.querySelector(".btn-primary");
    const res = await whileBusy(button, gettext("Wird geprüft …"), () => App.post(`${BASE}pruefen/`, { name: chosen, password: $("b-restore-password").value }));
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      showError("b-check-error", body.error);
      return;
    }
    const rows = [[gettext("Schule"), body.school], [gettext("Stand"), body.created_at], [gettext("Version"), body.version], [gettext("Personen"), body.people], [gettext("Geräte"), body.assets], [gettext("Benutzer"), body.users], [gettext("Dateien"), body.files]];
    $("b-summary").innerHTML = rows.map(([k, v]) => `<dt class="muted">${App.esc(k)}</dt><dd>${App.esc(v)}</dd>`).join("");
    $("b-check").classList.add("hidden");
    $("b-confirm").classList.remove("hidden");
    $("b-warning").classList.toggle("hidden", !!body.problem);
    $("b-confirm-field").classList.toggle("hidden", !!body.problem);
    $("b-confirm-button").classList.toggle("hidden", !!body.problem);
    if (body.problem) showError("b-confirm-error", body.problem);
  });

  $("b-confirm-word").addEventListener("input", (e) => {
    $("b-confirm-button").disabled = e.target.value !== dialog.dataset.confirmWord;
  });

  $("b-confirm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const res = await App.post(`${BASE}einspielen/`, { name: chosen, password: $("b-restore-password").value, confirm: $("b-confirm-word").value });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      showError("b-confirm-error", body.error);
      return;
    }
    dialog.close();
    started(body);
  });

  render();
  if (job?.state === "running") poll();
})();
