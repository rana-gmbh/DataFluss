// Right-click menus. Pages register what a right click on an element offers; list rows (tr[data-record]
// in #record-rows) get "Öffnen" by themselves, and a row can add its own actions with
//   <template data-menu-items><a href="…" data-icon="file">PDF</a><hr><a data-post="…" data-confirm="…" data-danger>Löschen</a></template>
// Inside text fields and over selected text the browser's own menu stays, for copy and paste.
//
// An item is {label, icon, danger, disabled, hint, confirm, and one of: run(), href, newWindow, post,
// data, items}. "-" is a separator. post sends a form with the CSRF token, so the server's redirect and
// message work as with a button.
window.ContextMenu = (function () {
  const providers = [];
  let root = null;
  let opener = null;
  let openedFor = null; // the event the open menu belongs to

  function register(selector, provide) {
    providers.push({ selector, provide });
  }

  function icon(name) {
    return name ? `<svg class="icon" aria-hidden="true"><use href="/static/img/icons.svg#${name}"></use></svg>` : '<span class="ctx-icon-gap"></span>';
  }

  function submit(url, data) {
    const form = document.createElement("form");
    form.method = "post";
    form.action = url;
    form.hidden = true;
    const fields = { csrfmiddlewaretoken: App.csrf(), ...(data || {}) };
    Object.entries(fields).forEach(([name, value]) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.value = value;
      form.appendChild(input);
    });
    document.body.appendChild(form);
    form.submit();
  }

  async function run(item) {
    close();
    if (item.disabled) return;
    if (item.confirm && !(await App.ask(item.confirm))) return;
    if (item.run) return item.run();
    if (item.post) return submit(item.post, item.data);
    if (item.newWindow) return window.open(item.newWindow, "_blank");
    if (item.href) location.href = item.href;
  }

  function build(items, level) {
    const menu = document.createElement("div");
    menu.className = "ctx-menu";
    menu.setAttribute("role", "menu");
    menu.dataset.level = level;
    let lastWasSeparator = true;
    items.forEach((item) => {
      if (!item) return;
      if (item === "-") {
        if (!lastWasSeparator) menu.appendChild(Object.assign(document.createElement("div"), { className: "ctx-sep", role: "separator" }));
        lastWasSeparator = true;
        return;
      }
      lastWasSeparator = false;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ctx-item" + (item.danger ? " ctx-danger" : "");
      button.setAttribute("role", "menuitem");
      button.disabled = !!item.disabled;
      button.addEventListener("mouseenter", () => { if (!button.disabled) button.focus({ preventScroll: true }); });
      button.innerHTML = `${icon(item.icon)}<span class="ctx-label">${App.esc(item.label)}</span>${item.hint ? `<span class="ctx-hint">${App.esc(item.hint)}</span>` : ""}${item.items ? icon("chevron") : ""}`;
      if (item.items && item.items.length) {
        button.setAttribute("aria-haspopup", "menu");
        const openSub = () => openSubmenu(button, item.items, level + 1);
        button.addEventListener("mouseenter", openSub);
        button.addEventListener("click", openSub);
        button.addEventListener("keydown", (e) => { if (e.key === "ArrowRight") { e.preventDefault(); openSub(); } });
      } else {
        button.addEventListener("mouseenter", () => closeFrom(level + 1));
        button.addEventListener("click", () => run(item));
      }
      menu.appendChild(button);
    });
    if (menu.lastChild?.classList.contains("ctx-sep")) menu.lastChild.remove();
    return menu;
  }

  function place(menu, x, y, besides) {
    root.appendChild(menu);
    const { innerWidth: w, innerHeight: h } = window;
    const r = menu.getBoundingClientRect();
    let left = x;
    let top = y;
    if (besides) {
      left = besides.right - 2;
      top = besides.top - 5;
      if (left + r.width > w - 8) left = besides.left - r.width + 2;
    }
    if (left + r.width > w - 8) left = Math.max(8, w - r.width - 8);
    if (top + r.height > h - 8) top = Math.max(8, h - r.height - 8);
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }

  function closeFrom(level) {
    root?.querySelectorAll(`.ctx-menu[data-level="${level - 1}"] .ctx-open`).forEach((b) => b.classList.remove("ctx-open"));
    root?.querySelectorAll(".ctx-menu").forEach((m) => { if (Number(m.dataset.level) >= level) m.remove(); });
  }

  function openSubmenu(button, items, level) {
    const current = root.querySelector(`.ctx-menu[data-level="${level}"]`);
    if (current && current.dataset.owner === button.dataset.key) return;
    closeFrom(level);
    button.dataset.key = button.dataset.key || Math.random().toString(36).slice(2);
    const menu = build(items, level);
    menu.dataset.owner = button.dataset.key;
    button.classList.add("ctx-open");
    place(menu, 0, 0, button.getBoundingClientRect());
    return menu;
  }

  function open(event, items) {
    close();
    const list = (items || []).filter(Boolean);
    if (!list.some((i) => i !== "-")) return false;
    event.preventDefault();
    openedFor = event;
    opener = document.activeElement;
    root = document.createElement("div");
    root.className = "ctx-root";
    document.body.appendChild(root);
    const menu = build(list, 0);
    const keyboard = event.clientX === 0 && event.clientY === 0;
    const at = keyboard ? event.target.getBoundingClientRect() : null;
    place(menu, keyboard ? at.left + 12 : event.clientX, keyboard ? at.bottom : event.clientY);
    menu.querySelector(".ctx-item:not(:disabled)")?.focus({ preventScroll: true });
    return true;
  }

  function close() {
    if (!root) return;
    root.remove();
    root = null;
    if (opener && document.contains(opener)) opener.focus?.({ preventScroll: true });
    opener = null;
  }

  // Items written into the page, see the comment at the top.
  function fromTemplate(template) {
    return [...template.content.children].map((el) => {
      if (el.tagName === "HR") return "-";
      const d = el.dataset;
      return {
        label: el.textContent.trim(), icon: d.icon, danger: "danger" in d, disabled: "disabled" in d, confirm: d.confirm,
        post: d.post, data: d.data ? JSON.parse(d.data) : null, newWindow: "newWindow" in d ? el.getAttribute("href") : null,
        href: "newWindow" in d ? null : el.getAttribute("href"),
      };
    });
  }

  function recordRowItems(row) {
    const link = row.querySelector("[data-record-link]") || row.querySelector("a[href]");
    const href = link?.getAttribute("href");
    const items = [];
    if (window.RecordPanel?.openRow && row.closest("#record-rows")) items.push({ label: gettext("Öffnen"), icon: "eye", run: () => RecordPanel.openRow(row) });
    if (href) {
      items.push({ label: gettext("Ganze Seite öffnen"), icon: "file", href });
      items.push({ label: gettext("In neuem Fenster öffnen"), icon: "external", newWindow: href });
    }
    return items;
  }

  function itemsFor(target) {
    for (let el = target; el && el !== document.body; el = el.parentElement) {
      for (const { selector, provide } of providers) {
        if (el.matches(selector)) {
          const items = provide(el, target);
          if (items && items.length) return items;
        }
      }
      if (el.matches("tr[data-record], [data-menu]")) {
        const template = el.querySelector("template[data-menu-items]");
        const own = template ? fromTemplate(template) : [];
        const base = el.matches("tr[data-record]") ? recordRowItems(el) : [];
        const items = [...base, ...(base.length && own.length ? ["-"] : []), ...own];
        if (items.length) return items;
      }
    }
    return null;
  }

  document.addEventListener("contextmenu", (event) => {
    if (event.target.closest(".ctx-root")) return event.preventDefault();
    if (event.defaultPrevented) return; // a page opened its own menu for this
    const typing = event.target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']");
    const selected = String(window.getSelection?.() || "").trim();
    const items = typing || selected ? null : itemsFor(event.target);
    if (items) return open(event, items);
    // A page may still open its own menu for this click after us (Tabulator does), so close only
    // a menu that belongs to an earlier click.
    setTimeout(() => { if (openedFor !== event) close(); }, 0);
  });

  document.addEventListener("mousedown", (e) => { if (root && !e.target.closest(".ctx-root")) close(); }, true);
  window.addEventListener("blur", close);
  window.addEventListener("resize", close);
  document.addEventListener("scroll", (e) => { if (root && !root.contains(e.target)) close(); }, true);
  document.addEventListener("keydown", (e) => {
    if (!root) return;
    const menu = document.activeElement?.closest(".ctx-menu") || root.querySelector(".ctx-menu");
    const buttons = [...menu.querySelectorAll(".ctx-item:not(:disabled)")];
    const at = buttons.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); buttons[(at + 1) % buttons.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); buttons[(at - 1 + buttons.length) % buttons.length]?.focus(); }
    else if (e.key === "ArrowLeft" && Number(menu.dataset.level) > 0) {
      e.preventDefault();
      const owner = root.querySelector(`[data-key="${menu.dataset.owner}"]`);
      closeFrom(Number(menu.dataset.level));
      owner?.focus();
    } else if (e.key === "ArrowRight" && document.activeElement?.getAttribute("aria-haspopup")) {
      e.preventDefault();
      setTimeout(() => root?.querySelector(`.ctx-menu[data-level="${Number(menu.dataset.level) + 1}"] .ctx-item:not(:disabled)`)?.focus(), 0);
    } else if (e.key === "Tab") { e.preventDefault(); close(); }
  }, true);

  return { register, open, close, submit };
})();
