(function () {
  const el = document.getElementById("asset-grid");
  const de = (iso) => { if (!iso) return ""; const [y, m, d] = iso.split("-"); return `${d}.${m}.${y}`; };
  // Keyed by the stable status value from the server; the label comes as status_label.
  const cls = { Ausgegeben: "badge-accent", Verfügbar: "badge-ok", Beschädigt: "badge-bad", Verkauft: "", Ausgemustert: "" };
  const table = new Tabulator(el, {
    height: "calc(100vh - 215px)", layout: "fitDataStretch", ajaxURL: "/api/geraete/", selectableRows: "highlight", index: "id",
    placeholder: gettext("Keine Geräte"), initialSort: [{ column: "number", dir: "asc" }],
    rowFormatter: (row) => row.getElement().classList.toggle("row-active", row.getData().id === RecordPanel.activeId),
    columns: [
      { formatter: "rowSelection", titleFormatter: "rowSelection", hozAlign: "center", headerSort: false, width: 40 },
      { title: gettext("Gerätenummer"), field: "number", cssClass: "mono", minWidth: 130 },
      { title: gettext("Typ"), field: "category", minWidth: 110 },
      { title: gettext("Seriennummer"), field: "serial", cssClass: "mono", minWidth: 140 },
      { title: gettext("Status"), field: "status", formatter: (c) => `<span class="badge ${cls[c.getValue()] || ""}">${App.esc(c.getData().status_label || c.getValue())}</span>`, width: 120 },
      { title: gettext("Bei"), field: "holder", minWidth: 170, formatter: (c) => App.esc(c.getValue()) + (c.getData().holder_archived ? ` <span class="badge badge-warn">${App.esc(gettext("Ehemalig"))}</span>` : "") },
      { title: gettext("Ausgabe"), field: "issued", formatter: (c) => de(c.getValue()), width: 110 },
    ],
  });
  RecordPanel.tabulator(table, { base: "/geraete/", rowUrl: (id) => `/api/geraete/?id=${id}` });
  table.on("rowContext", (e, row) => {
    const { id } = row.getData();
    ContextMenu.open(e, [
      { label: gettext("Öffnen"), icon: "eye", run: () => RecordPanel.open(id) },
      { label: gettext("Ganze Seite öffnen"), icon: "file", href: `/geraete/${id}/` },
      { label: gettext("In neuem Fenster öffnen"), icon: "external", newWindow: `/geraete/${id}/` },
    ]);
  });
  const search = document.getElementById("asset-search");
  const status = document.getElementById("asset-status");
  const preset = el.dataset.preset;
  const apply = () => {
    const q = search.value.trim().toLowerCase();
    table.setFilter((r) => (!status.value || r.status === status.value) && (preset !== "ehemalige" || r.holder_archived) && (!q || Object.values(r).some((v) => String(v).toLowerCase().includes(q))));
  };
  search.addEventListener("input", App.debounce(apply, 120));
  status.addEventListener("change", apply);
  table.on("dataProcessed", () => { document.getElementById("asset-count").textContent = table.getDataCount("active"); if (preset) apply(); });
  table.on("dataFiltered", (f, rows) => { document.getElementById("asset-count").textContent = rows.length; });
  table.on("rowSelectionChanged", (data) => {
    const box = document.getElementById("asset-print");
    box.classList.toggle("hidden", !data.length); box.classList.toggle("flex", !!data.length);
    document.getElementById("asset-selected").textContent = interpolate(gettext("%(n)s ausgewählt"), { n: data.length }, true);
    document.getElementById("asset-ids").value = data.map((r) => r.id).join(",");
  });
})();
