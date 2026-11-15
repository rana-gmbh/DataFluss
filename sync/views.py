"""The sync API a Mac talks to: /sync/v1/... with a device token, never with a session."""
import base64
import hashlib
import json
import secrets
import uuid
from functools import lru_cache, wraps

from django.conf import settings
from django.contrib.auth import authenticate
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.migrations.recorder import MigrationRecorder
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core import audit, modules
from core.models import SchoolSettings
from vault import crypto

from . import codec, server
from .models import Dataset, Device, Holding
from .scope import Scope

API = "1"


@lru_cache(maxsize=1)
def schema():
    """A fingerprint of the applied migrations of the loaded apps. Two nodes only sync with the same schema.

    Migrations of a module that is not loaded (empty tables of it, see core.modules) do not count."""
    loaded = modules.loaded_apps()
    applied = sorted(row for row in MigrationRecorder.Migration.objects.values_list("app", "name") if row[0] in loaded)
    return hashlib.sha256(json.dumps(applied).encode()).hexdigest()[:16]


def _versions_differ(request):
    """Why this computer and the server cannot sync, in words, once their schemas differ."""
    theirs = request.headers.get("X-DataFluss-Version") or ""
    if theirs and theirs != settings.DATAFLUSS_VERSION:
        return _("Dieser Computer hat DataFluss %(theirs)s, der Server %(ours)s. Bitte beide auf dieselbe Version bringen.") % {
            "theirs": theirs, "ours": settings.DATAFLUSS_VERSION}
    return _("Computer und Server haben verschiedene Versionen. Bitte beide aktualisieren.")


def _their_modules(request):
    """The modules the device reports: "name=version,name=version"."""
    pairs = (part.partition("=") for part in request.headers.get("X-DataFluss-Modules", "").split(",") if part)
    return {name.strip()[:40]: version.strip()[:20] for name, _sep, version in pairs if name.strip()}


def RESTORED_MESSAGE():
    return _("Der Server wurde aus einer Sicherung wiederhergestellt. Diese Kopie gleicht sich deshalb nicht mehr ab: "
             "bitte noch nicht übertragene Änderungen notieren, die Datenbank auf diesem Computer entfernen und neu mit dem Server verbinden.")


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _json(request):
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return None


def _refuse(status, message, **extra):
    return JsonResponse({"error": message, **extra}, status=status)


def device_api(view):
    """Token check, revocation and schema check for every call after registration."""

    @csrf_exempt
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return _refuse(401, _("Kein Gerät angemeldet."))
        device = Device.objects.select_related("user").filter(token_hash=hash_token(auth[7:].strip())).first()
        if device is None:
            return _refuse(401, _("Dieses Gerät ist unbekannt."))
        if device.revoked_at or not device.user.is_active:
            return _refuse(401, _("Dieses Gerät wurde getrennt."), revoked=True)
        known = request.headers.get("X-DataFluss-Dataset")
        if known and known != Dataset.current():
            return _refuse(409, RESTORED_MESSAGE(), restored=True)
        if request.headers.get("X-Schule-Schema") != schema():
            return _refuse(409, _versions_differ(request), schema=schema(), version=settings.DATAFLUSS_VERSION)
        version = request.headers.get("X-DataFluss-Version") or request.headers.get("X-Schule-Version", "")
        Device.objects.filter(pk=device.pk).update(last_seen=timezone.now(), last_address=audit.client_ip(request) or "",
                                                   app_version=version[:40], modules=_their_modules(request))
        return view(request, device, *args, **kwargs)

    return wrapped


def _sign_in(request, data, purpose):
    """The user's own password and second factor, as in the browser. Returns (user, None) or (None, refusal)."""
    from core.views import _code_is_good, _note_failure, _too_many_failures, _totp_context

    username, password = (data.get("username") or "").strip(), data.get("password") or ""
    if _too_many_failures(username, request):
        return None, _refuse(429, _("Zu viele Versuche. Bitte in einer Viertelstunde noch einmal."))
    user = authenticate(request, username=username, password=password)
    if user is None:
        _note_failure(request, f"Fehlgeschlagene {purpose}", username)
        return None, _refuse(403, _("Benutzername oder Passwort stimmen nicht."))
    if user.must_change_password:
        return None, _refuse(403, _("Bitte zuerst in der Webversion anmelden und ein eigenes Passwort festlegen."))
    if SchoolSettings.load().require_2fa:
        if not user.totp_confirmed:
            return None, _refuse(403, _("Bitte zuerst in der Webversion anmelden und die Zwei-Faktor-Anmeldung einrichten."))
        code = (data.get("code") or "").strip().replace(" ", "")
        if not code or not _code_is_good(user, crypto.decrypt(user.totp_secret, _totp_context(user)), code):
            _note_failure(request, f"Falscher Code bei der {purpose}", username)
            return None, _refuse(403, _("Der Code aus der Authenticator-App stimmt nicht."), needs_code=True)
    return user, None


