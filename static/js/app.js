window.App = (function () {
  // The cookie, because signing in again in another tab replaces the token this page was built with.
  const csrf = () => document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] || document.querySelector('meta[name="csrf-token"]').content;

  // Whatever goes wrong with a request ends up here, so nothing fails without a word.
  const PROBLEMS = {
    signedOut: gettext("Sie sind nicht mehr angemeldet. Ihre letzte Änderung wurde nicht gespeichert."),
    offline: gettext("Keine Verbindung zum Server. Ihre letzte Änderung wurde nicht gespeichert."),
  };

  function showProblem(kind) {
    document.getElementById("problem-text").textContent = PROBLEMS[kind];
    document.getElementById("problem-login").classList.toggle("hidden", kind !== "signedOut");
    document.getElementById("problem").classList.remove("hidden");
  }

  function clearProblem() { document.getElementById("problem").classList.add("hidden"); }

  function noteResponse(status) {
    if (status === 401) showProblem("signedOut");
    else if (status < 400) clearProblem();
  }

  const plainFetch = window.fetch.bind(window);
  window.fetch = async (url, options = {}) => {
    const headers = new Headers(options.headers);
    headers.set("X-Requested-With", "fetch");
    try {
      const res = await plainFetch(url, { ...options, headers });
      noteResponse(res.status);
      return res;
    } catch (err) {
      if (err.name !== "AbortError") showProblem("offline");
      throw err;
    }
  };

  function esc(text) {
    const d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  }

  function toast(text, options = {}) {
    const root = document.getElementById("toasts");
    const el = document.createElement("div");
    el.className = options.error ? "toast toast-error" : "toast";
    el.innerHTML = `<span>${esc(text)}</span>`;
    if (options.error) {
      // Stays until closed, because a refusal must not pass unread.
      el.setAttribute("role", "alert");
      const close = document.createElement("button");
      close.setAttribute("aria-label", gettext("Hinweis schließen"));
      close.textContent = "×";
      close.onclick = () => el.remove();
      el.appendChild(close);
      root.appendChild(el);
      return;
    }
    if (options.action) {
      const b = document.createElement("button");
      b.className = "underline font-semibold";
      b.textContent = options.action.label;
      b.onclick = () => { options.action.run(); el.remove(); };
      el.appendChild(b);
    }
    root.appendChild(el);
    setTimeout(() => el.remove(), options.ms || (options.action ? 7000 : 2500));
  }

  async function post(url, data = {}) {
    const body = new URLSearchParams();
    for (const [k, v] of Object.entries(data)) {
      if (Array.isArray(v)) v.forEach((x) => body.append(k, x));
      else body.append(k, v == null ? "" : v);
    }
    const res = await fetch(url, { method: "POST", headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" }, body });
    return res;
  }

  async function postJSON(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json", "X-Requested-With": "fetch" },
      body: JSON.stringify(payload),
    });
  }

  function ask(text) {
    const dialog = document.getElementById("confirm-dialog");
    document.getElementById("confirm-text").textContent = text;
    dialog.returnValue = "";
    dialog.showModal();
    return new Promise((done) => dialog.addEventListener("close", () => done(dialog.returnValue === "yes"), { once: true }));
  }

  function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  function openModal(html) {
    const root = document.getElementById("modal-root");
    root.innerHTML = html;
    htmx.process(root);
    root.querySelector("[autofocus]")?.focus();
  }

  function closeModal() { document.getElementById("modal-root").innerHTML = ""; }

  function zoomPhoto(url) {
    const dialog = document.getElementById("photo-zoom");
    dialog.querySelector("img").src = url;
    dialog.showModal();
  }

  // Global search
  function openSearch() {
    const modal = document.getElementById("search-modal");
    if (!modal) return;
    searchOpener = document.activeElement;
    modal.classList.remove("hidden");
    const input = document.getElementById("search-input");
    input.value = "";
    document.getElementById("search-results").innerHTML = "";
    input.focus();
  }
  let searchOpener = null;
  function closeSearch() {
    const modal = document.getElementById("search-modal");
    if (!modal || modal.classList.contains("hidden")) return;
    modal.classList.add("hidden");
    searchOpener?.focus();
  }

  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSearch(); }
    if (e.key === "Escape") { closeSearch(); closeModal(); }
    const modal = document.getElementById("search-modal");
    if (modal && !modal.classList.contains("hidden") && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      const hits = [...document.querySelectorAll("[data-search-hit]")];
      if (!hits.length) return;
      e.preventDefault();
      const i = hits.indexOf(document.activeElement);
      const next = e.key === "ArrowDown" ? i + 1 : i - 1;
      (hits[next] || document.getElementById("search-input")).focus();
    }
    if (modal && !modal.classList.contains("hidden") && e.key === "Enter" && document.activeElement.id === "search-input") {
      document.querySelector("[data-search-hit]")?.click();
    }
  });

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-open-search]")) openSearch();
    if (e.target.closest("[data-close-search]")) closeSearch();
    if (e.target.closest("[data-close-modal]")) closeModal();
    if (e.target.closest("#problem-close")) clearProblem();
    const zoom = e.target.closest("[data-zoom]");
    if (zoom) zoomPhoto(zoom.dataset.zoom);
    if (e.target.closest("#photo-zoom")) document.getElementById("photo-zoom").close();
    if (e.target.closest("[data-theme-toggle]")) {
      const dark = document.documentElement.classList.toggle("dark");
      try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch (err) {}
    }
    const backLink = e.target.closest("a[data-back]");
    if (backLink && document.referrer.startsWith(location.origin) && new URL(document.referrer).pathname !== location.pathname) {
      backLink.href = document.referrer; // Back to the list as it was, filters included.
    }
    const confirmEl = e.target.closest("[data-confirm]");
    if (confirmEl && !confirmEl.dataset.confirmed) {
      e.preventDefault();
      e.stopPropagation();
      ask(confirmEl.dataset.confirm).then((yes) => {
        if (!yes) return;
        confirmEl.dataset.confirmed = "1";
        confirmEl.click();
        delete confirmEl.dataset.confirmed;
      });
    }
  }, true);

  // Lists follow the typing. Only the rows change, so the cursor stays where it is.
  let liveRequest = 0;
  document.addEventListener("input", debounce(async (e) => {
    if (!e.target.matches("[data-live-search]")) return;
    const url = new URL(location.href);
    url.search = new URLSearchParams(new FormData(e.target.form)).toString();
    const mine = ++liveRequest;
    const res = await fetch(url);
    if (!res.ok || mine !== liveRequest) return;
    const fresh = new DOMParser().parseFromString(await res.text(), "text/html").querySelectorAll("[data-live]");
    document.querySelectorAll("[data-live]").forEach((part, index) => {
      if (fresh[index]) { part.innerHTML = fresh[index].innerHTML; htmx.process(part); }
    });
    history.replaceState(null, "", url);
    window.RecordPanel?.remark();
  }, 300));

  document.addEventListener("change", (e) => {
    if (e.target.matches("[data-autosubmit]")) e.target.form.requestSubmit();
  });

  // From far away a page takes a moment, so a click answers at once.
  document.addEventListener("click", (e) => {
    const link = e.target.closest("a[href]");
    if (!link || e.defaultPrevented || link.target || link.hasAttribute("download") || link.origin !== location.origin) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || link.getAttribute("href").startsWith("#")) return;
    document.documentElement.classList.add("navigating");
    setTimeout(() => document.documentElement.classList.remove("navigating"), 8000); // A file download never leaves the page.
  });
  window.addEventListener("pageshow", () => document.documentElement.classList.remove("navigating"));

  // Field autosave feedback from the server
  document.addEventListener("fieldSaved", (e) => {
    const d = e.detail;
    const input = document.querySelector(`[data-field="${d.key}"]`);
    input?.classList.remove("field-error");
    document.querySelector(`[data-field-error="${d.key}"]`)?.replaceChildren();
    if (d.undoable) {
      toast(interpolate(gettext("%(label)s gespeichert"), { label: d.label }, true), {
        action: {
          label: gettext("Rückgängig"),
          run: async () => {
            await post(d.url, { value: d.old });
            document.body.dispatchEvent(new Event("reloadForm"));
          },
        },
      });
    } else {
      toast(interpolate(gettext("%(label)s gespeichert"), { label: d.label }, true));
    }
    if (d.reload) document.body.dispatchEvent(new Event("reloadForm"));
  });

  document.addEventListener("fieldError", (e) => {
    const d = e.detail;
    document.querySelector(`[data-field="${d.key}"]`)?.classList.add("field-error");
    const box = document.querySelector(`[data-field-error="${d.key}"]`);
    if (box) box.textContent = d.message;
    toast(d.message, { error: true });
  });

  document.addEventListener("toast", (e) => toast(e.detail.value || e.detail.message || e.detail));

  document.addEventListener("htmx:configRequest", (e) => { e.detail.headers["X-CSRFToken"] = csrf(); });
  document.addEventListener("htmx:afterRequest", (e) => { if (e.detail.xhr?.status) noteResponse(e.detail.xhr.status); });
  document.addEventListener("htmx:sendError", () => showProblem("offline"));
  document.body?.addEventListener?.("htmx:responseError", (e) => {
    if (e.detail.xhr.status === 401) return;
    const text = e.detail.xhr.responseText || "";
    toast(text && text.length < 200 && !text.includes("<") ? text : gettext("Das hat nicht geklappt."), { error: true });
  });
  document.addEventListener("htmx:beforeRequest", (e) => { e.target.classList?.add("field-saving"); });
  document.addEventListener("htmx:afterRequest", (e) => { e.target.classList?.remove("field-saving"); });

  // Forms with a save button: leaving asks first, and sending waits until the session is known to be alive.
  const unsavedForm = () => document.querySelector("form[data-guard][data-dirty]");

  document.addEventListener("input", (e) => {
    const form = e.target.closest?.("form[data-guard]");
    if (form) form.dataset.dirty = "1";
  });

  // DaZ tests: a raw value fills the T-value from the norm table for the child's age on the test date, as Ninox did.
  // Only the changed raw value is looked up, so a value typed by hand stays until its raw value or the date changes.
  document.addEventListener("input", (e) => {
    const form = e.target.closest?.("form");
    const box = form?.querySelector("[data-norms] script");
    if (!box || !e.target.name) return;
    const norms = JSON.parse(box.textContent);
    const day = form.elements.date?.value;
    if (e.target.name !== "date" && !norms.targets[e.target.name]) return;
    const days = norms.born && day ? Math.round((Date.parse(day) - Date.parse(norms.born)) / 86400000) : NaN;
    const band = norms.bands.find(([start, end]) => days >= start && days < end);
    const status = form.querySelector("[data-norm-status]");
    if (status) {
      status.textContent = !norms.born ? norms.texts.no_birth : !day ? norms.texts.no_date
        : band ? interpolate(norms.texts.band, { band: band[2] }, true) : norms.texts.outside;
    }
    if (!band) return;
    const sources = e.target.name === "date" ? Object.keys(norms.targets) : [e.target.name];
    for (const source of sources) {
      const raw = (form.elements[source]?.value || "").trim().match(/^(\d+)(?:[,.]0*)?$/);
      const row = raw && band[3][String(Number(raw[1]))];
      if (!row) continue;
      for (const [target, column] of norms.targets[source]) if (form.elements[target] && row[column] !== null) form.elements[target].value = row[column];
    }
  });

  // The norm table window: shows how the report's T-values were found and lets the tables be adjusted.
  let normReport = null;
  const normDialog = () => document.getElementById("norm-dialog")
    || document.body.appendChild(Object.assign(document.createElement("dialog"), { id: "norm-dialog", className: "norm-dialog" }));
  const normDirty = () => normDialog().dataset.dirty && !confirm(gettext("Ungespeicherte Änderungen verwerfen?"));

  function showNorms(dialog, html) {
    dialog.innerHTML = html;
    delete dialog.dataset.dirty;
    // Saved tables count at once in the open report: the new values replace the old ones and are looked up again.
    const fresh = dialog.querySelector("[data-norms-fresh] script");
    const box = normReport?.querySelector("[data-norms] script");
    if (!fresh || !box) return;
    const next = JSON.parse(fresh.textContent);
    if (box.textContent.includes(JSON.stringify(next.bands))) return;
    box.textContent = JSON.stringify({ ...JSON.parse(box.textContent), bands: next.bands });
    normReport.elements.date?.dispatchEvent(new Event("input", { bubbles: true }));
  }

  async function loadNorms(url, options) {
    const dialog = normDialog();
    dialog.classList.add("is-loading");
    try {
      const res = await fetch(url, options);
      if (!res.ok && res.status !== 422) throw new Error(res.status);
      showNorms(dialog, await res.text());
      if (!dialog.open) dialog.showModal();
    } catch (e) {
      toast(gettext("Das hat nicht geklappt."), { error: true });
    } finally {
      dialog.classList.remove("is-loading");
    }
  }

  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-norm-window]");
    if (opener) {
      normReport = opener.closest("form");
      const params = new URLSearchParams();
      const box = normReport?.querySelector("[data-norms] script");
      if (box) {
        const norms = JSON.parse(box.textContent);
        const day = normReport.elements.date?.value;
        if (norms.born && day) params.set("tage", Math.round((Date.parse(day) - Date.parse(norms.born)) / 86400000));
        for (const source of Object.keys(norms.targets)) {
          const raw = (normReport.elements[source]?.value || "").trim().match(/^(\d+)(?:[,.]0*)?$/);
          if (raw) params.set(source, Number(raw[1]));
        }
      }
      loadNorms(`${opener.dataset.normWindow}?${params}`);
      return;
    }
    const band = e.target.closest("#norm-dialog [data-norm-band]");
    if (band) {
      e.preventDefault();
      if (!normDirty()) loadNorms(band.href);
      return;
    }
    if (e.target.closest("#norm-dialog [data-norm-close]") && !normDirty()) normDialog().close();
  });

  document.addEventListener("input", (e) => { if (e.target.closest("#norm-dialog form")) normDialog().dataset.dirty = "1"; });
  document.addEventListener("cancel", (e) => { if (e.target.id === "norm-dialog" && normDirty()) e.preventDefault(); }, true);
  document.addEventListener("submit", (e) => {
    const form = e.target.closest("#norm-dialog form");
    if (!form || e.defaultPrevented) return;
    e.preventDefault();
    loadNorms(form.getAttribute("action"), { method: "POST", body: new FormData(form, e.submitter) });
  });

  window.addEventListener("beforeunload", (e) => {
    if (unsavedForm()) { e.preventDefault(); e.returnValue = ""; }
  });

  // Capture, because the panel reads the form as soon as the event reaches it.
  document.addEventListener("submit", async (e) => {
    const form = e.target;
    const token = form.querySelector('[name="csrfmiddlewaretoken"]');
    if (token) token.value = csrf();
    if (form.target === "_blank") toast(gettext("PDF wird erstellt …"), { ms: 4000 });
    const guarded = form.matches("form[data-guard]") && !form.matches("[data-panel-form]");
    if (guarded && !form.dataset.checked) {
      e.preventDefault();
      const alive = await fetch("/konto/sitzung/").then((res) => res.ok).catch(() => false);
      if (!alive) return; // What was typed stays here, and the notice says what to do.
      form.dataset.checked = "1";
      form.requestSubmit(e.submitter);
      return;
    }
    delete form.dataset.checked;
    if (guarded) delete form.dataset.dirty;
    if (form.method !== "post" || form.target || form.hasAttribute("hx-post") || form.matches("[data-panel-form]")) return;
    // A second click on a slow connection must not send the form twice. Downloads leave the page open, hence the timer.
    if (form.dataset.sent) { e.preventDefault(); return; }
    form.dataset.sent = "1";
    setTimeout(() => delete form.dataset.sent, 8000);
  }, true);

  window.addEventListener("pageshow", () => document.querySelectorAll("form[data-sent]").forEach((form) => delete form.dataset.sent));

  document.addEventListener("htmx:afterSwap", (e) => {
    if (e.target.id === "modal-root") e.target.querySelector("[autofocus]")?.focus();
  });
  document.addEventListener("closeModal", closeModal);

  // Not in the desktop client: WebView2 serves its pages over https too, but sends a service worker's
  // requests past the shell, so they would never reach the database.
  if ("serviceWorker" in navigator && location.protocol === "https:" && !document.documentElement.hasAttribute("data-client")) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  const hint = document.getElementById("search-key");
  if (hint && !/Mac|iPhone|iPad/.test(navigator.platform)) hint.textContent = gettext("Strg K");

  return { ask, csrf, esc, toast, post, postJSON, debounce, openModal, closeModal };
})();

// Hidden secret values vanish again by themselves.
document.addEventListener("htmx:afterSettle", (e) => {
  e.target.querySelectorAll?.("[data-autohide]").forEach((el) => {
    setTimeout(() => { el.dispatchEvent(new Event("autohide")); }, Number(el.dataset.autohide) * 1000);
  });
});

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-copy]");
  if (!btn) return;
  const target = document.getElementById(btn.dataset.copy);
  await navigator.clipboard?.writeText(target ? target.textContent.trim() : "");
  App.toast(gettext("Kopiert"));
});
