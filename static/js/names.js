// Names a school gives its fields, choices, masks, tabs and sections keep the language they were typed in.
// Renaming one while another language is shown, or one that already has names for single languages,
// asks whether the new name is for this language only or for all of them (masks/i18n.py stores it).
//
// Forms: <form data-names data-has-translations="1"> with inputs marked data-name-input. On submit, if
// one of them changed, the answer goes along as a hidden "name_scope" field.
window.Names = (function () {
  const meta = document.querySelector('meta[name="app-language"]');
  const language = meta?.content || "de";
  const languageName = meta?.dataset.name || language;
  const defaultLanguage = "de";
  const single = meta?.dataset.single === "1";  // this database allows one language only
  let remembered = meta?.dataset.nameScope || "";

  // "this", "all", or null if the dialog was cancelled.
  function decide(hasOwnNames) {
    // One language only: nobody sees the others, so they keep their names (masks/i18n.py quiet_scope).
    if (single) return Promise.resolve(language === defaultLanguage && !hasOwnNames ? "all" : "this");
    if (remembered) return Promise.resolve(remembered);
    if (language === defaultLanguage && !hasOwnNames) return Promise.resolve("all");
    const dialog = document.getElementById("name-scope-dialog");
    if (!dialog) return Promise.resolve("all");
    const form = dialog.querySelector("form");
    form.reset();
    dialog.querySelector("[data-name-scope-this]").textContent = interpolate(gettext("Nur für %(language)s"), { language: languageName }, true);
    dialog.querySelector("[data-name-scope-text]").textContent = hasOwnNames
      ? gettext("Dieser Name wurde schon für einzelne Sprachen vergeben.")
      : interpolate(gettext("Die Oberfläche ist gerade auf %(language)s eingestellt."), { language: languageName }, true);
    return new Promise((resolve) => {
      dialog.addEventListener("close", function done() {
        dialog.removeEventListener("close", done);
        if (dialog.returnValue !== "ok") return resolve(null);
        const scope = form.elements.scope.value;
        if (form.elements.remember.checked) remember(scope);
        resolve(scope);
      });
      dialog.returnValue = "";
      dialog.showModal();
    });
  }

  function remember(scope) {
    remembered = scope;
    if (meta) meta.dataset.nameScope = scope;
    App.postJSON("/sprache/namen/", { scope });
  }

  // The name to show in the current language: own name, else the one typed first.
  function shown(base, names) {
    return (names && names[language]) || base;
  }

  // Applies a rename to a {base, names} pair the way masks/i18n.py does on the server.
  function apply(base, names, value, scope) {
    names = { ...(names || {}) };
    if (scope === "this") {
      names[language] = value;
      return { base, names };
    }
    return { base: value, names: {} };
  }

  // Asks for a new name, then whether it is for this language or all, then posts it as a form.
  async function renameAndPost(url, current, hasOwnNames, data, question) {
    const value = prompt(question || gettext("Neuer Name"), current);
    if (value === null || !value.trim() || value.trim() === current) return;
    const scope = await decide(hasOwnNames);
    if (!scope) return;
    ContextMenu.submit(url, { ...(data || {}), name: value.trim(), name_scope: scope });
  }

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-names]");
    if (!form || form.dataset.namesDecided) return;
    const changed = [...form.querySelectorAll("[data-name-input]")].some((input) => input.value !== input.defaultValue);
    if (!changed) return;
    event.preventDefault();
    const scope = await decide(form.dataset.hasTranslations === "1");
    if (!scope) return;
    let field = form.querySelector('input[name="name_scope"]');
    if (!field) {
      field = Object.assign(document.createElement("input"), { type: "hidden", name: "name_scope" });
      form.appendChild(field);
    }
    field.value = scope;
    form.dataset.namesDecided = "1";
    const submitter = event.submitter;
    if (submitter?.name) form.appendChild(Object.assign(document.createElement("input"), { type: "hidden", name: submitter.name, value: submitter.value }));
    form.submit();
  }, true);

  return { language, languageName, decide, shown, apply, remember, renameAndPost, get remembered() { return remembered; } };
})();
