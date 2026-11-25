"""The files of a published site, from a snapshot (publish/snapshot.py; docs/publish.md).

`build(publication)` returns a Site: {path: sha256} of every file and `read(path)` for its bytes. Pages are
rendered from the snapshot's plain data only, with relative links, so the same files work from a folder
(file://), under /p/<slug>/ on a DataFluss server and on any web space. The output is deterministic: the
same data gives the same bytes, so republishing sends only what changed.

Pictures are re-encoded once and kept in a cache (in the database's own storage, so sealed on a computer),
keyed by the attachment and its last change.
"""
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.template.loader import render_to_string
from django.utils import formats
from django.utils.dateparse import parse_date
from django.utils.translation import override

from . import snapshot

CACHE = "published/.cache"
MANIFEST = "datafluss-manifest.json"


class Site:
    def __init__(self):
        self.pages = {}   # path -> bytes
        self.stored = {}  # path -> name in default_storage (pictures and files from the cache)
        self.files = {}   # path -> sha256
        self.summary = {}

    def add(self, path, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.pages[path] = data
        self.files[path] = hashlib.sha256(data).hexdigest()

    def add_stored(self, path, name, digest):
        self.stored[path] = name
        self.files[path] = digest

    def read(self, path):
        if path in self.pages:
            return self.pages[path]
        with default_storage.open(self.stored[path], "rb") as handle:
            return handle.read()

    @property
    def size(self):
        return sum(len(p) for p in self.pages.values()) + sum(default_storage.size(n) for n in set(self.stored.values()))


def static_file(name):
    """A file of the app's static files, from the sources or the collected ones (the desktop app has only those)."""
    from django.contrib.staticfiles import finders

    found = finders.find(name)
    if found:
        return Path(found).read_bytes()
    return (Path(settings.STATIC_ROOT) / name).read_bytes()


@lru_cache(maxsize=1)
def _symbols():
    sprite = static_file("img/icons.svg").decode("utf-8")
    return {m.group(1): m.group(0) for m in re.finditer(r'<symbol id="([\w-]+)".*?</symbol>', sprite, re.S)}


def _with_icons(html):
    """The icons a page uses, as a sprite inside the page (an external sprite does not load from file://)."""
    used = sorted(set(re.findall(r'href="#i-([\w-]+)"', html)))
    symbols = _symbols()
    sprite = "".join(symbols[name].replace(f'<symbol id="{name}"', f'<symbol id="i-{name}"', 1) for name in used if name in symbols)
    return html.replace("<!--icons-->", f'<svg xmlns="http://www.w3.org/2000/svg" hidden style="display:none">{sprite}</svg>' if sprite else "", 1)


def _stylesheet():
    """The app's stylesheet with its fonts next to it; the mono font is left out (the site does not use it)."""
    css = static_file("css/app.css").decode("utf-8")
    css = re.sub(r"@font-face\{[^}]*JetBrains[^}]*\}", "", css)
    css = re.sub(r'@font-face \{[^}]*JetBrains[^}]*\}', "", css)
    return re.sub(r"url\(\"?/static/fonts/([\w.-]+)\"?\)", r'url("fonts/\1")', css)


def _replace(value, mapping):
    """The snapshot with each file's placeholder path replaced by its final one."""
    if isinstance(value, dict):
        return {k: _replace(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace(v, mapping) for v in value]
    if isinstance(value, str) and value in mapping:
        return mapping[value]
    return value


def _cached(publication, asset, used):
    """(storage name, sha256) of an asset's bytes, made once per change of its attachment."""
    attachment = asset.attachment
    stamp = hashlib.sha256(f"{attachment.file.name}|{attachment.size}|{attachment.updated_at.isoformat()}|{asset.variant}".encode()).hexdigest()[:16]
    name = f"{CACHE}/{publication.pk}/{attachment.pk}-{stamp}.bin"
    if default_storage.exists(name):
        with default_storage.open(name, "rb") as handle:
            data = handle.read()
    else:
        data = asset.data()
        name = default_storage.save(name, ContentFile(data))
    used.add(name.rsplit("/", 1)[-1])
    return name, data


def _prune(publication, used):
    folder = f"{CACHE}/{publication.pk}"
    try:
        _dirs, names = default_storage.listdir(folder)
    except (FileNotFoundError, NotImplementedError, OSError):
        return
    for name in names:
        if name not in used:
            default_storage.delete(f"{folder}/{name}")


def build(publication, progress=None, confirmed=None):
    """The whole site of a publication. Raises snapshot.Unsafe when the snapshot fails its check. With
    `confirmed`, never more fields than those (publish/service.py)."""
    say = progress or (lambda text: None)
    say("data")
    site_data = snapshot.build(publication, confirmed)
    site = Site()
    used, mapping = set(), {}
    total = len(site_data["assets"])
    for n, asset in enumerate(site_data["assets"], 1):
        if n % 25 == 0:
            say(f"files {n}/{total}")
        try:
            name, data = _cached(publication, asset, used)
        except Exception:  # noqa: BLE001 - a broken picture is left out, the site still comes
            mapping[asset.path] = ""
            continue
        final = snapshot.content_path(asset, data)
        mapping[asset.path] = final
        site.add_stored(final, name, hashlib.sha256(data).hexdigest())
    _prune(publication, used)
    data = _replace({k: v for k, v in site_data.items() if k != "assets"}, mapping)
    say("pages")
    _assets(site, data)
    # Stylesheets and the script carry their content in the address, so no cache (of a browser or a web
    # space) keeps an old one after an update. The search index does not: it changes with every change of
    # data, and every page would change with it.
    data["v"] = {name.replace(".", "_"): site.files[f"_assets/{name}"][:10] for name in ("app.css", "site.css", "site.js")}
    with override(publication.language):
        _pages(site, data)
    site.summary = snapshot.summary(site_data)
    return site


def _pages(site, data):
    generated = parse_date(data["generated"])
    data["generated_text"] = formats.date_format(generated, "DATE_FORMAT") if generated else data["generated"]
    search = {entry["u"]: entry["x"] for entry in data["search"]}
    for table in data["tables"]:
        table["has_gallery"] = any(row["cover"] for row in table["rows"]) or table["layout"] == "gallery"
        if not table["has_gallery"]:
            table["layout"] = "list"
        table["title_label"] = table.get("title_label") or ""
        for row in table["rows"]:
            row["search"] = search.get(row["href"], row["title"].lower())
    page = lambda template, context, root: _with_icons(render_to_string(template, {"site": data, "root": root, **context}))  # noqa: E731
    site.add("index.html", page("publish/site/index.html", {}, ""))
    for table in data["tables"]:
        site.add(table["href"], page("publish/site/table.html", {"table": table}, "../"))
    for record in data["pages"]:
        site.add(record["path"], page("publish/site/record.html", {"page": record}, "../"))


def _assets(site, data):
    site.add("_assets/app.css", _stylesheet())
    site.add("_assets/site.css", static_file("publish/site.css"))
    site.add("_assets/site.js", static_file("publish/site.js"))
    site.add("_assets/favicon.svg", static_file("img/favicon.svg"))
    site.add("_assets/fonts/Inter.woff2", static_file("fonts/Inter.woff2"))
    site.add("_assets/fonts/LICENSE-Inter.txt", static_file("fonts/LICENSE-Inter.txt"))
    if any(t["tmdb"] for t in data["tables"]):
        site.add("_assets/tmdb.svg", static_file("img/tmdb.svg"))
    index = json.dumps(data["search"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    site.add("_assets/search.js", f"window.DATAFLUSS_SEARCH={index};\n")
    site.add("robots.txt", "User-agent: *\nAllow: /\n" if data["indexable"] else "User-agent: *\nDisallow: /\n")


def manifest_bytes(site):
    """What is at the target after publishing: the manifest the next publish compares against."""
    return json.dumps({"generator": "DataFluss", "files": dict(sorted(site.files.items()))}, indent=1, sort_keys=True).encode()
