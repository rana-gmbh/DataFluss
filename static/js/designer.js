(function () {
  const cfg = JSON.parse(document.getElementById("designer-config").textContent);
  const $ = (id) => document.getElementById(id);
  let tree = JSON.parse(JSON.stringify(cfg.tree || {}));
  tree.page = { preset: "a4", margin: [12, 18, 16, 18], ...(tree.page || {}) };
  tree.rows = tree.rows || [];
  let selected = null;
  const undoStack = [];
  const sortables = [];
  let dirty = false;

  // Palette labels are UI text; the default contents (text, column titles) land in the printout and stay German.
  const ELEMENTS = [
    ["name", gettext("Name"), { type: "name", size: 17 }], ["text", gettext("Text"), { type: "text", text: "Text" }], ["heading", gettext("Überschrift"), { type: "heading", text: "Überschrift" }],
    ["photo", gettext("Foto"), { type: "photo", w_mm: 24, h_mm: 33.6 }], ["logo", gettext("Logo"), { type: "logo", w_mm: 20 }], ["qr", gettext("QR-Code"), { type: "qr", value: "https://", size_mm: 18 }],
    ["logins", gettext("Zugangsdaten"), { type: "logins", hide_empty: true }], ["devices", gettext("Geräteliste"), { type: "devices" }], ["cases", gettext("Schadensliste"), { type: "cases" }],
    ["list", gettext("Klassenliste"), { type: "list", columns: [{ key: "sort_name", title: "Name" }] }], ["signature", gettext("Unterschriften"), { type: "signature", text: "Datum, Unterschrift" }],
    ["spacer", gettext("Abstand"), { type: "spacer", h_mm: 5 }], ["divider", gettext("Linie"), { type: "divider" }],
  ];
  // An own table (tables/prints.py) has no logins, devices or damage reports, and its list shows the records of a link.
  if (cfg.lists) {
    const first = cfg.lists[0];
    for (let i = ELEMENTS.length - 1; i >= 0; i--) {
      const key = ELEMENTS[i][0];
      if (["logins", "devices", "cases"].includes(key) || (key === "list" && !first)) ELEMENTS.splice(i, 1);
      else if (key === "list") ELEMENTS[i] = ["list", gettext("Liste über Verknüpfung"), { type: "list", link: first.key, columns: first.columns.slice(0, 3) }];
    }
  }
  const LAYOUTS = [[12], [6, 6], [4, 4, 4], [3, 9], [9, 3], [3, 7, 2], [8, 4]];
  const LABELS = Object.fromEntries(ELEMENTS.map(([k, l]) => [k, l]));

  const fieldLabel = (key) => cfg.fields.find((f) => f.key === key)?.label || key;

  function describe(el) {
    switch (el.type) {
      case "field": return el.label || fieldLabel(el.key);
      case "text": case "heading": return el.text || LABELS[el.type];
      case "logins": return el.platforms?.length ? interpolate(gettext("Zugangsdaten: %(platforms)s"), { platforms: el.platforms.join(", ") }, true) : gettext("Zugangsdaten: alle für das Blatt");
      case "qr": return interpolate(gettext("QR-Code: %(value)s"), { value: el.value }, true);
      default: return LABELS[el.type] || el.type;
    }
  }

  function checkpoint() {
    undoStack.push(JSON.stringify(tree));
    if (undoStack.length > 60) undoStack.shift();
    $("d-undo").disabled = false;
    dirty = true;
    $("d-status").textContent = gettext("Ungespeicherte Änderungen");
  }

  function renderCanvas() {
    const canvas = $("d-canvas");
    canvas.innerHTML = "";
    if (!tree.rows.length) canvas.innerHTML = `<p class="muted text-center py-16 text-[13px]">${App.esc(gettext("Oben ein Zeilen-Layout wählen und Elemente hineinziehen."))}</p>`;
    tree.rows.forEach((row, ri) => {
      const rowEl = document.createElement("div");
      rowEl.className = "group flex gap-1 items-stretch rounded-lg";
      rowEl.innerHTML = `<div class="flex flex-col items-center gap-1 pt-1"><span class="row-grip muted cursor-grab"><svg class="icon"><use href="/static/img/icons.svg#grip"></use></svg></span>
        <button class="btn btn-ghost btn-sm btn-icon opacity-60" aria-label="${App.esc(gettext("Zeile entfernen"))}"><svg class="icon"><use href="/static/img/icons.svg#trash"></use></svg></button></div><div class="grid grid-cols-12 gap-1 flex-1 min-w-0"></div>`;
      rowEl.querySelector("button").addEventListener("click", () => { checkpoint(); tree.rows.splice(ri, 1); selected = null; refresh(); });
      const grid = rowEl.querySelector(".grid");
      row.cols.forEach((col, ci) => {
        const colEl = document.createElement("div");
        colEl.className = "dropzone rounded-lg border border-dashed p-1 space-y-1 min-w-0";
        colEl.style.gridColumn = `span ${col.w}/span ${col.w}`;
        colEl.style.borderColor = "var(--line-strong)";
        colEl.dataset.row = ri;
        colEl.dataset.col = ci;
        (col.items = col.items || []).forEach((el, ei) => {
          const item = document.createElement("div");
          const active = selected && selected.el === el;
          item.className = "builder-item px-2 py-1 text-[12.5px] flex items-center gap-1 cursor-grab min-w-0" + (active ? " ring-2 ring-[var(--accent)]" : "");
          item.innerHTML = `<span class="truncate flex-1">${App.esc(describe(el))}</span><button aria-label="${App.esc(gettext("Entfernen"))}" class="muted hover:text-[var(--bad)]"><svg class="icon"><use href="/static/img/icons.svg#x"></use></svg></button>`;
          item.addEventListener("click", (e) => {
            if (e.target.closest("button")) return;
            selected = { el };
            renderCanvas(); renderProps();
          });
          item.querySelector("button").addEventListener("click", () => { checkpoint(); col.items.splice(ei, 1); selected = null; refresh(); });
          colEl.appendChild(item);
        });
        sortables.push(new Sortable(colEl, {
          group: "print", animation: 120, draggable: ".builder-item",
          onAdd: (e) => {
            checkpoint();
            const data = e.item.dataset.el;
            if (data) { col.items.splice(e.newDraggableIndex, 0, JSON.parse(data)); e.item.remove(); }
            else {
              const source = tree.rows[e.from.dataset.row].cols[e.from.dataset.col];
              const [moved] = source.items.splice(e.oldDraggableIndex, 1);
              col.items.splice(e.newDraggableIndex, 0, moved);
            }
            refresh();
          },
          onUpdate: (e) => {
            checkpoint();
            const [moved] = col.items.splice(e.oldDraggableIndex, 1);
            col.items.splice(e.newDraggableIndex, 0, moved);
            refresh();
          },
        }));
        grid.appendChild(colEl);
      });
      canvas.appendChild(rowEl);
    });
    sortables.push(new Sortable(canvas, { handle: ".row-grip", animation: 120, onEnd: (e) => {
      if (e.oldIndex === e.newIndex) return;
      checkpoint();
      const [moved] = tree.rows.splice(e.oldIndex, 1);
      tree.rows.splice(e.newIndex, 0, moved);
      refresh();
    } }));
  }

  function renderPalette() {
    const palette = $("d-palette");
    palette.innerHTML = "";
    ELEMENTS.forEach(([key, label, data]) => {
      const el = document.createElement("div");
      el.className = "builder-item px-2 py-1 text-[13px] cursor-grab";
      el.textContent = label;
      el.dataset.el = JSON.stringify(data);
      palette.appendChild(el);
    });
    const fields = $("d-fields");
    fields.innerHTML = "";
    cfg.fields.forEach((f) => {
      const el = document.createElement("div");
      el.className = "builder-item px-2 py-1 text-[13px] cursor-grab truncate";
      el.textContent = f.label;
      el.dataset.el = JSON.stringify({ type: "field", key: f.key });
      fields.appendChild(el);
    });
    [palette, fields].forEach((list) => sortables.push(new Sortable(list, { group: { name: "print", pull: "clone", put: false }, sort: false })));
  }

  function renderLayouts() {
    const box = $("d-rowlayouts");
    box.innerHTML = "";
    LAYOUTS.forEach((layout) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-sm";
      b.setAttribute("aria-label", interpolate(gettext("Zeile mit %(n)s Spalten"), { n: layout.length }, true));
      b.innerHTML = `<span class="flex gap-px w-12">${layout.map((w) => `<span class="h-3 rounded-sm" style="flex:${w};background:var(--line-strong)"></span>`).join("")}</span>`;
      b.addEventListener("click", () => { checkpoint(); tree.rows.push({ cols: layout.map((w) => ({ w, items: [] })) }); refresh(); });
      box.appendChild(b);
    });
  }

  function input(label, value, onChange, type = "text", attrs = "") {
    const wrap = document.createElement("div");
    wrap.className = "mb-2";
    const id = "p" + Math.random().toString(36).slice(2, 8);
    wrap.innerHTML = `<label class="label" for="${id}">${App.esc(label)}</label>` + (type === "textarea" ? `<textarea id="${id}" class="input" ${attrs}></textarea>` : `<input id="${id}" class="input" type="${type}" ${attrs}>`);
    const control = wrap.querySelector("input,textarea");
    control.value = value ?? "";
    control.addEventListener("change", () => { checkpoint(); onChange(control.value); renderCanvas(); schedulePreview(); });
    return wrap;
  }

  function check(label, value, onChange) {
    const wrap = document.createElement("label");
    wrap.className = "flex items-center gap-2 text-[13px] mb-2";
    wrap.innerHTML = '<input type="checkbox" class="accent-[var(--accent)]">' + App.esc(label);
    const control = wrap.querySelector("input");
    control.checked = value;
    control.addEventListener("change", () => { checkpoint(); onChange(control.checked); schedulePreview(); });
    return wrap;
  }

  function renderProps() {
    const box = $("d-props");
    box.innerHTML = "";
    if (!selected) { box.innerHTML = `<p class="muted text-[13px]">${App.esc(gettext("Element anklicken, um es anzupassen."))}</p>`; return; }
    const el = selected.el;
    const set = (k) => (v) => { if (v === "" || v === null) delete el[k]; else el[k] = v; };
    const num = (k) => (v) => { if (v === "") delete el[k]; else el[k] = Number(v); };
    const title = document.createElement("div");
    title.className = "font-medium text-[13px] mb-2";
    title.textContent = el.type === "field" ? fieldLabel(el.key) : LABELS[el.type];
    box.appendChild(title);
    if (["text", "heading", "signature"].includes(el.type)) {
      box.appendChild(input(el.type === "signature" ? gettext("Unterschriften, eine pro Zeile") : gettext("Text (Felder als {{schluessel}})"), el.text, set("text"), "textarea"));
    }
    if (el.type === "text" || el.type === "heading") {
      const help = document.createElement("p");
      help.className = "muted text-[11.5px] mb-2";
      help.textContent = gettext("Beispiele:") + " " + cfg.fields.slice(0, 4).map((f) => `{{${f.key}}}`).join(" ");
      box.appendChild(help);
    }
    if (el.type === "heading") box.appendChild(check(gettext("Als schlichte Überschrift"), el.style === "plain", (v) => { if (v) el.style = "plain"; else delete el.style; }));
    if (el.type === "field") {
      box.appendChild(input(gettext("Bezeichnung"), el.label, set("label")));
      box.appendChild(check(gettext("Bezeichnung anzeigen"), el.show_label !== false, (v) => { if (v) delete el.show_label; else el.show_label = false; }));
      box.appendChild(check(gettext("Ausblenden, wenn leer"), !!el.hide_empty, (v) => { el.hide_empty = v; }));
      box.appendChild(check(gettext("Festbreitenschrift"), !!el.mono, (v) => { el.mono = v; }));
    }
    if (["text", "heading", "field", "name"].includes(el.type)) box.appendChild(input(gettext("Schriftgröße (pt)"), el.size, num("size"), "number", 'step="0.5" min="5" max="60"'));
    if (["text", "heading", "field"].includes(el.type)) {
      box.appendChild(check(gettext("Fett"), !!el.bold, (v) => { el.bold = v; }));
      box.appendChild(input(gettext("Farbe (#rrggbb)"), el.color, set("color")));
      const wrap = document.createElement("div");
      wrap.className = "mb-2";
      wrap.innerHTML = `<label class="label">${App.esc(gettext("Ausrichtung"))}</label><select class="input"><option value="">${App.esc(gettext("Standard"))}</option><option value="left">${App.esc(gettext("Links"))}</option><option value="center">${App.esc(gettext("Mittig"))}</option><option value="right">${App.esc(gettext("Rechts"))}</option></select>`;
      const sel = wrap.querySelector("select");
      sel.value = el.align || "";
      sel.addEventListener("change", () => { checkpoint(); set("align")(sel.value); schedulePreview(); });
      box.appendChild(wrap);
    }
    if (el.type === "photo") { box.appendChild(input(gettext("Breite (mm)"), el.w_mm, num("w_mm"), "number")); box.appendChild(input(gettext("Höhe (mm)"), el.h_mm, num("h_mm"), "number")); }
    if (el.type === "logo") box.appendChild(input(gettext("Breite (mm)"), el.w_mm, num("w_mm"), "number"));
    if (el.type === "qr") { box.appendChild(input(gettext("Inhalt (z. B. Adresse oder {{number}})"), el.value, set("value"))); box.appendChild(input(gettext("Größe (mm)"), el.size_mm, num("size_mm"), "number")); }
    if (el.type === "spacer") box.appendChild(input(gettext("Höhe (mm)"), el.h_mm, num("h_mm"), "number"));
    if (el.type === "logins") {
      box.appendChild(check(gettext("Leere Plattformen ausblenden"), el.hide_empty !== false, (v) => { el.hide_empty = v; }));
      const list = document.createElement("div");
      list.className = "mb-2";
      list.innerHTML = `<div class="label">${App.esc(gettext("Nur diese Plattformen (keine Auswahl = alle)"))}</div>`;
      cfg.platforms.forEach((p) => {
        const on = (el.platforms || []).includes(p.key);
        list.appendChild(check(p.name, on, (v) => {
          const next = new Set(el.platforms || []);
          v ? next.add(p.key) : next.delete(p.key);
          if (next.size) el.platforms = [...next]; else delete el.platforms;
          renderCanvas();
        }));
      });
      box.appendChild(list);
    }
    if (el.type === "list") {
      const list = document.createElement("div");
      list.className = "mb-2";
      let columns = cfg.listColumns;
      if (cfg.lists && cfg.lists.length) {
        const chosen = cfg.lists.find((l) => l.key === el.link) || cfg.lists[0];
        columns = chosen.columns;
        const label = document.createElement("label");
        label.className = "block mb-2";
        label.innerHTML = `<span class="label">${App.esc(gettext("Verknüpfung"))}</span>`;
        const select = document.createElement("select");
        select.className = "input";
        select.innerHTML = cfg.lists.map((l) => `<option value="${App.esc(l.key)}">${App.esc(l.label)}</option>`).join("");
        select.value = chosen.key;
        select.addEventListener("change", () => {
          const next = cfg.lists.find((l) => l.key === select.value);
          el.link = next.key;
          el.columns = next.columns.slice(0, 3);
          renderCanvas();
          renderProps();
        });
        label.appendChild(select);
        list.appendChild(label);
      }
      list.insertAdjacentHTML("beforeend", `<div class="label">${App.esc(gettext("Spalten"))}</div>`);
      columns.forEach((c) => {
        const on = (el.columns || []).some((x) => x.key === c.key);
        list.appendChild(check(c.title, on, (v) => {
          el.columns = v ? [...(el.columns || []), c] : (el.columns || []).filter((x) => x.key !== c.key);
        }));
      });
      box.appendChild(list);
    }
  }

  function renderPage() {
    const preset = $("d-preset");
    preset.innerHTML = cfg.presets.map((p) => `<option value="${p.key}">${App.esc(p.label)}</option>`).join("");
    preset.value = tree.page.preset;
    preset.addEventListener("change", () => { checkpoint(); tree.page.preset = preset.value; schedulePreview(); });
    const box = $("d-margins");
    box.innerHTML = "";
    [gettext("Oben"), gettext("Rechts"), gettext("Unten"), gettext("Links")].forEach((label, i) => {
      const wrap = document.createElement("div");
      wrap.innerHTML = `<label class="label text-[10.5px]">${App.esc(label)}</label><input class="input h-8 px-1 text-center text-[12.5px]" type="number" min="0" max="60" aria-label="${App.esc(interpolate(gettext("Rand %(side)s"), { side: label }, true))}">`;
      const control = wrap.querySelector("input");
      control.value = (tree.page.margin || [12, 18, 16, 18])[i];
      control.addEventListener("change", () => { checkpoint(); tree.page.margin = tree.page.margin || [12, 18, 16, 18]; tree.page.margin[i] = Number(control.value); schedulePreview(); });
      box.appendChild(wrap);
    });
    const footer = $("d-footer");
    footer.value = tree.page.footer_text ?? "";
    footer.addEventListener("change", () => {
      checkpoint();
      if (footer.value) tree.page.footer_text = footer.value; else delete tree.page.footer_text;
      schedulePreview();
    });
  }

  function refresh() {
    sortables.splice(0).forEach((s) => s.destroy());
    renderCanvas();
    renderPalette();
    renderProps();
    schedulePreview();
  }

  let previewBuffer = null;
  const body = () => ({ tree, codeMode: $("d-code").checked, codeHtml: $("d-html").value, codeCss: $("d-css").value, name: $("d-name").value });
  const schedulePreview = App.debounce(async () => {
    const res = await App.postJSON(`/berichte/${cfg.id}/vorschau/`, body());
    const warnings = $("d-warnings");
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      warnings.innerHTML = `<p class="badge-bad rounded-lg px-2 py-1">${App.esc(data.error || gettext("Vorschau nicht möglich"))}</p>`;
      return;
    }
    const header = res.headers.get("X-PDF-Warnings");
    const list = header ? JSON.parse(header) : [];
    warnings.innerHTML = list.map((w) => `<p class="badge-warn rounded-lg px-2 py-1 mb-1">${App.esc(w)}</p>`).join("");
    previewBuffer = await res.arrayBuffer();
    showPreview();
  }, 450);

  async function showPreview() {
    if (!previewBuffer || !window.PdfView) return;
    const pages = await PdfView.render($("d-preview"), previewBuffer.slice(0));
    if (pages) $("d-pages").textContent = interpolate(ngettext("%(n)s Seite", "%(n)s Seiten", pages), { n: pages }, true);
  }
  window.addEventListener("pdfview-ready", showPreview);

  async function save() {
    const res = await App.postJSON(`/berichte/${cfg.id}/speichern/`, body());
    if (res.ok) {
      dirty = false;
      const data = await res.json();
      $("d-status").textContent = interpolate(gettext("Gespeichert als Version %(version)s"), { version: data.version }, true);
      App.toast(gettext("Gespeichert"));
    } else App.toast(gettext("Nicht gespeichert"));
  }
  $("d-save").addEventListener("click", save);
  $("d-undo").addEventListener("click", () => {
    if (!undoStack.length) return;
    tree = JSON.parse(undoStack.pop());
    $("d-undo").disabled = !undoStack.length;
    selected = null;
    refresh();
  });
  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

  // Code mode
  if (cfg.canCode) { $("d-code-toggle").classList.remove("hidden"); $("d-code-toggle").classList.add("flex"); }
  $("d-name").value = cfg.name;
  $("d-name").addEventListener("change", () => { dirty = true; });
  $("d-html").value = cfg.codeHtml || "";
  $("d-css").value = cfg.codeCss || "";
  const applyCode = () => {
    const on = $("d-code").checked;
    $("d-canvas").classList.toggle("hidden", on);
    $("d-codebox").classList.toggle("hidden", !on);
    schedulePreview();
  };
  $("d-code").checked = !!cfg.codeMode;
  $("d-code").addEventListener("change", () => { dirty = true; applyCode(); });
  ["d-html", "d-css"].forEach((id) => $(id).addEventListener("input", () => { dirty = true; schedulePreview(); }));

  renderPage();
  renderLayouts();
  refresh();
  applyCode();
})();
