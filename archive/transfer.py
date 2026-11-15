"""A database of the desktop client moves to a server, and a server's full backup comes to a computer.

Transfer (phase 6 of docs/DATAFLUSS-MODULES.md in the private repository): the computer's own database
becomes the server's data. On the computer, an IT person enters the server's address, their account
on the server (password and code) and a password for the backup. The server signs them in, says
whether it holds data already and hands out a ticket. The computer packs its database into a
container, uploads it in pieces and asks the server to restore it, which first backs up the server's
own state. Afterwards the server has the computer's data and accounts; the computer's database stays
as it was and the person connects anew, so there is no forked history.

Full backup: a connected copy holds only what its user may see. IT can still fetch the server's
complete backup onto the computer, with the account's password and code again (a device token alone
is not enough to carry everything away).

Server side: the views api_* under /sync/v1/ (no session; a signed ticket after the sign-in).
Computer side: check() and start() for the backup page, run in the sidecar.
"""
import json
import logging
import time
import uuid
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.http import FileResponse, JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_noop
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core import audit, updates

from . import jobs, snapshot
from .container import ContainerError

log = logging.getLogger(__name__)
CHUNK = 8 * 1024 * 1024
TICKET_SECONDS = 3 * 3600
RETRIES = 5
POLL_SECONDS = 2
SALT = "datafluss.transfer"


# ------------------------------------------------------------------ server

def _refuse(status, message, **extra):
    return JsonResponse({"error": message, **extra}, status=status)


def _json(request):
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return None


def holdings():
    """What a server holds already, as the confirmation names it."""
    from core.models import User
    from devices.models import Asset
    from people.models import Person
    from vault.models import Credential

    return {"people": Person._base_manager.count(), "assets": Asset._base_manager.count(),
            "credentials": Credential._base_manager.count(), "users": User.objects.count()}


def has_data(counts=None):
    """A fresh server has its first IT account (create_admin) and nothing else."""
    counts = counts or holdings()
    return bool(counts["people"] or counts["assets"] or counts["credentials"] or counts["users"] > 1)


def _ticket(kind, **values):
    return signing.dumps({"k": kind, **values}, salt=SALT)


def _read_ticket(request, kind):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        data = signing.loads(auth[7:].strip(), salt=SALT, max_age=TICKET_SECONDS)
    except signing.BadSignature:
        return None
    return data if data.get("k") == kind else None


def _job_state():
    job = jobs.current() or {}
    return {"kind": job.get("kind", ""), "state": job.get("state", ""), "step": _(job["step"]) if job.get("step") else "",
            "done": job.get("done"), "total": job.get("total"), "message": _(job["message"]) if job.get("message") else ""}


def _signed_in_it(request, data, purpose):
    from sync.views import _sign_in

    user, refusal = _sign_in(request, data, purpose)
    if refusal:
        return None, refusal
    if not user.can("admin.backup"):
        return None, _refuse(403, _("Dieses Konto darf auf dem Server keine Sicherungen einspielen. Dafür braucht es die Rolle IT."))
    return user, None


@csrf_exempt
@require_http_methods(["POST"])
def api_transfer_start(request):
    """Signs the person in and checks that the server can take the database: returns a ticket and what the server holds."""
    data = _json(request)
    if data is None:
        return _refuse(400, _("Ungültige Anfrage."))
    user, refusal = _signed_in_it(request, data, "Anmeldung für eine Übertragung")
    if refusal:
        return refusal
    if updates.blocks_restore():
        return _refuse(409, _("Auf dem Server läuft gerade eine Aktualisierung. Bitte später noch einmal."))
    if jobs.is_running():
        return _refuse(409, _("Auf dem Server läuft gerade ein Sicherungsvorgang. Bitte später noch einmal."))
    outline = data.get("outline") or {}
    try:
        problem = snapshot.incompatible(outline) if outline else _("Ungültige Anfrage.")
    except (KeyError, TypeError, ValueError):
        problem = _("Ungültige Anfrage.")
    if problem:
        return _refuse(409, problem)
    counts = holdings()
    audit.log("backup", f"Übertragung einer Datenbank vorbereitet von {user.username}", request=request, user=user)
    return JsonResponse({"ticket": _ticket("transfer", u=str(user.pk), id=uuid.uuid4().hex, name=user.username),
                         "has_data": has_data(counts), "holdings": counts, "chunk": CHUNK})


