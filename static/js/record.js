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
      if (this.pending && this.fresh()) { this.pending = false; setTimeout(() => this.options()[0]?.click()); }
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
