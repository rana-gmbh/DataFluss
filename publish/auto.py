"""Publishing by itself (docs/publish.md "Automatisch"): after changes, once nothing changed for a while, or
every day at a set time.

Changes are read from the sync journal (sync.Journal), which holds every change made here and every change
that arrived from another computer, so an edit on a connected Mac reaches the website once it is synced. The
journal id of the last change a website holds is kept (`Publication.auto_marker`); a publish starts when a
newer one is there and the quiet time has passed.

A publication never publishes by itself:
- before someone published it once by hand (and so confirmed it),
- while it needs a confirmation (the choice or role changed, or the role sees more than was confirmed),
- to a ZIP (there is nobody to hand it to),
- for half an hour after an automatic publish failed.

`tick()` looks at all publications once. On a server the command `publish_auto --loop` runs it every
minute (deploy/start.sh); in the desktop app a thread of the sidecar does, while the database is open.
"""
import logging
import threading
from datetime import datetime, timedelta

from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Publication

log = logging.getLogger(__name__)
RETRY = timedelta(minutes=30)
EVERY_SECONDS = 60
FIRST_SECONDS = 90  # after opening a database, give the app and the sync a moment first
# Changes of these kinds may change any published table: fields, tables, links, files, roles, record pages.
# Records count only for the chosen tables, since they are what changes most.
WATCHED = ("tables.table", "tables.field", "tables.link", "tables.attachment", "core.userrole", "masks.mask", "masks.maskversion")


def marker(publication):
    """(journal id, time) of the last change that may concern the publication; (0, None) without any."""
    from sync.models import Journal
    from tables.models import Record

    records = Record._base_manager.filter(table__key__in=list(publication.tables())).values("pk")
    row = (Journal.objects.filter(Q(entity__in=WATCHED) | Q(entity="tables.record", record_id__in=records))
           .order_by("-id").values_list("id", "at").first())
    return row or (0, None)


def due(publication, now=None):
    """(due, note): whether it should publish now, and if it waits for a person, why (shown on its page)."""
    from . import service

    now = now or timezone.now()
    if publication.auto == Publication.OFF or publication.target == Publication.ZIP or publication.published_at is None:
        return False, ""
    if publication.busy_until and publication.busy_until > now:
        return False, ""
    if service.needs_confirmation(publication):
        return False, _("Wartet auf Bestätigung: Auswahl oder Rolle haben sich geändert, oder die Rolle sieht jetzt mehr.")
    if publication.auto_retry_at and publication.auto_retry_at > now:
        return False, ""
    if publication.auto == Publication.CHANGES:
        last, at = marker(publication)
        return bool(last > publication.auto_marker and at and now - at >= timedelta(minutes=publication.auto_quiet)), ""
    if publication.auto == Publication.DAILY:
        local = timezone.localtime(now)
        planned = timezone.make_aware(datetime.combine(local.date(), publication.auto_time), local.tzinfo)
        return bool(local >= planned and publication.auto_day != local.date()), ""
    return False, ""


def tick(now=None):
    """Publish every publication that is due, one after the other. Returns how many ran."""
    from django.core.exceptions import ValidationError

    from . import service

    ran = 0
    for publication in Publication.objects.exclude(auto=Publication.OFF).exclude(published_at=None):
        try:
            ready, note = due(publication, now)
            if note != publication.auto_note:
                Publication.objects.filter(pk=publication.pk).update(auto_note=note)
            if not ready:
                continue
            service.start(publication, service.PUBLISH, background=False, automatic=True)
            ran += 1
        except ValidationError:
            continue  # it started by hand in the meantime, or needs a confirmation after all
        except Exception:  # noqa: BLE001 - one publication must not stop the others
            log.exception("Automatic publish of %s failed", publication.pk)
    return ran


class Ticker(threading.Thread):
    """Runs `tick` every minute in the desktop app's sidecar while the database is open."""

    def __init__(self):
        super().__init__(daemon=True, name="publish-auto")
        self.stop = threading.Event()

    def run(self):
        if self.stop.wait(FIRST_SECONDS):
            return
        while True:
            try:
                close_old_connections()
                tick()
            except Exception:  # noqa: BLE001 - the next round tries again
                log.exception("Automatic publishing round failed")
            finally:
                close_old_connections()
            if self.stop.wait(EVERY_SECONDS):
                return
