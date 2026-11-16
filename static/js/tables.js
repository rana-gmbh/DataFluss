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

// The new-field form guesses the type from the name while it is typed (tables/infer.py label_rules),
// until the type is chosen by hand.
document.addEventListener("alpine:init", () => {
  const fold = (t) => t.toLowerCase().replace(/ä/g, "a").replace(/ö/g, "o").replace(/ü/g, "u").replace(/ß/g, "ss");
  Alpine.data("newField", () => ({
    type: "text",
    touched: false,
    linkTarget: "",
    init() { this.linkTarget = this.$refs.target?.value || ""; },
    rules: JSON.parse(document.getElementById("type-rules")?.textContent || "[]").map((r) => ({ ...r, re: new RegExp(r.pattern, "i") })),
    guess(label) {
      if (this.touched) return;
      const text = label.trim();
      const hit = text ? this.rules.find((r) => r.re.test(r.fold ? fold(text) : text)) : null;
      this.type = hit ? hit.type : "text";
      if (hit && hit.target) this.linkTarget = hit.target;
    },
  }));
});
