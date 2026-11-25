"""What DataFluss servers and apps read (docs/website.md, "Schnittstelle"): the list of templates and their files.
Stable URLs under /api/v1/; fields are only ever added."""
from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse

from . import services
from .models import LICENSE, Template, Version
from .views import _file, listed


def _picture(version):
    first = version.pictures.first()
    return settings.SITE_URL + reverse("picture", args=[first.uid]) if first else ""


def index(request):
    rows = []
    for template in listed().prefetch_related("published__pictures").order_by("key"):
        version, data = template.published, template.published.data
        rows.append({
            "id": template.key, "version": version.number, "title": data.get("title", ""), "titles": data.get("titles") or {},
            "description": data.get("description", ""), "descriptions": data.get("descriptions") or {},
            "category": template.category, "official": template.official, "author": template.author, "license": LICENSE,
            "rating": float(template.rating), "ratings": template.ratings, "downloads": template.downloads,
            "has_demo": bool(data.get("demo")), "datafluss": data.get("datafluss", ""),
            "changes": data.get("changes") if isinstance(data.get("changes"), dict) else {},
            "picture": _picture(version),
            "tables": [t.get("name", "") for t in data.get("tables", [])],
            "sha256": version.sha256, "size": len(services.file_bytes(version)), "updated": template.updated.isoformat(),
            "url": settings.SITE_URL + reverse("api_file", args=[template.key, version.number]),
            "page": settings.SITE_URL + reverse("template_detail", args=[template.key]),
        })
    response = JsonResponse({"format": 1, "templates": rows}, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "public, max-age=300"
    return response


def file(request, key, number):
    template = get_object_or_404(Template, key=key, published__isnull=False, withdrawn=False)
    version = template.versions.filter(number=number, status__in=[Version.APPROVED, Version.REPLACED]).first()
    if version is None:
        raise Http404
    services.count_download(template)
    response = _file(template, version, attachment=False)
    response["Cache-Control"] = "public, max-age=86400"
    return response
