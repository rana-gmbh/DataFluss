// "Sammlung übernehmen" (tables/collection.py): the browser reads the MyMovies XML file, which may be larger
// than a request may be, and sends a few values per title; then it runs the import step by step, showing
// the progress, until all titles are in or someone stops it.
(function () {
  "use strict";

  // ---------------------------------------------------------------- reading the file
  const upload = document.querySelector("[data-collection-upload]");
  if (upload) {
    const input = upload.querySelector("[data-collection-file]");
    const status = upload.querySelector("[data-collection-status]");
    const say = (text, bad) => { status.textContent = text; status.classList.toggle("text-[var(--bad)]", !!bad); };
    const child = (el, tag) => { for (const c of el.children) if (c.tagName === tag) return c; return null; };
    const text = (el, ...path) => {
      let node = el;
      for (const tag of path) { node = node && child(node, tag); }
      return node ? node.textContent.trim() : "";
    };

    input.addEventListener("change", async () => {
      const file = input.files[0];
      if (!file) return;
      input.disabled = true;
      say(gettext("Datei wird gelesen …"));
      try {
        const doc = new DOMParser().parseFromString(await file.text(), "application/xml");
        if (doc.querySelector("parsererror") || doc.documentElement.tagName !== "Titles") {
          throw new Error(gettext("Das ist keine XML-Datei aus MyMovies."));
        }
        const titles = [];
        for (const title of doc.documentElement.children) {
          if (title.tagName !== "Title") continue;
          titles.push({
            media: text(title, "MediaType"), local: text(title, "LocalTitle"), original: text(title, "OriginalTitle"),
            year: text(title, "ProductionYear"), imdb: text(title, "IMDB"),
            rating: text(title, "Personal", "Rating"), added: text(title, "Personal", "Added"),
          });
        }
        if (!titles.length) throw new Error(gettext("In der Datei wurden keine Titel gefunden."));
        say(interpolate(gettext("%(n)s Einträge gefunden, werden vorbereitet …"), { n: titles.length }, true));
        const response = await App.postJSON(upload.dataset.url, { filename: file.name, titles });
        const body = await response.json().catch(() => ({}));
        if (!response.ok || !body.url) throw new Error(body.error || gettext("Die Datei konnte nicht übernommen werden."));
        location.href = body.url;
      } catch (error) {
        say(error.message, true);
        input.disabled = false;
        input.value = "";
      }
    });
  }

  // ---------------------------------------------------------------- running the import
  const run = document.querySelector("[data-collection-run]");
  if (!run || run.dataset.finished) return;
  const start = run.querySelector("[data-collection-start]");
  const stop = run.querySelector("[data-collection-stop]");
  const note = run.querySelector("[data-collection-note]");
  const log = run.querySelector("[data-collection-log]");
  let going = false;
  let retries = 0;

  const show = (body) => {
    run.querySelector("[data-collection-done]").textContent = body.done;
    run.querySelector("[data-collection-fill]").style.width = `${body.total ? Math.round((body.done / body.total) * 100) : 0}%`;
    run.querySelector("[data-collection-bar]").setAttribute("aria-valuenow", body.done);
    for (const title of body.last || []) {
      const item = document.createElement("li");
      item.textContent = `✓ ${title}`;
      log.prepend(item);
    }
    while (log.children.length > 60) log.lastElementChild.remove();
    for (const key of ["created", "existing"]) {
      const cell = document.querySelector(`[data-report="${key}"]`);
      if (cell && key in body) cell.textContent = body[key];
    }
    for (const key of ["unmatched", "problems"]) {
      const box = document.querySelector(`[data-report-list="${key}"]`);
      if (!box || !Array.isArray(body[key])) continue;
      if (key === "unmatched") document.querySelector('[data-report="unmatched"]').textContent = body[key].length;
      box.hidden = !body[key].length;
      box.querySelector("ul").replaceChildren(...body[key].map((t) => Object.assign(document.createElement("li"), { textContent: t })));
    }
  };
  const setGoing = (on) => {
    going = on;
    start.hidden = on;
    stop.hidden = !on;
    start.querySelector("span").textContent = gettext("Weiter");
  };
  const wait = (ms) => new Promise((done) => setTimeout(done, ms));

  async function loop() {
    setGoing(true);
    note.textContent = gettext("Läuft … Diese Seite offen lassen.");
    while (going) {
      let body;
      try {
        const response = await App.postJSON(run.dataset.url, {});
        body = await response.json();
        if (!response.ok) throw new Error(body.error || response.statusText);
      } catch (error) {
        body = { paused: gettext("Keine Verbindung zur Datenbank.") };
      }
      if (body.done !== undefined) show(body);
      if (body.finished) {
        note.textContent = gettext("Fertig.");
        setGoing(false);
        start.hidden = true;
        location.reload(); // the report with links, from the server
        return;
      }
      if (body.busy) { await wait(3000); continue; }
      if (body.paused) {
        retries += 1;
        if (retries > 3) {
          note.textContent = body.paused;
          setGoing(false);
          return;
        }
        note.textContent = `${body.paused} ${gettext("Neuer Versuch in 15 Sekunden …")}`;
        await wait(15000);
        continue;
      }
      retries = 0;
      note.textContent = gettext("Läuft … Diese Seite offen lassen.");
    }
    note.textContent = gettext("Angehalten.");
  }

  start.addEventListener("click", () => { retries = 0; loop(); });
  stop.addEventListener("click", () => setGoing(false));
})();
