import time
from datetime import datetime

from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from core import audit, updates
from core.permissions import require

from . import jobs, nightly, plan, snapshot, transfer
from .container import ContainerError

SHOW_RESULT_FOR = 600
CONFIRM_WORD = "EINSPIELEN"


def _visible_job():
    """A result stays on the page for a while, an old one says nothing anymore."""
    job = jobs.current()
    if job and job["state"] != "running" and time.time() - job["beat"] > SHOW_RESULT_FOR:
        return None
    if job:
        # The job stores its step and message in German, because it runs without a language.
        job = {**job, "step": _(job["step"]) if job.get("step") else "", "message": _(job["message"]) if job.get("message") else ""}
    return job


def _busy():
    return JsonResponse({"error": _("Es läuft bereits ein Sicherungsvorgang.")}, status=409)


@require("admin.backup")
@require_GET
def home(request):
    jobs.forget_leftovers()
    return render(request, "archive/home.html", {
        "job": _visible_job(),
        "containers": jobs.containers(),
        "min_password": jobs.MIN_PASSWORD,
        "nightly": nightly.summary(),
        "confirm_word": CONFIRM_WORD,
        # The desktop client: a database of its own gets a plan, a copy of a server only the server's backups.
        "on_client": plan.is_client(),
        "connected": plan.is_connected(),
        "plan": plan.load() if plan.applies() else None,
        "plan_choices": {"every": [("day", _("Täglich")), ("week", _("Wöchentlich"))], "keep": plan.KEEP_CHOICES},
        "plan_default_keep": plan.DEFAULT_KEEP,
        "transferred": transfer.transferred() if plan.applies() else {},
        "active_nav": "backups",
    })


@require("admin.backup")
@require_GET
def status(request):
    return JsonResponse(_visible_job() or {})


@require("admin.backup")
@require_POST
def create(request):
    password = request.POST.get("password", "")
    if len(password) < jobs.MIN_PASSWORD:
        return JsonResponse({"error": _("Das Passwort braucht mindestens %(n)s Zeichen.") % {"n": jobs.MIN_PASSWORD}}, status=400)
    if password != request.POST.get("repeat"):
        return JsonResponse({"error": _("Die beiden Passwörter stimmen nicht überein.")}, status=400)
    try:
        job = jobs.start_backup(password)
    except jobs.Busy:
        return _busy()
    audit.log("backup", "Sicherung gestartet", request=request)
    return JsonResponse(job)


@require("admin.backup")
@require_POST
def upload(request):
    try:
        offset, total = int(request.POST.get("offset", "")), int(request.POST.get("total", ""))
    except ValueError:
        return HttpResponseBadRequest()
    upload_id = request.POST.get("id")
    chunk = request.FILES.get("chunk")
    try:
        if chunk is None:
            name = jobs.finish_upload(upload_id, total, request.POST.get("name", ""))
            audit.log("backup", f"Sicherung {name} hochgeladen", request=request)
            return JsonResponse({"name": name})
        return JsonResponse({"offset": jobs.receive_chunk(upload_id, offset, total, chunk)})
    except jobs.UploadError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


def _summary(manifest):
    return {
        "school": manifest["school"],
        "created_at": timezone.localtime(datetime.fromisoformat(manifest["created_at"])).strftime("%d.%m.%Y %H:%M"),
        "version": manifest["version"],
        "people": manifest["rows"].get("people.person", 0),
        "assets": manifest["rows"].get("devices.asset", 0),
        "users": manifest["rows"].get("core.user", 0),
        "files": manifest["files"],
        "problem": snapshot.incompatible(manifest),
    }


@require("admin.backup")
@require_POST
def check(request):
    try:
        return JsonResponse(_summary(jobs.inspect(request.POST.get("name"), request.POST.get("password", ""))))
    except FileNotFoundError:
        raise Http404
    except ContainerError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require("admin.backup")
@require_POST
def restore(request):
    name, password = request.POST.get("name"), request.POST.get("password", "")
    if request.POST.get("confirm") != CONFIRM_WORD:
        return JsonResponse({"error": _("Bitte zur Bestätigung %(word)s eingeben.") % {"word": CONFIRM_WORD}}, status=400)
    if updates.blocks_restore():
        return JsonResponse({"error": _("Es läuft gerade eine Aktualisierung.")}, status=409)
    try:
        jobs.locate(name)
        job = jobs.start_restore(name, password, request.user.username)
    except FileNotFoundError:
        raise Http404
    except jobs.Busy:
        return _busy()
    audit.log("backup", f"Einspielen von {name} gestartet", request=request)
    return JsonResponse(job)


