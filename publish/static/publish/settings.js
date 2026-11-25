// Einstellungen → Veröffentlichen (templates/publish/detail.html): the desktop app's folder picker.
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-pick-folder]");
  if (!button) return;
  const res = await fetch("/__client/pick-folder", { method: "POST" }).catch(() => null);
  if (!res || !res.ok) return;
  const { path } = await res.json();
  if (path) button.closest("form").querySelector("[data-folder-input]").value = path;
});
