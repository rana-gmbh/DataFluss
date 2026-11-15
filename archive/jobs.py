"""Backups run in the background, and their state lives in files.

An import replaces the whole database, so nothing about it can be kept there.
"""
import json
import logging
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows, in the desktop client
    fcntl = None
    import msvcrt

from django.conf import settings
from django.db import connection
from django.utils import timezone
from django.utils.text import get_valid_filename
from django.utils.translation import gettext as _, gettext_noop

from core import audit

from . import snapshot
from .container import ContainerError, is_container

log = logging.getLogger(__name__)
STALE_AFTER = 300
UPLOAD_ID = re.compile(r"[0-9a-f]{32}")
NAME = re.compile(r"[\w.-]+" + re.escape(snapshot.EXTENSION))
SPARE_BYTES = 200 * 1024 * 1024
MIN_PASSWORD = 12
UNDONE = gettext_noop("Der Vorgang wurde unterbrochen. Es wurde nichts geändert.")


class Busy(Exception):
    pass


def root():
    path = Path(settings.BACKUP_ROOT)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_file():
    return root() / "job.json"


def _save(job):
    temporary = root() / f"job-{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(job))
    for attempt in range(20):
        try:
            temporary.replace(_state_file())
            return
        except PermissionError:  # Windows, while a request is reading the state file
            if attempt == 19:
                raise
            time.sleep(0.05)


def current():
    try:
        job = json.loads(_state_file().read_text())
    except (FileNotFoundError, ValueError):
        return None
    if job["state"] == "running" and time.time() - job["beat"] > STALE_AFTER:
        job.update(state="failed", message=UNDONE)
    return job


def is_running():
    job = current()
    return bool(job and job["state"] == "running")


def _spawn(target, *args):
    threading.Thread(target=target, args=args, daemon=True).start()


def _run(job, work):
    def progress(step, done=None, total=None):
        job.update(step=step, done=done, total=total, beat=time.time())
        _save(job)

    try:
        job.update(state="done", message=work(progress) or "")
    except ContainerError as exc:
        job.update(state="failed", message=str(exc))
    except Exception:
        log.exception("Backup job failed")
        job.update(state="failed", message=gettext_noop("Das hat nicht geklappt. Es wurde nichts geändert. Details stehen im Serverprotokoll."))
    finally:
        job["beat"] = time.time()
        _save(job)
        connection.close()


@contextmanager
def _locked():
    """Only one process at a time decides whether a job may start (gunicorn runs several)."""
    with (root() / "job.lock").open("w") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
            return
        # Windows: lock the first byte. LK_LOCK tries for ten seconds, then raises OSError.
        msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _claim(kind):
    with _locked():
        if is_running():
            raise Busy
        job = {"kind": kind, "state": "running", "step": gettext_noop("Wird vorbereitet"), "done": None, "total": None, "message": "", "beat": time.time()}
        _save(job)
    return job


def _start(kind, work):
    job = _claim(kind)
    _spawn(_run, job, work)
    return job


def run_now(kind, work):
    """For the nightly command, which is a process of its own and can simply wait."""
    job = _claim(kind)
    _run(job, work)
    return job


def new_name(prefix):
    stamp = timezone.localtime().strftime("%Y-%m-%d-%H%M%S")
    return f"{prefix}-{stamp}{snapshot.EXTENSION}"


def _describe(name, manifest, origin):
    (root() / f"{name}.json").write_text(json.dumps({
        "origin": origin,
        "created_at": manifest["created_at"],
        "school": manifest["school"],
        "version": manifest["version"],
        "people": manifest["rows"].get("people.person", 0),
        "files": manifest["files"],
    }))


def make_container(name, password, origin, progress):
    manifest = snapshot.create(root() / name, password, progress)
    _describe(name, manifest, origin)
    return manifest


def start_backup(password):
    def work(progress):
        make_container(new_name("schulverwaltung"), password, "erstellt", progress)

    return _start("backup", work)


def start_restore(name, password, username):
    def work(progress):
        path = locate(name)
        manifest = snapshot.read_manifest(path, password)

        def keep_current_state():
            make_container(new_name("vor-dem-einspielen"), password, "automatisch", progress)

        snapshot.restore(path, password, manifest, keep_current_state, progress)
        # The accounts have just been replaced, so the name is all that is left of who did this.
        audit.log("backup", f"Sicherung {name} eingespielt von {username}")

    return _start("restore", work)


def locate(name):
    """Only plain names of existing containers, so nothing else in the folder can be reached."""
    if not NAME.fullmatch(name or ""):
        raise FileNotFoundError(name)
    path = root() / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def inspect(name, password):
    manifest = snapshot.read_manifest(locate(name), password)
    _describe(name, manifest, _details(name).get("origin", "hochgeladen"))
    return manifest


def _details(name):
    try:
        return json.loads((root() / f"{name}.json").read_text())
    except (FileNotFoundError, ValueError):
        return {}


def containers():
    rows = []
    for path in root().glob(f"*{snapshot.EXTENSION}"):
        stat = path.stat()
        details = _details(path.name)
        created = datetime.fromisoformat(details["created_at"]) if details else datetime.fromtimestamp(stat.st_mtime, timezone.get_current_timezone())
        rows.append({"name": path.name, "size": stat.st_size, "created": created, "details": details})
    return sorted(rows, key=lambda r: r["created"], reverse=True)


def delete(name):
    path = locate(name)
    path.unlink()
    (root() / f"{name}.json").unlink(missing_ok=True)


def _uploads():
    path = root() / "uploads"
    path.mkdir(exist_ok=True)
    return path


def forget_leftovers():
    """Interrupted uploads and backups leave unfinished files behind."""
    for part in [*_uploads().glob("*.part"), *root().glob("*.part")]:
        if time.time() - part.stat().st_mtime > 24 * 3600:
            part.unlink(missing_ok=True)


class UploadError(Exception):
    pass


def receive_chunk(upload_id, offset, total, chunk):
    """Appends one piece and returns how much has arrived. A repeated piece changes nothing."""
    if not UPLOAD_ID.fullmatch(upload_id or ""):
        raise UploadError(_("Ungültiges Hochladen."))
    part = _uploads() / f"{upload_id}.part"
    size = part.stat().st_size if part.exists() else 0
    if offset != size:
        return size
    if offset == 0:
        if not is_container(chunk.read(8)):
            raise UploadError(_("Das ist keine Sicherungsdatei."))
        chunk.seek(0)
        if shutil.disk_usage(root()).free < total + SPARE_BYTES:
            raise UploadError(_("Auf dem Server ist nicht genug Platz für diese Datei."))
    with part.open("ab") as out:
        for piece in chunk.chunks():
            out.write(piece)
    return part.stat().st_size


def finish_upload(upload_id, total, filename):
    if not UPLOAD_ID.fullmatch(upload_id or ""):
        raise UploadError(_("Ungültiges Hochladen."))
    part = _uploads() / f"{upload_id}.part"
    if not part.is_file() or part.stat().st_size != total:
        raise UploadError(_("Die Datei ist nicht vollständig angekommen."))
    stem = get_valid_filename(Path(filename).name.removesuffix(snapshot.EXTENSION)) or "hochgeladen"
    name, number = f"{stem}{snapshot.EXTENSION}", 1
    while (root() / name).exists():
        number += 1
        name = f"{stem}-{number}{snapshot.EXTENSION}"
    part.replace(root() / name)
    return name
