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
    form.querySelectorAll("[data-table]").forEach((row) => {
      const chosen = row.querySelector(`input[name="table_${row.dataset.table}"]:checked`);
      const before = (initial.tables || {})[row.dataset.table] || "none";
      if (chosen && chosen.value !== before) lines.push(`${row.dataset.title}: ${levelName[before]} → ${levelName[chosen.value]}`);
    });
    return lines;
  }

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
