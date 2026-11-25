"""What a publication shows, as plain data (docs/publish.md "Sicherheit").

`build(publication)` reads the database once, through a PublicationViewer (publish/viewer.py), and returns
dicts, lists and strings only: no model objects reach the page templates (publish/render.py), so a template
cannot reach a value that was not chosen. Files come along as Asset objects that read and re-encode them
only when the site is written.

Paths in the snapshot are relative to the site's root ("filme/<id>.html", "_media/<hash>.webp"); the
templates put the way back to the root in front.

Before the snapshot is returned, `check` makes sure every field key in it is one the viewer sees, and stops
the publication otherwise.
"""
import hashlib
import io
import re
from types import SimpleNamespace

from django.utils import timezone
from django.utils.translation import gettext as _, override

from core import fieldtypes as ft
from core.models import SchoolSettings
from tables import formula, links, look, masks as table_masks, services
from tables.models import Attachment, Record

from .viewer import PublicationViewer

MEDIA, ASSETS = "_media", "_assets"
THUMB, LARGE = 480, 1600  # longest side of a picture in lists and galleries, and on its own page
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_FILE = 50 * 1024 * 1024  # a larger file is left out of the site
RECORD_URL = re.compile(r"^/tabellen/([\w-]+)/([0-9a-f-]{36})/$")
FILE_URL = re.compile(r"^/tabellen/([\w-]+)/([0-9a-f-]{36})/datei/([0-9a-f-]{36})/$")
SEARCH_LIMIT = 2000  # characters of searchable text per record in the search index
LIST_TYPES = {"text", "textarea", "number", "rating", "bool", "choice", "multichoice", "date", "datetime", "email", "phone", "url",
              "formula", "link", "backlink", "file", "image"}


class Unsafe(Exception):
    """The snapshot holds something the viewer may not see. Nothing is published."""


class Asset:
    """A file of the site that is made when it is written: a picture re-encoded (which drops EXIF and GPS
    data) or a file copied. `path` depends on the content, so an unchanged file keeps its path."""

    def __init__(self, attachment, variant):
        self.attachment, self.variant = attachment, variant
        self._data = None
        self.path = None

    def data(self):
        if self._data is None:
            self._data = _make(self.attachment, self.variant)
        return self._data


def _make(attachment, variant):
    with attachment.file.open("rb") as handle:
        raw = handle.read()
    if variant == "file":
        return raw
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(raw)) as image:
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
        image.thumbnail((variant, variant))
        out = io.BytesIO()
        image.save(out, "WEBP", quality=82, method=4)  # a new file: no EXIF, GPS or other metadata comes along
    return out.getvalue()


class Collector:
    """The files a site uses, once each, and the attachments it may use at all."""

    def __init__(self, viewer, publication):
        self.viewer, self.publication = viewer, publication
        self.assets = {}  # (attachment id, variant) -> Asset
        self._attachments = {}

    def attachment(self, pk):
        if pk not in self._attachments:
            found = Attachment.objects.select_related("record__table").filter(pk=pk, record__deleted_at=None).first()
            if found is not None:
                table = found.record.table
                if self.viewer.table_right(table) != "read" or found.field not in self.viewer.visible_keys(table):
                    found = None
            self._attachments[pk] = found
        return self._attachments[pk]

    def picture(self, pk, size):
        """The path of a published picture, or None."""
        found = self.attachment(pk)
        if found is None or found.content_type not in IMAGE_TYPES or found.size > MAX_FILE:
            return None
        return self._asset(found, size)

    def file(self, pk):
        """A file to download: pictures as pictures, other files only when the table's files are published."""
        found = self.attachment(pk)
        if found is None or found.size > MAX_FILE:
            return None
        if found.content_type in IMAGE_TYPES:
            return self._asset(found, LARGE)
        if not (self.publication.tables().get(found.record.table.key) or {}).get("files"):
            return None
        return self._asset(found, "file")

    def _asset(self, attachment, variant):
        key = (attachment.pk, variant)
        if key not in self.assets:
            asset = Asset(attachment, variant)
            ext = "webp" if variant != "file" else _extension(attachment.name)
            asset.path = f"{MEDIA}/{attachment.pk}-{variant}.{ext}"  # renamed by content when the site is written
            self.assets[key] = asset
        return self.assets[key].path

    def from_url(self, url, size=LARGE):
        match = FILE_URL.match(url or "")
        return self.picture(match.group(3), size) if match else None


