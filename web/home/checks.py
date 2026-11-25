from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def home_ready(app_configs, **kwargs):
    home, site, sites = (urlsplit(u).hostname for u in (settings.HOME_URL, settings.SITE_URL, settings.SITES_URL))
    found = []
    if not settings.HOME_URL.startswith("https://"):
        found.append(Error("HOME_URL muss mit https:// beginnen (z. B. https://datafluss.de).", id="web.E201"))
    if home in (site, sites):
        found.append(Error("HOME_URL braucht einen eigenen Host neben SITE_URL und SITES_URL.", id="web.E202"))
    return found