@csrf_exempt
@require_http_methods(["POST"])
def register(request):
    """A Mac connects with its user's own password and second factor, and gets a token and the vault key."""
    data = _json(request)
    if data is None:
        return _refuse(400, _("Ungültige Anfrage."))
    if data.get("schema") != schema():
        return _refuse(409, _versions_differ(request), schema=schema(), version=settings.DATAFLUSS_VERSION)
    user, refusal = _sign_in(request, data, "Anmeldung eines Macs")
    if refusal:
        return refusal
    token = secrets.token_urlsafe(32)
    name = (data.get("name") or "Mac").strip()[:120]
    device = Device.objects.create(node=f"mac-{secrets.token_hex(5)}", name=name, user=user, token_hash=hash_token(token),
                                   last_seen=timezone.now(), last_address=audit.client_ip(request) or "")
    audit.log("device", f"Mac verbunden: {name} ({user.username})", request=request, user=user, target=device.pk)
    return JsonResponse({
        "device": str(device.pk), "node": device.node, "token": token, "user": str(user.pk),
        "server": settings.SERVER_ID, "school": SchoolSettings.load().name, "dataset": Dataset.current(),
        # Every node of a school holds the same vault key, so passwords and 2FA secrets stay readable.
        "vault_key": base64.b64encode(crypto._key()).decode(),
    })


@require_http_methods(["GET"])
def info(request):
    """What a computer checks before it connects: this server's DataFluss version and modules."""
    return JsonResponse({"api": API, "version": settings.DATAFLUSS_VERSION, "modules": modules.manifest(), "schema": schema(),
                         "transfer": True})  # takes a database from a computer (archive/transfer.py), since 1.1


@csrf_exempt
@require_http_methods(["POST"])
def module(request):
    """A module bundle of this server's version (schule/bundles.py), for a computer that lacks it.

    Before connecting, a computer signs in with the user's password and code; a connected one uses its
    device token, also when its schema is older (it fetches the bundle of the version it updates to)."""
    from django.http import FileResponse

    from schule import bundles

    data = _json(request)
    if data is None:
        return _refuse(400, _("Ungültige Anfrage."))
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        device = Device.objects.select_related("user").filter(token_hash=hash_token(auth[7:].strip())).first()
        if device is None or device.revoked_at or not device.user.is_active:
            return _refuse(401, _("Dieses Gerät ist unbekannt oder getrennt."))
    else:
        _user, refusal = _sign_in(request, data, "Anmeldung für ein Modul")
        if refusal:
            return refusal
    module_id = str(data.get("id") or "")
    archive = bundles.archive_for(module_id, settings.DATAFLUSS_VERSION) if module_id in modules.manifest() else None
    if archive is None:
        return _refuse(404, _("Dieses Modul gibt der Server nicht heraus."))
    return FileResponse(archive.open("rb"), as_attachment=True, filename=archive.name, content_type="application/octet-stream")


@device_api
@require_http_methods(["POST"])
def pull(request, device):
    data = _json(request) or {}
    return JsonResponse({**server.pull(device, data.get("ack", "")), "dataset": Dataset.current()})


@device_api
@require_http_methods(["POST"])
def push(request, device):
    data = _json(request)
    if not data or not data.get("batch"):
        return _refuse(400, _("Ungültige Anfrage."))
    return JsonResponse(server.push_atomic(device, data["batch"][:64], data.get("items", [])))


def _files_in_scope(device, names):
    """The names a device may fetch: files that records it holds (or is just receiving) point to."""
    logo = SchoolSettings.load().logo.name
    held = {}
    rows = list(Holding.objects.filter(device=device).values_list("entity", "record_id"))
    rows += [(e, uuid.UUID(i)) for e, i in device.pending.get("add", [])]
    for entity, pk in rows:
        held.setdefault(entity, set()).add(pk)
    allowed = {n for n in names if n and n == logo}
    for model in codec.synced_models():
        entity = codec.entity_of(model)
        for field in model._meta.concrete_fields:
            if field.get_internal_type() in ("FileField", "ImageField") and held.get(entity):
                found = model._base_manager.filter(**{f"{field.attname}__in": names, "pk__in": held[entity]}).values_list(field.attname, flat=True)
                allowed.update(found)
    return allowed


def _bad_name(name):
    return not name or name.startswith(("/", ".")) or ".." in name


@device_api
@require_http_methods(["POST"])
def media_batch(request, device):
    """Several files at once, as base64, so a first copy does not need one request per photo."""
    names = [n for n in (_json(request) or {}).get("names", [])[:100] if not _bad_name(n)]
    files = {}
    for name in _files_in_scope(device, names):
        if default_storage.exists(name):
            with default_storage.open(name) as handle:
                files[name] = base64.b64encode(handle.read()).decode()
    return JsonResponse({"files": files})


@device_api
@require_http_methods(["GET", "POST"])
def media(request, device):
    name = request.GET.get("name", "")
    if _bad_name(name):
        return _refuse(400, _("Ungültiger Name."))
    if request.method == "GET":
        if name not in _files_in_scope(device, [name]) or not default_storage.exists(name):
            return _refuse(404, _("Nicht gefunden."))
        with default_storage.open(name) as handle:
            return HttpResponse(handle.read(), content_type="application/octet-stream")
    # A Mac uploads a file before the change that points to it.
    body = request.body
    if default_storage.exists(name):
        with default_storage.open(name) as handle:
            same = hashlib.sha256(handle.read()).digest() == hashlib.sha256(body).digest()
        return JsonResponse({"stored": same, "name": name}, status=200 if same else 409)
    stored = default_storage.save(name, ContentFile(body))
    return JsonResponse({"stored": stored == name, "name": stored})


@device_api
@require_http_methods(["GET"])
def whoami(request, device):
    scope = Scope(device.user)
    return JsonResponse({"device": str(device.pk), "user": device.user.username, "server": settings.SERVER_ID,
                         "records": sum(len(p) for p in scope.records().values())})