def _extension(name):
    ext = (name.rsplit(".", 1)[-1] if "." in name else "bin").lower()
    return ext if re.fullmatch(r"[a-z0-9]{1,8}", ext) else "bin"


def content_path(asset, data):
    """The final path of an asset: named after its content."""
    digest = hashlib.sha256(data).hexdigest()[:20]
    ext = asset.path.rsplit(".", 1)[-1]
    return f"{MEDIA}/{digest}.{ext}"


# ---------------------------------------------------------------- the snapshot

def build(publication):
    with override(publication.language):
        return _build(publication)


def _build(publication):
    viewer = PublicationViewer(publication)
    collector = Collector(viewer, publication)
    school = SchoolSettings.load()
    tables = viewer.readable_tables()
    pages = {(t.key, str(pk)) for t in tables for pk in Record.objects.filter(table=t).values_list("pk", flat=True)}
    site = {
        "title": publication.name, "organisation": school.name, "language": publication.language, "indexable": publication.indexable,
        "generated": timezone.localdate().isoformat(), "tables": [], "pages": [], "search": [],
    }
    for table in tables:
        entry, records, search = _table(table, viewer, collector, pages)
        site["tables"].append(entry)
        site["search"] += search
        for record in records:
            site["pages"].append(_page(table, record, viewer, collector, pages))
    site["assets"] = list(collector.assets.values())
    check(site, viewer, tables)
    return site


def _href(url, pages):
    """A link of the app as a link of the site: only to records that are published."""
    match = RECORD_URL.match(url or "")
    if match and (match.group(1), match.group(2)) in pages:
        return f"{match.group(1)}/{match.group(2)}.html"
    return ""


def _fields(table, viewer):
    """The fields the viewer sees, in the table's order, never a secret one."""
    return [f for f in services.fields_of(table, user=viewer) if f.type != "secret" and ft.TYPES.get(f.type)]


def _cell(record, field, context, related, viewer):
    """The text of one value in a list, as the app shows it."""
    if field.type == "formula":
        text, problem = formula.display(record, field, context)
        return "" if problem or text == _("nicht sichtbar") else str(text)
    if field.type in ("link", "backlink", "file", "image"):
        return related.get((record.pk, field.key), "")
    if field.type == "bool":
        return _("Ja") if services.value(record, field) else ""
    if field.type == "rating":
        value = services.value(record, field)
        return "" if value in (None, "") else format(value, "f").rstrip("0").rstrip(".").replace(".", ",")
    return str(services.display(record, field, localized=True))


def _related(table, records, fields, viewer, pages):
    """{(record id, field key): text} for links, backlinks and files of the whole list."""
    texts = {}
    ids = [r.pk for r in records]
    for field in fields:
        if field.type == "link":
            from tables.models import Link

            rows = list(Link.objects.filter(record_id__in=ids, field=field.key).exclude(target_id=None).order_by("order", "created_at"))
            found = links.resolve(viewer, links.kind_of(field), [r.target_id for r in rows], links.target_table(field))
            for row in rows:
                target = found[row.target_id]
                if target["visible"]:
                    texts.setdefault((row.record_id, field.key), []).append(str(target["title"]))
        elif field.type == "backlink":
            source, link = links.backlink_source(field)
            if link is None or not services.may_read(viewer, source):
                continue
            from tables.models import Link

            rows = Link.objects.filter(field=link.key, target_id__in=ids, record__table=source, record__deleted_at=None).select_related("record")
            for row in rows.order_by("record__title"):
                texts.setdefault((row.target_id, field.key), []).append(row.record.title or _("(ohne Titel)"))
        elif field.type in ("file", "image"):
            for row in Attachment.objects.filter(record_id__in=ids, field=field.key).order_by("created_at"):
                texts.setdefault((row.record_id, field.key), []).append(row.name)
    return {key: ", ".join(values) for key, values in texts.items()}


