"""Publications of the server for a connected computer (docs/publish.md "Vom Mac aus"): the list, and
publishing one again. Signed in with the computer's device token, like the sync API; the computer's user
needs the switch "Veröffentlichen" on the server. Confirming what becomes public stays on the server's page."""
import json

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from sync.views import device_api

from . import service
from .models import Publication


def row(publication, request):
    base = request.build_absolute_uri("/").rstrip("/")
    published = publication.published_at is not None
    return {
        "id": str(publication.pk), "name": publication.name, "target": publication.target, "target_label": str(publication.get_target_display()),
        "status": publication.status, "progress": publication.progress, "error": publication.error,
        "published_at": publication.published_at.isoformat() if published else None,
        "url": f"{base}/p/{publication.slug}/" if published and publication.target == Publication.SERVER else "",
        "manage_url": f"{base}/einstellungen/veroeffentlichen/{publication.pk}/",
        "needs_confirmation": service.needs_confirmation(publication), "auto": str(publication.get_auto_display()),
        "auto_note": publication.auto_note,
    }


def _refuse(status, message, **extra):
    return JsonResponse({"error": str(message), **extra}, status=status)


@require_POST
@device_api
def remote_list(request, device):
    if not device.user.can("publish.manage"):
        return JsonResponse({"allowed": False, "publications": []})
    return JsonResponse({"allowed": True, "publications": [row(p, request) for p in Publication.objects.all()]})


@require_POST
@device_api
def remote_run(request, device):
    from django.utils.translation import gettext as _

    if not device.user.can("publish.manage"):
        return _refuse(403, _("Ihre Rolle darf auf dem Server nicht veröffentlichen."))
    try:
        pk = (json.loads(request.body or b"{}") or {}).get("id")
        publication = Publication.objects.filter(pk=pk).first()
    except (ValueError, ValidationError):
        publication = None
    if publication is None:
        return _refuse(404, _("Diese Veröffentlichung gibt es auf dem Server nicht."))
    if service.needs_confirmation(publication):
        return _refuse(409, _("Auf dem Server muss zuerst jemand bestätigen, was veröffentlicht wird."), confirm_url=row(publication, request)["manage_url"])
    if publication.target == Publication.ZIP:
        return _refuse(409, _("Eine ZIP-Datei lässt sich nur auf dem Server selbst herunterladen."))
    try:
        service.start(publication, service.PUBLISH, user=device.user, request=request)
    except ValidationError as exc:
        return _refuse(409, " ".join(exc.messages))
    publication.refresh_from_db()
    return JsonResponse({"publication": row(publication, request)})
