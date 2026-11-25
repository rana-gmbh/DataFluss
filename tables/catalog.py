"""Templates from the catalog on datafluss.de and from files (docs/tables.md "Von datafluss.de", docs/website.md).

Nothing that comes from outside is trusted: every file is checked with template_check.check_untrusted here, whatever
the catalog checked before, and a catalog file must match the size and SHA-256 its index names. What is applied is
kept as a CatalogTemplate, so the database knows it again for the update notice and "Ergänzen".

Where it runs: on a server and on a computer with its own database. A computer connected to a server leaves it to the
server (sync/middleware.py SERVER_ONLY); the tables reach it by sync.

index(), fetch() and read_file() need no database, so the desktop app's sidecar can ask for the index before a
database exists (client/sidecar/sidecar.py --catalog-index).
"""
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.utils.translation import gettext as _

from . import template_check as tc

TIMEOUT = 15
MAX_INDEX = 2 * 1024 * 1024
CHECK_EVERY = timedelta(hours=20)  # "once a day", whatever the hour the nightly work runs
CACHE_KEY = "tables.catalog.index"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CatalogError(Exception):
    """The message is shown to the person who asked."""


def base_url():
    return (getattr(settings, "TEMPLATE_CATALOG_URL", "") or "").rstrip("/")


def enabled():
    return bool(base_url())


def _allowed(url):
    """Only the catalog's own origin, and only https (http while developing, for a site on this computer)."""
    base, target = urlsplit(base_url()), urlsplit(str(url or ""))
    schemes = ("https", "http") if settings.DEBUG else ("https",)
    return bool(base.netloc) and target.scheme in schemes and target.scheme == base.scheme and target.netloc == base.netloc


def _context():
    try:
        import truststore  # the computer's own certificate store, as for sync (sync/client.py)

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def _open(url, limit):
    if not _allowed(url):
        raise CatalogError(_("Diese Adresse gehört nicht zum Vorlagenkatalog."))
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "DataFluss"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_context()) as response:
            if not _allowed(response.geturl()):  # a redirect elsewhere is not followed further
                raise CatalogError(_("Der Vorlagenkatalog leitet an eine fremde Adresse weiter."))
            body = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise CatalogError(_("Diese Vorlage gibt es im Katalog nicht (mehr)."))
        raise CatalogError(_("Der Vorlagenkatalog antwortet mit einem Fehler (%(code)s). Bitte später noch einmal versuchen.") % {"code": exc.code})
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CatalogError(_("Der Vorlagenkatalog ist nicht erreichbar. Besteht eine Internetverbindung?"))
    if len(body) > limit:
        raise CatalogError(_("Der Vorlagenkatalog hat zu viele Daten geschickt."))
    return body


# ---------------------------------------------------------------- the index

def _text(value, limit=1000):
    return value[:limit] if isinstance(value, str) else ""


def _names(value, limit=1000):
    if not isinstance(value, dict):
        return {}
    return {k: v[:limit] for k, v in list(value.items())[:10] if isinstance(k, str) and tc.LANGUAGE.match(k) and isinstance(v, str)}


def _number(value, kind=int):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else kind(0)


def _entry(raw):
    """One template of the index with every value of the expected type, or None."""
    if not isinstance(raw, dict):
        return None
    tid, version = raw.get("id"), raw.get("version")
    if not isinstance(tid, str) or not tc.KEY.match(tid) or not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return None
    if not isinstance(raw.get("sha256"), str) or not SHA256.match(raw["sha256"]) or not _allowed(raw.get("url")):
        return None
    changes = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}
    return {
        "id": tid, "version": version, "title": _text(raw.get("title"), 100) or tid, "titles": _names(raw.get("titles"), 100),
        "description": _text(raw.get("description")), "descriptions": _names(raw.get("descriptions")),
        "author": _text(raw.get("author"), 100), "license": _text(raw.get("license"), 30), "official": raw.get("official") is True,
        "rating": float(min(_number(raw.get("rating"), float), 5)), "ratings": int(_number(raw.get("ratings"))),
        "downloads": int(_number(raw.get("downloads"))), "has_demo": raw.get("has_demo") is True,
        "datafluss": _text(raw.get("datafluss"), 20), "size": int(_number(raw.get("size"))),
        "tables": [_text(t, 100) for t in raw.get("tables") or [] if isinstance(t, str)][:30] if isinstance(raw.get("tables"), list) else [],
        "sha256": raw["sha256"], "url": raw["url"],
        "page": raw["page"] if _allowed(raw.get("page")) else "", "picture": raw["picture"] if _allowed(raw.get("picture")) else "",
        "changes": {v: _names(t, 500) for v, t in list(changes.items())[:200] if isinstance(v, str) and v.isdigit()},
    }


def index(cache=True):
    """The catalog's templates [{id, version, title, …}], checked field by field; CatalogError if it cannot be read."""
    if not enabled():
        raise CatalogError(_("Der Vorlagenkatalog ist auf diesem Server ausgeschaltet."))
    if cache:
        from django.core.cache import cache as store

        found = store.get(CACHE_KEY)
        if found is not None:
            return found
    try:
        raw = json.loads(_open(base_url() + "/api/v1/vorlagen.json", MAX_INDEX))
    except ValueError:
        raise CatalogError(_("Der Vorlagenkatalog hat keine lesbare Liste geschickt."))
    if not isinstance(raw, dict) or raw.get("format") != 1 or not isinstance(raw.get("templates"), list):
        raise CatalogError(_("Der Vorlagenkatalog hat keine lesbare Liste geschickt."))
    found, seen = [], set()
    for item in raw["templates"][:1000]:
        entry = _entry(item)
        if entry is not None and entry["id"] not in seen:
            seen.add(entry["id"])
            found.append(entry)
    if cache:
        store.set(CACHE_KEY, found, 600)
    return found


