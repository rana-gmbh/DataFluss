"""What search engines read: robots.txt keeps them out of accounts and the review queue, sitemap.xml lists the
start page and every published template."""
from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse
from django.utils.html import escape

from .views import listed

PRIVATE = ["/pruefen/", "/konto/", "/anmelden/", "/abmelden/", "/hochladen/", "/meine-vorlagen/", "/sprache/", "/api/"]


def robots(request):
    lines = ["User-agent: *", *[f"Disallow: {path}" for path in PRIVATE], f"Sitemap: {settings.SITE_URL}/sitemap.xml", ""]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


def sitemap(request):
    urls = [(settings.SITE_URL + "/", None)]
    urls += [(settings.SITE_URL + reverse("template_detail", args=[t.key]), t.updated) for t in listed().order_by("key")]
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url, when in urls:
        body.append(f"<url><loc>{escape(url)}</loc>" + (f"<lastmod>{when.date().isoformat()}</lastmod>" if when else "") + "</url>")
    body.append("</urlset>")
    return HttpResponse("\n".join(body), content_type="application/xml; charset=utf-8")
