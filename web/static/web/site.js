// The template site's only script: a star picker over the plain select (which works without script too), and
// questions before buttons marked data-confirm. No inline script: the content policy forbids it.
(function () {
  function starPicker(box) {
    const select = box.querySelector("select");
    if (!select) return;
    const picker = document.createElement("div");
    picker.className = "star-picker";
    picker.setAttribute("role", "slider");
    picker.setAttribute("tabindex", "0");
    picker.setAttribute("aria-valuemin", "0.5");
    picker.setAttribute("aria-valuemax", "5");
    picker.setAttribute("aria-label", select.labels[0] ? select.labels[0].textContent : "Sterne");
    const stars = [];
    for (let i = 1; i <= 5; i++) {
      const button = document.createElement("button");
      button.type = "button";
      button.tabIndex = -1;
      button.setAttribute("aria-hidden", "true");
      button.addEventListener("click", (event) => {
        const rect = button.getBoundingClientRect();
        set(event.clientX - rect.left < rect.width / 2 ? i - 0.5 : i);
      });
      button.addEventListener("mousemove", (event) => {
        const rect = button.getBoundingClientRect();
        draw(event.clientX - rect.left < rect.width / 2 ? i - 0.5 : i);
      });
      stars.push(button);
      picker.append(button);
    }
    const out = document.createElement("output");
    picker.append(out);
    picker.addEventListener("mouseleave", () => draw(value()));
    picker.addEventListener("keydown", (event) => {
      const now = value() || 0;
      if (event.key === "ArrowRight" || event.key === "ArrowUp") set(Math.min(5, now + 0.5));
      else if (event.key === "ArrowLeft" || event.key === "ArrowDown") set(Math.max(0.5, now - 0.5));
      else if (event.key === "Home") set(0.5);
      else if (event.key === "End") set(5);
      else return;
      event.preventDefault();
    });
    function value() { return parseFloat(select.value) || 0; }
    function draw(v) {
      stars.forEach((star, index) => {
        star.classList.toggle("full", v >= index + 1);
        star.classList.toggle("half", v === index + 0.5);
      });
      out.textContent = v ? String(v).replace(".", ",") : "";
    }
    function set(v) {
      select.value = String(v);  // the options are 0.5, 1, 1.5 … as the server writes them
      picker.setAttribute("aria-valuenow", String(v));
      draw(v);
    }
    select.hidden = true;
    box.append(picker);
    draw(value());
  }
  document.querySelectorAll("[data-star-input]").forEach(starPicker);
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-confirm]");
    if (button && !window.confirm(button.dataset.confirm)) event.preventDefault();
  });
})();
