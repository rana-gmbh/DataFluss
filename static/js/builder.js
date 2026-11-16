(function () {
  const cfg = JSON.parse(document.getElementById("builder-config").textContent);
  const WIDTHS = [[3, "¼"], [4, "⅓"], [6, "½"], [8, "⅔"], [12, "1"]];
  let tree = JSON.parse(JSON.stringify(cfg.tree));
  if (!tree.tabs || !tree.tabs.length) tree.tabs = [{ id: uid("t"), title: gettext("Stammdaten"), sections: [{ id: uid("s"), title: "", items: [] }] }];
  let activeTab = tree.tabs[0].id;
  let selected = null; // {section, index}
  let dirty = false;
  const history = [];
  const sortables = [];

  function uid(p) { return p + Math.random().toString(36).slice(2, 8); }
  // Tab and section titles and field labels can have their own name per language (masks/i18n.py).
  const titleOf = (node) => Names.shown(node.title || "", node.titles);
  const labelOf = (item) => Names.shown(item.label || "", item.labels);
  // Renames node[prop] (with node[prop + "s"] for single languages) after asking for which languages.
  // Resolves to false if the dialog was cancelled.
  async function rename(node, prop, value) {
    const names = node[prop + "s"];
    const current = Names.shown(node[prop] || "", names);
    value = (value || "").trim();
    if (value === current) return true;
    const scope = value ? await Names.decide(!!names && Object.keys(names).length > 0) : "all";
    if (!scope) return false;
    checkpoint();
    const next = Names.apply(node[prop] || "", names, value, scope);
    if (next.base) node[prop] = next.base; else delete node[prop];
    if (Object.keys(next.names).length) node[prop + "s"] = next.names; else delete node[prop + "s"];
    return true;
  }
  const elements = (n) => interpolate(ngettext("%(n)s Element", "%(n)s Elementen", n), { n }, true);
  const $ = (id) => document.getElementById(id);
  const fieldInfo = (key) => cfg.fields.find((f) => f.key === key);
  const blockInfo = (key) => cfg.blocks.find((b) => b.key === key);
  const tab = () => tree.tabs.find((t) => t.id === activeTab);
  const used = () => {
    const set = new Set();
    tree.tabs.forEach((t) => t.sections.forEach((s) => s.items.forEach((i) => { if (i.field) set.add(i.field); })));
    return set;
  };

  function checkpoint() {
    history.push(JSON.stringify(tree));
    if (history.length > 50) history.shift();
    $("builder-undo").disabled = false;
    dirty = true;
    status();
  }
  function status() {
    const base = cfg.published ? interpolate(gettext("Veröffentlicht: Version %(n)s"), { n: cfg.published }, true) : gettext("Noch nicht veröffentlicht");
    $("builder-status").textContent = base + (dirty ? ` · ${gettext("Ungespeicherte Änderungen")}` : cfg.isDraft ? ` · ${gettext("Entwurf gespeichert")}` : "");
  }
  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

  function paletteItem(data, label, sub, disabled) {
    const el = document.createElement("div");
    el.className = "builder-item flex items-center gap-2 px-2 py-1.5 text-[13px] cursor-grab" + (disabled ? " opacity-50" : "");
    el.dataset.item = JSON.stringify(data);
    el.innerHTML = `<span class="truncate flex-1">${App.esc(label)}</span><span class="muted text-[11px] shrink-0">${App.esc(sub)}</span>`;
    return el;
  }

  function renderPalette() {
    const q = $("palette-search").value.trim().toLowerCase();
    const fields = $("palette-fields");
    fields.innerHTML = "";
    const inUse = used();
    cfg.fields.filter((f) => !q || f.label.toLowerCase().includes(q)).forEach((f) => {
      fields.appendChild(paletteItem({ type: "field", field: f.key, w: f.type === "textarea" ? 12 : 6 }, f.label, inUse.has(f.key) ? gettext("platziert") : f.typeLabel, inUse.has(f.key)));
    });
    const blocks = $("palette-blocks");
    blocks.innerHTML = "";
    cfg.blocks.forEach((b) => blocks.appendChild(paletteItem({ type: "block", block: b.key, w: 12 }, b.label, gettext("Baustein"))));
    blocks.appendChild(paletteItem({ type: "note", text: gettext("Hinweistext"), w: 12 }, gettext("Hinweistext"), gettext("Text")));
    [fields, blocks].forEach((list) => sortables.push(new Sortable(list, { group: { name: "mask", pull: "clone", put: false }, sort: false, animation: 120, filter: ".opacity-50" })));
  }

  function renderTabs() {
    const box = $("builder-tabs");
    box.innerHTML = "";
    tree.tabs.forEach((t) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.dataset.id = t.id;
      btn.className = "tab flex items-center gap-1" + (t.id === activeTab ? " active" : "");
      btn.innerHTML = `<span>${App.esc(titleOf(t) || gettext("Reiter"))}</span>`;
      btn.addEventListener("click", () => { if (activeTab === t.id) return renameTab(t); activeTab = t.id; selected = null; render(); });
      box.appendChild(btn);
    });
    sortables.push(new Sortable(box, { animation: 120, onEnd: (e) => {
      if (e.oldIndex === e.newIndex) return;
      checkpoint();
      const [moved] = tree.tabs.splice(e.oldIndex, 1);
      tree.tabs.splice(e.newIndex, 0, moved);
      render();
    } }));
  }

  async function renameTab(t, allowDelete = true) {
    const name = prompt(allowDelete ? gettext("Name des Reiters (leer lassen zum Löschen)") : gettext("Name des Reiters"), titleOf(t));
    if (name === null) return;
    if (name.trim()) { if (await rename(t, "title", name)) render(); return; }
    if (!allowDelete || tree.tabs.length < 2) return;
    checkpoint();
    tree.tabs = tree.tabs.filter((x) => x !== t);
    activeTab = tree.tabs[0].id;
    render();
  }

  function itemTitle(item) {
    if (item.type === "field") return labelOf(item) || fieldInfo(item.field)?.label || item.field;
    if (item.type === "block") return blockInfo(item.block)?.label || item.block;
    return (item.text || gettext("Hinweistext")).slice(0, 60);
  }

  function renderSections() {
    const root = $("builder-sections");
    root.innerHTML = "";
    tab().sections.forEach((section, si) => {
      const box = document.createElement("section");
      box.className = "surface p-3";
      box.dataset.id = section.id;
      box.innerHTML = `<div class="flex items-center gap-2 mb-2"><span class="muted cursor-grab section-grip"><svg class="icon"><use href="/static/img/icons.svg#grip"></use></svg></span>
        <input class="input h-8 text-[13px] font-semibold flex-1" placeholder="${App.esc(gettext("Abschnitt ohne Titel"))}" aria-label="${App.esc(gettext("Titel des Abschnitts"))}"><button class="btn btn-ghost btn-sm btn-icon" aria-label="${App.esc(gettext("Abschnitt entfernen"))}"><svg class="icon"><use href="/static/img/icons.svg#trash"></use></svg></button></div>
        <div class="grid grid-cols-12 gap-2 dropzone rounded-lg p-1" data-section="${si}"></div>`;
      const title = box.querySelector("input");
      title.value = titleOf(section);
      title.addEventListener("change", async () => { if (!(await rename(section, "title", title.value))) title.value = titleOf(section); });
      box.querySelector("button").addEventListener("click", () => {
        if (section.items.length && !confirm(gettext("Abschnitt mit allen Feldern entfernen?"))) return;
        checkpoint();
        tab().sections.splice(si, 1);
        if (!tab().sections.length) tab().sections.push({ id: uid("s"), title: "", items: [] });
        selected = null;
        render();
      });
      const zone = box.querySelector(".dropzone");
      section.items.forEach((item, ii) => {
        const el = document.createElement("div");
        const isSel = selected && selected.section === section && selected.item === item;
        el.className = "builder-item p-2 cursor-grab min-w-0" + (isSel ? " ring-2 ring-[var(--accent)]" : "");
        el.dataset.si = si;
        el.dataset.ii = ii;
        el.style.gridColumn = `span ${window.innerWidth < 768 ? 12 : item.w}/span ${window.innerWidth < 768 ? 12 : item.w}`;
        const rule = item.show_if ? ` <span class="badge badge-accent">${App.esc(gettext("bedingt"))}</span>` : "";
        const kind = item.type === "block" ? gettext("Baustein") : item.type === "note" ? gettext("Text") : fieldInfo(item.field)?.typeLabel || gettext("Unbekannt");
        el.innerHTML = `<div class="flex items-start gap-1"><div class="min-w-0 flex-1"><div class="truncate text-[13px] font-medium">${App.esc(itemTitle(item))}${rule}</div><div class="muted text-[11px]">${App.esc(kind)}</div></div>
          <button class="btn btn-ghost btn-sm btn-icon -m-1" data-remove aria-label="${App.esc(gettext("Entfernen"))}"><svg class="icon"><use href="/static/img/icons.svg#x"></use></svg></button></div>
          <div class="flex gap-0.5 mt-1">${WIDTHS.map(([w, l]) => `<button type="button" class="px-1.5 rounded text-[11px] ${item.w === w ? "bg-[var(--accent)] text-white" : "bg-[var(--surface-2)] soft"}" data-w="${w}" aria-label="${App.esc(interpolate(gettext("Breite %(w)s"), { w: l }, true))}">${l}</button>`).join("")}</div>`;
        el.addEventListener("click", (e) => {
          if (e.target.closest("button")) return;
          selected = { section, item };
          render();
        });
        el.querySelector("button[data-remove]").addEventListener("click", () => {
          checkpoint();
          section.items.splice(ii, 1);
          selected = null;
          render();
        });
        el.querySelectorAll("[data-w]").forEach((b) => b.addEventListener("click", () => {
          checkpoint();
          item.w = Number(b.dataset.w);
          render();
        }));
        zone.appendChild(el);
      });
      if (!section.items.length) {
        const hint = document.createElement("p");
        hint.className = "col-span-12 muted text-[13px] text-center py-4 pointer-events-none empty-hint";
        hint.textContent = gettext("Felder hierher ziehen");
        zone.appendChild(hint);
      }
      sortables.push(new Sortable(zone, {
        group: "mask", animation: 120, draggable: ".builder-item", ghostClass: "sortable-ghost",
        onAdd: (e) => {
          const data = e.item.dataset.item;
          checkpoint();
          if (data) {
            section.items.splice(e.newDraggableIndex, 0, JSON.parse(data));
            e.item.remove();
          } else {
            const from = tab().sections[Number(e.from.dataset.section)];
            const [moved] = from.items.splice(e.oldDraggableIndex, 1);
            section.items.splice(e.newDraggableIndex, 0, moved);
          }
          render();
        },
        onUpdate: (e) => {
          checkpoint();
          const [moved] = section.items.splice(e.oldDraggableIndex, 1);
          section.items.splice(e.newDraggableIndex, 0, moved);
          render();
        },
      }));
      root.appendChild(box);
    });
    sortables.push(new Sortable(root, { handle: ".section-grip", animation: 120, onEnd: (e) => {
      if (e.oldIndex === e.newIndex) return;
      checkpoint();
      const [moved] = tab().sections.splice(e.oldIndex, 1);
      tab().sections.splice(e.newIndex, 0, moved);
      render();
    } }));
  }

  function renderProps() {
    const box = $("builder-props");
    if (!selected) { box.innerHTML = `<p class="muted text-[13px]">${App.esc(gettext("Element auswählen, um es anzupassen."))}</p>`; return; }
    const item = selected.item;
    const fieldsOptions = cfg.fields.filter((f) => ["choice", "bool", "class", "text"].includes(f.type)).map((f) => `<option value="${f.key}">${App.esc(f.label)}</option>`).join("");
    let html = "";
    if (item.type === "field") {
      html += `<div class="mb-2"><label class="label">${App.esc(gettext("Bezeichnung"))}</label><input class="input" data-label placeholder="${App.esc(fieldInfo(item.field)?.label || "")}"></div>
               <div class="mb-2"><label class="label">${App.esc(gettext("Hilfetext"))}</label><input class="input" data-prop="help"></div>`;
    }
    if (item.type === "note") html += `<div class="mb-2"><label class="label">${App.esc(gettext("Text"))}</label><textarea class="input" data-prop="text"></textarea></div>`;
    html += `<div class="mt-3 pt-3 border-t" style="border-color: var(--line)"><h3 class="label">${App.esc(gettext("Nur anzeigen, wenn"))}</h3>
      <select class="input mb-1.5" data-rule="field" aria-label="${App.esc(gettext("Feld"))}"><option value="">${App.esc(gettext("immer anzeigen"))}</option>${fieldsOptions}</select>
      <select class="input mb-1.5" data-rule="op" aria-label="${App.esc(gettext("Vergleich"))}"><option value="eq">${App.esc(gettext("gleich"))}</option><option value="ne">${App.esc(gettext("ungleich"))}</option></select>
      <input class="input" data-rule="value" placeholder="${App.esc(gettext("Wert, z. B. Kiga"))}" aria-label="${App.esc(gettext("Wert"))}"></div>`;
    box.innerHTML = html;
    box.querySelectorAll("[data-prop]").forEach((input) => {
      input.value = item[input.dataset.prop] || "";
      input.addEventListener("change", () => { checkpoint(); if (input.value) item[input.dataset.prop] = input.value; else delete item[input.dataset.prop]; renderSections(); });
    });
    const labelInput = box.querySelector("[data-label]");
    if (labelInput) {
      labelInput.value = labelOf(item);
      labelInput.addEventListener("change", async () => { if (await rename(item, "label", labelInput.value)) renderSections(); else labelInput.value = labelOf(item); });
    }
    const rule = item.show_if || { field: "", op: "eq", value: "" };
    box.querySelectorAll("[data-rule]").forEach((input) => {
      input.value = rule[input.dataset.rule] || (input.dataset.rule === "op" ? "eq" : "");
      input.addEventListener("change", () => {
        checkpoint();
        const next = {};
        box.querySelectorAll("[data-rule]").forEach((i) => { next[i.dataset.rule] = i.value; });
        if (next.field) item.show_if = next; else delete item.show_if;
        renderSections();
      });
    });
  }

  function render() {
    sortables.splice(0).forEach((s) => s.destroy());
    renderTabs();
    renderSections();
    renderPalette();
    renderProps();
    status();
  }

  async function save(publish) {
    const res = await App.postJSON(`/einstellungen/masken/${cfg.id}/speichern/`, { tree, publish });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return App.toast(body.error || gettext("Nicht gespeichert"));
    dirty = false;
    if (publish) { cfg.published = body.published; cfg.isDraft = false; App.toast(gettext("Veröffentlicht. Die Maske ist jetzt aktiv.")); }
    else { cfg.isDraft = true; App.toast(gettext("Entwurf gespeichert")); }
    status();
  }

  $("builder-save").addEventListener("click", () => save(false));
  $("builder-publish").addEventListener("click", () => { if (confirm(gettext("Maske veröffentlichen? Alle Benutzer sehen sie sofort."))) save(true); });
  $("builder-undo").addEventListener("click", () => {
    if (!history.length) return;
    tree = JSON.parse(history.pop());
    $("builder-undo").disabled = !history.length;
    if (!tree.tabs.some((t) => t.id === activeTab)) activeTab = tree.tabs[0].id;
    selected = null;
    render();
  });
  $("builder-add-tab").addEventListener("click", () => {
    const name = prompt(gettext("Name des Reiters"));
    if (!name || !name.trim()) return;
    checkpoint();
    const t = { id: uid("t"), title: name.trim(), sections: [{ id: uid("s"), title: "", items: [] }] };
    tree.tabs.push(t);
    activeTab = t.id;
    render();
  });
  $("builder-add-section").addEventListener("click", () => { checkpoint(); tab().sections.push({ id: uid("s"), title: "", items: [] }); render(); });
  $("palette-search").addEventListener("input", () => { sortables.splice(0).forEach((s) => s.destroy()); renderTabs(); renderSections(); renderPalette(); });
  $("builder-preview").addEventListener("click", async () => {
    const res = await App.postJSON(`/einstellungen/masken/${cfg.id}/vorschau/`, { tree });
    if (res.ok) App.openModal(await res.text());
    else App.toast((await res.json().catch(() => ({}))).error || gettext("Vorschau nicht möglich"));
  });

  // New field dialog
  const dialog = $("field-dialog");
  const form = $("field-form");
  form.type.innerHTML = cfg.types.map(([v, l]) => `<option value="${v}">${App.esc(l)}</option>`).join("");
  form.type.addEventListener("change", () => $("field-options").classList.toggle("hidden", !["choice", "multichoice"].includes(form.type.value)));
  $("builder-new-field").addEventListener("click", () => { form.reset(); $("field-error").textContent = ""; $("field-options").classList.add("hidden"); dialog.showModal(); });
  $("field-cancel").addEventListener("click", () => dialog.close());
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const res = await App.postJSON(cfg.fieldCreateUrl || "/einstellungen/felder/neu/", {
      entity: cfg.entity, label: form.label.value, type: form.type.value, options: form.options.value,
      required: form.required.checked, in_grid: form.in_grid.checked,
    });
    const body = await res.json();
    if (!res.ok) { $("field-error").textContent = body.error; return; }
    cfg.fields.push(body);
    dialog.close();
    render();
    App.toast(interpolate(gettext("%(field)s angelegt. Jetzt in die Maske ziehen."), { field: body.label }, true));
  });

  // Right-click menus: what the buttons and drag and drop do, plus moving and duplicating
  const sectionName = (s, i) => titleOf(s) || interpolate(gettext("Abschnitt %(n)s"), { n: i + 1 }, true);
  function removeSection(si) {
    checkpoint();
    tab().sections.splice(si, 1);
    if (!tab().sections.length) tab().sections.push({ id: uid("s"), title: "", items: [] });
    selected = null;
    render();
  }
  function moveTo(fromSection, item, toSection) {
    checkpoint();
    fromSection.items.splice(fromSection.items.indexOf(item), 1);
    toSection.items.push(item);
    selected = null;
    render();
  }
  // Every section of every tab, grouped by tab, for "Verschieben nach" and "Einfügen in"
  function sectionTargets(act, skip) {
    const ofTab = (t) => t.sections.map((s, i) => s === skip ? null : { label: sectionName(s, i), run: () => act(s, t) }).filter(Boolean);
    if (tree.tabs.length === 1) return ofTab(tree.tabs[0]);
    return tree.tabs.map((t) => ({ label: titleOf(t) || gettext("Reiter"), items: ofTab(t) })).filter((g) => g.items.length);
  }
  async function duplicateField(key, section, index, width) {
    const res = await App.postJSON(`/einstellungen/felder/${fieldInfo(key).id}/duplizieren/`, {});
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return App.toast(body.error || gettext("Das Feld ließ sich nicht duplizieren"), { error: true });
    cfg.fields.push(body);
    if (section) {
      checkpoint();
      section.items.splice(index + 1, 0, { type: "field", field: body.key, w: width });
    }
    render();
    App.toast(interpolate(section ? gettext("%(field)s angelegt und daneben platziert. Umbenennen über Rechtsklick → Feld bearbeiten.") : gettext("%(field)s angelegt. Umbenennen über Rechtsklick → Feld bearbeiten."), { field: body.label }, true));
  }
  const editField = (key) => ({ label: gettext("Feld bearbeiten"), icon: "pen", newWindow: (cfg.fieldEditUrl || "/einstellungen/felder/{id}/").replace("{id}", fieldInfo(key).id) });

  ContextMenu.register("#builder-sections .builder-item", (el) => {
    const section = tab().sections[Number(el.dataset.si)];
    const index = Number(el.dataset.ii);
    const item = section.items[index];
    const isField = item.type === "field" && fieldInfo(item.field);
    const move = (delta) => () => { checkpoint(); section.items.splice(index, 1); section.items.splice(index + delta, 0, item); render(); };
    return [
      { label: gettext("Eigenschaften"), icon: "settings", run: () => { selected = { section, item }; render(); $("builder-props").querySelector("input, textarea, select")?.focus(); } },
      { label: gettext("Breite"), icon: "columns", items: WIDTHS.map(([w, l]) => ({ label: l === "1" ? gettext("Ganze Zeile") : l, hint: item.w === w ? "✓" : "", run: () => { checkpoint(); item.w = w; render(); } })) },
      "-",
      { label: gettext("Nach vorne"), disabled: index === 0, run: move(-1) },
      { label: gettext("Nach hinten"), disabled: index === section.items.length - 1, run: move(1) },
      { label: gettext("Verschieben nach"), icon: "move", items: sectionTargets((to) => moveTo(section, item, to), section) },
      "-",
      isField && cfg.canDuplicate !== false && { label: gettext("Feld duplizieren"), icon: "copyplus", run: () => duplicateField(item.field, section, index, item.w) },
      !isField && item.type !== "field" && { label: gettext("Duplizieren"), icon: "copyplus", run: () => { checkpoint(); section.items.splice(index + 1, 0, JSON.parse(JSON.stringify(item))); render(); } },
      isField && editField(item.field),
      "-",
      { label: gettext("Aus der Maske entfernen"), icon: "x", danger: true, run: () => { checkpoint(); section.items.splice(index, 1); selected = null; render(); } },
    ];
  });

  ContextMenu.register("#builder-sections > section", (el) => {
    const sections = tab().sections;
    const si = sections.findIndex((s) => s.id === el.dataset.id);
    const section = sections[si];
    const swap = (delta) => () => { checkpoint(); sections.splice(si, 1); sections.splice(si + delta, 0, section); render(); };
    const insert = (at) => () => { checkpoint(); sections.splice(at, 0, { id: uid("s"), title: "", items: [] }); render(); };
    return [
      { label: gettext("Umbenennen"), icon: "pen", run: () => el.querySelector("input")?.focus() },
      { label: gettext("Abschnitt darüber einfügen"), icon: "plus", run: insert(si) },
      { label: gettext("Abschnitt darunter einfügen"), icon: "plus", run: insert(si + 1) },
      "-",
      { label: gettext("Nach oben"), disabled: si === 0, run: swap(-1) },
      { label: gettext("Nach unten"), disabled: si === sections.length - 1, run: swap(1) },
      tree.tabs.length > 1 && { label: gettext("In Reiter verschieben"), icon: "move", items: tree.tabs.filter((t) => t !== tab()).map((t) => ({ label: titleOf(t) || gettext("Reiter"), run: () => {
        checkpoint();
        sections.splice(si, 1);
        if (!sections.length) sections.push({ id: uid("s"), title: "", items: [] });
        t.sections.push(section);
        render();
      } })) },
      "-",
      { label: gettext("Abschnitt entfernen …"), icon: "trash", danger: true, confirm: section.items.length ? interpolate(gettext("%(section)s mit %(elements)s entfernen?"), { section: sectionName(section, si), elements: elements(section.items.length) }, true) : null, run: () => removeSection(si) },
    ];
  });

  ContextMenu.register("#builder-tabs .tab", (el) => {
    const i = tree.tabs.findIndex((t) => t.id === el.dataset.id);
    const t = tree.tabs[i];
    const swap = (delta) => () => { checkpoint(); tree.tabs.splice(i, 1); tree.tabs.splice(i + delta, 0, t); render(); };
    const count = t.sections.reduce((n, s) => n + s.items.length, 0);
    return [
      { label: gettext("Umbenennen"), icon: "pen", run: () => renameTab(t, false) },
      { label: gettext("Nach links"), disabled: i === 0, run: swap(-1) },
      { label: gettext("Nach rechts"), disabled: i === tree.tabs.length - 1, run: swap(1) },
      "-",
      { label: gettext("Reiter löschen …"), icon: "trash", danger: true, disabled: tree.tabs.length === 1, confirm: count ? interpolate(gettext("Reiter %(tab)s mit %(elements)s löschen?"), { tab: titleOf(t), elements: elements(count) }, true) : interpolate(gettext("Reiter %(tab)s löschen?"), { tab: titleOf(t) }, true), run: () => {
        checkpoint();
        tree.tabs.splice(i, 1);
        if (activeTab === t.id) activeTab = tree.tabs[0].id;
        selected = null;
        render();
      } },
    ];
  });

  ContextMenu.register("#palette-fields .builder-item, #palette-blocks .builder-item", (el) => {
    const data = JSON.parse(el.dataset.item);
    const placed = data.type === "field" && used().has(data.field);
    const add = (section, t) => { checkpoint(); section.items.push({ ...data }); activeTab = t.id; render(); };
    return [
      { label: gettext("Einfügen in"), icon: "plus", disabled: placed, hint: placed ? gettext("schon platziert") : "", items: placed ? [] : sectionTargets(add) },
      data.type === "field" && "-",
      data.type === "field" && cfg.canDuplicate !== false && { label: gettext("Feld duplizieren"), icon: "copyplus", run: () => duplicateField(data.field) },
      data.type === "field" && editField(data.field),
    ];
  });

  const renameMask = $("builder-rename");
  if (renameMask) renameMask.addEventListener("click", async () => {
    if (dirty) await save(false); // the page reloads after renaming
    if (dirty) return;
    Names.renameAndPost(`/einstellungen/masken/${cfg.id}/aktion/`, cfg.name, cfg.hasNames, { action: "rename" }, gettext("Name der Maske"));
  });

  render();
})();
