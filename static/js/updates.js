(function () {
  const source = document.getElementById("update-status");
  if (!source || !document.getElementById("u-apply")) return;
  const $ = (id) => document.getElementById(id);
  const STEPS = { queued: gettext("In der Warteschlange von Coolify"), in_progress: gettext("Neue Version wird gebaut und geprüft") };
  const RESULTS = { ok: "badge-ok", failed: "badge-bad" };
  let status = JSON.parse(source.textContent);
  let working = false;
  const startedVersion = status.app_version;

  const time = (iso) => (iso ? new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "–");
  const commit = (c) => (c ? `<span class="mono">${App.esc(c.sha)}</span> <span class="muted">${App.esc(c.subject)}</span>` : "–");

  function render() {
    const running = status.state === "running";
    $("u-current").innerHTML = `<span class="mono">${App.esc(status.app_version)}</span>`;
    $("u-latest").innerHTML = status.check_error ? `<span class="text-[var(--bad)]">${App.esc(status.check_error)}</span>` : commit(status.latest);
    $("u-checked").textContent = time(status.checked_at);

    const apply = $("u-apply");
    const unknown = !status.latest;
    apply.disabled = running || working || (!unknown && !status.behind);
    apply.lastChild.textContent = status.behind ? interpolate(gettext("Jetzt aktualisieren (%(n)s)"), { n: status.behind }, true) : unknown ? gettext("Neueste Version installieren") : gettext("Ist aktuell");
    $("u-check").disabled = running || working;

    const progress = $("u-progress");
    progress.classList.toggle("hidden", !running);
    if (running) progress.innerHTML = `<p class="text-[13px] font-semibold">… ${App.esc(STEPS[status.step] || status.step)}</p><p class="muted text-[12px]">${App.esc(gettext("Das dauert meist ein bis drei Minuten. Sie können weiterarbeiten."))}</p>`;

    const result = $("u-result");
    const show = !running && !!RESULTS[status.result] && !!status.message;
    result.classList.toggle("hidden", !show);
    if (show) {
      result.className = `rounded-lg px-3 py-2 text-[13px] ${RESULTS[status.result]}`;
      result.textContent = status.message;
    }

    const restore = $("u-restore");
    restore.classList.toggle("hidden", !status.can_restore);
    restore.disabled = working;

    $("u-commits").innerHTML = (status.commits || []).length
      ? status.commits.map((c) => `<div class="px-4 py-2 border-b last:border-0 text-[13px]" style="border-color: var(--line)"><div>${App.esc(c.subject)}</div><div class="muted text-[12px]"><span class="mono">${App.esc(c.sha)}</span> · ${time(c.date)}</div></div>`).join("")
      : `<p class="muted text-[13px] p-4">${App.esc(gettext("Keine neuen Änderungen."))}</p>`;
    const logs = $("u-logs");
    logs.classList.toggle("hidden", !status.deployment_url);
    if (status.deployment_url) logs.href = status.deployment_url;
  }

  // Right after the switch the proxy can still pick the old, stopped container.
  async function reloadWhenStable() {
    let good = 0;
    for (let tries = 0; good < 4 && tries < 40; tries++) {
      await new Promise((done) => setTimeout(done, 1500));
      const res = await fetch(location.href, { cache: "no-store" }).catch(() => null);
      good = res?.ok ? good + 1 : 0;
    }
    location.reload();
  }

  async function poll() {
    try {
      const res = await fetch("/einstellungen/aktualisierung/status/", { headers: { "X-Requested-With": "fetch" } });
      if (res.ok && !res.redirected) {
        status = await res.json();
        // A new version brings new scripts and styles.
        if (status.app_version !== startedVersion && status.state !== "running") { reloadWhenStable(); return; }
        render();
      }
    } catch (e) { /* Possible for a moment while containers are switched. */ }
    if (status.state === "running") setTimeout(poll, 3000);
  }

  async function send(action) {
    working = true;
    render();
    const res = await App.post("/einstellungen/aktualisierung/starten/", { action });
    working = false;
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      App.toast(body.error || gettext("Das hat nicht geklappt."), { ms: 6000 });
      render();
      return;
    }
    status = body;
    render();
    if (status.state === "running") setTimeout(poll, 2000);
  }

  $("u-check").addEventListener("click", () => send("check"));
  $("u-restore").addEventListener("click", async () => {
    if (await App.ask(interpolate(gettext("Datenbank auf den Stand von %(when)s zurücksetzen? Alles, was seitdem gespeichert wurde, geht verloren."), { when: status.restore_to }, true))) send("restore");
  });
  $("u-apply").addEventListener("click", async () => {
    if (await App.ask(gettext("Jetzt aktualisieren? Vorher wird die Datenbank gesichert."))) send("apply");
  });
  render();
  if (status.state === "running") poll();
})();
