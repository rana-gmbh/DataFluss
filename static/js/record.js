document.addEventListener("alpine:init", () => {
  // The open tab is shared across records, so browsing stays on e.g. "IT Informationen".
  Alpine.data("recordTabs", (kind, first) => ({
    tab: first,
    key: "record-tab:" + kind,
    init() {
      let title = null;
      try { title = sessionStorage.getItem(this.key); } catch (e) {}
      if (!title) return;
      const match = [...this.$root.querySelectorAll("[data-tab-id]")].find((b) => b.textContent.trim() === title);
      if (match) this.tab = match.dataset.tabId;
    },
    show(id, button) {
      this.tab = id;
      try { sessionStorage.setItem(this.key, button.textContent.trim()); } catch (e) {}
    },
  }));

  // A rating field (templates/tables/_rating.html): pointer or keys pick whole or half stars; picking the
  // same value again clears it. The hidden input saves like any other field.
  // The same rating can show twice (the head of the page and the form); the other copy follows, without saving.
  Alpine.data("ratingInput", (value, top, half, key) => ({
    value: Number(value) || 0,
    preview: null,
    init() {
      window.addEventListener("rating-set", (e) => { if (e.detail.key === key && e.detail.from !== this.$el) this.value = e.detail.value; });
      const row = this.$refs.stars.firstElementChild;
      row.setAttribute("aria-hidden", "true");
      row.removeAttribute("role");
      this.$watch("preview", () => this.paint());
      this.$watch("value", () => this.paint());
    },
    paint() { this.$refs.stars.firstElementChild.style.setProperty("--pct", `${App.starShare(this.preview ?? this.value, top)}%`); },
    at(event) {
      const box = this.$refs.stars.getBoundingClientRect();
      const units = (Math.min(Math.max(event.clientX - box.left, 0), box.width) / box.width) * (26 * top - 2);
      const index = Math.min(Math.floor(units / 26), top - 1);
      return half && units - index * 26 <= 12 ? index + 0.5 : index + 1;
    },
    hover(event) { if (event.pointerType !== "touch") this.preview = this.at(event); },
    leave() { this.preview = null; },
    pick(event) { const v = this.at(event); this.set(v === this.value ? 0 : v); },
    key(event) {
      const step = half ? 0.5 : 1;
      let v = this.value;
      if (["ArrowRight", "ArrowUp"].includes(event.key)) v += step;
      else if (["ArrowLeft", "ArrowDown"].includes(event.key)) v -= step;
      else if (["Home", "Delete", "Backspace", "0"].includes(event.key)) v = 0;
      else if (event.key === "End") v = top;
      else if (/^[1-9]$/.test(event.key)) v = Math.min(Number(event.key), top);
      else return;
      event.preventDefault();
      this.set(Math.max(0, Math.min(v, top)));
    },
    set(v) {
      this.value = v;
      this.preview = null;
      this.$refs.input.value = v ? String(v) : "";
      this.$refs.input.dispatchEvent(new Event("change", { bubbles: true }));
      window.dispatchEvent(new CustomEvent("rating-set", { detail: { key, value: v, from: this.$el } }));
    },
    text() { const v = this.preview ?? this.value; return v ? String(v).replace(".", ",") : "–"; },
  }));

  // Search at TMDB (templates/tables/_tmdb_dialog.html): type, arrows, Enter takes the title over.
  Alpine.data("tmdbSearch", (url) => ({
    q: "", kind: "", mode: "add", action: "", title: "", active: 0, status: "watched", how: "", busy: false, request: null,
    init() {
      try { this.status = localStorage.getItem("tmdb-status") || "watched"; this.how = localStorage.getItem("tmdb-how") || ""; } catch (e) {}
      this.$watch("status", (v) => { try { localStorage.setItem("tmdb-status", v); } catch (e) {} });
      this.$watch("how", (v) => { try { localStorage.setItem("tmdb-how", v); } catch (e) {} });
    },
    show(detail) {
      Object.assign(this, { mode: detail.mode || "add", action: detail.action, title: detail.title || "", q: detail.q || "", busy: false });
      this.$refs.results.innerHTML = "";
      this.$refs.dialog.showModal();
      this.$nextTick(() => { this.$refs.q.focus(); this.$refs.q.select(); });
      if (this.q) this.find();
    },
    reset() { this.request?.abort(); },
    setKind(kind) { this.kind = kind; this.find(); },
    async find() {
      this.request?.abort();
      const q = this.q.trim();
      if (!q) { this.$refs.results.innerHTML = ""; return; }
      this.request = new AbortController();
      this.$refs.results.classList.add("is-loading");
      try {
        const res = await fetch(`${url}?${new URLSearchParams({ q, art: this.kind })}`, { signal: this.request.signal });
        if (!res.ok) throw new Error(String(res.status));
        this.$refs.results.innerHTML = await res.text();
        this.active = 0;
        this.mark();
      } catch (e) {
        if (e.name !== "AbortError") this.$refs.results.innerHTML = `<p class="p-4 text-[13px] text-[var(--bad)]">${App.esc(gettext("Die Suche hat nicht geklappt."))}</p>`;
      } finally {
        this.$refs.results.classList.remove("is-loading");
      }
    },
    options() { return [...this.$refs.results.querySelectorAll("[role=option]")]; },
    mark() { this.options().forEach((o, n) => { o.classList.toggle("is-active", n === this.active); o.setAttribute("aria-selected", String(n === this.active)); }); },
    move(step) {
      const n = this.options().length;
      if (!n) return;
      this.active = (this.active + step + n) % n;
      this.mark();
      this.options()[this.active].scrollIntoView({ block: "nearest" });
    },
    choose() { this.options()[this.active]?.click(); },
    pick(event) {
      const option = event.target.closest("[role=option]");
      if (!option) return;
      if (option.dataset.url) { location.href = option.dataset.url; return; }
      this.$refs.kind.value = option.dataset.kind;
      this.$refs.id.value = option.dataset.id;
      this.busy = true;
      this.$refs.form.requestSubmit();
    },
  }));

  // Search of a link field (templates/tables/_link_widget.html): arrows move, Enter picks. Enter before
  // the options for the typed text arrived waits for them instead of picking a stale one.
  Alpine.data("linkPicker", () => ({
    open: false,
    active: 0,
    pending: false,
    options() { return [...this.$root.querySelectorAll("[role=option]")]; },
    fresh() { const q = this.$root.querySelector("[data-q]"); return q && q.dataset.q === this.$root.querySelector("input").value; },
    mark() { this.options().forEach((o, n) => o.classList.toggle("bg-[var(--surface-2)]", n === this.active)); },
    move(step) {
      this.open = true;
      this.active = Math.max(0, Math.min(this.active + step, this.options().length - 1));
      this.mark();
      this.options()[this.active]?.scrollIntoView({ block: "nearest" });
    },
    choose() {
      if (!this.fresh()) { this.pending = true; return; }
      (this.options()[this.active] || this.options()[0])?.click();
    },
    loaded() {
      this.active = 0;
      this.mark();
      // htmx ignores a click on new content until its swap is fully done, hence the extra tick.
      // Enter pressed before the list was seen picks a match, but never creates a new record.
      if (this.pending && this.fresh()) { this.pending = false; setTimeout(() => this.options().find((o) => !o.hasAttribute("data-create"))?.click()); }
    },
  }));
});