@csrf_exempt
@require_http_methods(["POST"])
def api_transfer_chunk(request):
    """One piece of the container, read as a stream; answers how much has arrived."""
    ticket = _read_ticket(request, "transfer")
    if ticket is None:
        return _refuse(401, _("Die Übertragung ist abgelaufen. Bitte neu beginnen."))
    try:
        offset, total = int(request.GET.get("offset", "")), int(request.GET.get("total", ""))
    except ValueError:
        return _refuse(400, _("Ungültige Anfrage."))
    part = jobs._uploads() / f"{ticket['id']}.part"
    size = part.stat().st_size if part.exists() else 0
    if offset != size:
        return JsonResponse({"offset": size})
    first = request.read(8)
    if offset == 0:
        from .container import is_container
        import shutil

        if not is_container(first):
            return _refuse(400, _("Das ist keine Sicherungsdatei."))
        if shutil.disk_usage(jobs.root()).free < total + jobs.SPARE_BYTES:
            return _refuse(400, _("Auf dem Server ist nicht genug Platz für diese Datenbank."))
    with part.open("ab") as out:
        out.write(first)
        while piece := request.read(1024 * 1024):
            out.write(piece)
    return JsonResponse({"offset": part.stat().st_size})


@csrf_exempt
@require_http_methods(["POST"])
def api_transfer_finish(request):
    """The container is complete: check it and restore it, after a backup of the server's own state."""
    ticket = _read_ticket(request, "transfer")
    if ticket is None:
        return _refuse(401, _("Die Übertragung ist abgelaufen. Bitte neu beginnen."))
    data = _json(request) or {}
    if updates.blocks_restore():
        return _refuse(409, _("Auf dem Server läuft gerade eine Aktualisierung. Bitte später noch einmal."))
    try:
        name = jobs.finish_upload(ticket["id"], int(data.get("total", -1)), f"uebertragung-{timezone.localtime():%Y-%m-%d-%H%M%S}")
        manifest = jobs.inspect(name, data.get("password", ""))
    except (jobs.UploadError, ContainerError, ValueError) as exc:
        return _refuse(400, str(exc))
    problem = snapshot.incompatible(manifest)
    if problem:
        return _refuse(409, problem)
    if has_data() and not data.get("replace"):
        return _refuse(409, _("Auf dem Server liegen inzwischen Daten. Bitte die Übertragung neu beginnen."))
    try:
        jobs.start_restore(name, data["password"], ticket["name"])
    except jobs.Busy:
        return _refuse(409, _("Auf dem Server läuft gerade ein Sicherungsvorgang. Bitte später noch einmal."))
    audit.log("backup", f"Übertragene Datenbank {name} wird eingespielt (von {ticket['name']})")
    return JsonResponse(_job_state())


@csrf_exempt
@require_http_methods(["GET"])
def api_transfer_status(request):
    """The server's backup job; works across the restore, which replaces the accounts but not the ticket."""
    if _read_ticket(request, "transfer") is None and _read_ticket(request, "backup") is None:
        return _refuse(401, _("Die Übertragung ist abgelaufen. Bitte neu beginnen."))
    return JsonResponse(_job_state())


