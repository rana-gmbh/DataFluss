"""A Mac's side of sync. Runs inside the client's sidecar, in a thread of its own.

Every round pushes the Mac's own changes first, then pulls until the server has nothing more.
Local changes the server has not confirmed yet are never overwritten by a pull; the server decides
about them when they arrive, and sends the record again if the Mac's version lost.
"""
import hashlib
import json
import logging
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import quote

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import close_old_connections, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import SchoolSettings, User

from . import apply, codec
from .models import CREATED, DELETED, ClientState, FieldVersion, Journal

log = logging.getLogger(__name__)
ROUND_SECONDS = 30
PUSH_BATCH = 300


class SyncError(Exception):
    """Shown to the person as it is."""


class Revoked(SyncError):
    pass


class Restored(SyncError):
    """The server's data was replaced by a restore; this copy must be connected anew."""


def _context():
    try:
        import truststore  # the Mac's own certificate store, so a school's own certificate authority works

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def _request(method, url, token=None, body=None, raw=None, timeout=120, extra=None):
    from .views import schema

    from core import modules

    headers = {
        "X-Schule-Schema": schema(), "X-Schule-Version": settings.APP_VERSION, "X-DataFluss-Version": settings.DATAFLUSS_VERSION,
        "X-DataFluss-Modules": ",".join(f"{name}={version}" for name, version in modules.manifest().items()),
        "User-Agent": "DataFluss-Windows" if sys.platform == "win32" else "DataFluss-Mac",
        **(extra or {}),
    }
    data = None
    if raw is not None:
        data, headers["Content-Type"] = raw, "application/octet-stream"
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_context() if url.startswith("https") else None) as response:
            payload = response.read()
            return response.status, payload
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            answer = json.loads(payload)
        except ValueError:
            answer = {}
        if exc.code == 409 and answer.get("restored"):
            raise Restored(answer.get("error") or "")
        if exc.code == 401 and answer.get("revoked"):
            raise Revoked(answer.get("error", _("Dieses Gerät wurde getrennt.")))
        error = SyncError(answer.get("error") or _("Der Server antwortet mit %(code)s.") % {"code": exc.code})
        error.answer = answer
        raise error
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SyncError(_("Der Server ist nicht erreichbar (%(reason)s).") % {"reason": getattr(exc, "reason", exc)})


def _api(state, token, path, body=None):
    extra = {"X-DataFluss-Dataset": state.dataset} if getattr(state, "dataset", "") else None
    status, payload = _request("POST", f"{state.server_url}/sync/v1/{path}", token, body=body, extra=extra)
    return json.loads(payload)


def normalize_url(url):
    url = (url or "").strip().rstrip("/")
    if not url.startswith(("https://", "http://")):
        url = f"https://{url}"
    return url


def server_info(url):
    """The server's version, modules and schema, or None for a server older than DataFluss 0.4."""
    try:
        status, payload = _request("GET", f"{url}/sync/v1/info", timeout=30)
        return json.loads(payload)
    except (SyncError, ValueError):
        return None


def fetch_module(url, module_id, credentials=None, token=None):
    """Downloads a module bundle from the server into this computer's modules folder (schule/bundles.py).
    The bundle is kept only if its signature holds. Returns False when this computer has no such folder."""
    import tempfile
    from pathlib import Path

    from schule import bundles

    folders = bundles.folders()
    if not folders:
        return False
    folders[0].mkdir(parents=True, exist_ok=True)
    status, payload = _request("POST", f"{url}/sync/v1/module", token, body={**(credentials or {}), "id": module_id}, timeout=300)
    with tempfile.NamedTemporaryFile(dir=folders[0], suffix=".part", delete=False) as part:
        part.write(payload)
    try:
        bundles.unpack(Path(part.name), folders[0])
    except Exception as exc:  # noqa: BLE001 - a bad bundle is refused, whatever is wrong with it
        raise SyncError(_("Das Modul vom Server ließ sich nicht prüfen (%(reason)s).") % {"reason": exc})
    finally:
        Path(part.name).unlink(missing_ok=True)
    return True


