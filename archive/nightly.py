"""The automatic backup. Runs at night, proves that the file is complete, then copies it off this server."""
import json
import logging
import shutil
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _

from core import audit

from . import jobs, offsite, snapshot
from .container import ContainerError

log = logging.getLogger(__name__)
PREFIX = "nacht"
FIRST_HOUR = 2
KEEP_HERE = 7
KEEP_ELSEWHERE = 30
TOO_OLD = timedelta(hours=36)


def _state_file():
    return jobs.root() / "nightly.json"


def last():
    try:
        return json.loads(_state_file().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save(result):
    temporary = jobs.root() / f"nightly-{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(result))
    temporary.replace(_state_file())


def is_set_up():
    return len(settings.BACKUP_PASSWORD) >= jobs.MIN_PASSWORD


def is_due():
    """Asked every hour. A failed night tries again in the next hour."""
    now = timezone.localtime()
    previous = last()
    finished = previous.get("state") == "ok" and previous.get("offsite") != "failed"
    done_today = finished and previous.get("day") == now.date().isoformat()
    return is_set_up() and now.hour >= FIRST_HOUR and not done_today


def _needed_space():
    return last().get("size", 0) + jobs.SPARE_BYTES


def _forget_old():
    mine = [row["name"] for row in jobs.containers() if row["name"].startswith(f"{PREFIX}-")]
    for name in sorted(mine, reverse=True)[KEEP_HERE:]:
        jobs.delete(name)


def _waiting_for_copy():
    """Tonight's good file, if only its copy failed. A new one every hour would push out the older nights."""
    previous = last()
    today = timezone.localtime().date().isoformat()
    if previous.get("state") == "ok" and previous.get("offsite") == "failed" and previous.get("day") == today:
        if (jobs.root() / previous["name"]).is_file():
            return previous["name"]
    return ""


def _new_file(progress):
    if shutil.disk_usage(jobs.root()).free < _needed_space():
        raise ContainerError(_("Auf dem Server ist nicht genug Platz für die Sicherung."))
    name = jobs.new_name(PREFIX)
    jobs.make_container(name, settings.BACKUP_PASSWORD, "automatisch", progress)
    try:
        snapshot.verify(jobs.root() / name, settings.BACKUP_PASSWORD, progress)
    except ContainerError:
        jobs.delete(name)
        raise ContainerError(_("Die neue Sicherung ließ sich nicht vollständig lesen und wurde verworfen."))
    return name


def empty_bins():
    """The modules' nightly work, such as emptying their recycle bins (core/modules.py nightly)."""
    from core import modules

    for job in modules.hooks("nightly"):
        job()


def run():
    """Returns the result, which the backup page shows too."""
    outcome = {"offsite": "on" if offsite.enabled() else "off"}

    def work(progress):
        if not is_set_up():
            raise ContainerError(_("BACKUP_PASSWORD fehlt oder hat weniger als %(n)s Zeichen.") % {"n": jobs.MIN_PASSWORD})
        name = _waiting_for_copy() or _new_file(progress)
        path = jobs.root() / name
        outcome.update(name=name, size=path.stat().st_size)
        _forget_old()
        if offsite.enabled():
            try:
                offsite.copy(path, progress)
                offsite.prune(KEEP_ELSEWHERE)
                outcome["offsite"] = "ok"
            except Exception as exc:  # The file on this server is good, so the night still counts.
                log.exception("Offsite copy failed")
                outcome.update(offsite="failed", problem=str(exc)[:300])

    try:
        job = jobs.run_now("backup", work)
    except jobs.Busy:
        return {}
    now = timezone.localtime()
    result = {**outcome, "state": "ok" if job["state"] == "done" else "failed", "day": now.date().isoformat(), "at": now.isoformat()}
    if result["state"] == "failed":
        result["problem"] = job["message"]
    _save(result)
    if result["state"] != "ok" or result["offsite"] == "failed":
        audit.log("backup", f"Automatische Sicherung fehlgeschlagen: {result.get('problem', '')}"[:200])
    else:
        audit.log("backup", f"Automatische Sicherung {result['name']} erstellt und geprüft")
    return result


def summary():
    """What the backup page says: level ok, warn or bad, and one sentence."""
    from . import plan

    if plan.is_client():  # a database on a computer: its own plan, or the server's backups for a copy
        if plan.applies():
            return plan.summary()
        if plan.is_connected():
            return {"level": "ok", "text": _("Diese Datenbank ist eine Kopie vom Server. Gesichert wird auf dem Server.")}
        return {"level": "ok", "text": _("Eine Demo mit erfundenen Personen braucht keine Sicherung.")}
    if not is_set_up():
        return {"level": "warn", "text": _("Die automatische Sicherung ist nicht eingerichtet: BACKUP_PASSWORD fehlt.")}
    result = last()
    if not result:
        return {"level": "warn", "text": _("Es gibt noch keine automatische Sicherung. Die erste entsteht ab 2 Uhr nachts.")}
    when = datetime.fromisoformat(result["at"])
    moment = timezone.localtime(when)
    day = _("%(date)s, %(time)s Uhr") % {"date": moment.strftime("%d.%m.%Y"), "time": moment.strftime("%H:%M")}
    problem = _(result["problem"]) if result.get("problem") else ""
    if result["state"] != "ok":
        return {"level": "bad", "text": _("Die automatische Sicherung vom %(day)s ist fehlgeschlagen: %(problem)s") % {"day": day, "problem": problem}}
    if timezone.now() - when > TOO_OLD:
        return {"level": "bad", "text": _("Die letzte automatische Sicherung ist vom %(day)s. Seitdem ist keine neue entstanden.") % {"day": day}}
    if result["offsite"] == "failed":
        return {"level": "bad", "text": _("Die Sicherung vom %(day)s liegt nur auf diesem Server, die Kopie außer Haus ist fehlgeschlagen: %(problem)s") % {"day": day, "problem": problem}}
    if result["offsite"] == "off":
        return {"level": "warn", "text": _("Letzte automatische Sicherung: %(day)s, geprüft. Sie liegt nur auf diesem Server, weil keine Kopie außer Haus eingerichtet ist.") % {"day": day}}
    return {"level": "ok", "text": _("Letzte automatische Sicherung: %(day)s, geprüft und außer Haus kopiert.") % {"day": day}}