// A new own-table record (templates/tables/record.html): the cursor starts in its first field.
document.addEventListener("alpine:initialized", () => setTimeout(() => {
  const bar = document.querySelector("[data-focus-first]");
  if (!bar) return;
  const first = [...document.querySelectorAll("[data-focus-first] ~ * :is(input, textarea, select)")]
    .find((el) => el.type !== "hidden" && !el.disabled && !el.readOnly && el.offsetParent !== null);
  first?.focus();
}));

// The record form reloads after a save when computed fields or conditions follow it. The field being typed
// in meanwhile (Tab moved on) keeps its focus, its unsaved text and the cursor.
(function () {
  let editing = null;
  document.addEventListener("htmx:beforeSwap", (e) => {
    const form = e.detail.target;
    if (!form || form.id !== "record-form") return;
    const el = document.activeElement;
    editing = el && el.id && form.contains(el) && el.matches("input, textarea, select")
      ? { id: el.id, value: el.value, checked: el.checked, start: el.selectionStart, end: el.selectionEnd } : null;
  });
  document.addEventListener("htmx:afterSwap", () => {
    if (!editing) return;
    const was = editing;
    editing = null;
    // The new form's tabs are hidden (x-cloak) until Alpine has set them up, and a hidden field takes no focus.
    setTimeout(() => {
      const el = document.getElementById(was.id);
      if (!el) return;
      if (el.type === "checkbox" || el.type === "radio") el.checked = was.checked;
      else if (el.value !== was.value) el.value = was.value;
      el.focus();
      try { if (was.start !== null) el.setSelectionRange(was.start, was.end); } catch (err) {}
    });
  });
})();
