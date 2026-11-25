"""What websites need before the site goes public (`python manage.py check --deploy`, docs/website.md "Websites")."""
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def sites_ready(app_configs, **kwargs):
    found = []
    sites, site = urlsplit(settings.SITES_URL), urlsplit(settings.SITE_URL)
    if sites.scheme != "https":
        found.append(Error("SITES_URL muss mit https:// beginnen.", id="web.E101"))
    if not sites.hostname or sites.hostname == site.hostname or (sites.hostname or "").endswith("." + (site.hostname or "")):
        # Hosted pages are someone else's HTML: on the template site's own host (or below it) they would share its cookies.
        found.append(Error("SITES_URL braucht einen eigenen Host neben SITE_URL (z. B. seiten.datafluss.de neben vorlagen.datafluss.de).",
                           id="web.E102"))
    if getattr(settings, "SESSION_COOKIE_DOMAIN", None) or getattr(settings, "CSRF_COOKIE_DOMAIN", None):
        found.append(Error("SESSION_COOKIE_DOMAIN/CSRF_COOKIE_DOMAIN müssen leer bleiben, damit gehostete Seiten keine Anmeldung sehen.",
                           id="web.E103"))
    return found
