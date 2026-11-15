// The guided tour (core/tour.py): a highlighted element and a short note beside it, step by step.
// Starts by itself at a user's first sign-in, and again from the "?" button or Hilfe → Rundgang.
(() => {
  const data = document.getElementById("tour-steps");
  const config = document.getElementById("tour-config");
  if (!data || !config) return;
  const all = JSON.parse(data.textContent);
  let steps = [], index = 0, spot = null, card = null, shown = false;

  const visible = (el) => {
    if (!el) return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0 && box.right > 0 && box.left < window.innerWidth && getComputedStyle(el).visibility !== "hidden";
  };

  function seen() {
    if (!shown) return; // nothing was shown, so it may start next time
    const token = document.querySelector('meta[name="csrf-token"]')?.content || "";
    fetch(config.dataset.seenUrl, { method: "POST", headers: { "X-CSRFToken": token } }).catch(() => {});
  }

  function close() {
    spot?.remove();
    card?.remove();
    spot = card = null;
    document.removeEventListener("keydown", keys, true);
    window.removeEventListener("resize", place);
    seen();
  }

  function keys(e) {
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); go(index + 1); }
    else if (e.key === "ArrowLeft" && index > 0) { e.preventDefault(); go(index - 1); }
  }

  function place() {
    const target = document.querySelector(`[data-tour="${steps[index].anchor}"]`);
    if (!target || !card) return;
    const box = target.getBoundingClientRect();
    const pad = 6;
    Object.assign(spot.style, { top: `${box.top - pad}px`, left: `${box.left - pad}px`, width: `${box.width + pad * 2}px`, height: `${box.height + pad * 2}px` });
    const width = Math.min(320, window.innerWidth - 24);
    card.style.width = `${width}px`;
    const height = card.offsetHeight;
    let left = box.right + 14, top = box.top;
    if (left + width > window.innerWidth - 12) { left = Math.max(12, Math.min(box.left, window.innerWidth - width - 12)); top = box.bottom + 14; }
    if (top + height > window.innerHeight - 12) top = Math.max(12, box.top - height - 14);
    Object.assign(card.style, { top: `${Math.max(12, top)}px`, left: `${left}px` });
  }

  function button(label, primary, run) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = primary ? "btn btn-primary btn-sm" : "btn btn-ghost btn-sm";
    b.textContent = label;
    b.addEventListener("click", run);
    return b;
  }

  function go(next) {
    if (next >= steps.length) return close();
    index = next;
    const step = steps[index];
    const target = document.querySelector(`[data-tour="${step.anchor}"]`);
    target.scrollIntoView({ block: "nearest" });
    card.replaceChildren();
    const title = document.createElement("div");
    title.style.cssText = "font-weight:600;margin-bottom:4px";
    title.textContent = step.title;
    const text = document.createElement("p");
    text.style.cssText = "font-size:13px;line-height:1.45;margin:0 0 12px";
    text.textContent = step.text;
    const foot = document.createElement("div");
    foot.style.cssText = "display:flex;align-items:center;gap:6px";
    const count = document.createElement("span");
    count.className = "muted";
    count.style.cssText = "font-size:12px;flex:1";
    count.textContent = interpolate(gettext("%(n)s von %(m)s"), { n: index + 1, m: steps.length }, true);
    foot.append(count, button(gettext("Überspringen"), false, close));
    if (index > 0) foot.append(button(gettext("Zurück"), false, () => go(index - 1)));
    foot.append(button(index === steps.length - 1 ? gettext("Fertig") : gettext("Weiter"), true, () => go(index + 1)));
    card.append(title, text, foot);
    card.setAttribute("aria-label", step.title);
    place();
    shown = true;
    card.querySelector(".btn-primary").focus();
  }

  function start() {
    if (card) return;
    steps = all.filter((step) => visible(document.querySelector(`[data-tour="${step.anchor}"]`)));
    if (!steps.length) return;
    spot = document.createElement("div");
    spot.style.cssText = "position:fixed;z-index:70;border-radius:10px;box-shadow:0 0 0 9999px rgb(0 0 0 / .45);pointer-events:none;transition:all .15s";
    card = document.createElement("div");
    card.className = "surface";
    card.setAttribute("role", "dialog");
    card.style.cssText = "position:fixed;z-index:71;padding:14px 16px;box-shadow:0 12px 32px rgb(0 0 0 / .25)";
    document.body.append(spot, card);
    document.addEventListener("keydown", keys, true);
    window.addEventListener("resize", place);
    go(0);
  }

  window.startTour = start;
  document.addEventListener("click", (e) => { if (e.target.closest("[data-tour-start]")) start(); });
  if (config.dataset.auto) window.addEventListener("load", () => setTimeout(start, 300));
})();
