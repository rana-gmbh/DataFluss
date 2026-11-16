// Side panel: details beside the list, arrow keys move through the current list
window.RecordPanel = (function () {
  const DEFAULT_SHARE = 0.46;
  const SHARE_KEY = "record-panel-share";
  const MIN_WIDTH = 360;
  const panel = document.getElementById("record-panel");
  const body = document.getElementById("record-panel-body");
  const divider = document.getElementById("record-panel-divider");
  const sideBySide = window.matchMedia("(min-width: 1024px)");
  const api = { activeId: null, tabulator: attachTabulator, show, remark: () => list?.mark(null, api.activeId), open: (id) => open(id), openRow: (row) => open(row.dataset.record) };
  let list = null;
  let request = null;
  let focusAfterLoad = false;
  let unsaved = false;
  let saving = false;
  let share = DEFAULT_SHARE;
  try { share = Number(localStorage.getItem(SHARE_KEY)) || share; } catch (e) {}

  const mayLeave = () => !unsaved || confirm(gettext("Ungespeicherte Änderungen verwerfen?"));

  async function show(id) {
    request?.abort();
    request = new AbortController();
    body.classList.add("is-loading");
    try {
      const res = await fetch(`${list.base}${id}/panel/`, { signal: request.signal });
      if (!res.ok || id !== api.activeId) return;
      body.innerHTML = await res.text();
      unsaved = false;
      htmx.process(body);
      body.querySelectorAll("[data-toast]").forEach((el) => App.toast(el.dataset.toast, { error: "error" in el.dataset }));
      if (focusAfterLoad) {
        focusAfterLoad = false;
        const heading = body.querySelector("h2");
        heading?.setAttribute("tabindex", "-1");
        heading?.focus({ preventScroll: true });
      }
    } catch (e) {
      if (e.name !== "AbortError") App.toast(gettext("Datensatz konnte nicht geladen werden"));
    } finally {
      if (id === api.activeId) body.classList.remove("is-loading");
    }
  }
  const showSoon = App.debounce(show, 90);

  function setActive(id) {
    const previous = api.activeId;
    api.activeId = id;
    list.mark(previous, id);
  }

  function open(id) {
    if (!sideBySide.matches) { location.href = `${list.base}${id}/`; return; }
    if (id === api.activeId || !mayLeave()) return;
    const wasClosed = panel.classList.contains("hidden");
    setActive(id);
    if (wasClosed) applyShare();
    panel.classList.remove("hidden");
    divider.classList.remove("hidden");
    if (wasClosed) list.resized?.();
    focusAfterLoad = true;
    rememberInAddress(id);
    show(id);
  }

  // The address names the open record, so a reload or a shared link comes back to it.
  function rememberInAddress(id) {
    const url = new URL(location.href);
    if (id) url.searchParams.set("offen", id); else url.searchParams.delete("offen");
    history.replaceState(null, "", url);
  }

  function reopen() {
    // Only a record of this list. Anything else would load a foreign address into the page.
    const id = new URLSearchParams(location.search).get("offen");
    if (id && list.ids().includes(id)) open(id);
  }

  function close() {
    if (!mayLeave()) return;
    request?.abort();
    const previous = api.activeId;
    setActive(null);
    rememberInAddress(null);
    list.focusRow?.(previous);
    unsaved = false;
    panel.classList.add("hidden");
    divider.classList.add("hidden");
    body.innerHTML = "";
    list.resized?.();
  }

  body.addEventListener("click", (e) => { if (e.target.closest("[data-panel-close]")) close(); });

  document.addEventListener("keydown", (e) => {
    if (!api.activeId || list.busy?.()) return;
    if (!document.getElementById("search-modal")?.classList.contains("hidden")) return;
    if (document.querySelector("dialog[open], #modal-root > *")) return;
    if (e.key === "Escape") { close(); return; }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input, textarea, select, [contenteditable]")) return;
    e.preventDefault();
    const ids = list.ids();
    const next = ids[ids.indexOf(api.activeId) + (e.key === "ArrowDown" ? 1 : -1)];
    if (!next || !mayLeave()) return;
    unsaved = false;
    setActive(next);
    list.reveal(next);
    showSoon(next);
  });

  // Divider between list and panel: drag it or use the arrow keys, double click resets
  function fit(value) {
    const min = Math.min(MIN_WIDTH / panel.parentElement.clientWidth, 0.5);
    return Math.min(Math.max(value, min), 1 - min);
  }

  function applyShare() {
    const width = fit(share);
    panel.style.width = `${width * 100}%`;
    divider.setAttribute("aria-valuenow", Math.round(width * 100));
  }

  function saveShare() {
    try { localStorage.setItem(SHARE_KEY, share); } catch (e) {}
    list.resized?.();
  }

  function setShare(value) {
    share = fit(value);
    applyShare();
  }

  divider.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    divider.focus({ preventScroll: true });
    divider.setPointerCapture(e.pointerId);
    document.body.classList.add("panel-resizing");
    const grab = panel.getBoundingClientRect().left - e.clientX;
    const box = panel.parentElement.getBoundingClientRect();
    const drag = (move) => setShare((box.right - move.clientX - grab) / box.width);
    divider.addEventListener("pointermove", drag);
    divider.addEventListener("lostpointercapture", () => {
      divider.removeEventListener("pointermove", drag);
      document.body.classList.remove("panel-resizing");
      saveShare();
    }, { once: true });
  });

  divider.addEventListener("dblclick", () => { setShare(DEFAULT_SHARE); saveShare(); });

  divider.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    setShare(share + (e.key === "ArrowLeft" ? 0.02 : -0.02));
    saveShare();
  });

  window.addEventListener("resize", App.debounce(() => { if (api.activeId) applyShare(); }, 150));

  // Forms with a save button stay in the panel instead of leaving the list
  body.addEventListener("input", (e) => { if (e.target.closest("[data-panel-form]")) unsaved = true; });
  body.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!form.matches("[data-panel-form]")) return;
    e.preventDefault();
    if (saving) return;
    saving = true;
    const id = api.activeId;
    try {
      const res = await fetch(form.getAttribute("action"), { method: "POST", body: new FormData(form, e.submitter), redirect: "manual" });
      if (res.ok || res.type === "opaqueredirect") {
        await show(id);
        list.refresh?.(id);
      } else if (res.status === 409 || res.status === 422) {
        // The answer is the whole page. Only its form belongs here: it keeps the input and says what is wrong.
        const fresh = new DOMParser().parseFromString(await res.text(), "text/html").querySelector("form[data-guard]");
        if (fresh) {
          fresh.setAttribute("data-panel-form", "");
          form.replaceWith(fresh);
          htmx.process(fresh);
          body.scrollTop = 0;
        }
      } else if (res.status !== 401) {
        App.toast(gettext("Das hat nicht geklappt."), { error: true });
      }
    } catch (err) {
      // The notice at the top says that nothing was saved, and the form keeps what was typed.
    }
    saving = false;
  });

  // Changes made in the panel show up in the list straight away
  const refreshSoon = App.debounce(() => { if (api.activeId) list.refresh?.(api.activeId); }, 150);
  panel.addEventListener("htmx:afterRequest", (e) => { if (e.detail.requestConfig?.verb === "post") refreshSoon(); });
  document.body.addEventListener("reloadForm", refreshSoon);

  function attachTabulator(table, { base, rowUrl, busy }) {
    const reformat = (id) => { const row = id && table.getRow(id); if (row) row.reformat(); };
    list = {
      base,
      busy,
      mark(previous, id) { reformat(previous); reformat(id); },
      ids: () => table.getRows("active").map((r) => r.getData().id),
      reveal: (id) => table.scrollToRow(table.getRow(id), "nearest", false).catch(() => {}),
      resized: () => table.redraw(true),
      async refresh(id) {
        const res = await fetch(rowUrl(id));
        if (res.ok) table.updateData(await res.json());
      },
    };
    // A click on an editable cell waits a moment: opening the panel narrows the list, and the second click
    // of a double click (edit the cell) would land on another cell.
    let waiting = null;
    table.on("cellClick", (e, cell) => {
      if (!cell.getField()) return;
      clearTimeout(waiting);
      const id = cell.getData().id;
      if (api.activeId || !cell.getColumn().getDefinition().editor) open(id);
      else waiting = setTimeout(() => open(id), 260);
    });
    table.element.addEventListener("dblclick", () => clearTimeout(waiting), true); // the cell editor swallows Tabulator's own event
    let first = true;
    table.on("dataProcessed", () => { if (first) reopen(); first = false; });
  }

  // Lists rendered by the server: rows carry data-record, their main link opens the panel
  function attachRows(rows) {
    const mark = () => rows.querySelectorAll("[data-record]").forEach((row) => row.classList.toggle("row-active", row.dataset.record === api.activeId));
    list = {
      base: rows.dataset.base,
      mark,
      focusRow: (id) => rows.querySelector(`[data-record="${id}"] [data-record-link]`)?.focus(),
      ids: () => [...rows.querySelectorAll("[data-record]")].map((row) => row.dataset.record),
      reveal: (id) => rows.querySelector(`[data-record="${id}"]`)?.scrollIntoView({ block: "nearest" }),
      async refresh() {
        const res = await fetch(location.href);
        if (!res.ok) return;
        const fresh = new DOMParser().parseFromString(await res.text(), "text/html").getElementById(rows.id);
        if (fresh) { rows.innerHTML = fresh.innerHTML; mark(); }
      },
    };
    rows.addEventListener("click", (e) => {
      const row = e.target.closest("[data-record]");
      const link = e.target.closest("a, button");
      if (!row || e.metaKey || e.ctrlKey || e.shiftKey || (link && !link.matches("[data-record-link]"))) return;
      e.preventDefault();
      open(row.dataset.record);
    });
  }

  const rows = document.getElementById("record-rows");
  if (rows) { attachRows(rows); reopen(); }

  return api;
})();