def _check_server(url, credentials=None):
    """Before connecting: a module this computer lacks is fetched from the server, a different version
    is named in words. Servers older than DataFluss 0.4 have no /info; the schema check of register holds."""
    from core import modules

    info = server_info(url)
    if info is None:
        return
    lacking = set(info.get("modules") or {}) - set(modules.manifest())
    if lacking:
        if credentials and all([fetch_module(url, module_id, credentials) for module_id in sorted(lacking)]):
            # The running app cannot load code any more; a new start of this database loads the module.
            raise SyncError(_("Das Modul %(names)s wurde vom Server geladen. Bitte noch einmal verbinden, "
                              "mit einem neuen Code aus der Authenticator-App.") % {"names": modules.titles(lacking)})
        raise SyncError(_("Der Server nutzt das Modul %(names)s, das in dieser DataFluss-Version fehlt.") % {"names": modules.titles(lacking)})
    from .views import schema

    # Versions without new migrations sync with each other, as before; only a different schema stops.
    if info.get("schema") != schema() and info.get("version") and info["version"] != settings.DATAFLUSS_VERSION:
        raise SyncError(_("Der Server hat DataFluss %(server)s, dieser Computer %(here)s. Bitte beide auf dieselbe Version bringen.") % {
            "server": info["version"], "here": settings.DATAFLUSS_VERSION})


def register(url, username, password, code, name):
    """Connects this (empty) database to a server. Returns the server's answer, token and vault key included."""
    from .views import schema

    url = normalize_url(url)
    _check_server(url, {"username": username, "password": password, "code": code})
    status, payload = _request("POST", f"{url}/sync/v1/register", body={
        "username": username, "password": password, "code": code, "name": name, "schema": schema()})
    answer = json.loads(payload)
    ClientState.objects.update_or_create(pk=1, defaults={
        "server_url": url, "server_node": answer["server"], "device_id": answer["device"], "user_id": answer["user"],
        "dataset": answer.get("dataset", "")})
    return answer


# ---------------------------------------------------------------- push


def _local_entries(state):
    return Journal.objects.filter(origin=settings.SERVER_ID, id__gt=state.pushed_until).order_by("id")


def _upload_files(state, token, names):
    for name in names:
        if not name or not default_storage.exists(name):
            continue
        with default_storage.open(name) as handle:
            content = handle.read()
        status, payload = _request("POST", f"{state.server_url}/sync/v1/media?name={quote(name)}", token, raw=content)


def _push_items(entries):
    """Local changes as push items, one per record and key, with the oldest base and the current value."""
    per_record = {}
    for entry in entries:
        per_record.setdefault((entry.entity, entry.record_id), []).append(entry)
    items, files = [], []
    for (entity, pk), rows in per_record.items():
        try:
            model = codec.model_of(entity)
        except LookupError:
            continue
        keys = [r.key for r in rows]
        instance = model._base_manager.filter(pk=pk).first()
        if DELETED in keys or instance is None:
            last = [r for r in rows if r.key == DELETED]
            if CREATED in keys:
                continue  # made and removed here before the server ever saw it
            items.append({"e": entity, "id": str(pk), "op": "delete", "v": (last[-1] if last else rows[-1]).hlc})
            continue
        versions = dict(FieldVersion.objects.filter(entity=entity, record_id=pk).values_list("key", "hlc"))
        if CREATED in keys:
            data = codec.snapshot(instance)
            items.append({"e": entity, "id": str(pk), "op": "upsert", "f": data, "v": versions})
            files += [data[f.name] for f in model._meta.concrete_fields if apply.is_file_field(model, f.name) and data.get(f.name)]
            continue
        first_base = {}
        for row in rows:
            first_base.setdefault(row.key, row.base)
        for key, base in first_base.items():
            value = codec.encode_key(instance, key)
            items.append({"e": entity, "id": str(pk), "op": "set", "k": key, "val": value, "v": versions.get(key, rows[-1].hlc), "base": base})
            name, sub = codec.split(key)
            if sub is None and apply.is_file_field(model, name) and value:
                files.append(value)
    return items, files