def _table(table, viewer, collector, pages):
    spec = viewer.publication.tables().get(table.key) or {}
    fields = _fields(table, viewer)
    found = look.resolve(table, viewer, fields)
    records = list(Record.objects.filter(table=table).order_by("title", "pk"))
    for record in records:
        record.table = table
    context = formula.Context(viewer)
    if any(f.type == "formula" for f in fields):
        context.prefetch(records)
    related = _related(table, records, [f for f in fields if f.type in ("link", "backlink", "file", "image")], viewer, pages)
    covers = look.first_files(table, [r.pk for r in records], found["cover"]) if found["cover"] is not None else {}
    columns = [f for f in fields if f.in_grid and f.type in LIST_TYPES]
    named = services.title_field(table, fields)
    if named is not None:
        columns = [f for f in columns if f.key != named.key]
    subtitle = found["subtitle"]
    rows, search = [], []
    for record in records:
        cells = {f.key: _cell(record, f, context, related, viewer) for f in fields if f.type in LIST_TYPES}
        rating = None
        if found["rating"] is not None and services.value(record, found["rating"]) not in (None, ""):
            rating = {"value": float(services.value(record, found["rating"])), "stars": ft.stars(found["rating"])}
        row = {
            "id": str(record.pk), "title": record.title or _("(ohne Titel)"), "href": f"{table.key}/{record.pk}.html",
            "cover": collector.from_url(covers.get(record.pk), THUMB) if covers.get(record.pk) else None,
            "flag": cells.get(found["flag"].key, "") if found["flag"] else "",
            "subtitle": " · ".join(t for t in (cells.get(f.key, "") for f in subtitle) if t),
            "rating": rating,
            "cells": [{"key": f.key, "text": cells.get(f.key, ""), "type": f.type,
                       "rating": float(services.value(record, f)) if f.type == "rating" and services.value(record, f) not in (None, "") else None,
                       "stars": ft.stars(f) if f.type == "rating" else 0} for f in columns],
        }
        rows.append(row)
        # Only published values: never Record.search_text, which holds every searchable field of the record.
        words = " ".join(t for f in fields if (f.type in services.SEARCHABLE or f.type in ("formula", "link")) and f is not named
                         and (t := cells.get(f.key, "")))
        search.append({"t": table.display_name, "n": row["title"], "s": row["subtitle"], "u": row["href"],
                       "x": f"{row['title']} {words}".lower()[:SEARCH_LIMIT]})
    layout = spec.get("layout") if spec.get("layout") in look.LAYOUTS else found["layout"]
    if found["cover"] is None:
        layout = "list"
    entry = {
        "key": table.key, "name": table.display_name, "description": table.description, "icon": table.icon, "href": f"{table.key}/index.html",
        "layout": layout, "shape": found["shape"], "count": len(rows), "rows": rows,
        "covers": [row["cover"] for row in rows if row["cover"]][:4],
        "columns": [{"key": f.key, "label": str(f.display_label), "type": f.type} for f in columns],
        "tmdb": bool((table.look or {}).get("tmdb")),
        "title_label": str(named.display_label) if named is not None else _("Name"),
        "field_keys": sorted({f.key for f in fields}),
    }
    return entry, records, search


def _tree(table, viewer):
    """The record page's layout for the publication's role, without blocks (the history is never published)
    and without conditions on fields that are not published (whether a field shows must not tell anything
    about one that does not)."""
    tree = table_masks.tree_for(table, SimpleNamespace(role=viewer.publication.role))
    seen = viewer.visible_keys(table)
    tabs = []
    for tab in tree.get("tabs", []):
        sections = []
        for section in tab.get("sections", []):
            items = []
            for item in section.get("items", []):
                if item.get("type") == "block":
                    continue
                item = dict(item)
                if (item.get("show_if") or {}).get("field") and item["show_if"]["field"] not in seen:
                    item.pop("show_if")
                items.append(item)
            sections.append({**section, "items": items})
        tabs.append({**tab, "sections": sections})
    return {"tabs": tabs}


def _item(entry, collector, pages):
    """One field of a record page as plain data."""
    field = entry["fd"]
    kind = field.type
    item = {"w": entry["w"], "kind": "field", "key": field.key, "type": kind, "label": str(entry["label"]), "help": str(entry.get("help") or ""),
            "text": "", "rating": None, "stars": 0, "targets": [], "files": []}
    if kind in ("link", "backlink"):
        item["targets"] = [{"title": str(t["title"]), "href": _href(t.get("url"), pages)} for t in entry.get("targets", []) if t.get("visible")]
    elif kind in ("file", "image"):
        for f in entry.get("files", []):
            match = FILE_URL.match(f["url"])
            href = collector.file(match.group(3)) if match else None
            thumb = collector.picture(match.group(3), THUMB) if match and kind == "image" else None
            item["files"].append({"name": f["name"], "href": href or "", "thumb": thumb or ""})
    elif kind == "formula":
        text = str(entry.get("value") or "")
        item["text"] = "" if entry.get("problem") or text == _("nicht sichtbar") else text
    elif kind == "rating":
        value = entry.get("value")
        item["rating"] = float(value) if value not in (None, "") else None
        item["stars"], item["text"] = entry.get("stars") or 5, str(entry.get("display") or "")
    elif kind == "bool":
        item["text"] = _("Ja") if entry.get("value") else _("Nein")
    elif kind in ("date", "choice", "multichoice", "number", "datetime"):
        item["text"] = str(entry.get("display") or "") if entry.get("value") not in (None, "", []) else ""
    else:
        item["text"] = str(entry.get("value") or "")
    return item