@csrf_exempt
@require_http_methods(["POST"])
def api_backup_start(request):
    """A connected computer asks for the server's full backup: device token plus the account's password and code."""
    from sync.models import Device
    from sync.views import hash_token

    auth = request.headers.get("Authorization", "")
    device = Device.objects.select_related("user").filter(token_hash=hash_token(auth[7:].strip())).first() if auth.startswith("Bearer ") else None
    if device is None or device.revoked_at or not device.user.is_active:
        return _refuse(401, _("Dieses Gerät ist unbekannt oder getrennt."))
    data = _json(request)
    if data is None:
        return _refuse(400, _("Ungültige Anfrage."))
    data["username"] = device.user.username  # only the account this computer is connected with
    user, refusal = _signed_in_it(request, data, "Anmeldung für eine vollständige Sicherung")
    if refusal:
        return refusal
    password = data.get("backup_password", "")
    if len(password) < jobs.MIN_PASSWORD:
        return _refuse(400, _("Das Passwort der Sicherung braucht mindestens %(n)s Zeichen.") % {"n": jobs.MIN_PASSWORD})
    name = jobs.new_name("fuer-computer")

    def work(progress):
        jobs.make_container(name, password, "erstellt", progress)

    try:
        jobs._start("backup", work)
    except jobs.Busy:
        return _refuse(409, _("Auf dem Server läuft gerade ein Sicherungsvorgang. Bitte später noch einmal."))
    audit.log("backup", f"Vollständige Sicherung für {device.name} angefordert", request=request, user=user, target=device.pk)
    return JsonResponse({"ticket": _ticket("backup", name=name, device=str(device.pk))})


@require_http_methods(["GET"])
def api_backup_file(request):
    ticket = _read_ticket(request, "backup")
    if ticket is None:
        return _refuse(401, _("Die Anfrage ist abgelaufen. Bitte neu beginnen."))
    try:
        path = jobs.locate(ticket["name"])
    except FileNotFoundError:
        return _refuse(404, _("Die Sicherung ist noch nicht fertig."))
    audit.log("backup", f"Sicherung {ticket['name']} auf einen Computer geladen")
    return FileResponse(path.open("rb"), as_attachment=True, filename=path.name, content_type="application/octet-stream")


# ------------------------------------------------------------------ computer

def outline():
    """What the server checks before the upload: the backup's format, migrations and row counts."""
    from django.db import connection

    from core.models import SchoolSettings

    return {"format": snapshot.FORMAT, "migrations": snapshot._applied_migrations(),
            "rows": {m._meta.label_lower: m._base_manager.count() for m in snapshot._models()},
            "school": SchoolSettings.load().name, "datafluss": settings.DATAFLUSS_VERSION, "vendor": connection.vendor}


def _client():
    from sync import client

    return client


def _answer(payload):
    return json.loads(payload or b"{}")


def check(url, username, password, code):
    """Signs in at the server. Returns its answer (ticket, has_data, holdings); raises SyncError with the reason."""
    client = _client()
    url = client.normalize_url(url)
    info = client.server_info(url)
    if info is None:
        raise client.SyncError(_("Unter dieser Adresse antwortet kein DataFluss-Server."))
    if not info.get("transfer"):
        raise client.SyncError(_("Der Server hat DataFluss %(version)s. Für die Übertragung braucht er Version 1.1 oder neuer.") % {"version": info.get("version", "")})
    _status, payload = client._request("POST", f"{url}/sync/v1/transfer/start",
                                       body={"username": username, "password": password, "code": code, "outline": outline()})
    return {**_answer(payload), "url": url}


def _state_file():
    return jobs.root() / "transfer.json"


