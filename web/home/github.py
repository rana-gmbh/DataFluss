"""The newest release on GitHub, for the download buttons. Asked by the server (never by the visitor's browser),
at most once an hour; without an answer the page links to the release page instead."""
import json
import logging
import urllib.request

from django.core.cache import cache

REPOSITORY = "rana-gmbh/DataFluss"
RELEASES = f"https://github.com/{REPOSITORY}/releases"
API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CACHE_KEY = "home.latest_release"


def _fetch():
    request = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "DataFluss"})
    with urllib.request.urlopen(request, timeout=5) as response:
        data = json.loads(response.read(512 * 1024))
    assets = {a.get("name", ""): a.get("browser_download_url", "") for a in data.get("assets") or [] if isinstance(a, dict)}

    def pick(suffix):
        url = next((u for n, u in assets.items() if n.endswith(suffix)), "")
        return url if url.startswith(f"https://github.com/{REPOSITORY}/releases/download/") else ""

    tag = str(data.get("tag_name") or "")
    return {"version": tag.removeprefix("v")[:20], "date": str(data.get("published_at") or "")[:10],
            "mac": pick(".dmg"), "windows": pick("-setup.exe"), "notes": f"{RELEASES}/tag/{tag}" if tag else RELEASES}


def latest():
    found = cache.get(CACHE_KEY)
    if found is None:
        try:
            found = _fetch()
        except Exception:  # noqa: BLE001 - GitHub away or changed: the page still works
            logging.getLogger(__name__).warning("GitHub release not read", exc_info=True)
            found = {}
        cache.set(CACHE_KEY, found, 3600 if found else 300)
    return found
