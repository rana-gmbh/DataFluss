"""Running a publication (docs/publish.md "Ablauf"): build the site and bring a target up to date, in a
thread of its own, because a request of the desktop app ends after 300 seconds and a server's worker earlier.

One run at a time per publication (`busy_until`). The page asks for the state (`Publication.status`,
`progress`) until it is done. Three kinds of run:

    publish   the publication's own target; a ZIP is kept in storage for the download button
    preview   the site in published/.preview/<id>/, opened in the app (publish/views.py preview)
"""
import logging
import threading
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.db import close_old_connections, connections
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _, override

from core import audit

from . import render, snapshot, targets
from .models import Publication

log = logging.getLogger(__name__)
LOCK = timedelta(minutes=30)
PUBLISH, PREVIEW = "publish", "preview"
# Tests run the work in the request: a thread would not see the test's transaction.
IN_THREAD = True


def zip_name(publication):
    return f"published/.zip/{publication.pk}.zip"


def preview_folder(publication):
    return f"published/.preview/{publication.pk}"


def effective_fields(publication):
    """{table key: [field keys]} the publication shows now: its choice as its role sees it."""
    return {table.key: sorted(f.key for f in fields) for table, fields in snapshot.published_fields(publication)}


def confirmed_state(publication):
    """What a confirmation covers: the choice, the role and the fields that result from both."""
    return {"choice": publication.choice or {}, "role": publication.role, "fields": effective_fields(publication)}


def state_hash(state):
    """A short hash of a confirmed state: the confirmation page sends it back, so a list that changed in
    between is not confirmed unseen."""
    import hashlib
    import json

    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:24]


def grown(publication):
    """[(table key, field key or None)] the publication would show now and was not confirmed: a table the
    role came to read, a field it came to see. Empty when nothing grew."""
    confirmed = (publication.published_choice or {}).get("fields")
    if confirmed is None:
        return [(key, None) for key in effective_fields(publication)] or [("", None)]
    out = []
    for table, keys in effective_fields(publication).items():
        if table not in confirmed:
            out.append((table, None))
        else:
            out += [(table, key) for key in keys if key not in confirmed[table]]
    return out


def needs_confirmation(publication):
    """Whether publishing must ask first: never confirmed, the choice or role changed, or the role sees more
    than was confirmed. Fewer fields and new or changed records never ask."""
    stored = publication.published_choice or {}
    if stored.get("choice") != (publication.choice or {}) or stored.get("role") != publication.role:
        return True
    return bool(grown(publication))


def confirm(publication, seen_hash):
    """Store what the person confirmed, if it is still what they saw. Returns False when it changed."""
    state = confirmed_state(publication)
    if state_hash(state) != seen_hash:
        return False
    Publication.objects.filter(pk=publication.pk).update(published_choice=state, auto_note="")
    publication.published_choice = state
    return True


def start(publication, kind, user=None, request=None, background=None, automatic=False):
    """Claim the publication and run `kind`, in a thread unless `background` is False. Raises ValidationError
    when it is busy, or when a publish needs a confirmation first."""
    if kind == PUBLISH and needs_confirmation(publication):
        raise ValidationError(_("Bitte zuerst bestätigen, was veröffentlicht wird."))
    now = timezone.now()
    claimed = Publication.objects.filter(pk=publication.pk).filter(Q(busy_until=None) | Q(busy_until__lt=now)).update(
        busy_until=now + LOCK, status=Publication.RUNNING, progress=_("Wird vorbereitet …"), error="")
    if not claimed:
        raise ValidationError(_("Diese Veröffentlichung läuft gerade."))
    if kind == PUBLISH:
        how = "automatisch" if automatic else "von Hand"
        audit.log("publish", f"Veröffentlichung {publication.name} gestartet ({publication.target}, {how})", request=request, user=user,
                  target=publication.pk)
    if IN_THREAD if background is None else background:
        threading.Thread(target=_thread, args=(publication.pk, kind, automatic), daemon=True, name=f"publish-{publication.pk}").start()
    else:
        run(publication.pk, kind, automatic)


def _thread(pk, kind, automatic):
    close_old_connections()
    try:
        run(pk, kind, automatic)
    finally:
        connections.close_all()


def _say(pk):
    def say(text):
        Publication.objects.filter(pk=pk).update(progress=str(text)[:200], busy_until=timezone.now() + LOCK)
    return say