def push(state, token):
    entries = list(_local_entries(state))
    if not entries:
        return 0
    until = entries[-1].id
    items, files = _push_items(entries)
    _upload_files(state, token, files)
    sent = 0
    for start in range(0, max(len(items), 1), PUSH_BATCH):
        part = items[start:start + PUSH_BATCH]
        if not part:
            break
        answer = _api(state, token, "push", {"batch": f"{settings.SERVER_ID}-{until}-{start}", "items": part})
        if answer.get("evict"):
            with transaction.atomic():
                for entity, pk in answer["evict"]:
                    apply.evict(codec.model_of(entity), [uuid.UUID(pk)])
        sent += len(part)
    ClientState.objects.filter(pk=1).update(pushed_until=until)
    state.pushed_until = until
    return sent


# ---------------------------------------------------------------- pull


def _pending(state, entity, pk):
    return set(Journal.objects.filter(origin=settings.SERVER_ID, id__gt=state.pushed_until, entity=entity, record_id=pk).values_list("key", flat=True))


def _apply_users(rows):
    for row in rows:
        pk = uuid.UUID(row.pop("id"))
        if User.objects.filter(pk=pk).exists():
            User.objects.filter(pk=pk).update(**row)  # accounts are the server's alone
        else:
            User(pk=pk, **row).save_base(raw=True, force_insert=True)


def _apply_page(state, page):
    _apply_users(page.get("users", []))
    if page.get("school"):
        SchoolSettings.load()
        SchoolSettings.objects.filter(pk=1).update(**page["school"])
    order = {codec.entity_of(m): i for i, m in enumerate(codec.synced_models())}
    for entity in sorted(page.get("evict", {}), key=lambda e: -order.get(e, 0)):
        apply.evict(codec.model_of(entity), [uuid.UUID(p) for p in page["evict"][entity]])
    for record in page.get("records", []):
        model, pk = codec.model_of(record["e"]), uuid.UUID(record["id"])
        # A record in full is the server's word: local changes to it that were not sent are dropped.
        Journal.objects.filter(origin=settings.SERVER_ID, id__gt=state.pushed_until, entity=record["e"], record_id=pk).delete()
        apply.upsert(model, pk, record["f"], record["v"], state.server_node)
    for change in page.get("changes", []):
        model, pk = codec.model_of(change["e"]), uuid.UUID(change["id"])
        if change["k"] in _pending(state, change["e"], pk):
            continue
        apply.set_key(model, pk, change["k"], change["val"], change["v"], state.server_node)
    for delete in page.get("deletes", []):
        model, pk = codec.model_of(delete["e"]), uuid.UUID(delete["id"])
        Journal.objects.filter(origin=settings.SERVER_ID, id__gt=state.pushed_until, entity=delete["e"], record_id=pk).delete()
        apply.remove(model, pk, delete["v"], state.server_node)


def _download_files(state, token, names):
    import base64

    missing = [n for n in dict.fromkeys(names) if n and not default_storage.exists(n)]
    for start in range(0, len(missing), 50):
        try:
            answer = _api(state, token, "media-batch", {"names": missing[start:start + 50]})
        except SyncError as exc:
            log.warning("files: %s", exc)
            return
        for name, content in answer.get("files", {}).items():
            if not default_storage.exists(name):
                stored = default_storage.save(name, ContentFile(base64.b64decode(content)))
                if stored != name:
                    log.warning("file %s stored as %s", name, stored)


def missing_files():
    """Files that records here point to but that are not on this Mac (yet)."""
    names = set()
    logo = SchoolSettings.objects.filter(pk=1).values_list("logo", flat=True).first()
    if logo:
        names.add(logo)
    for model in codec.synced_models():
        for field in model._meta.concrete_fields:
            if apply.is_file_field(model, field.name):
                names.update(n for n in model._base_manager.exclude(**{field.attname: ""}).values_list(field.attname, flat=True) if n)
    return sorted(n for n in names if not default_storage.exists(n))


