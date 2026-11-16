// Verwaltung → Tabellen: drag the fields of a table into a new order (tables/views.py table_field_order).
(function () {
  const body = document.querySelector("[data-field-order]");
  if (!body || !window.Sortable) return;
  Sortable.create(body, {
    handle: "[data-handle]",
    animation: 120,
    onEnd: async () => {
      const keys = [...body.querySelectorAll("tr[data-key]")].map((row) => row.dataset.key);
      const res = await App.postJSON(body.dataset.fieldOrder, { keys });
      if (!res.ok) App.toast(gettext("Die Reihenfolge wurde nicht gespeichert."), { error: true });
    },
  });
})();