def _page(table, record, viewer, collector, pages):
    record.table = table
    tabs = []
    for tab in table_masks.build_view(_tree(table, viewer), record, viewer, readonly=True):
        sections = []
        for section in tab["sections"]:
            items = []
            for entry in section["items"]:
                if entry["type"] == "field":
                    items.append(_item(entry, collector, pages))
                elif entry["type"] == "note":
                    items.append({"w": entry["w"], "kind": "note", "text": str(entry.get("text") or "")})
            if items:
                sections.append({"title": str(section["title"] or ""), "items": items})
        if sections:
            tabs.append({"id": f"tab{len(tabs) + 1}", "title": str(tab["title"] or ""), "sections": sections})
    head = look.head(record, viewer, False)
    if head:
        rating = head.get("rating")
        head = {
            "cover": collector.from_url(head["cover"], LARGE) if head.get("cover") else None,
            "backdrop": collector.from_url(head["backdrop"], LARGE) if head.get("backdrop") else None,
            "blurred": head["blurred"], "shape": head["shape"], "flag": str(head.get("flag") or ""),
            "subtitle": [str(t) for t in head.get("subtitle") or []], "text": str(head.get("text") or ""),
            "rating": {"value": float(rating["value"]), "stars": rating.get("stars") or 5, "text": str(rating.get("display") or "")}
            if rating and rating.get("value") not in (None, "") else None,
        }
        if not head["cover"] and not head["backdrop"]:
            head = None
    cast = []
    if (table.look or {}).get("tmdb"):
        from tables import tmdb

        for person in tmdb.cast_context(record, viewer):
            photo = collector.from_url(person.get("photo"), THUMB) if person.get("photo") else None
            cast.append({"name": str(person["name"]), "role": str(person.get("role") or ""), "initials": str(person.get("initials") or ""),
                         "photo": photo or "", "href": _href(person.get("url"), pages)})
    return {"table": table.key, "table_name": table.display_name, "id": str(record.pk), "title": record.title or _("(ohne Titel)"),
            "path": f"{table.key}/{record.pk}.html", "head": head, "tabs": tabs, "cast": cast, "tmdb": bool((table.look or {}).get("tmdb"))}


# ---------------------------------------------------------------- the check before anything is written

def check(site, viewer, tables):
    """Stop when the snapshot names a field the viewer does not see, or a table it does not read."""
    by_key = {t.key: t for t in tables}
    for entry in site["tables"]:
        table = by_key.get(entry["key"])
        if table is None or viewer.table_right(table) != "read":
            raise Unsafe(f"table {entry['key']}")
        seen = viewer.visible_keys(table)
        named = {f.key for f in services.fields_of(table) if f.type == "secret"}
        keys = set(entry["field_keys"]) | {c["key"] for c in entry["columns"]}
        for row in entry["rows"]:
            keys |= {c["key"] for c in row["cells"]}
        if not keys <= seen or keys & named:
            raise Unsafe(f"fields {sorted(keys - seen)} of {entry['key']}")
    for page in site["pages"]:
        table = by_key.get(page["table"])
        if table is None:
            raise Unsafe(f"page of {page['table']}")
        seen = viewer.visible_keys(table)
        for tab in page["tabs"]:
            for section in tab["sections"]:
                for item in section["items"]:
                    if item["kind"] == "field" and item["key"] not in seen:
                        raise Unsafe(f"field {item['key']} of {page['table']}")
    for asset in site["assets"]:
        attachment = asset.attachment
        if attachment.field not in viewer.visible_keys(attachment.record.table):
            raise Unsafe(f"file of {attachment.field}")


def summary(site):
    """What goes public, for the confirmation: tables with their fields, records and files."""
    return {
        "tables": [{"name": t["name"], "count": t["count"]} for t in site["tables"]],
        "records": sum(t["count"] for t in site["tables"]),
        "files": len(site["assets"]),
    }


def published_fields(publication):
    """[(table, [fields])] the viewer sees: what the confirmation lists."""
    viewer = PublicationViewer(publication)
    return [(t, _fields(t, viewer)) for t in viewer.readable_tables()]

