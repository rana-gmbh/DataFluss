"""The backup plan of a database in the desktop client: a container in a folder of the user's choice.

A server backs up at night with BACKUP_PASSWORD (nightly.py). A database on a computer has no such
setting, so it keeps its plan in two files beside its backups: `plan.json` (folder, interval, how many
to keep, the password encrypted with the database's vault key) and `plan-last.json` (the last result).
Files and not the database, because a restore replaces the database and the plan must survive it.

The sidecar runs the plan while the database is open (Scheduler): when it opens and then every
quarter of an hour, if the last good backup is older than the interval. The start screen reads both
files (without the password) to say when a backup is overdue.
"""
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _, gettext_noop

from core import audit
from vault import crypto

from . import jobs, snapshot
from .container import ContainerError

log = logging.getLogger(__name__)
EVERY = {"day": timedelta(days=1), "week": timedelta(days=7)}
KEEP_CHOICES = (3, 7, 14, 30)
DEFAULT_KEEP = 7
CHECK_SECONDS = 15 * 60
FIRST_CHECK_SECONDS = 60
RETRY_AFTER = timedelta(hours=1)
PASSWORD_CONTEXT = "backup-plan"


def is_client():
    """A database of the desktop client (the sidecar sets its name)."""
    return bool(os.environ.get("SCHULE_CLIENT_DB_NAME"))


def is_connected():
    """A copy of a server's data: the server backs it up, and it holds only its user's part anyway."""
    return bool(os.environ.get("SYNC_TOKEN"))


def is_demo():
    """The shell starts a demo database, invented people only, with ALLOW_DEMO_DATA."""
    return os.environ.get("ALLOW_DEMO_DATA") == "1"


def applies():
    """Only a database of its own on a computer needs a plan; invented people need none."""
    return is_client() and not is_connected() and not is_demo()


def _plan_file():
    return jobs.root() / "plan.json"


def _last_file():
    return jobs.root() / "plan-last.json"