@require("admin.backup")
@require_POST
def delete(request):
    name = request.POST.get("name")
    if jobs.is_running():
        messages.error(request, _("Während eines Sicherungsvorgangs kann nichts gelöscht werden."))
        return redirect("backups")
    try:
        jobs.delete(name)
    except FileNotFoundError:
        raise Http404
    audit.log("backup", f"Sicherung {name} gelöscht", request=request)
    messages.success(request, _("Die Sicherung wurde gelöscht."))
    return redirect("backups")


@require("admin.backup")
@require_GET
def download(request, name):
    try:
        path = jobs.locate(name)
    except FileNotFoundError:
        raise Http404
    audit.log("backup", f"Sicherung {name} heruntergeladen", request=request)
    return FileResponse(path.open("rb"), as_attachment=True, filename=name, content_type="application/octet-stream")


def _only_own_database():
    if not plan.applies():
        return JsonResponse({"error": _("Nur für eine eigene Datenbank auf diesem Computer.")}, status=400)
    return None


@require("admin.backup")
@require_POST
def plan_save(request):
    refusal = _only_own_database()
    if refusal:
        return refusal
    password = request.POST.get("password", "")
    if password != request.POST.get("repeat", ""):
        return JsonResponse({"error": _("Die beiden Passwörter stimmen nicht überein.")}, status=400)
    try:
        keep = int(request.POST.get("keep", ""))
    except ValueError:
        keep = 0
    try:
        plan.save(request.POST.get("folder", ""), request.POST.get("every", ""), keep, password)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    audit.log("backup", f"Regelmäßige Sicherung eingerichtet: {request.POST.get('folder', '')}"[:200], request=request)
    return JsonResponse({"ok": True})


@require("admin.backup")
@require_POST
def plan_run(request):
    """Backs up into the plan's folder now, in the background like every backup."""
    refusal = _only_own_database()
    if refusal:
        return refusal
    if not plan.is_set_up():
        return JsonResponse({"error": _("Bitte zuerst die regelmäßige Sicherung einrichten.")}, status=400)
    try:
        job = plan.start()
    except jobs.Busy:
        return _busy()
    return JsonResponse(job)


@require("admin.backup")
@require_POST
def plan_off(request):
    refusal = _only_own_database()
    if refusal:
        return refusal
    plan.turn_off()
    audit.log("backup", "Regelmäßige Sicherung ausgeschaltet", request=request)
    messages.success(request, _("Die regelmäßige Sicherung ist ausgeschaltet. Die Dateien im Ordner bleiben."))
    return redirect("backups")


def _sync_error():
    from sync.client import SyncError

    return SyncError


@require("admin.backup")
@require_POST
def transfer_check(request):
    """Step one of moving this database to a server: sign in there and learn what the server holds."""
    refusal = _only_own_database()
    if refusal:
        return refusal
    password = request.POST.get("backup_password", "")
    if len(password) < jobs.MIN_PASSWORD:
        return JsonResponse({"error": _("Das Passwort der Sicherung braucht mindestens %(n)s Zeichen.") % {"n": jobs.MIN_PASSWORD}}, status=400)
    if password != request.POST.get("backup_repeat"):
        return JsonResponse({"error": _("Die beiden Passwörter stimmen nicht überein.")}, status=400)
    if jobs.is_running():
        return _busy()
    try:
        answer = transfer.check(request.POST.get("url", ""), request.POST.get("username", "").strip(),
                                request.POST.get("password", ""), request.POST.get("code", ""))
    except _sync_error() as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({key: answer[key] for key in ("ticket", "has_data", "holdings", "url")})


@require("admin.backup")
@require_POST
def transfer_run(request):
    refusal = _only_own_database()
    if refusal:
        return refusal
    try:
        job = transfer.start(request.POST.get("url", ""), request.POST.get("ticket", ""),
                             request.POST.get("backup_password", ""), request.POST.get("replace") == "1")
    except jobs.Busy:
        return _busy()
    audit.log("backup", f"Übertragung auf {request.POST.get('url', '')} gestartet"[:200], request=request)
    return JsonResponse(job)


@require("admin.backup")
@require_POST
def server_backup(request):
    """A connected copy fetches the server's full backup (archive/transfer.py)."""
    if not plan.is_connected():
        return JsonResponse({"error": _("Nur für eine Datenbank, die mit einem Server verbunden ist.")}, status=400)
    password = request.POST.get("backup_password", "")
    if len(password) < jobs.MIN_PASSWORD:
        return JsonResponse({"error": _("Das Passwort der Sicherung braucht mindestens %(n)s Zeichen.") % {"n": jobs.MIN_PASSWORD}}, status=400)
    if password != request.POST.get("backup_repeat"):
        return JsonResponse({"error": _("Die beiden Passwörter stimmen nicht überein.")}, status=400)
    if jobs.is_running():
        return _busy()
    try:
        job = transfer.fetch_server_backup(request.POST.get("password", ""), request.POST.get("code", ""), password)
    except _sync_error() as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except jobs.Busy:
        return _busy()
    return JsonResponse(job)
