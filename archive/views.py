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

from . import jobs, nightly, snapshot
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
