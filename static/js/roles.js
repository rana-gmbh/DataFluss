// Role editor (templates/core/role_edit.html): before saving, a short list of what changes and for how
// many accounts, so nobody loses or gains a right by a slip of the mouse.
(function () {
  const form = document.getElementById("role-form");
  if (!form) return;
  const initial = JSON.parse(form.dataset.initial || "{}");
  const levelName = { none: gettext("Kein Zugriff"), read: gettext("Lesen"), edit: gettext("Bearbeiten") };

  function changes() {
    const lines = [];
    form.querySelectorAll("[data-area]").forEach((row) => {
      const chosen = row.querySelector(`input[name="area_${row.dataset.area}"]:checked`);
      if (!chosen) return;
      const before = (initial.levels || {})[row.dataset.area] || "none";
      if (chosen.value !== before) lines.push(`${row.dataset.title}: ${levelName[before]} → ${levelName[chosen.value]}`);
    });
    form.querySelectorAll('input[name="extra"]').forEach((box) => {
      const before = (initial.extras || []).includes(box.value);
      if (box.checked !== before) lines.push(`${box.dataset.title}: ${box.checked ? gettext("ja") : gettext("nein")}`);
    });
    let fields = 0;
    form.querySelectorAll('input[name^="field__"]:checked').forEach((radio) => {
      const [entity, key] = [radio.name.slice(7, radio.name.lastIndexOf("__")), radio.name.slice(radio.name.lastIndexOf("__") + 2)];
      const before = ((initial.fields || {})[entity] || {})[key] || "edit";
      if (radio.value !== before) fields += 1;
    });
    if (fields) lines.push(interpolate(ngettext("%s Feld mit anderem Recht", "%s Felder mit anderem Recht", fields), [fields]));
    form.querySelectorAll("[data-table]").forEach((row) => {
      const chosen = row.querySelector(`input[name="table_${row.dataset.table}"]:checked`);
      const before = (initial.tables || {})[row.dataset.table] || "none";
      if (chosen && chosen.value !== before) lines.push(`${row.dataset.title}: ${levelName[before]} → ${levelName[chosen.value]}`);
    });
    return lines;
  }

  // "Alle bearbeiten" / "Alle nur lesen": every field of the table shown now (that the search leaves visible).
  form.querySelectorAll("[data-all-fields]").forEach((button) => button.addEventListener("click", () => {
    const shown = [...form.querySelectorAll("[data-field-entity]")].find((box) => box.offsetParent !== null);
    shown?.querySelectorAll("[data-field-row]").forEach((row) => {
      if (row.offsetParent === null) return;
      const radio = row.querySelector(`input[value="${button.dataset.allFields}"]`);
      if (radio && !radio.disabled) radio.checked = true;
    });
    form.dispatchEvent(new Event("change", { bubbles: true }));
  }));

  form.addEventListener("submit", (e) => {
    if (form.dataset.confirmed) return;
    const lines = changes();
    const accounts = Number(form.dataset.accounts || 0);
    if (!lines.length || !accounts) return;
    e.preventDefault();
    const head = interpolate(ngettext("Das gilt sofort für %s Konto:", "Das gilt sofort für %s Konten:", accounts), [accounts]);
    document.getElementById("confirm-text").style.whiteSpace = "pre-line"; // one change per line
    App.ask(`${head}\n\n${lines.slice(0, 12).join("\n")}${lines.length > 12 ? "\n…" : ""}`).then((yes) => {
      if (!yes) return;
      form.dataset.confirmed = "1";
      form.requestSubmit();
    });
  });
})();
