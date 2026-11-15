"""On a Mac connected to a server: the sync status in the sidebar and the "sync now" button."""
import os

from django.http import Http404, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from . import client


def _on_a_connected_mac():
    if not os.environ.get("SYNC_TOKEN"):
        raise Http404  # a server has no sync status of its own


@require_GET
def status(request):
    _on_a_connected_mac()
    return JsonResponse({**client.status(), "running": client.WORKER is not None})


@require_POST
def now(request):
    _on_a_connected_mac()
    if client.WORKER is None:
        return JsonResponse({"state": "off"})
    result = client.WORKER.now()
    return JsonResponse({**result, **client.status()})
