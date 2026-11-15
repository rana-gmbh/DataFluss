// Right-click menu of the list of masks, with renaming in the language on screen.
(function () {
  ContextMenu.register("tr[data-mask]", (row) => {
    const d = row.dataset;
    return [
      { label: gettext("Gestalten"), icon: "pen", href: d.builder },
      { label: gettext("In neuem Fenster öffnen"), icon: "external", newWindow: d.builder },
      { label: gettext("Umbenennen …"), icon: "type", run: () => Names.renameAndPost(d.action, d.name, d.hasNames === "1", { action: "rename", next: "list" }, gettext("Name der Maske")) },
      d.isDefault !== "1" && "-",
      d.isDefault !== "1" && { label: gettext("Maske löschen"), icon: "trash", danger: true, confirm: gettext("Maske löschen?"), post: d.action, data: { action: "delete" } },
    ];
  });
})();
