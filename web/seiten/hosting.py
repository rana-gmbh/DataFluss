"""The sites' own host (SITES_URL, seiten.datafluss.de): the hosted files and the hosting API, nothing else.

SitesHostMiddleware answers every request to that host before sessions, CSRF or accounts come into play, so
no cookie of the template site is ever read or set there, and a hosted page cannot reach an account page on
the same host. The pages themselves run in a sandbox as well (publish/hosting.py SANDBOX_CSP)."""
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseNotFound, HttpResponseRedirect, JsonResponse

from publish import hosting

from .models import Site


def host():
    return urlsplit(settings.SITES_URL).netloc.lower()


def root(site):
    return Path(settings.MEDIA_ROOT) / "seiten" / site.slug


def url(site):
    return f"{settings.SITES_URL.rstrip('/')}/{site.slug}/"


def _by_token(request):
    token = hosting.bearer(request)
    found = Site.objects.select_related("owner__site_access").filter(token_hash=hosting.token_hash(token)).first() if token else None
    if found is None or found.disabled or not getattr(found.owner, "site_access", None) or not found.owner.site_access.granted:
        return None
    return found


def api(request, path):
    site = _by_token(request)
    if site is None:
        return JsonResponse({"error": "unknown, disabled or missing site token"}, status=401)
    if request.method == "PUT" and int(request.META.get("CONTENT_LENGTH") or 0) > hosting.MAX_FILE:
        return JsonResponse({"error": "file too large"}, status=413)
    return hosting.api(request, root(site), path, settings.SITES_QUOTA_MB * 1024 * 1024, {"name": site.name, "url": url(site)})


class SitesHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.get_host().lower() != host():
            return self.get_response(request)
        path = request.path
        if path == "/api/hosting/v1/site":
            return api(request, None)
        if path.startswith("/api/hosting/v1/files/"):
            return api(request, path.removeprefix("/api/hosting/v1/files/"))
        if request.method not in ("GET", "HEAD"):
            return HttpResponse(status=405)
        if path in ("/", ""):
            return HttpResponseRedirect(settings.SITE_URL + "/websites/")
        if path == "/robots.txt":
            return HttpResponse("User-agent: *\nAllow: /\n", content_type="text/plain")
        slug, _sep, rest = path.lstrip("/").partition("/")
        site = Site.objects.filter(slug=slug, disabled=False, owner__site_access__status="granted").first()
        if site is None:
            return HttpResponseNotFound("Diese Website gibt es nicht (mehr).", content_type="text/plain; charset=utf-8")
        if not _sep:
            return HttpResponseRedirect(f"/{slug}/")
        try:
            return hosting.serve(root(site), rest)
        except Http404:
            return HttpResponseNotFound("Nicht gefunden.", content_type="text/plain; charset=utf-8")