def pull(state, token, progress=None, rounds=200):
    received = 0
    for _round in range(rounds):
        page = _api(state, token, "pull", {"ack": state.pull_ack})
        with transaction.atomic():
            _apply_page(state, page)
            ClientState.objects.filter(pk=1).update(pull_ack=page["page"])
        state.pull_ack = page["page"]
        if page.get("dataset") and not state.dataset:  # copies connected before DataFluss 0.4 learn it now
            ClientState.objects.filter(pk=1).update(dataset=page["dataset"])
            state.dataset = page["dataset"]
        count = len(page.get("records", [])) + len(page.get("changes", [])) + len(page.get("deletes", []))
        received += count
        if progress:
            progress(_("Daten werden übertragen"), received, None)
        if page.get("done"):
            break
    if progress:
        progress(_("Bilder und Dokumente werden geladen"), None, None)
    _download_files(state, token, missing_files())
    return received


# ---------------------------------------------------------------- rounds


def sync_once(token, progress=None):
    state = ClientState.load()
    if state is None or state.revoked:
        return {"state": "off"}
    ClientState.objects.filter(pk=1).update(last_attempt=timezone.now())
    try:
        sent = push(state, token)
        received = pull(state, token, progress)
    except Revoked as exc:
        ClientState.objects.filter(pk=1).update(revoked=True, last_error=str(exc))
        raise
    except SyncError as exc:
        ClientState.objects.filter(pk=1).update(last_error=str(exc))
        if getattr(exc, "answer", {}).get("version", settings.DATAFLUSS_VERSION) != settings.DATAFLUSS_VERSION:
            prefetch(state, token, force=True)  # the server is on another version: get its bundles now
        raise
    ClientState.objects.filter(pk=1).update(last_success=timezone.now(), last_error="")
    prefetch(state, token)
    return {"state": "ok", "sent": sent, "received": received}


PREFETCH_SECONDS = 3600
_last_prefetch = 0.0


def prefetch(state, token, force=False):
    """Keeps a bundle of every module this database uses, for the server's version (schule/bundles.py).

    Also for modules this build carries: a later version may no longer carry them (DataFluss 1.0 leaves the
    premium module out of the public build), and a server ahead of this computer hands out the bundle of
    the version this computer updates to, so it works offline right after its own update. At most hourly."""
    global _last_prefetch
    from core import modules
    from schule import bundles
    from schule import modules as catalog

    if not bundles.folders() or (not force and time.monotonic() - _last_prefetch < PREFETCH_SECONDS):
        return
    _last_prefetch = time.monotonic()
    info = server_info(state.server_url) or {}
    version = info.get("version")
    for module_id in info.get("modules") or {}:
        if not version or module_id in catalog.REQUIRED or module_id not in modules.manifest() or bundles.archive_for(module_id, version):
            continue
        try:
            fetch_module(state.server_url, module_id, token=token)
        except SyncError as exc:
            log.warning("Module %s %s not fetched: %s", module_id, version, exc)


WORKER = None  # set by the sidecar of a connected database


class Worker:
    """Syncs every half minute, and soon after every local change."""

    def __init__(self, token, on_revoked=None):
        self.token = token
        self.on_revoked = on_revoked
        self.kick = threading.Event()
        self.stop = threading.Event()
        self.lock = threading.Lock()

    def start(self):
        from . import journal

        journal.listeners.append(lambda: self.kick.set())
        threading.Thread(target=self._run, name="sync", daemon=True).start()

    def now(self):
        """One round right away, for the "sync now" button. Returns its result."""
        with self.lock:
            try:
                return sync_once(self.token)
            except Revoked:
                if self.on_revoked:
                    self.on_revoked()
                return {"state": "revoked"}
            except SyncError as exc:
                return {"state": "error", "error": str(exc)}
            finally:
                close_old_connections()

    def _run(self):
        from django.db import connection

        connection.ensure_connection()
        while not self.stop.is_set():
            result = self.now()
            if result.get("state") == "revoked":
                return
            self.kick.wait(ROUND_SECONDS)
            if self.kick.is_set():
                self.kick.clear()
                time.sleep(2)  # gather a few quick edits into one round


def status():
    state = ClientState.load()
    if state is None:
        return {"connected": False}
    waiting = _local_entries(state).values("entity", "record_id").distinct().count()
    return {
        "connected": True, "server": state.server_url, "revoked": state.revoked, "waiting": waiting,
        "last_success": state.last_success.isoformat() if state.last_success else None,
        "last_error": state.last_error,
    }


def fingerprint(content):
    return hashlib.sha256(content).hexdigest()
