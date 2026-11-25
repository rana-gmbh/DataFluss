(function () {
  const cfg = JSON.parse(document.getElementById("grid-config").textContent);
  const $ = (id) => document.getElementById(id);
  const de = (iso) => { if (!iso) return ""; const [y, m, d] = iso.split("-"); return `${d}.${m}.${y}`; };
  const deTime = (iso) => { if (!iso) return ""; const t = new Date(iso); return isNaN(t) ? iso : t.toLocaleString(document.documentElement.lang || "de", { dateStyle: "medium", timeStyle: "short" }); };
  const number = (c, v) => {
    if (v === null || v === undefined || v === "") return "";
    if (typeof v === "string" && isNaN(Number(v))) return v; // "#Fehler", "nicht sichtbar"
    // A computed number without set decimal places shows up to two (an average, a share). A plain whole
    // number (a year, an inventory number) keeps its digits together.
    const places = c.decimals ?? null;
    const text = Number(v).toLocaleString(document.documentElement.lang || "de", {
      minimumFractionDigits: places ?? 0, maximumFractionDigits: places ?? 2, useGrouping: !(places === 0 && !c.unit) });
    return c.unit ? `${text} ${c.unit}` : text;
  };
  const LS = `grid:${cfg.entity}`;
  const FILTER_KEY = `grid-filters:${cfg.entity}`;
  const columnByKey = Object.fromEntries(cfg.columns.map((c) => [c.key, c]));
  const recordBase = cfg.recordBase || "/personen/";
  const actionUrl = cfg.actionUrl || "/personen/aktion/";
  const fieldUrl = (id, key) => (cfg.fieldUrl || "/api/personen/{id}/feld/{key}/").replace("{id}", id).replace("{key}", key);
  const viewsUrl = cfg.viewsUrl || "/api/ansichten/";
  const viewDeleteUrl = (id) => (cfg.viewDeleteUrl || "/api/ansichten/{id}/loeschen/").replace("{id}", id);
  let state = { scope: "active" };
  try { state = { ...state, ...JSON.parse(sessionStorage.getItem(LS) || "{}") }; } catch (e) {}

  // Column filters: key -> list of chosen values
  let filters = {};
  try { filters = JSON.parse(sessionStorage.getItem(FILTER_KEY) || "{}"); } catch (e) {}

  // Filter values are kept as they are stored ("Ja"/"Nein" for yes/no columns), so saved views work in every language.
  const shown = (c, row) => {
    const value = row[c.key];
    if (c.type === "bool") return value ? "Ja" : "Nein";
    if (c.type === "date") return de(value);
    if (c.type === "datetime") return deTime(value);
    return value === null || value === undefined ? "" : String(value);
  };
  const EMPTY = gettext("(leer)");
  const BOOL_LABELS = { Ja: gettext("Ja"), Nein: gettext("Nein") };
  // Choices are stored by value; optionLabels has their names in the language on screen.
  const optionLabel = (c, v) => (c && c.optionLabels && c.optionLabels[v]) || v;
  const ratingText = (v, top = 5) => { const n = Number(v); const whole = Math.floor(n); const half = n - whole >= 0.5; return "★".repeat(whole) + (half ? "½" : "") + "☆".repeat(Math.max(0, top - whole - (half ? 1 : 0))); };
  const label = (c, v) => (v === "" ? EMPTY : c && c.type === "bool" ? BOOL_LABELS[v] || v : c && c.type === "rating" ? ratingText(v, c.stars) : optionLabel(c, v));

  // "Artikel 2" before "Artikel 10", umlauts where the language puts them.
  const collator = new Intl.Collator(document.documentElement.lang || "de", { numeric: true, sensitivity: "base" });
  const natural = (a, b) => collator.compare(String(a ?? ""), String(b ?? ""));

  // Stars in a cell: a click picks, arrows step, Enter keeps, Escape leaves it as it was.
  function ratingEditor(c) {
    const top = c.stars || 5;
    const step = c.half === false ? 1 : 0.5;
    return (cell, onRendered, success, cancel) => {
      let value = Number(cell.getValue()) || 0;
      const box = document.createElement("div");
      box.className = "rating-input px-1";
      box.tabIndex = 0;
      box.setAttribute("role", "slider");
      box.setAttribute("aria-valuemin", "0");
      box.setAttribute("aria-valuemax", String(top));
      const paint = (v) => { box.innerHTML = App.stars(v, top, "stars-sm"); box.setAttribute("aria-valuenow", String(v)); };
      const at = (event) => {
        const rect = box.firstElementChild.getBoundingClientRect();
        const units = (Math.min(Math.max(event.clientX - rect.left, 0), rect.width) / rect.width) * (26 * top - 2);
        const index = Math.min(Math.floor(units / 26), top - 1);
        return step === 0.5 && units - index * 26 <= 12 ? index + 0.5 : index + 1;
      };
      paint(value);
      box.addEventListener("pointermove", (e) => paint(at(e)));
      box.addEventListener("pointerleave", () => paint(value));
      box.addEventListener("click", (e) => { const v = at(e); success(v === value ? "" : v); });
      box.addEventListener("keydown", (e) => {
        if (e.key === "ArrowRight" || e.key === "ArrowUp") value = Math.min(top, value + step);
        else if (e.key === "ArrowLeft" || e.key === "ArrowDown") value = Math.max(0, value - step);
        else if (e.key === "Enter") { success(value || ""); return; }
        else if (e.key === "Escape") { cancel(); return; }
        else return;
        e.preventDefault();
        paint(value);
      });
      box.addEventListener("blur", () => success(value || ""));
      onRendered(() => box.focus());
      return box;
    };
  }

  function column(c) {
    // Tabulator writes a title as HTML, and a field name is anyone's text (also from a template): escape it.
    const col = { title: c.title, titleFormatter: () => App.esc(c.title), field: c.key, visible: c.visible, minWidth: 80, resizable: true, headerClick: (e, column) => openMenu(e, column, c) };
    if (c.type === "photo") {
      return { title: "", field: "photo_url", width: 44, hozAlign: "center", headerSort: false, resizable: false, visible: c.visible,
        formatter: (cell) => { const r = cell.getData(); return r.photo_url ? `<img class="grid-avatar" src="${App.esc(r.photo_url)}" alt="" loading="lazy">` : `<span class="avatar" style="width:24px;height:24px;font-size:10px">${App.esc(r.initials)}</span>`; } };
    }
    if (c.key === "first_name" || c.key === "last_name") col.minWidth = 130;
    if (c.wide) col.minWidth = 180; // The title of an own table's record, and long texts.
    if (c.key === "last_name") col.formatter = (cell) => App.esc(cell.getValue());
    if (c.type === "date") { col.formatter = (cell) => de(cell.getValue()); col.editor = c.editable ? "date" : false; col.width = 110; }
    else if (c.type === "bool") { col.formatter = "tickCross"; col.formatterParams = { allowEmpty: true, crossElement: "" }; col.editor = c.editable ? "tickCross" : false; col.hozAlign = "center"; col.width = 90; }
    else if (c.type === "class") {
      col.width = 110;
      col.sorter = "alphanum";
      col.formatter = (cell) => App.esc(cell.getValue());
      if (c.editable) {
        col.editor = "list";
        col.editorParams = { values: [{ label: "–", value: "" }, ...c.options.map((o) => ({ label: o.label, value: o.label }))], autocomplete: true, listOnEmpty: true, itemFormatter: App.esc };
      }
    } else if (c.type === "choice") {
      col.editor = c.editable ? "list" : false; col.width = 110;
      col.editorParams = { values: [{ label: "–", value: "" }, ...c.options.map((v) => ({ label: optionLabel(c, v), value: v }))], autocomplete: true, listOnEmpty: true, itemFormatter: App.esc };
      col.formatter = (cell) => App.esc(optionLabel(c, cell.getValue() ?? ""));
      col.sorter = (a, b) => String(optionLabel(c, a ?? "")).localeCompare(String(optionLabel(c, b ?? "")));
    } else if (c.type === "multichoice") {
      col.formatter = (cell) => App.esc([].concat(cell.getValue() || []).map((v) => optionLabel(c, v)).join(", "));
    } else if (c.type === "number") {
      col.editor = c.editable ? "number" : false; col.editorParams = { selectContents: true }; col.width = 100; col.sorter = "number";
      col.formatter = (cell) => App.esc(number(c, cell.getValue())); col.hozAlign = "right";
    } else if (c.type === "rating") {
      col.width = 26 + 16 * (c.stars || 5); col.sorter = "number";
      col.formatter = (cell) => (cell.getValue() ? App.stars(cell.getValue(), c.stars || 5, "stars-sm") : "");
      if (c.editable) col.editor = ratingEditor(c);
    } else if (c.type === "datetime") { col.formatter = (cell) => App.esc(deTime(cell.getValue())); col.width = 150; }
    else if (c.editable) { col.editor = "input"; col.editorParams = { selectContents: true }; }
    if (!col.sorter && !["date", "bool", "number", "datetime", "photo"].includes(c.type)) col.sorter = natural;
    if (c.key === "device" || c.key === "serial") col.cssClass = "mono";
    return col;
  }

  const classIdByName = Object.fromEntries(cfg.classes.map((c) => [c.label, c.value]));
  const rowHeader = { formatter: "rowSelection", titleFormatter: "rowSelection", hozAlign: "center", headerHozAlign: "center", headerSort: false, width: 40, resizable: false, frozen: true };

  const table = new Tabulator("#grid", {
    height: "calc(100vh - 215px)",
    layout: "fitDataStretch",
    index: "id",
    ajaxURL: cfg.dataUrl,
    ajaxParams: () => ({ entity: cfg.entity, scope: state.scope }),
    ajaxResponse: (url, params, response) => response,
    columns: [rowHeader, ...cfg.columns.map(column)],
    initialSort: [{ column: cfg.initialSort || "last_name", dir: "asc" }],
    headerSortClickElement: "icon",
    movableColumns: true,
    persistence: { sort: true, columns: ["width", "visible"] },
    persistenceID: `schule-${cfg.entity}`,
    selectableRows: "highlight",
    editTriggerEvent: "dblclick",
    placeholder: gettext("Keine Einträge"),
    rowFormatter: (row) => {
      const id = row.getData().id;
      row.getElement().classList.toggle("row-active", id === RecordPanel.activeId);
      document.querySelector(`#gallery [data-card="${id}"]`)?.classList.toggle("is-active", id === RecordPanel.activeId);
    },
    locale: "de",
    langs: { de: { data: { loading: gettext("Lädt"), error: gettext("Fehler") } } },
  });

  const countEl = $("grid-count");
  table.on("dataProcessed", () => { countEl.textContent = table.getDataCount("active"); });
  table.on("dataFiltered", (f, rows) => { countEl.textContent = rows.length; });

  function fitHeight() {
    const top = $("grid").getBoundingClientRect().top;
    table.setHeight(`${Math.max(280, window.innerHeight - top - 24)}px`);
  }
  window.addEventListener("resize", App.debounce(fitHeight, 150));

  const refreshRow = async (id) => {
    const res = await fetch(`${cfg.dataUrl}?entity=${cfg.entity}&id=${id}`);
    if (res.ok) table.updateData(await res.json());
  };
  table.on("cellEdited", async (cell) => {
    const key = cell.getField();
    const def = columnByKey[key];
    const id = cell.getData().id;
    let value = cell.getValue();
    if (def.type === "class") value = classIdByName[value] || "";
    let res;
    try {
      res = await App.postJSON(fieldUrl(id, key), { value });
    } catch (e) {
      cell.restoreOldValue();
      return;
    }
    if (res.ok) {
      if (cfg.records) refreshRow(id); // computed fields and the title follow the change
      if (id === RecordPanel.activeId) RecordPanel.show(id);
      App.toast(interpolate(gettext("%(field)s gespeichert"), { field: def.title }, true), { action: { label: gettext("Rückgängig"), run: async () => {
        const old = cell.getOldValue();
        await App.postJSON(fieldUrl(id, key), { value: def.type === "class" ? (classIdByName[old] || "") : old });
        cell.restoreOldValue();
        if (cfg.records) refreshRow(id);
        if (id === RecordPanel.activeId) RecordPanel.show(id);
      } } });
    } else {
      const body = await res.json().catch(() => ({}));
      if (res.status !== 401) App.toast(body.error || gettext("Nicht gespeichert"), { error: true });
      cell.restoreOldValue();
    }
  });

  // Filtering
  const search = $("grid-search");
  const presets = cfg.records ? {} : {
    "ohne-email": (r) => !r.email && r.school_type !== "Kiga",
    "neue-codes": (r) => r.new_codes_needed === true,
    "ohne-foto": (r) => !r.photo_url,
    "ohne-geraet": (r) => !r.device,
  };
  const PRESET_TITLES = { "ohne-email": gettext("Ohne E-Mail"), "neue-codes": gettext("Neue Codes nötig"), "ohne-foto": gettext("Ohne Foto"), "ohne-geraet": gettext("Ohne Gerät") };
  let activePreset = cfg.preset || "";

  function matches(row, exceptKey) {
    const preset = presets[activePreset];
    if (preset && !preset(row)) return false;
    for (const [key, values] of Object.entries(filters)) {
      if (key === exceptKey || !values.length || !columnByKey[key]) continue;
      if (!values.includes(shown(columnByKey[key], row))) return false;
    }
    const q = search.value.trim().toLowerCase();
    const text = (k, v) => `${v} ${columnByKey[k]?.optionLabels ? [].concat(v ?? []).map((x) => optionLabel(columnByKey[k], x)).join(" ") : ""}`.toLowerCase();
    return !q || q.split(/\s+/).every((t) => Object.entries(row).some(([k, v]) => k !== "id" && !k.startsWith("_") && text(k, v).includes(t)));
  }

  function applyFilters() {
    table.setFilter((row) => matches(row));
    renderChips();
    try { sessionStorage.setItem(FILTER_KEY, JSON.stringify(filters)); } catch (e) {}
  }
  search.addEventListener("input", App.debounce(applyFilters, 120));

  function setFilter(key, values) {
    if (values.length) filters[key] = values; else delete filters[key];
    applyFilters();
  }

  function renderChips() {
    const box = $("grid-chips");
    const keys = Object.keys(filters).filter((k) => columnByKey[k]);
    cfg.columns.forEach((c) => {
      const col = c.type === "photo" ? null : table.getColumn(c.key);
      col?.getElement().classList.toggle("col-filtered", keys.includes(c.key));
    });
    box.innerHTML = "";
    box.classList.toggle("hidden", !keys.length && !activePreset);
    box.classList.toggle("flex", !!keys.length || !!activePreset);
    if (presets[activePreset]) {
      const chip = document.createElement("button");
      chip.className = "badge badge-accent cursor-pointer";
      chip.title = gettext("Filter entfernen");
      chip.innerHTML = `${App.esc(PRESET_TITLES[activePreset])} <svg class="icon"><use href="/static/img/icons.svg#x"></use></svg>`;
      chip.addEventListener("click", () => { activePreset = ""; applyFilters(); });
      box.appendChild(chip);
    }
    keys.forEach((key) => {
      const values = filters[key].map((v) => label(columnByKey[key], v));
      const text = values.length > 4 ? `${values.slice(0, 4).join(", ")} +${values.length - 4}` : values.join(", ");
      const chip = document.createElement("button");
      chip.className = "badge badge-accent cursor-pointer";
      chip.title = gettext("Filter entfernen");
      chip.innerHTML = `${App.esc(columnByKey[key].title)}: ${App.esc(text)} <svg class="icon"><use href="/static/img/icons.svg#x"></use></svg>`;
      chip.addEventListener("click", () => setFilter(key, []));
      box.appendChild(chip);
    });
    if (keys.length || activePreset) {
      const clear = document.createElement("button");
      clear.className = "text-[12px] muted underline ml-1";
      clear.textContent = gettext("Alle Filter löschen");
      clear.addEventListener("click", () => { filters = {}; activePreset = ""; applyFilters(); });
      box.appendChild(clear);
    }
    fitHeight();
  }

  // Menu on column titles: sort, then pick values
  let menu = null;
  function closeMenu() { menu?.remove(); menu = null; }

  function openMenu(event, column, c) {
    if (event.target.closest(".tabulator-col-sorter, .tabulator-col-resize-handle")) return;
    const reopening = menu && menu.dataset.key === c.key;
    closeMenu();
    if (reopening) return;

    const counts = new Map();
    table.getData().forEach((row) => {
      if (!matches(row, c.key)) return;
      const value = shown(c, row);
      counts.set(value, (counts.get(value) || 0) + 1);
    });
    (filters[c.key] || []).forEach((v) => { if (!counts.has(v)) counts.set(v, 0); });
    const collator = new Intl.Collator("de", { numeric: true, sensitivity: "base" });
    const values = [...counts.keys()].sort((a, b) => (a === "") - (b === "") || collator.compare(a, b));

    menu = document.createElement("div");
    menu.className = "grid-menu";
    menu.dataset.key = c.key;
    menu.setAttribute("role", "dialog");
    menu.setAttribute("aria-label", interpolate(gettext("Filter für %(field)s"), { field: c.title }, true));
    const searchLabel = App.esc(gettext("Werte suchen"));
    menu.innerHTML = `
      <div class="p-1 border-b" style="border-color: var(--line)">
        <button class="grid-menu-item" data-sort="asc">${App.esc(gettext("Aufsteigend sortieren"))}</button>
        <button class="grid-menu-item" data-sort="desc">${App.esc(gettext("Absteigend sortieren"))}</button>
      </div>
      <div class="p-2 pb-1"><input class="input h-8 text-[13px]" placeholder="${searchLabel}" aria-label="${searchLabel}"></div>
      <div class="flex-1 overflow-y-auto p-1 min-h-0" data-list></div>
      <div class="flex items-center justify-between p-2 border-t" style="border-color: var(--line)">
        <span class="muted text-[12px]" data-info></span><button class="btn btn-sm" data-clear>${App.esc(gettext("Zurücksetzen"))}</button>
      </div>`;
    document.body.appendChild(menu);

    const list = menu.querySelector("[data-list]");
    const info = menu.querySelector("[data-info]");
    const updateInfo = () => {
      const n = (filters[c.key] || []).length;
      info.textContent = n
        ? interpolate(gettext("%(n)s ausgewählt"), { n }, true)
        : interpolate(ngettext("%(n)s Wert", "%(n)s Werte", values.length), { n: values.length }, true);
    };
    function renderList(query) {
      list.innerHTML = "";
      const chosen = new Set(filters[c.key] || []);
      const needle = (query || "").toLowerCase();
      const visible = values.filter((v) => label(c, v).toLowerCase().includes(needle)).slice(0, 400);
      visible.forEach((value) => {
        const item = document.createElement("label");
        item.className = "grid-menu-item";
        item.innerHTML = `<input type="checkbox" class="accent-[var(--accent)]"><span class="flex-1 truncate"></span><span class="muted text-[12px]">${counts.get(value)}</span>`;
        item.querySelector("span").textContent = label(c, value);
        const box = item.querySelector("input");
        box.checked = chosen.has(value);
        box.addEventListener("change", () => {
          const next = new Set(filters[c.key] || []);
          box.checked ? next.add(value) : next.delete(value);
          setFilter(c.key, [...next]);
          updateInfo();
        });
        list.appendChild(item);
      });
      if (!visible.length) list.innerHTML = `<p class="muted text-[13px] p-2">${App.esc(gettext("Keine Werte"))}</p>`;
    }
    renderList("");
    updateInfo();
    const input = menu.querySelector("input.input");
    input.addEventListener("input", () => renderList(input.value));
    menu.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => {
      table.setSort(c.key, b.dataset.sort);
      closeMenu();
    }));
    menu.querySelector("[data-clear]").addEventListener("click", () => { setFilter(c.key, []); renderList(input.value); updateInfo(); });

    const rect = column.getElement().getBoundingClientRect();
    const left = Math.min(rect.left, window.innerWidth - 276);
    menu.style.left = `${Math.max(8, left)}px`;
    menu.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - menu.offsetHeight - 8)}px`;
    input.focus();
    event.stopPropagation();
  }

  document.addEventListener("mousedown", (e) => {
    if (menu && !menu.contains(e.target) && !e.target.closest(".tabulator-col")) closeMenu();
  });

  // Scope
  const scope = $("grid-scope") || { value: "active", addEventListener() {} };
  scope.value = state.scope;
  scope.addEventListener("change", () => { state.scope = scope.value; persist(); table.setData(); });

  function persist() {
    try { sessionStorage.setItem(LS, JSON.stringify({ scope: state.scope })); } catch (e) {}
  }

  // Column chooser
  const colBox = $("grid-columns");
  function renderColumns() {
    colBox.innerHTML = "";
    cfg.columns.filter((c) => c.type !== "photo").forEach((c) => {
      const col = table.getColumn(c.key);
      const label = document.createElement("label");
      label.className = "flex items-center gap-2 px-1.5 py-1 text-[13px] rounded hover:bg-[var(--surface-2)]";
      label.innerHTML = `<input type="checkbox" class="accent-[var(--accent)]" ${col && col.isVisible() ? "checked" : ""}> ${App.esc(c.title)}`;
      label.querySelector("input").addEventListener("change", (e) => { e.target.checked ? col.show() : col.hide(); table.redraw(); });
      colBox.appendChild(label);
    });
  }


  // Gallery (tables/look.py): the rows of the list, with its search, filters, saved views and order, as
  // cards with their cover. The list stays underneath and keeps doing the filtering and sorting.
  const look = cfg.look;
  const gallery = $("gallery");
  let layout = "list";
  const cellText = (c, v) => {
    if (!c || v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length)) return "";
    if (c.type === "bool") return v ? c.title : "";
    if (c.type === "date") return de(v);
    if (c.type === "datetime") return deTime(v);
    if (c.type === "number") return number(c, v);
    if (c.type === "rating") return "";
    if (c.type === "choice" || c.type === "multichoice") return [].concat(v).map((x) => optionLabel(c, x)).join(", ");
    return String(v);
  };
  function card(row) {
    const sub = (look.subtitle || []).map((k) => cellText(columnByKey[k], row[k])).filter(Boolean).join(" · ");
    const flag = look.flag && row[look.flag] ? optionLabel(columnByKey[look.flag], row[look.flag]) : "";
    const rating = look.rating && Number(row[look.rating]) ? App.stars(row[look.rating], columnByKey[look.rating]?.stars || 5, "stars-sm") : "";
    const cover = row._cover ? `<img src="${App.esc(row._cover)}" alt="" loading="lazy" decoding="async">` : `<div class="gallery-empty">${App.esc(row.name)}</div>`;
    return `<button type="button" class="gallery-card${row.id === RecordPanel.activeId ? " is-active" : ""}" data-card="${App.esc(row.id)}">`
      + `<div class="gallery-cover">${cover}${flag ? `<span class="gallery-flag">${App.esc(flag)}</span>` : ""}</div>`
      + `<div class="gallery-title">${App.esc(row.name)}</div>${sub ? `<div class="gallery-sub">${App.esc(sub)}</div>` : ""}${rating}</button>`;
  }
  function renderGallery() {
    if (layout !== "gallery") return;
    const rows = table.getData("active");
    gallery.innerHTML = rows.length ? rows.map(card).join("") : `<p class="muted text-[13px] p-2">${App.esc(gettext("Keine Einträge"))}</p>`;
  }
  const renderGallerySoon = App.debounce(renderGallery, 30);
  function fitGallery() {
    if (layout !== "gallery") return;
    gallery.style.height = `${Math.max(280, window.innerHeight - gallery.getBoundingClientRect().top - 24)}px`;
  }
  const gallerySort = $("gallery-sort");
  function renderSortChoices() {
    const sorted = table.getSorters()[0];
    const current = sorted ? `${sorted.field}:${sorted.dir}` : "";
    const keys = ["name", ...cfg.columns.filter((c) => ["number", "rating", "date", "datetime", "choice", "text"].includes(c.type)).map((c) => c.key)];
    const title = (k) => (k === "name" ? gettext("Titel") : columnByKey[k]?.title || k);
    gallerySort.innerHTML = keys.flatMap((k) => [["asc", "↑"], ["desc", "↓"]].map(([dir, arrow]) =>
      `<option value="${App.esc(k)}:${dir}" ${current === `${k}:${dir}` ? "selected" : ""}>${App.esc(title(k))} ${arrow}</option>`)).join("");
    if (!current) gallerySort.insertAdjacentHTML("afterbegin", `<option value="" selected>${App.esc(gettext("Sortieren"))}</option>`);
  }
  function setLayout(next, remember = true) {
    if (!look || !gallery) return;
    layout = next === "gallery" ? "gallery" : "list";
    document.querySelectorAll("[data-layout]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.layout === layout)));
    $("grid").classList.toggle("hidden", layout === "gallery");
    gallery.classList.toggle("hidden", layout !== "gallery");
    gallerySort.classList.toggle("hidden", layout !== "gallery");
    if (remember) try { sessionStorage.setItem(`${LS}:layout`, layout); } catch (e) {}
    if (layout === "gallery") { renderSortChoices(); renderGallery(); fitGallery(); } else { table.redraw(true); fitHeight(); }
  }
  if (look && gallery) {
    let saved = null;
    try { saved = sessionStorage.getItem(`${LS}:layout`); } catch (e) {}
    document.querySelectorAll("[data-layout]").forEach((b) => b.addEventListener("click", () => setLayout(b.dataset.layout)));
    ["dataFiltered", "dataSorted", "dataProcessed", "rowUpdated", "rowDeleted"].forEach((name) => table.on(name, renderGallerySoon));
    table.on("dataSorted", () => { if (layout === "gallery") renderSortChoices(); });
    gallerySort.addEventListener("change", () => {
      const [field, dir] = gallerySort.value.split(":");
      if (field) table.setSort(field, dir);
    });
    gallery.addEventListener("click", (e) => {
      const item = e.target.closest("[data-card]");
      if (!item) return;
      if (e.metaKey || e.ctrlKey) { window.open(`${recordBase}${item.dataset.card}/`, "_blank"); return; }
      RecordPanel.open(item.dataset.card);
      gallery.querySelectorAll(".gallery-card").forEach((c) => c.classList.toggle("is-active", c.dataset.card === RecordPanel.activeId));
    });
    gallery.addEventListener("contextmenu", (e) => {
      const item = e.target.closest("[data-card]");
      const data = item && table.getData().find((row) => String(row.id) === item.dataset.card);
      if (data) rowMenu(e, data); // the same menu as a row of the list
    });
    window.addEventListener("resize", App.debounce(fitGallery, 150));
    table.on("tableBuilt", () => setLayout(saved || look.layout || "list", false));
  }

  // Saved views
  const viewSelect = $("grid-views");
  const deleteBtn = $("grid-delete-view");
  function renderViews(selected) {
    const own = App.esc(gettext("(eigene)"));
    viewSelect.innerHTML = `<option value="">${App.esc(gettext("Ansicht wählen"))}</option>` + cfg.views.map((v) => `<option value="${v.id}" ${v.id === selected ? "selected" : ""}>${App.esc(v.name)}${v.shared ? "" : ` ${own}`}</option>`).join("");
  }
  function applyView(view) {
    const conf = view.config || {};
    if (conf.scope) { scope.value = conf.scope; state.scope = conf.scope; }
    if (conf.columns) {
      cfg.columns.forEach((c) => {
        if (c.type === "photo") return;
        const col = table.getColumn(c.key);
        if (col) (conf.columns.includes(c.key) ? col.show() : col.hide());
      });
    }
    activePreset = conf.preset || "";
    filters = { ...(conf.filters || {}) };
    if (conf.sort && conf.sort.length) table.setSort(conf.sort);
    if (conf.layout) setLayout(conf.layout);
    table.setData().then(() => { applyFilters(); renderColumns(); });
  }
  viewSelect.addEventListener("change", () => {
    const view = cfg.views.find((v) => v.id === viewSelect.value);
    deleteBtn.classList.toggle("hidden", !view || view.shared);
    if (view) applyView(view);
    else { filters = {}; activePreset = ""; applyFilters(); }
  });
  $("grid-save-view").addEventListener("click", async () => {
    const name = prompt(gettext("Name der Ansicht"));
    if (!name) return;
    const columns = table.getColumns().filter((c) => c.isVisible() && c.getField()).map((c) => c.getField());
    const sort = table.getSorters().map((s) => ({ column: s.field, dir: s.dir }));
    const res = await App.postJSON(viewsUrl, { entity: cfg.entity, name, config: { columns, sort, scope: state.scope, preset: activePreset, filters, ...(look ? { layout } : {}) } });
    if (res.ok) {
      const view = await res.json();
      cfg.views = cfg.views.filter((v) => v.id !== view.id).concat(view);
      renderViews(view.id);
      deleteBtn.classList.remove("hidden");
      App.toast(gettext("Ansicht gespeichert"));
    }
  });
  deleteBtn.addEventListener("click", async () => {
    if (!confirm(gettext("Ansicht löschen?"))) return;
    await App.post(viewDeleteUrl(viewSelect.value));
    cfg.views = cfg.views.filter((v) => v.id !== viewSelect.value);
    renderViews();
    deleteBtn.classList.add("hidden");
  });

  // Bulk actions
  const bulk = $("grid-bulk");
  const selectedIds = () => table.getSelectedData().map((r) => r.id);
  table.on("rowSelectionChanged", (data) => {
    bulk.classList.toggle("hidden", !data.length);
    bulk.classList.toggle("flex", !!data.length);
    $("grid-bulk-count").textContent = interpolate(gettext("%(n)s ausgewählt"), { n: data.length }, true);
    fitHeight();
  });
  async function runBulk(action, value, question, ids = selectedIds()) {
    if (question && !(await App.ask(question))) return;
    const res = await App.postJSON(actionUrl, { ids, action, value });
    if (res.ok) {
      const body = await res.json();
      App.toast(cfg.records
        ? interpolate(ngettext("Erledigt: %(n)s Datensatz", "Erledigt: %(n)s Datensätze", body.count), { n: body.count }, true)
        : interpolate(ngettext("Erledigt: %(n)s Person", "Erledigt: %(n)s Personen", body.count), { n: body.count }, true));
      table.setData();
    } else if (res.status !== 401) App.toast(gettext("Das hat nicht geklappt"), { error: true });
  }
  const count = (n) => cfg.records
    ? interpolate(ngettext("%(n)s Datensatz", "%(n)s Datensätze", n), { n }, true)
    : interpolate(ngettext("%(n)s Person", "%(n)s Personen", n), { n }, true);
  const people = () => count(selectedIds().length);
  const chosenLabel = (select) => select.options[select.selectedIndex].textContent;
  const classSel = $("bulk-class");
  if (classSel) {
    classSel.innerHTML = `<option value="">${App.esc(gettext("Klasse zuweisen"))}</option>` + cfg.classes.map((c) => `<option value="${c.value}">${App.esc(c.label)}</option>`).join("");
    classSel.addEventListener("change", () => {
      if (classSel.value) runBulk("class", classSel.value, interpolate(gettext("%(who)s in die Klasse %(klasse)s verschieben?"), { who: people(), klasse: chosenLabel(classSel) }, true));
      classSel.value = "";
    });
    if (cfg.entity !== "pupil") classSel.classList.add("hidden");
  }
  const loginSel = $("bulk-logins");
  if (loginSel) {
    if (cfg.platforms.length) {
      loginSel.innerHTML = `<option value="">${App.esc(gettext("Zugänge erzeugen"))}</option>` + cfg.platforms.map((p) => `<option value="${p.id}">${App.esc(p.name)}</option>`).join("");
      loginSel.addEventListener("change", () => {
        if (loginSel.value) runBulk("logins", loginSel.value, interpolate(gettext("%(platform)s: Zugänge für %(who)s erzeugen?"), { platform: chosenLabel(loginSel), who: people() }, true));
        loginSel.value = "";
      });
    } else loginSel.classList.add("hidden");
  }
  $("bulk-archive")?.addEventListener("click", () => runBulk("archive", undefined, interpolate(gettext("%(who)s archivieren?"), { who: people() }, true)));
  $("bulk-restore")?.addEventListener("click", () => runBulk("restore"));
  const printSel = $("bulk-print");
  if (printSel && !cfg.templates.length && cfg.records) printSel.classList.add("hidden");
  if (printSel) printSel.innerHTML = `<option value="">${App.esc(gettext("Bericht drucken"))}</option>` + cfg.templates.map((t) => `<option value="${t.id}">${App.esc(t.name)}</option>`).join("");
  printSel?.addEventListener("change", () => {
    if (!printSel.value) return;
    const form = $("print-form");
    form.template.value = printSel.value;
    form.ids.value = selectedIds().join(",");
    form.submit();
    printSel.value = "";
  });

  // Right click on a row: the actions of the bulk bar and more, for the row or the selection it belongs to
  function printFor(ids, template) {
    const form = $("print-form");
    form.template.value = template;
    form.ids.value = ids.join(",");
    form.submit();
  }

  async function moveToBin(ids, names) {
    const what = ids.length === 1 ? names[0] : count(ids.length);
    if (!(await App.ask(interpolate(gettext("%(what)s in den Papierkorb legen? Von dort lässt sich das 30 Tage lang zurückholen."), { what }, true)))) return;
    const res = await App.postJSON(actionUrl, { ids, action: "delete" });
    if (!res.ok) { if (res.status !== 401) App.toast(gettext("Das hat nicht geklappt"), { error: true }); return; }
    const body = await res.json();
    if (ids.includes(RecordPanel.activeId) && body.count) location.reload();
    table.setData();
    body.refused.forEach((reason) => App.toast(reason, { error: true }));
    if (!body.count) return;
    const done = body.count === 1 && ids.length === 1
      ? interpolate(gettext("%(name)s liegt im Papierkorb"), { name: names[0] }, true)
      : interpolate(gettext("%(who)s im Papierkorb"), { who: count(body.count) }, true);
    App.toast(done, { action: { label: gettext("Rückgängig"), run: async () => {
      const back = await App.postJSON(actionUrl, { ids, action: "bring_back" });
      if (back.ok) { table.setData(); App.toast(gettext("Wiederhergestellt")); }
    } } });
  }

  table.on("rowContext", (e, row) => rowMenu(e, row.getData()));

  // The menu of a record, from the list (right click on a row) or the gallery (on a card).
  function rowMenu(e, data) {
    const chosen = table.getSelectedData();
    const rows = chosen.some((r) => r.id === data.id) && chosen.length > 1 ? chosen : [data];
    const ids = rows.map((r) => r.id);
    const names = rows.map((r) => r.name);
    const single = rows.length === 1;
    const url = `${recordBase}${data.id}/`;
    const archived = rows.every((r) => r.archived);
    const items = single
      ? [
          { label: gettext("Öffnen"), icon: "eye", run: () => RecordPanel.open(data.id) },
          { label: gettext("Ganze Seite öffnen"), icon: "file", href: url },
          { label: gettext("In neuem Fenster öffnen"), icon: "external", newWindow: url },
          "-",
          cfg.canEdit && !cfg.records && { label: gettext("Duplizieren"), icon: "copyplus", post: `${url}duplizieren/`, confirm: interpolate(gettext("%(name)s duplizieren? Die Kopie bekommt Daten und Formularfelder, aber kein Bild, keine Karten, Zugänge oder Geräte."), { name: data.name }, true) },
        ]
      : [{ label: interpolate(gettext("%(who)s ausgewählt"), { who: count(rows.length) }, true), disabled: true }, "-"];
    const who = single ? names[0] : count(ids.length);
    if (cfg.templates.length) items.push({ label: gettext("Drucken"), icon: "printer", items: cfg.templates.map((t) => ({ label: t.name, run: () => printFor(ids, t.id) })) });
    if (cfg.canEdit && cfg.entity === "pupil" && cfg.classes.length) {
      items.push({ label: gettext("In Klasse verschieben"), icon: "move", items: cfg.classes.map((c) => ({ label: c.label, run: () => runBulk("class", c.value, interpolate(gettext("%(who)s in die Klasse %(klasse)s verschieben?"), { who, klasse: c.label }, true), ids) })) });
    }
    if (cfg.platforms.length) {
      items.push({ label: gettext("Zugänge erzeugen"), icon: "key", items: cfg.platforms.map((p) => ({ label: p.name, run: () => runBulk("logins", p.id, interpolate(gettext("%(platform)s: Zugänge für %(who)s erzeugen?"), { platform: p.name, who }, true), ids) })) });
    }
    if (cfg.canArchive || cfg.canDelete) items.push("-");
    if (cfg.canArchive) {
      items.push(archived
        ? { label: gettext("Wiederherstellen"), icon: "archive-restore", run: () => runBulk("restore", undefined, null, ids) }
        : { label: gettext("Archivieren"), icon: "archive", run: () => runBulk("archive", undefined, interpolate(gettext("%(who)s archivieren?"), { who }, true), ids) });
    }
    if (cfg.canArchive || cfg.canDelete) {
      items.push({ label: single ? gettext("Löschen …") : interpolate(gettext("%(who)s löschen …"), { who: count(ids.length) }, true), icon: "trash", danger: true, run: () => moveToBin(ids, names) });
    }
    ContextMenu.open(e, items);
  }

  // Export exactly what is shown
  function exportRows(format) {
    const columns = table.getColumns().filter((c) => c.isVisible() && c.getField() && c.getField() !== "photo_url").map((c) => ({ key: c.getField(), title: c.getDefinition().title }));
    const ids = table.getData("active").map((r) => r.id);
    const form = $("export-form");
    form.payload.value = JSON.stringify({ entity: cfg.entity, format, columns, ids });
    form.submit();
  }
  $("grid-export-xlsx")?.addEventListener("click", () => exportRows("xlsx"));
  $("grid-export-csv")?.addEventListener("click", () => exportRows("csv"));

  $("bulk-delete")?.addEventListener("click", () => { const rows = table.getSelectedData(); moveToBin(rows.map((r) => r.id), rows.map((r) => r.name)); });
  RecordPanel.tabulator(table, { base: recordBase, rowUrl: (id) => `${cfg.dataUrl}?entity=${cfg.entity}&id=${id}`, busy: () => menu });
  document.addEventListener("keydown", (e) => { if (menu && e.key === "Escape") closeMenu(); });

  table.on("tableBuilt", () => {
    renderColumns();
    renderViews();
    applyFilters();
  });
})();