def entry(template_id, fresh=True):
    """One template of the index. Fresh by default: what is applied must be what the catalog offers now, not what
    the list showed a few minutes ago (index() keeps the list for 10 minutes)."""
    return next((e for e in index(cache=not fresh) if e["id"] == template_id), None)


# ---------------------------------------------------------------- files

def reserved_ids():
    """Ids a template from outside may not have: those of the platform and of the modules."""
    from . import blueprints

    platform = {tid for tid, t in blueprints.available().items() if t.get("origin") not in ("catalog", "file")}
    return platform | tc.library_ids()


def read_file(raw, reserved=None):
    """(data, problems) of a template file from anywhere, after the strict check."""
    data, problems = tc.load(raw)
    if problems:
        return None, problems
    problems = tc.check_untrusted(data, reserved=reserved_ids() if reserved is None else reserved)
    return (None, problems) if problems else (data, [])


def fetch(item, reserved=None):
    """The checked file of a catalog entry. CatalogError with the reasons when it is not exactly what the index says."""
    raw = _open(item["url"], tc.MAX_BYTES)
    if (item.get("size") and len(raw) != item["size"]) or hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise CatalogError(_("Die Datei aus dem Katalog stimmt nicht mit seinem Verzeichnis überein."))
    data, problems = read_file(raw, reserved)
    if problems:
        raise CatalogError(_("Die Vorlage aus dem Katalog ist ungültig: %(problems)s") % {"problems": " ".join(problems[:5])})
    if data["id"] != item["id"] or data["version"] != item["version"]:
        raise CatalogError(_("Die Datei aus dem Katalog stimmt nicht mit seinem Verzeichnis überein."))
    return data


def info(item):
    """What is kept of a catalog entry besides the file."""
    return {"sha256": item.get("sha256", ""), "author": item.get("author", ""), "license": item.get("license", ""), "page": item.get("page", "")}


# ---------------------------------------------------------------- applying and updating

def install(data, source, user=None, demo=False, request=None, details=None):
    """Applies a checked template from the catalog or a file and keeps it. Returns blueprints.apply's report."""
    from django.db import transaction

    from . import blueprints
    from .models import CatalogTemplate

    kept = CatalogTemplate.objects.filter(template_id=data["id"]).first()
    if kept is not None and kept.source != source:
        raise blueprints.BlueprintError(_("Eine Vorlage mit der Kennung %(id)s stammt hier aus einer anderen Quelle.") % {"id": data["id"]})
    details = details or {}
    with transaction.atomic():
        report = blueprints.apply(data, user, demo=demo, request=request, source=source)
        if kept is None or data["version"] >= kept.version:
            CatalogTemplate.objects.update_or_create(template_id=data["id"], defaults={
                "version": data["version"], "data": data, "source": source,
                "sha256": details.get("sha256") or hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
                "author": str(details.get("author") or (data.get("catalog") or {}).get("author") or "")[:100],
                "license": str(details.get("license") or (data.get("catalog") or {}).get("license") or "")[:30],
                "page": details.get("page", "") if source == CatalogTemplate.CATALOG else "",
                "latest_version": None, "latest_changes": {}})
    return report


def update(template_id, user=None, request=None):
    """Fetches the catalog's newest version of an applied template and adds what it brings."""
    from . import blueprints
    from .models import CatalogTemplate

    kept = CatalogTemplate.objects.filter(template_id=template_id, source=CatalogTemplate.CATALOG).first()
    item = entry(template_id) if kept is not None else None  # fresh, so a version published since counts
    if item is None:
        raise CatalogError(_("Diese Vorlage gibt es im Katalog nicht (mehr)."))
    if item["version"] <= (blueprints.installed_version(template_id) or 0):
        raise CatalogError(_("Hier ist schon die neueste Version angewendet."))
    return install(fetch(item), CatalogTemplate.CATALOG, user, request=request, details=info(item))


def check_updates(force=False):
    """Part of the nightly work: once a day, whether the catalog has newer versions of what is applied here. Returns
    how many are new. Nothing is asked without catalog templates, with the switch off, or on a connected computer."""
    from django.utils import timezone

    from archive import plan
    from core import audit
    from core.models import SchoolSettings

    from . import blueprints
    from .models import CatalogTemplate

    rows = list(CatalogTemplate.objects.filter(source=CatalogTemplate.CATALOG))
    if not rows or not enabled() or plan.is_connected() or not SchoolSettings.load().catalog_updates:
        return 0
    now = timezone.now()
    if not force and all(r.checked_at and now - r.checked_at < CHECK_EVERY for r in rows):
        return 0
    items = {e["id"]: e for e in index(cache=False)}
    new = []
    for row in rows:
        item, installed = items.get(row.template_id), blueprints.installed_version(row.template_id) or row.version
        newer = item if item and item["version"] > installed else None
        if newer and row.latest_version != newer["version"]:
            new.append(newer["title"])
        row.latest_version = newer["version"] if newer else None
        row.latest_changes = {v: t for v, t in newer["changes"].items() if int(v) > installed} if newer else {}
        row.checked_at = now
        row.save(update_fields=["latest_version", "latest_changes", "checked_at"])
    if new:
        audit.log("table", f"Neue Versionen im Vorlagenkatalog: {', '.join(new)}")
    return len(new)
