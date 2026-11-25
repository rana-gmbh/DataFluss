"""The hosting API of a DataFluss server (publish/hosting.py): sites that other computers publish here with
their token, served under /p/<slug>/ (publish/views.py site_file)."""
from pathlib import Path

from django.conf import settings
from django.views.decorators.csrf import csrf_exempt

from archive import plan

from . import hosting
from .models import HostedSite


def root(site):
    return Path(settings.MEDIA_ROOT) / "hosted" / site.slug


def _site(request):
    token = hosting.bearer(request)
    return HostedSite.objects.filter(token_hash=hosting.token_hash(token)).first() if token else None


def _refused():
    from django.http import JsonResponse

    return JsonResponse({"error": "unknown or missing site token"}, status=401)


@csrf_exempt
def site(request):
    found = None if plan.is_client() else _site(request)
    if found is None:
        return _refused()
    return hosting.api(request, root(found), None, found.quota, {"name": found.name, "url": request.build_absolute_uri(f"/p/{found.slug}/")})


@csrf_exempt
def files(request, path):
    found = None if plan.is_client() else _site(request)
    if found is None:
        return _refused()
    return hosting.api(request, root(found), path, found.quota, {})