def _read(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _write(path, data):
    temporary = path.with_name(f"{path.name}-{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data))
    temporary.replace(path)


def load():
    """The plan without its password, or {}."""
    plan = _read(_plan_file())
    return {k: v for k, v in plan.items() if k != "password"} if plan.get("folder") else {}


def last():
    return _read(_last_file())


def is_set_up():
    return bool(load())


def _password():
    return crypto.decrypt(_read(_plan_file()).get("password", ""), PASSWORD_CONTEXT)


def save(folder, every, keep, password=""):
    """Sets the plan up or changes it. Without a new password the stored one stays."""
    folder = Path(folder or "").expanduser()
    if not folder.is_absolute():
        raise ValueError(_("Bitte einen Ordner wählen."))
    if every not in EVERY:
        raise ValueError(_("Bitte angeben, wie oft gesichert werden soll."))
    if keep not in KEEP_CHOICES:
        raise ValueError(_("Bitte angeben, wie viele Sicherungen bleiben sollen."))
    if password and len(password) < jobs.MIN_PASSWORD:
        raise ValueError(_("Das Passwort braucht mindestens %(n)s Zeichen.") % {"n": jobs.MIN_PASSWORD})
    stored = _read(_plan_file()).get("password", "")
    if not password and not stored:
        raise ValueError(_("Bitte ein Passwort für die Sicherungen festlegen."))
    problem = _folder_problem(folder)
    if problem:
        raise ValueError(problem)
    _write(_plan_file(), {
        "folder": str(folder), "every": every, "keep": keep,
        "password": crypto.encrypt(password, PASSWORD_CONTEXT) if password else stored,
        "prefix": _prefix(),
    })


def turn_off():
    _plan_file().unlink(missing_ok=True)


def _prefix():
    """Names the files of this database, so two databases can share a folder and keep their own count."""
    name = slugify(os.environ.get("SCHULE_CLIENT_DB_NAME", "")) or "datenbank"
    node = settings.SERVER_ID.removeprefix("client-")
    return f"{name}-{node}"[:80]


def _folder_problem(folder):
    if not folder.is_dir():
        return _("Der Ordner %(folder)s ist nicht erreichbar. Ist das Laufwerk angeschlossen?") % {"folder": folder}
    probe = folder / f".datafluss-{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return _("In den Ordner %(folder)s darf DataFluss nicht schreiben.") % {"folder": folder}
    if Path(settings.BACKUP_ROOT).resolve() in (folder.resolve(), *folder.resolve().parents):
        return _("Bitte einen Ordner außerhalb der Datenbank wählen, am besten auf einem anderen Laufwerk.")
    return ""


def _own_files(folder, prefix):
    pattern = re.compile(re.escape(prefix) + r"-\d{4}-\d{2}-\d{2}-\d{6}" + re.escape(snapshot.EXTENSION))
    return sorted(p for p in folder.iterdir() if pattern.fullmatch(p.name))


def is_due(now=None):
    plan = load()
    if not plan:
        return False
    now = now or timezone.now()
    result = last()
    if result.get("state") == "failed" and result.get("at") and now - datetime.fromisoformat(result["at"]) < RETRY_AFTER:
        return False
    good = result.get("ok_at")
    return not good or now - datetime.fromisoformat(good) >= EVERY[plan["every"]] - timedelta(minutes=30)


def run():
    """Makes one backup into the folder, reads it back, and drops the oldest beyond the plan's count.
    Returns the result, which the page and the start screen show. For the scheduler, which can wait."""
    if not load():
        return {}
    try:
        job = jobs._claim("backup")
    except jobs.Busy:
        return {}
    return _execute(job)


def start():
    """The page's "Jetzt sichern": the job is claimed before the answer, then runs in the background."""
    job = jobs._claim("backup")
    jobs._spawn(_execute, job)
    return job


def _execute(job):
    plan = _read(_plan_file())
    previous = last()
    outcome = {}

    def work(progress):
        folder = Path(plan["folder"])
        problem = _folder_problem(folder)
        if problem:
            raise ContainerError(problem)
        password = _password()
        stamp = timezone.localtime().strftime("%Y-%m-%d-%H%M%S")
        target = folder / f"{plan['prefix']}-{stamp}{snapshot.EXTENSION}"
        snapshot.create(target, password, progress)
        try:
            snapshot.verify(target, password, progress)
        except ContainerError:
            target.unlink(missing_ok=True)
            raise ContainerError(gettext_noop("Die neue Sicherung ließ sich nicht vollständig lesen und wurde verworfen."))
        for old in _own_files(folder, plan["prefix"])[:-plan["keep"]]:
            old.unlink(missing_ok=True)
        outcome.update(name=target.name, size=target.stat().st_size)
        return gettext_noop("Die Sicherung liegt geprüft im gewählten Ordner.")

    jobs._run(job, work)
    now = timezone.now()
    result = {**outcome, "state": "ok" if job["state"] == "done" else "failed", "at": now.isoformat(),
              "ok_at": now.isoformat() if job["state"] == "done" else previous.get("ok_at", "")}
    if result["state"] == "failed":
        result["problem"] = job["message"]
        audit.log("backup", f"Regelmäßige Sicherung fehlgeschlagen: {job['message']}"[:200])
    else:
        audit.log("backup", f"Regelmäßige Sicherung {result['name']} erstellt und geprüft")
    _write(_last_file(), result)
    return result


def _day(moment):
    moment = timezone.localtime(datetime.fromisoformat(moment))
    return _("%(date)s, %(time)s Uhr") % {"date": moment.strftime("%d.%m.%Y"), "time": moment.strftime("%H:%M")}


def summary():
    """Level ok, warn or bad, and one sentence, as nightly.summary() for a server."""
    plan = load()
    if not plan:
        return {"level": "warn", "text": _("Für diese Datenbank ist keine regelmäßige Sicherung eingerichtet. "
                                           "Geht dieser Computer verloren, sind die Daten weg.")}
    result = last()
    problem = _(result["problem"]) if result.get("problem") else ""
    if result.get("state") == "failed":
        return {"level": "bad", "text": _("Die Sicherung vom %(day)s ist fehlgeschlagen: %(problem)s") % {"day": _day(result["at"]), "problem": problem}}
    if not result.get("ok_at"):
        return {"level": "warn", "text": _("Die regelmäßige Sicherung ist eingerichtet. Die erste entsteht in den nächsten Minuten.")}
    if timezone.now() - datetime.fromisoformat(result["ok_at"]) > EVERY[plan["every"]] + timedelta(days=1):
        return {"level": "bad", "text": _("Die letzte Sicherung ist vom %(day)s. Sie entsteht nur, während die Datenbank geöffnet ist.") % {"day": _day(result["ok_at"])}}
    return {"level": "ok", "text": _("Letzte Sicherung: %(day)s, geprüft, in %(folder)s.") % {"day": _day(result["ok_at"]), "folder": plan["folder"]}}


class Scheduler(threading.Thread):
    """Runs in the sidecar of a database of its own while it is open: the plan when it is due, and the
    modules' nightly work (such as emptying recycle bins) once a day, as deploy/start.sh does on a server.
    A copy of a server leaves both to the server."""

    def __init__(self):
        super().__init__(daemon=True, name="backup-plan")
        self.stop = threading.Event()
        self.bins_day = ""

    def tick(self):
        from . import nightly

        today = timezone.localdate().isoformat()
        if self.bins_day != today:
            self.bins_day = today
            nightly.empty_bins()
        if is_due():
            run()

    def run(self):
        if self.stop.wait(FIRST_CHECK_SECONDS):
            return
        while True:
            try:
                close_old_connections()
                self.tick()
            except Exception:  # noqa: BLE001 - the next round tries again; the result file says what went wrong
                log.exception("Backup plan round failed")
            finally:
                close_old_connections()
            if self.stop.wait(CHECK_SECONDS):
                return
