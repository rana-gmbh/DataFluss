from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse

from katalog.views import listed

from . import github


def index(request):
    templates = list(listed().order_by("-official", "-rating", "-downloads")[:3])
    for t in templates:  # pages of the template site, whose URLs this host does not have
        t.page_url = settings.SITE_URL + reverse("template_detail", args=[t.key], urlconf="datafluss_web.urls")
    return render(request, "home/index.html", {
        "release": github.latest(), "releases": github.RELEASES, "repository": f"https://github.com/{github.REPOSITORY}",
        "templates": templates, "catalog_url": settings.SITE_URL, "sites_url": settings.SITES_URL,
    })


def robots(request):
    return HttpResponse("User-agent: *\nDisallow: /sprache/\n", content_type="text/plain; charset=utf-8")


def not_found(request, exception=None):
    return render(request, "home/404.html", {"catalog_url": settings.SITE_URL}, status=404)
