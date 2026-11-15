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
});
