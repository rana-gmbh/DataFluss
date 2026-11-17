"""How the records of a table look (Table.look).

    {"cover": "f_poster", "backdrop": "f_hintergrund", "subtitle": ["f_jahr", "f_art"], "flag": "f_status",
     "rating": "f_bewertung", "text": "f_handlung", "layout": "gallery", "shape": "poster",
     "tmdb": {"fields": {…}}}

The cover (an image field) makes the list offer a gallery and gives the record page a head; the backdrop is
the picture behind that head (without one, the cover is shown blurred). Subtitle fields, a flag (a choice
shown on the card) and a rating join the title. "tmdb" belongs to tables/tmdb.py. Everything is optional
and refers to fields by key; a field that is gone, archived, of another type or hidden for the role is
left out, so the look never shows more than the record page would.
"""
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core import fieldrules, fieldtypes as ft

from . import services
from .models import Attachment

LAYOUTS = ("list", "gallery")
SHAPES = ("poster", "square", "wide")
SUBTITLE_TYPES = ("text", "number", "rating", "choice", "multichoice", "date", "datetime", "email", "phone", "url", "bool", "formula")
TEXT_TYPES = ("textarea", "text")
MAX_SUBTITLES = 3


def resolve(table, user=None, fields=None):
    """The look with Field objects in place of keys, for what this user may see."""
    look = table.look or {}
    by_key = {f.key: f for f in (fields if fields is not None else services.fields_of(table, user=user))}

    def pick(name, types):
        field = by_key.get(look.get(name) or "")
        return field if field is not None and field.type in types else None

    subtitle = [by_key[k] for k in look.get("subtitle") or [] if k in by_key and by_key[k].type in SUBTITLE_TYPES][:MAX_SUBTITLES]
    cover = pick("cover", ("image",))
    return {
        "cover": cover, "backdrop": pick("backdrop", ("image",)), "subtitle": subtitle, "flag": pick("flag", ("choice",)),
        "rating": pick("rating", ("rating",)), "text": pick("text", TEXT_TYPES),
        "layout": look.get("layout") if look.get("layout") in LAYOUTS and cover is not None else "list",
        "shape": look.get("shape") if look.get("shape") in SHAPES else "poster",
    }


def has_head(resolved):
    return resolved["cover"] is not None or resolved["backdrop"] is not None


def grid_config(table, user):
    """What static/js/grid.js needs for the gallery; None when the table has no cover."""
    found = resolve(table, user)
    if found["cover"] is None:
        return None
    return {"cover": found["cover"].key, "subtitle": [f.key for f in found["subtitle"]], "flag": found["flag"].key if found["flag"] else "",
            "rating": found["rating"].key if found["rating"] else "", "layout": found["layout"], "shape": found["shape"]}


def file_url(table, record_id, attachment_id):
    return f"/tabellen/{table.key}/{record_id}/datei/{attachment_id}/"


def first_files(table, record_ids, field):
    """{record id: url of its first file in `field`}, in one query."""
    found = {}
    for record_id, pk in Attachment.objects.filter(record_id__in=record_ids, field=field.key).order_by("created_at").values_list("record_id", "pk"):
        found.setdefault(record_id, file_url(table, record_id, pk))
    return found


def _text(record, field, user):
    if field.type == "formula":
        from . import formula

        text, problem = formula.display(record, field, formula.Context(user))
        return "" if problem else str(text)
    if field.type == "bool":
        return field.display_label if services.value(record, field) else ""
    if field.type == "rating":
        return ""
    return str(services.display(record, field, localized=True))


def head(record, user, editable):
    """The head of a record page: pictures, subtitle, flag, rating and summary. None without a cover or backdrop."""
    from . import masks as table_masks

    table = record.table
    fields = services.fields_of(table, user=user)
    found = resolve(table, user, fields)
    if not has_head(found):
        return None
    cover = first_files(table, [record.pk], found["cover"]).get(record.pk) if found["cover"] else None
    backdrop = first_files(table, [record.pk], found["backdrop"]).get(record.pk) if found["backdrop"] else None
    if not cover and not backdrop:
        return None
    rating = None
    if found["rating"] is not None:
        may_change = editable and fieldrules.may_change(user, fieldrules.table_entity(table), found["rating"].key)
        rating = table_masks._field_entry({"w": 12}, record, found["rating"], user, may_change)
        rating["label"] = found["rating"].display_label
    flag = services.display(record, found["flag"], localized=True) if found["flag"] else ""
    return {
        "cover": cover, "backdrop": backdrop or cover, "blurred": not backdrop, "shape": found["shape"],
        "subtitle": [t for t in (_text(record, f, user) for f in found["subtitle"]) if t],
        "flag": flag, "rating": rating, "text": _text(record, found["text"], user) if found["text"] else "",
    }


def clean(table, data):
    """The look from the table's settings form; what tables/tmdb.py keeps there stays."""
    fields = {f.key: f for f in services.fields_of(table)}

    def field_of(name, types):
        key = (data.get(name) or "").strip()
        if not key:
            return None
        if key not in fields or fields[key].type not in types:
            raise ValidationError(_("Unbekanntes Feld."))
        return key

    look = {k: v for k, v in (table.look or {}).items() if k == "tmdb"}
    for name, types in (("cover", ("image",)), ("backdrop", ("image",)), ("flag", ("choice",)), ("rating", ("rating",)), ("text", TEXT_TYPES)):
        if key := field_of(name, types):
            look[name] = key
    subtitle = [k for k in (data.getlist("subtitle") if hasattr(data, "getlist") else data.get("subtitle") or []) if k]
    if any(k not in fields or fields[k].type not in SUBTITLE_TYPES for k in subtitle):
        raise ValidationError(_("Unbekanntes Feld."))
    if subtitle:
        look["subtitle"] = list(dict.fromkeys(subtitle))[:MAX_SUBTITLES]
    if data.get("layout") == "gallery" and "cover" in look:
        look["layout"] = "gallery"
    if data.get("shape") in SHAPES and data.get("shape") != "poster":
        look["shape"] = data["shape"]
    return look


def choices(table):
    """Fields offered in the settings form, by role in the look."""
    fields = [f for f in services.fields_of(table) if ft.TYPES.get(f.type)]
    return {
        "images": [f for f in fields if f.type == "image"],
        "subtitle": [f for f in fields if f.type in SUBTITLE_TYPES],
        "flags": [f for f in fields if f.type == "choice"],
        "ratings": [f for f in fields if f.type == "rating"],
        "texts": [f for f in fields if f.type in TEXT_TYPES],
    }
