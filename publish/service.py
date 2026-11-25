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


def confirmed_state(publication):
    """What a confirmation covers: the choice and the role. Changing either asks again before publishing."""
    return {"choice": publication.choice or {}, "role": publication.role}


def needs_confirmation(publication):
    return publication.published_choice != confirmed_state(publication)


def start(publication, kind, user=None, request=None):
    """Claim the publication and run `kind` in the background. Raises ValidationError when it is busy."""
    now = timezone.now()
    claimed = Publication.objects.filter(pk=publication.pk).filter(Q(busy_until=None) | Q(busy_until__lt=now)).update(
        busy_until=now + LOCK, status=Publication.RUNNING, progress=_("Wird vorbereitet …"), error="")
    if not claimed:
        raise ValidationError(_("Diese Veröffentlichung läuft gerade."))
    if kind == PUBLISH:
        Publication.objects.filter(pk=publication.pk).update(published_choice=confirmed_state(publication))
        audit.log("publish", f"Veröffentlichung {publication.name} gestartet ({publication.target})", request=request, user=user, target=publication.pk)
    if IN_THREAD:
        threading.Thread(target=_thread, args=(publication.pk, kind), daemon=True, name=f"publish-{publication.pk}").start()
    else:
        run(publication.pk, kind)


def _thread(pk, kind):
    close_old_connections()
    try:
        run(pk, kind)
    finally:
        connections.close_all()


def _say(pk):
    def say(text):
        Publication.objects.filter(pk=pk).update(progress=str(text)[:200], busy_until=timezone.now() + LOCK)
    return say


def run(pk, kind):
    publication = Publication.objects.get(pk=pk)
    say = _say(pk)
    try:
        with override(publication.language):
            site = render.build(publication, progress=lambda step: say(_progress(step)))
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
            done = _("Veröffentlicht: %(sent)s Dateien übertragen, %(gone)s entfernt.") % {"sent": sent, "gone": gone}
        fields = {"status": Publication.DONE, "progress": done, "error": "", "busy_until": None}
        if kind == PUBLISH:
            fields.update(published_at=timezone.now(), manifest={"target": targets.fingerprint(publication), "files": site.files})
            audit.log("publish", f"Veröffentlichung {publication.name}: {sent} Dateien übertragen, {gone} entfernt", target=publication.pk)
        Publication.objects.filter(pk=pk).update(**fields)
    except snapshot.Unsafe as exc:
        log.error("Publication %s stopped by its check: %s", pk, exc)
        _failed(pk, _("Angehalten: Die Prüfung hat Daten gefunden, die nicht veröffentlicht werden dürfen. Nichts wurde übertragen."))
    except ValidationError as exc:
        _failed(pk, " ".join(exc.messages))
    except Exception as exc:  # noqa: BLE001 - shown on the page, logged with its trace
        log.exception("Publication %s failed", pk)
        _failed(pk, _("Fehler: %(error)s") % {"error": exc})


def _failed(pk, message):
    Publication.objects.filter(pk=pk).update(status=Publication.FAILED, error=str(message)[:2000], progress="", busy_until=None)


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
                                                        progress="", error="")
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
