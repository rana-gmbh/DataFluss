// Only in the desktop client: the switcher between its databases at the top of the sidebar.
// The addresses under /__client/ are answered by the client itself, never by the server.
document.addEventListener("alpine:init", () => {
  const COLORS = ["#2553eb", "#0f766e", "#7c3aed", "#c2410c", "#be185d", "#4d7c0f", "#0369a1", "#a16207"];
  // The sync status of a database connected to a server, at the bottom of the sidebar.
  Alpine.data("syncStatus", () => ({
    level: "wait", text: gettext("Synchronisation …"), detail: "", busy: false,
    init() {
      this.load();
      setInterval(() => this.load(), 15000);
    },
    show(data) {
      const ago = data.last_success ? Math.round((Date.now() - Date.parse(data.last_success)) / 60000) : null;
      const when = ago === null ? gettext("noch nie") : ago < 1 ? gettext("gerade eben") : ago < 60
        ? interpolate(ngettext("vor %(n)s Min.", "vor %(n)s Min.", ago), { n: ago }, true)
        : interpolate(ngettext("vor %(n)s Std.", "vor %(n)s Std.", Math.round(ago / 60)), { n: Math.round(ago / 60) }, true);
      const waiting = data.waiting ? ` · ${interpolate(ngettext("%(n)s Änderung wartet", "%(n)s Änderungen warten", data.waiting), { n: data.waiting }, true)}` : "";
      if (data.revoked) {
        this.level = "bad"; this.text = gettext("Vom Server getrennt"); this.detail = data.last_error || "";
      } else if (data.last_error) {
        this.level = "warn"; this.text = `${gettext("Offline")}${waiting}`; this.detail = `${data.last_error} ${interpolate(gettext("Zuletzt synchronisiert: %(when)s."), { when }, true)}`;
      } else {
        this.level = data.waiting ? "warn" : "ok"; this.text = `${interpolate(gettext("Synchronisiert %(when)s"), { when }, true)}${waiting}`; this.detail = data.server || "";
      }
    },
    async load() {
      try { this.show(await (await fetch("/sync/mac/status", { cache: "no-store" })).json()); } catch { /* the next try comes */ }
    },
    async now() {
      this.busy = true;
      try {
        const data = await (await App.postJSON("/sync/mac/now", {})).json();
        this.show(data);
        if (data.state === "error") App.toast(data.error, { error: true });
      } finally {
        this.busy = false;
      }
    },
  }));

  // The shell's shortcuts: ⌘1 and ⇧⌘O on macOS, Strg+1 and Strg+⇧+O on Windows.
  const MAC = /Mac/.test(navigator.platform);
  const CTRL = gettext("Strg K").replace(/\s*K$/, "");

  Alpine.data("dbSwitcher", () => ({
    open: false,
    items: [],
    current: "",
    init() {
      const button = this.$el.querySelector("[title]");
      if (!MAC && button) button.title = button.title.replace("⇧⌘O", this.keys("O", true));
    },
    keys(key, shift = false) {
      return MAC ? `${shift ? "⇧" : ""}⌘${key}` : `${CTRL}+${shift ? "⇧+" : ""}${key}`;
    },
    async toggle() {
      this.open = !this.open;
      if (this.open) await this.load();
    },
    async load() {
      try {
        const data = await (await fetch("/__client/databases", { cache: "no-store" })).json();
        this.items = data.items;
        this.current = data.current;
      } catch {
        this.items = [];
      }
    },
    color(id) {
      return COLORS[[...id].reduce((sum, c) => sum + c.charCodeAt(0), 0) % COLORS.length];
    },
    // The same order as the window menu, which sorts the open databases by name.
    openIndex(id) {
      const open = this.items.filter((d) => d.open).sort((a, b) => a.name.localeCompare(b.name));
      return open.findIndex((d) => d.id === id) + 1;
    },
    async go(id) {
      this.open = false;
      if (id === this.current) return;
      const response = await fetch(`/__client/open/${id}`, { method: "POST" });
      if (!response.ok) App.toast(await response.text(), { error: true });
    },
    launcher() {
      this.open = false;
      fetch("/__client/launcher", { method: "POST" });
    },
  }));
});