def run(pk, kind, automatic=False):
    from . import auto

    publication = Publication.objects.get(pk=pk)
    say = _say(pk)
    marker = auto.marker(publication)[0]  # changes after this point are published next time
    # A publish shows at most what was confirmed, even if the role came to see more since (needs_confirmation
    # stops that before, this holds when it changes during the run). A preview shows what it would be.
    confirmed = (publication.published_choice or {}).get("fields", {}) if kind == PUBLISH else None
    try:
        with override(publication.language):
            site = render.build(publication, progress=lambda step: say(_progress(step)), confirmed=confirmed)
        if kind == PREVIEW:
            target = targets.StorageTarget(preview_folder(publication))
            sent, gone = targets.send(publication, site, target, progress=say, old=targets.manifest_at(target))
            done = _("Vorschau fertig: %(n)s Dateien.") % {"n": len(site.files)}
        elif publication.target == Publication.ZIP:
            say(_("ZIP wird gepackt …"))
            with targets.zip_file(site) as packed:
                default_storage.delete(zip_name(publication))
                default_storage.save(zip_name(publication), File(packed))
            sent, gone = len(site.files), 0
            done = _("ZIP fertig: %(n)s Dateien.") % {"n": len(site.files)}
        else:
            target = targets.target_for(publication)
            sent, gone = targets.send(publication, site, target, progress=say)
            targets.remember_host_key(publication, target)
            done = _("Veröffentlicht: %(sent)s Dateien übertragen, %(gone)s entfernt.") % {"sent": sent, "gone": gone}
        fields = {"status": Publication.DONE, "progress": done, "error": "", "busy_until": None}
        if kind == PUBLISH:
            fields.update(published_at=timezone.now(), manifest={"target": targets.fingerprint(publication), "files": site.files},
                          auto_marker=marker, auto_retry_at=None, auto_note="")
            if automatic and publication.auto == Publication.DAILY:
                fields["auto_day"] = timezone.localdate()
            # What was shown is confirmed from now on; when the role sees less, it shrinks, so seeing more
            # again asks again.
            shown = effective_fields(publication)
            confirmed_fields = (publication.published_choice or {}).get("fields", {})
            fields["published_choice"] = {**(publication.published_choice or {}), "fields": {
                t: [k for k in keys if k in set(confirmed_fields.get(t) or [])] for t, keys in shown.items() if t in confirmed_fields}}
            audit.log("publish", f"Veröffentlichung {publication.name}: {sent} Dateien übertragen, {gone} entfernt", target=publication.pk)
        Publication.objects.filter(pk=pk).update(**fields)
    except snapshot.Unsafe as exc:
        log.error("Publication %s stopped by its check: %s", pk, exc)
        _failed(pk, _("Angehalten: Die Prüfung hat Daten gefunden, die nicht veröffentlicht werden dürfen. Nichts wurde übertragen."), automatic)
    except ValidationError as exc:
        _failed(pk, " ".join(exc.messages), automatic)
    except Exception as exc:  # noqa: BLE001 - shown on the page, logged with its trace
        log.exception("Publication %s failed", pk)
        _failed(pk, _("Fehler: %(error)s") % {"error": exc}, automatic)


def _failed(pk, message, automatic=False):
    fields = {"status": Publication.FAILED, "error": str(message)[:2000], "progress": "", "busy_until": None}
    if automatic:
        from . import auto

        fields["auto_retry_at"] = timezone.now() + auto.RETRY  # a target that is down is not asked every minute
    Publication.objects.filter(pk=pk).update(**fields)


def _progress(step):
    if step == "data":
        return _("Daten werden gelesen …")
    if step == "pages":
        return _("Seiten werden erstellt …")
    if step.startswith("files "):
        return _("Bilder und Dateien: %(n)s") % {"n": step.removeprefix("files ")}
    return step


def withdraw(publication, request=None):
    """Take a publication off this server (/p/<slug>/) and drop its preview and ZIP."""
    if publication.target == Publication.SERVER:
        target = targets.ServerTarget(publication)
        target.remove_all({**targets.manifest_at(target), **((publication.manifest or {}).get("files") or {})})
    clean_local(publication)
    Publication.objects.filter(pk=publication.pk).update(manifest={}, published_at=None, published_choice=None, status=Publication.IDLE,
                                                        progress="", error="", auto_marker=0, auto_note="")
    audit.log("publish", f"Veröffentlichung {publication.name} zurückgezogen", request=request, target=publication.pk)


def clean_local(publication):
    """The preview, the ZIP and the picture cache of a publication."""
    default_storage.delete(zip_name(publication))
    target = targets.StorageTarget(preview_folder(publication))
    target.remove_all(targets.manifest_at(target))
    from .render import CACHE

    folder = f"{CACHE}/{publication.pk}"
    try:
        _dirs, names = default_storage.listdir(folder)
    except (FileNotFoundError, NotImplementedError, OSError):
        names = []
    for name in names:
        default_storage.delete(f"{folder}/{name}")