def transferred():
    """The last transfer of this database: {url, at}, for the button that connects anew."""
    try:
        return json.loads(_state_file().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _with_retries(call):
    client = _client()
    for attempt in range(1, RETRIES + 1):
        try:
            return call()
        except client.SyncError as exc:
            # A refusal with a reason is final; a dropped connection is tried again.
            if getattr(exc, "answer", None) or attempt == RETRIES:
                raise
            time.sleep(attempt * 2)


def _follow(url, ticket, progress, prefix):
    """Waits for the server's job and shows its steps. Returns its final state."""
    client = _client()
    while True:
        time.sleep(POLL_SECONDS)
        _status, payload = _with_retries(lambda: client._request("GET", f"{url}/sync/v1/transfer/status", ticket, timeout=60))
        state = _answer(payload)
        if state.get("state") != "running":
            return state
        progress(f"{prefix}: {state.get('step', '')}", state.get("done"), state.get("total"))


def start(url, ticket, password, replace):
    """Packs this database, uploads it and has the server restore it, as a background job of this database."""
    client = _client()

    def work(progress):
        path = jobs.root() / f".uebertragung-{uuid.uuid4().hex}{snapshot.EXTENSION}"
        try:
            snapshot.create(path, password, progress)
            total = path.stat().st_size
            offset = 0
            with path.open("rb") as source:
                while offset < total:
                    progress(gettext_noop("Wird auf den Server übertragen"), offset // (1024 * 1024), total // (1024 * 1024))
                    source.seek(offset)
                    piece = source.read(CHUNK)
                    _status, payload = _with_retries(lambda: client._request(
                        "POST", f"{url}/sync/v1/transfer/chunk?offset={offset}&total={total}", ticket, raw=piece, timeout=300))
                    offset = _answer(payload)["offset"]
            _with_retries(lambda: client._request("POST", f"{url}/sync/v1/transfer/finish", ticket,
                                                  body={"total": total, "password": password, "replace": bool(replace)}))
        except client.SyncError as exc:
            raise ContainerError(str(exc))
        finally:
            path.unlink(missing_ok=True)
        state = _follow(url, ticket, progress, _("Server"))
        if state.get("state") != "done":
            raise ContainerError(_("Der Server konnte die Datenbank nicht einspielen: %(reason)s") % {"reason": state.get("message", "")})
        _state_file().write_text(json.dumps({"url": url, "at": timezone.now().isoformat()}))
        audit.log("backup", f"Datenbank auf den Server {url} übertragen")
        return gettext_noop("Die Datenbank ist auf dem Server. Arbeiten Sie ab jetzt mit dem Server: "
                            "Änderungen in dieser Datenbank auf dem Computer erreichen ihn nicht mehr.")

    return jobs._start("transfer", work)


def _download(url, ticket, target):
    """Streams a file to the disk, so a backup of several gigabytes needs no memory of that size."""
    import shutil
    import urllib.error
    import urllib.request

    client = _client()
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {ticket}", "User-Agent": "DataFluss"})
    try:
        with urllib.request.urlopen(request, timeout=300, context=client._context() if url.startswith("https") else None) as response:
            with target.open("wb") as out:
                shutil.copyfileobj(response, out, 1024 * 1024)
    except urllib.error.HTTPError as exc:
        try:
            reason = json.loads(exc.read()).get("error", "")
        except ValueError:
            reason = ""
        error = client.SyncError(reason or _("Der Server antwortet mit %(code)s.") % {"code": exc.code})
        error.answer = {"error": reason}
        raise error
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise client.SyncError(_("Der Server ist nicht erreichbar (%(reason)s).") % {"reason": getattr(exc, "reason", exc)})


def fetch_server_backup(password, code, backup_password):
    """On a connected copy: has the server make its full backup and downloads it into this database's
    backups. Signs in synchronously (so a wrong code shows at once), then downloads in the background."""
    import os

    from sync.models import ClientState

    client = _client()
    state = ClientState.load()
    token = os.environ.get("SYNC_TOKEN", "")
    if state is None or not token:
        raise client.SyncError(_("Diese Datenbank ist mit keinem Server verbunden."))
    url = state.server_url
    _status, payload = client._request("POST", f"{url}/sync/v1/backup/start", token,
                                       body={"password": password, "code": code, "backup_password": backup_password})
    ticket = _answer(payload)["ticket"]

    def work(progress):
        server = _follow(url, ticket, progress, _("Server"))
        if server.get("state") != "done":
            raise ContainerError(_("Der Server konnte keine Sicherung erstellen: %(reason)s") % {"reason": server.get("message", "")})
        progress(gettext_noop("Sicherung wird geladen"))
        name = jobs.new_name("server")
        part = jobs.root() / f"{name}.part"
        try:
            _with_retries(lambda: _download(f"{url}/sync/v1/backup/file", ticket, part))
            part.replace(jobs.root() / name)
        except client.SyncError as exc:
            raise ContainerError(str(exc))
        finally:
            part.unlink(missing_ok=True)
        manifest = snapshot.read_manifest(jobs.root() / name, backup_password)
        jobs._describe(name, manifest, "server")
        return gettext_noop("Die vollständige Sicherung des Servers liegt in der Liste unten. Bitte herunterladen und an einem anderen Ort aufbewahren.")

    return jobs._start("download", work)
