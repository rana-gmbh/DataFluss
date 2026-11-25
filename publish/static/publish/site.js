// The published site (templates/publish/site/): light or dark, search over every table, filter, sort,
// gallery or list, and tabs on record pages. Without JavaScript everything is still there to read.
(function () {
  const root = document.documentElement;
  let stored = null;
  try { stored = localStorage.getItem("theme"); } catch (e) {}
  root.classList.toggle("dark", stored ? stored === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);
  root.classList.add("js");

  const fold = (text) => String(text || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  const collator = new Intl.Collator(root.lang || "de", { numeric: true, sensitivity: "base" });

  document.addEventListener("DOMContentLoaded", () => {
    const base = document.body.dataset.root || "";

    const toggle = document.querySelector("[data-theme-toggle]");
    if (toggle) {
      toggle.hidden = false;
      toggle.addEventListener("click", () => {
        const dark = !root.classList.contains("dark");
        root.classList.toggle("dark", dark);
        try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch (e) {}
      });
    }

    // Search over all tables (the index is _assets/search.js).
    const input = document.getElementById("site-search");
    const results = document.getElementById("site-results");
    if (input && results) {
      input.hidden = false;
      let active = -1;
      const render = () => {
        const words = fold(input.value).split(/\s+/).filter(Boolean);
        const index = window.DATAFLUSS_SEARCH || [];
        results.textContent = "";
        active = -1;
        if (!words.length) { results.hidden = true; return; }
        const named = (r) => words.every((w) => fold(r.n).includes(w)); // matches in the title first
        const hits = index.filter((r) => { const text = fold(r.x); return words.every((w) => text.includes(w)); })
          .sort((a, b) => named(b) - named(a)).slice(0, 30);
        if (!hits.length) {
          const none = document.createElement("div");
          none.className = "muted";
          none.style.padding = "0.5rem 0.6rem";
          none.textContent = results.dataset.emptyText || "–";
          results.appendChild(none);
        }
        for (const hit of hits) {
          const a = document.createElement("a");
          a.href = base + hit.u;
          const title = document.createElement("div");
          title.className = "site-hit-title";
          title.textContent = hit.n;
          const sub = document.createElement("div");
          sub.className = "site-hit-sub";
          sub.textContent = [hit.t, hit.s].filter(Boolean).join(" · ");
          a.append(title, sub);
          results.appendChild(a);
        }
        results.hidden = false;
      };
      input.addEventListener("input", render);
      input.addEventListener("keydown", (e) => {
        const links = [...results.querySelectorAll("a")];
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          active = Math.max(0, Math.min(links.length - 1, active + (e.key === "ArrowDown" ? 1 : -1)));
          links.forEach((l, i) => l.classList.toggle("is-active", i === active));
        } else if (e.key === "Enter" && links.length) {
          location.href = links[Math.max(active, 0)].href;
        } else if (e.key === "Escape") {
          input.value = ""; render();
        }
      });
      document.addEventListener("click", (e) => { if (!e.target.closest(".site-search")) results.hidden = true; });
      input.addEventListener("focus", () => { if (input.value) render(); });
    }

    // A table: filter, gallery or list, sort.
    const tools = document.querySelector("[data-tools]");
    if (tools) {
      tools.hidden = false;
      const filter = tools.querySelector("[data-filter]");
      const none = document.querySelector("[data-none]");
      const views = document.querySelectorAll("[data-layout]");
      const KEY = "site-view:" + location.pathname.replace(/[^/]*$/, "");
      const show = (name) => views.forEach((v) => { v.hidden = v.dataset.layout !== name; });
      let chosen = null;
      try { chosen = sessionStorage.getItem(KEY); } catch (e) {}
      const radios = tools.querySelectorAll("[data-view]");
      if (chosen && [...radios].some((r) => r.value === chosen)) {
        radios.forEach((r) => { r.checked = r.value === chosen; });
        show(chosen);
      }
      radios.forEach((r) => r.addEventListener("change", () => {
        show(r.value);
        try { sessionStorage.setItem(KEY, r.value); } catch (e) {}
      }));
      filter.addEventListener("input", () => {
        const words = fold(filter.value).split(/\s+/).filter(Boolean);
        let any = false;
        document.querySelectorAll("[data-row]").forEach((row) => {
          const text = fold(row.dataset.text);
          const ok = words.every((w) => text.includes(w));
          row.hidden = !ok;
          if (ok) any = true;
        });
        if (none) none.hidden = any;
      });
      document.querySelectorAll("[data-sort]").forEach((button) => button.addEventListener("click", () => {
        const table = button.closest("table");
        const body = table.tBodies[0];
        const column = Number(button.dataset.sort);
        const numeric = button.hasAttribute("data-numeric");
        const up = button.getAttribute("aria-sort") !== "ascending";
        table.querySelectorAll("[data-sort]").forEach((b) => b.removeAttribute("aria-sort"));
        button.setAttribute("aria-sort", up ? "ascending" : "descending");
        const value = (row) => row.cells[column].dataset.value || "";
        const rows = [...body.rows].sort((a, b) => {
          const x = value(a), y = value(b);
          if (x === "" || y === "") return x === y ? 0 : x === "" ? 1 : -1; // empty values last
          const order = numeric ? Number(x.replace(",", ".")) - Number(y.replace(",", ".")) : collator.compare(x, y);
          return up ? order : -order;
        });
        body.append(...rows);
      }));
    }

    // Record pages: tabs.
    const tabs = document.querySelector(".site-tabs");
    if (tabs) {
      tabs.hidden = false;
      const panels = document.querySelectorAll("[data-panel]");
      const open = (id) => {
        tabs.querySelectorAll("[data-tab]").forEach((t) => { t.classList.toggle("active", t.dataset.tab === id); t.setAttribute("aria-selected", String(t.dataset.tab === id)); });
        panels.forEach((p) => { p.hidden = p.dataset.panel !== id; });
      };
      tabs.querySelectorAll("[data-tab]").forEach((t) => t.addEventListener("click", () => open(t.dataset.tab)));
      open(tabs.querySelector("[data-tab]").dataset.tab);
    }
  });
})();
