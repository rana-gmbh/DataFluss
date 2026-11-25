"""Checking template files (tables/blueprints.py) without the database, so the template site (web/) can use it too.

- check(data): what the platform's own templates must meet before they can be applied.
- check_untrusted(data): the much stricter check for templates someone else wrote (uploads to datafluss.de,
  later files installed from there). Everything not known is refused: every level has a list of keys with their
  types, every reference must point into the template, and sizes have limits. A template is data only and runs no
  code, but its texts reach pages, styles and printouts, so only plain values may pass.
- load(raw): reads an uploaded file within the limits; notes(data) and summary(data) for people reviewing one;
  canonical(data, catalog) the file as the catalog hands it out.

Nothing here imports models: only core/fieldtypes.py, tables/formula.py's token rules and prints/presets.py.
"""
import copy
import json
import re
from pathlib import Path

from django.utils.translation import gettext as _

from core import fieldtypes as ft
from prints.presets import PRESETS

FORMAT = 1
LIBRARY = Path(__file__).resolve().parent / "library"
KEY = re.compile(r"^[a-z0-9_]{1,20}$")
FIELD_KEY = re.compile(r"^f_[a-z0-9_]{1,57}$")
RIGHTS = ("none", "read", "edit")

# Limits for templates of others (the platform's own are far below them).
MAX_BYTES = 1024 * 1024
MAX_DEPTH = 20
MAX_TABLES = 30
MAX_FIELDS = 150
MAX_OPTIONS = 200
MAX_ITEMS = 50  # masks, prints, views each
MAX_DEMO_ROWS = 200
MAX_TREE_NODES = 2000
# The roles a template may give rights to (IT always has them all): the platform's and those of a database
# without Schulverwaltung. Unknown roles are refused, so a template cannot reach a module's roles.
UNTRUSTED_ROLES = ("team", "gast", "sekretariat", "schulleitung")

TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
ICON = re.compile(r"^[a-z0-9-]{1,30}$")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
LANGUAGE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f  ]")
URL = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
REF = re.compile(r"^(@[a-z0-9_]{1,20}\.)?f_[a-z0-9_]{1,57}$")
FORMULA_REF = re.compile(r'"(?:[^"\\]|\\.)*"|\{([^{}]*)\}')
FORMULA_RESULTS = ("text", "number", "bool", "date", "datetime")
LAYOUTS = ("list", "gallery")
SHAPES = ("poster", "square", "wide")
MASK_BLOCKS = ("history",)
PRINT_ELEMENTS = ("heading", "text", "name", "field", "photo", "logo", "qr", "list", "signature", "spacer", "divider")
ALIGN = ("left", "center", "right", "justify")
VALIGN = ("flex-start", "center", "flex-end")
TMDB_FIELDS = ("title", "original_title", "kind", "year", "runtime", "seasons", "episodes", "genres", "director", "creators",
               "cast", "overview", "tagline", "poster", "backdrop", "images", "cast_photos", "tmdb_id", "tmdb_url", "synced")
TMDB_PERSON = ("name", "photo", "tmdb_id", "tmdb_url", "synced")
# What each level may hold (also used by blueprints.export to leave out what the designers keep besides).
TABLE_KEYS = ("key", "name", "names", "description", "descriptions", "icon", "in_nav", "rights", "section", "title_field", "look", "fields")
FIELD_KEYS = ("key", "label", "labels", "help_text", "type", "options", "config", "required", "unique", "in_grid")
OPTION_KEYS = ("value", "color", "labels")
FIELD_CONFIG = {"number": ("decimals", "unit", "min", "max"), "rating": ("max", "half"), "link": ("target", "table", "multiple", "reverse"),
                "file": ("multiple",), "image": ("multiple",), "formula": ("expression", "result", "decimals"), "backlink": ("link",)}
LOOK_KEYS = ("cover", "backdrop", "subtitle", "flag", "rating", "text", "layout", "shape", "tmdb")
TAB_KEYS = ("id", "title", "titles", "sections")
SECTION_KEYS = ("id", "title", "titles", "items")
MASK_ITEM_KEYS = ("type", "w", "field", "label", "labels", "help", "block", "text", "show_if")
PAGE_KEYS = ("preset", "margin", "footer", "footer_text", "cell_padding")
COLUMN_KEYS = ("w", "valign", "items")
ELEMENT_KEYS = ("type", "text", "style", "size", "bold", "align", "color", "key", "label", "hide_empty", "mono", "show_label",
                "w_mm", "h_mm", "size_mm", "value", "columns", "link")
VIEW_KEYS = ("filters", "sort", "columns", "layout", "scope", "preset")


def library_ids():
    """The ids of the platform's own templates: nobody else may use them."""
    ids = set()
    for path in LIBRARY.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            ids.add(data["id"])
    return ids


# ---------------------------------------------------------------- the platform's check

def check(data):
    """Problems of a template file, in plain words; empty when it can be applied."""
    problems = []
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return [_("Unbekanntes Vorlagenformat.")]
    if not isinstance(data.get("id"), str) or not KEY.match(data["id"]):
        problems.append(_("Die Vorlage braucht eine Kennung aus Kleinbuchstaben, Ziffern und _."))
    if not isinstance(data.get("version"), int) or isinstance(data.get("version"), bool) or data["version"] < 1:
        problems.append(_("Die Vorlage braucht eine Versionsnummer."))
    if not data.get("title"):
        problems.append(_("Die Vorlage braucht einen Titel."))
    tables = data.get("tables")
    if not isinstance(tables, list) or not tables:
        return problems + [_("Die Vorlage enthält keine Tabellen.")]
    keys = set()
    for table in tables:
        key = table.get("key") if isinstance(table, dict) else None
        if not isinstance(key, str) or not KEY.match(key) or key in keys:
            problems.append(_("Tabelle %(key)s: ungültige oder doppelte Kennung.") % {"key": key})
            continue
        keys.add(key)
        if not table.get("name"):
            problems.append(_("Tabelle %(key)s: Name fehlt.") % {"key": key})
        for role, right in (table.get("rights") or {}).items():
            if right not in RIGHTS:
                problems.append(_("Tabelle %(key)s: unbekanntes Recht %(right)s.") % {"key": key, "right": right})
        field_keys = set()
        for field in table.get("fields") or []:
            fkey = field.get("key") if isinstance(field, dict) else None
            if not isinstance(fkey, str) or not FIELD_KEY.match(fkey) or fkey in field_keys:
                problems.append(_("Tabelle %(key)s: ungültiges oder doppeltes Feld %(field)s.") % {"key": key, "field": fkey})
                continue
            field_keys.add(fkey)
            kind = ft.TYPES.get(field.get("type"))
            if kind is None or not kind.tables:
                problems.append(_("Feld %(field)s: unbekannter Typ.") % {"field": fkey})
            if not field.get("label"):
                problems.append(_("Feld %(field)s: Bezeichnung fehlt.") % {"field": fkey})
            if kind is not None and kind.options and not field.get("options"):
                problems.append(_("Feld %(field)s: Optionen fehlen.") % {"field": fkey})
            config = field.get("config") or {}
            if field.get("type") == "link":
                target = config.get("target")
                if target not in ("record", "pupil", "staff", "asset"):
                    problems.append(_("Feld %(field)s: unbekanntes Ziel der Verknüpfung.") % {"field": fkey})
                elif target == "record" and config.get("table") not in {t.get("key") for t in tables if isinstance(t, dict)}:
                    problems.append(_("Feld %(field)s: Die verknüpfte Tabelle fehlt in der Vorlage.") % {"field": fkey})
            if field.get("type") == "formula" and not config.get("expression"):
                problems.append(_("Feld %(field)s: Formel fehlt.") % {"field": fkey})
        if table.get("look") is not None and not isinstance(table.get("look"), dict):
            problems.append(_("Tabelle %(key)s: ungültige Darstellung.") % {"key": key})
        if table.get("title_field") and table["title_field"] not in field_keys:
            problems.append(_("Tabelle %(key)s: Das Titelfeld fehlt.") % {"key": key})
    for group in ("masks", "prints", "views"):
        for item in data.get(group) or []:
            if not isinstance(item, dict) or item.get("table") not in keys or not item.get("name"):
                problems.append(_("%(group)s: Eintrag ohne Tabelle oder Namen.") % {"group": group})
    for table_key in (data.get("demo") or {}):
        if table_key not in keys:
            problems.append(_("Beispieldaten für eine unbekannte Tabelle %(key)s.") % {"key": table_key})
    return problems


# ---------------------------------------------------------------- reading an upload

def load(raw):
    """(data, problems) for the bytes of an uploaded file."""
    if len(raw) > MAX_BYTES:
        return None, [_("Die Datei ist zu groß (höchstens 1 MB).")]
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, [_("Die Datei ist keine gültige Vorlage (kein JSON).")]
    if _depth(data) > MAX_DEPTH:
        return None, [_("Die Vorlage ist zu tief verschachtelt.")]
    return data, []


def _depth(value, level=0):
    if level > MAX_DEPTH:
        return level
    if isinstance(value, dict):
        return max([_depth(v, level + 1) for v in value.values()] or [level + 1])
    if isinstance(value, list):
        return max([_depth(v, level + 1) for v in value] or [level + 1])
    return level


def _nodes(value):
    if isinstance(value, dict):
        return 1 + sum(_nodes(v) for v in value.values())
    if isinstance(value, list):
        return 1 + sum(_nodes(v) for v in value)
    return 1


# ---------------------------------------------------------------- the strict check

class _Problems(list):
    def add(self, where, text):
        self.append(f"{where}: {text}" if where else text)


class _Strict:
    """Walks a template and notes every value that is not exactly what it may be."""

    def __init__(self, data, reserved):
        self.data, self.reserved, self.p = data, reserved, _Problems()
        self.tables = {t["key"]: t for t in data["tables"]}
        self.fields = {key: {f["key"]: f for f in (t.get("fields") or [])} for key, t in self.tables.items()}

    # -- small helpers

    def keys(self, obj, allowed, where):
        if not isinstance(obj, dict):
            self.p.add(where, _("erwartet ein Objekt."))
            return False
        unknown = sorted(set(obj) - set(allowed))
        if unknown:
            self.p.add(where, _("unbekannte Angabe %(keys)s.") % {"keys": ", ".join(map(str, unknown[:5]))})
        return True

    def text(self, value, limit, where, required=False, multiline=False):
        if value is None or value == "":
            if required:
                self.p.add(where, _("fehlt."))
            return
        if not isinstance(value, str):
            self.p.add(where, _("muss ein Text sein."))
        elif len(value) > limit:
            self.p.add(where, _("zu lang (höchstens %(n)s Zeichen).") % {"n": limit})
        elif CONTROL.search(value) or (not multiline and ("\n" in value or "\t" in value)):
            self.p.add(where, _("enthält Steuerzeichen."))

    def names(self, value, limit, where):
        if value is None:
            return
        if not isinstance(value, dict) or len(value) > 10:
            self.p.add(where, _("ungültige Übersetzungen."))
            return
        for code, text in value.items():
            if not isinstance(code, str) or not LANGUAGE.match(code):
                self.p.add(where, _("unbekannte Sprache %(code)s.") % {"code": code})
            self.text(text, limit, f"{where} {code}")

    def boolean(self, value, where):
        if value is not None and not isinstance(value, bool):
            self.p.add(where, _("muss ja oder nein sein."))

    def number(self, value, where, low, high, whole=False):
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (whole and not isinstance(value, int)):
            self.p.add(where, _("muss eine Zahl sein."))
        elif not low <= value <= high:
            self.p.add(where, _("muss zwischen %(low)s und %(high)s liegen.") % {"low": low, "high": high})

    def choice(self, value, allowed, where, optional=True):
        if value is None and optional:
            return
        if value not in allowed:
            self.p.add(where, _("ungültiger Wert %(value)s.") % {"value": str(value)[:40]})

    def field_ref(self, table, key, where, types=None):
        field = self.fields.get(table, {}).get(key) if isinstance(key, str) else None
        if field is None:
            self.p.add(where, _("verweist auf ein Feld, das es nicht gibt."))
        elif types and field.get("type") not in types:
            self.p.add(where, _("verweist auf ein Feld der falschen Art."))
        return field

    def sized(self, value, limit, where):
        if value is None:
            return []
        if not isinstance(value, list):
            self.p.add(where, _("erwartet eine Liste."))
            return []
        if len(value) > limit:
            self.p.add(where, _("zu viele Einträge (höchstens %(n)s).") % {"n": limit})
            return []
        return value

    # -- the template

    def run(self):
        data = self.data
        self.keys(data, ("format", "id", "version", "title", "titles", "description", "descriptions", "requires", "database",
                         "order", "listed", "changes", "tables", "masks", "prints", "views", "demo", "catalog", "datafluss"), "")
        if data["id"] in self.reserved:
            self.p.add("", _("Die Kennung %(id)s ist vergeben. Bitte eine eigene wählen.") % {"id": data["id"]})
        self.text(data.get("title"), 100, _("Titel"), required=True)
        self.names(data.get("titles"), 100, _("Titel"))
        self.text(data.get("description"), 1000, _("Beschreibung"), multiline=True)
        self.names(data.get("descriptions"), 1000, _("Beschreibung"))
        if data.get("requires") not in (None, []):
            self.p.add("", _("Eine geteilte Vorlage ist eine Datenbank für sich und kann keine Module voraussetzen."))
        self.boolean(data.get("database"), "database")
        self.boolean(data.get("listed"), "listed")
        self.number(data.get("order"), "order", 0, 10000, whole=True)
        self.number(data.get("version"), _("Version"), 1, 100000, whole=True)
        self.text(data.get("datafluss"), 20, "datafluss")
        if data.get("catalog") is not None and self.keys(data["catalog"], ("source", "author", "license", "url"), "catalog"):
            for key, value in data["catalog"].items():
                self.text(value, 200, f"catalog {key}")
        changes = data.get("changes")
        if changes is not None and self.keys(changes, [k for k in changes if isinstance(k, str) and k.isdigit()] if isinstance(changes, dict) else (), _("Änderungen")):
            if len(changes) > 200:
                self.p.add(_("Änderungen"), _("zu viele Einträge."))
            for version, text in list(changes.items())[:200]:
                self.names(text, 500, _("Änderungen in Version %(n)s") % {"n": version})
        if len(data["tables"]) > MAX_TABLES:
            self.p.add("", _("Zu viele Tabellen (höchstens %(n)s).") % {"n": MAX_TABLES})
            return self.p
        for table in data["tables"]:
            self.table(table)
        for group, limit, check in (("masks", MAX_ITEMS, self.mask), ("prints", MAX_ITEMS, self.print_), ("views", MAX_ITEMS, self.view)):
            for item in self.sized(data.get(group), limit, group):
                check(item)
        self.demo(data.get("demo"))
        return self.p

    def table(self, table):
        key = table["key"]
        where = _("Tabelle %(key)s") % {"key": key}
        self.keys(table, TABLE_KEYS, where)
        self.text(table.get("name"), 100, where, required=True)
        self.names(table.get("names"), 100, where)
        self.text(table.get("description"), 300, where, multiline=True)
        self.names(table.get("descriptions"), 300, where)
        self.text(table.get("section"), 60, where)
        self.boolean(table.get("in_nav"), where)
        if table.get("icon") is not None and not (isinstance(table["icon"], str) and ICON.match(table["icon"])):
            self.p.add(where, _("ungültiges Symbol."))
        rights = table.get("rights")
        if rights is not None and self.keys(rights, UNTRUSTED_ROLES, f"{where} rights"):
            for role, right in rights.items():
                self.choice(right, ("read", "edit"), f"{where} rights {role}", optional=False)
        if table.get("title_field") is not None and not isinstance(table["title_field"], str):
            self.p.add(where, _("ungültiges Titelfeld."))
        fields = self.sized(table.get("fields"), MAX_FIELDS, where)
        for field in fields:
            self.field(key, field)
        if table.get("look") is not None:
            self.look(key, table["look"], where)

    def field(self, table, field):
        key = field["key"]
        where = _("Feld %(field)s") % {"field": key}
        self.keys(field, FIELD_KEYS, where)
        self.text(field.get("label"), 100, where, required=True)
        self.names(field.get("labels"), 100, where)
        self.text(field.get("help_text"), 200, where)
        for flag in ("required", "unique", "in_grid"):
            self.boolean(field.get(flag), where)
        kind = field.get("type")
        options = self.sized(field.get("options"), MAX_OPTIONS, where)
        if options and not ft.TYPES[kind].options:
            self.p.add(where, _("Nur Auswahlfelder haben Optionen."))
        for option in options:
            if self.keys(option, OPTION_KEYS, where):
                self.text(option.get("value"), 100, where, required=True)
                if option.get("color") not in (None, "") and not (isinstance(option["color"], str) and COLOR.match(option["color"])):
                    self.p.add(where, _("ungültige Farbe."))
                self.names(option.get("labels"), 100, where)
        config = field.get("config")
        if config is None:
            return
        if not self.keys(config, FIELD_CONFIG.get(kind, ()), where):
            return
        if kind == "number":
            self.number(config.get("decimals"), where, 0, 6, whole=True)
            self.text(config.get("unit"), 10, where)
            for bound in ("min", "max"):
                if config.get(bound) is not None and not _numeric(config[bound]):
                    self.p.add(where, _("Grenze muss eine Zahl sein."))
        elif kind == "rating":
            self.number(config.get("max"), where, 3, 10, whole=True)
            self.boolean(config.get("half"), where)
        elif kind in ("file", "image"):
            self.boolean(config.get("multiple"), where)
        elif kind == "link":
            if config.get("target") != "record" or config.get("table") not in self.tables:
                self.p.add(where, _("Eine geteilte Vorlage verknüpft nur ihre eigenen Tabellen."))
            self.boolean(config.get("multiple"), where)
            self.text(config.get("reverse"), 100, where)
        elif kind == "formula":
            self.formula(config.get("expression"), where)
            self.choice(config.get("result"), FORMULA_RESULTS, where)
            self.number(config.get("decimals"), where, 0, 6, whole=True)
        elif kind == "backlink":
            link = config.get("link")
            other, _dot, fkey = link.partition(".") if isinstance(link, str) else ("", "", "")
            target = self.fields.get(other, {}).get(fkey)
            if target is None or target.get("type") != "link" or (target.get("config") or {}).get("table") != table:
                self.p.add(where, _("Die Gegenseite gehört zu keiner Verknüpfung dieser Vorlage."))

    def formula(self, expression, where):
        self.text(expression, 2000, where, required=True, multiline=True)
        if not isinstance(expression, str):
            return
        for match in FORMULA_REF.finditer(expression):
            inner = match.group(1)
            if inner is None:
                continue  # a string literal
            inner = inner.strip()
            if not REF.match(inner) or (inner.startswith("@") and inner[1:].split(".")[0] not in self.tables):
                self.p.add(where, _("Die Formel verweist auf %(ref)s, das es in der Vorlage nicht gibt.") % {"ref": inner[:40]})

    def look(self, table, look, where):
        where = f"{where} look"
        if not self.keys(look, LOOK_KEYS, where):
            return
        for name, types in (("cover", ("image",)), ("backdrop", ("image",)), ("flag", ("choice",)), ("rating", ("rating",)),
                            ("text", ("text", "textarea", "formula"))):
            if look.get(name) not in (None, ""):
                self.field_ref(table, look[name], f"{where} {name}", types)
        for key in self.sized(look.get("subtitle"), 3, f"{where} subtitle"):
            self.field_ref(table, key, f"{where} subtitle")
        self.choice(look.get("layout"), LAYOUTS, where)
        self.choice(look.get("shape"), SHAPES, where)
        if look.get("tmdb") is not None:
            self.tmdb(table, look["tmdb"], f"{where} tmdb")

    def tmdb(self, table, tmdb, where):
        if not self.keys(tmdb, ("fields", "genres", "kinds", "people", "person", "watch", "language"), where):
            return
        if tmdb.get("language") is not None and not (isinstance(tmdb["language"], str) and LANGUAGE.match(tmdb["language"])):
            self.p.add(where, _("ungültige Sprache."))
        for group, names in (("fields", TMDB_FIELDS), ("person", TMDB_PERSON)):
            if tmdb.get(group) is not None and self.keys(tmdb[group], names, f"{where} {group}"):
                for name, key in tmdb[group].items():
                    self.field_ref(table, key, f"{where} {group} {name}")
        if tmdb.get("kinds") is not None and self.keys(tmdb["kinds"], ("movie", "tv"), f"{where} kinds"):
            for value in tmdb["kinds"].values():
                self.text(value, 100, f"{where} kinds")
        genres = tmdb.get("genres")
        if genres is not None and self.keys(genres, [k for k in genres if isinstance(k, str) and k.isdigit() and len(k) < 8] if isinstance(genres, dict) else (), f"{where} genres"):
            for value in list(genres.values())[:200]:
                for name in (value if isinstance(value, list) and len(value) <= 5 else [value]):
                    self.text(name, 100, f"{where} genres")
        people = tmdb.get("people")
        if people is not None and self.keys(people, ("cast", "directors"), f"{where} people"):
            for name, spec in people.items():
                if self.keys(spec, ("table", "link"), f"{where} people {name}"):
                    if spec.get("table") not in self.tables:
                        self.p.add(f"{where} people {name}", _("verweist auf eine Tabelle, die es nicht gibt."))
                    self.field_ref(table, spec.get("link"), f"{where} people {name}", ("link",))
        watch = tmdb.get("watch")
        if watch is not None and self.keys(watch, ("status", "watched", "watchlist", "viewings"), f"{where} watch"):
            self.field_ref(table, watch.get("status"), f"{where} watch status", ("choice",))
            self.text(watch.get("watched"), 100, f"{where} watch")
            self.text(watch.get("watchlist"), 100, f"{where} watch")
            viewings = watch.get("viewings")
            if viewings is not None and self.keys(viewings, ("table", "link", "date", "how"), f"{where} watch viewings"):
                other = viewings.get("table")
                if other not in self.tables:
                    self.p.add(f"{where} watch viewings", _("verweist auf eine Tabelle, die es nicht gibt."))
                else:
                    for name in ("link", "date", "how"):
                        if viewings.get(name) is not None:
                            self.field_ref(other, viewings[name], f"{where} watch viewings {name}")

    def _item_head(self, item, group, extra):
        where = group
        if not self.keys(item, ("table", "name", "names", "description", *extra), where):
            return None
        table = item["table"]
        where = f"{group} „{str(item.get('name'))[:40]}“"
        self.text(item.get("name"), 120 if group == "prints" else 80, where, required=True)
        self.names(item.get("names"), 80, where)
        self.text(item.get("description"), 200, where)
        return table, where

    def mask(self, item):
        head = self._item_head(item, "masks", ("tree",))
        if head is None:
            return
        table, where = head
        tree = item.get("tree")
        if tree is None:
            return
        if not self.keys(tree, ("tabs",), where):
            return
        if _nodes(tree) > MAX_TREE_NODES:
            self.p.add(where, _("zu groß."))
            return
        for tab in self.sized(tree.get("tabs"), 20, where):
            if not self.keys(tab, TAB_KEYS, where):
                continue
            self.token(tab.get("id"), where)
            self.text(tab.get("title"), 80, where)
            self.names(tab.get("titles"), 80, where)
            for section in self.sized(tab.get("sections"), 30, where):
                if not self.keys(section, SECTION_KEYS, where):
                    continue
                self.token(section.get("id"), where)
                self.text(section.get("title"), 120, where)
                self.names(section.get("titles"), 120, where)
                for entry in self.sized(section.get("items"), MAX_FIELDS, where):
                    self.mask_item(table, entry, where)

    def token(self, value, where):
        if value not in (None, "") and not (isinstance(value, str) and TOKEN.match(value)):
            self.p.add(where, _("ungültige Kennung %(id)s.") % {"id": str(value)[:40]})

    def mask_item(self, table, entry, where):
        if not self.keys(entry, MASK_ITEM_KEYS, where):
            return
        kind = entry.get("type")
        self.choice(kind, ("field", "block", "note"), where, optional=False)
        self.number(entry.get("w"), where, 1, 12, whole=True)
        if kind == "field":
            self.field_ref(table, entry.get("field"), where)
        elif kind == "block":
            self.choice(entry.get("block"), MASK_BLOCKS, where, optional=False)
        self.text(entry.get("label"), 200, where)
        self.names(entry.get("labels"), 200, where)
        self.text(entry.get("help"), 200, where)
        self.text(entry.get("text"), 1000, where, multiline=True)
        rule = entry.get("show_if")
        if rule is not None and self.keys(rule, ("field", "op", "value"), where):
            self.field_ref(table, rule.get("field"), where)
            self.choice(rule.get("op"), ("eq", "ne"), where)
            self.text(rule.get("value"), 100, where)

    def print_(self, item):
        head = self._item_head(item, "prints", ("tree",))
        if head is None:
            return
        table, where = head
        tree = item.get("tree")
        if tree is None:
            return
        if not self.keys(tree, ("page", "rows"), where):
            return
        if _nodes(tree) > MAX_TREE_NODES:
            self.p.add(where, _("zu groß."))
            return
        page = tree.get("page")
        if page is not None and self.keys(page, PAGE_KEYS, where):
            self.choice(page.get("preset"), tuple(PRESETS), where)
            margin = page.get("margin")
            if margin is not None:
                if not isinstance(margin, list) or len(margin) != 4:
                    self.p.add(where, _("Ränder brauchen vier Zahlen."))
                else:
                    for value in margin:
                        self.number(value, where, 0, 100)
            self.boolean(page.get("footer"), where)
            self.text(page.get("footer_text"), 200, where)
            if isinstance(page.get("footer_text"), str) and set(page["footer_text"]) & set("'\"\\"):
                self.p.add(where, _("Die Fußzeile darf keine Anführungszeichen enthalten."))
            self.number(page.get("cell_padding"), where, 0, 30)
        for row in self.sized(tree.get("rows"), 100, where):
            if not self.keys(row, ("cols",), where):
                continue
            for col in self.sized(row.get("cols"), 12, where):
                if not self.keys(col, COLUMN_KEYS, where):
                    continue
                self.number(col.get("w"), where, 1, 12)
                self.choice(col.get("valign"), VALIGN, where)
                for element in self.sized(col.get("items"), MAX_ITEMS, where):
                    self.print_element(table, element, where)

    def print_element(self, table, el, where):
        if not self.keys(el, ELEMENT_KEYS, where):
            return
        self.choice(el.get("type"), PRINT_ELEMENTS, where, optional=False)
        self.text(el.get("text"), 2000, where, multiline=True)
        self.choice(el.get("style"), ("plain", "bar", ""), where)
        self.choice(el.get("align"), ALIGN, where)
        if el.get("color") not in (None, "") and not (isinstance(el["color"], str) and COLOR.match(el["color"])):
            self.p.add(where, _("ungültige Farbe."))
        for flag in ("bold", "hide_empty", "mono", "show_label"):
            self.boolean(el.get(flag), where)
        self.number(el.get("size"), where, 4, 72)
        for name in ("w_mm", "h_mm", "size_mm"):
            self.number(el.get(name), where, 0, 300)
        if el.get("key") is not None and not (isinstance(el["key"], str) and TOKEN.match(el["key"])):
            self.p.add(where, _("ungültiges Feld."))
        self.text(el.get("label"), 100, where)
        self.text(el.get("value"), 500, where)
        if el.get("link") is not None:
            self.list_link(table, el["link"], where)
        for column in self.sized(el.get("columns"), 20, where):
            if self.keys(column, ("key", "title"), where):
                if not (isinstance(column.get("key"), str) and TOKEN.match(column["key"])):
                    self.p.add(where, _("ungültige Spalte."))
                self.text(column.get("title"), 100, where)

    def list_link(self, table, link, where):
        """A list in a printout: a link field of the table, or "@other.f_link" for the records of another table
        of the template whose link points here."""
        if isinstance(link, str) and link.startswith("@"):
            other, _dot, key = link[1:].partition(".")
            target = self.fields.get(other, {}).get(key)
            if target is None or target.get("type") != "link" or (target.get("config") or {}).get("table") != table:
                self.p.add(where, _("Die Liste verweist auf keine Verknüpfung zu dieser Tabelle."))
            return
        self.field_ref(table, link, where, ("link", "backlink"))

    def view(self, item):
        head = self._item_head(item, "views", ("config",))
        if head is None:
            return
        table, where = head
        config = item.get("config")
        if config is None or not self.keys(config, VIEW_KEYS, where):
            return
        filters = config.get("filters")
        if filters is not None and self.keys(filters, list(self.fields.get(table, {})), where):
            for values in filters.values():
                for value in self.sized(values, 50, where):
                    self.text(value, 100, where)
        for rule in self.sized(config.get("sort"), 5, where):
            if self.keys(rule, ("column", "dir"), where):
                if not (isinstance(rule.get("column"), str) and TOKEN.match(rule["column"])):
                    self.p.add(where, _("ungültige Sortierung."))
                self.choice(rule.get("dir"), ("asc", "desc"), where)
        for key in self.sized(config.get("columns"), MAX_FIELDS, where):
            if not (isinstance(key, str) and TOKEN.match(key)):
                self.p.add(where, _("ungültige Spalte."))
        self.choice(config.get("layout"), LAYOUTS, where)
        for name in ("scope", "preset"):
            if config.get(name) not in (None, "") and not (isinstance(config[name], str) and TOKEN.match(config[name])):
                self.p.add(where, _("ungültiger Wert."))

    def demo(self, demo):
        if demo is None:
            return
        if not self.keys(demo, list(self.tables), _("Beispieldaten")):
            return
        for table, rows in demo.items():
            where = _("Beispieldaten %(key)s") % {"key": table}
            fields = self.fields.get(table, {})
            for row in self.sized(rows, MAX_DEMO_ROWS, where):
                if not self.keys(row, [k for k, f in fields.items() if f.get("type") not in ("formula", "backlink", "file", "image")], where):
                    continue
                for key, value in row.items():
                    if isinstance(value, list):
                        if len(value) > 50 or not all(isinstance(v, str) and len(v) <= 300 for v in value):
                            self.p.add(where, _("ungültiger Wert für %(field)s.") % {"field": key})
                    elif isinstance(value, str):
                        self.text(value, 5000, f"{where} {key}", multiline=True)
                    elif value is not None and not isinstance(value, (int, float, bool)):
                        self.p.add(where, _("ungültiger Wert für %(field)s.") % {"field": key})


def _numeric(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value).replace(",", "."))
        return True
    except ValueError:
        return False


def check_untrusted(data, reserved=None):
    """Problems of a template someone else wrote, in plain words; empty when it may be shared and applied.
    `reserved`: ids nobody else may use (default: the platform's own templates)."""
    try:
        problems = check(data)
        if problems:
            return problems
        if _depth(data) > MAX_DEPTH:
            return [_("Die Vorlage ist zu tief verschachtelt.")]
        if len(json.dumps(data, ensure_ascii=False).encode()) > MAX_BYTES:
            return [_("Die Datei ist zu groß (höchstens 1 MB).")]
        return list(_Strict(data, library_ids() if reserved is None else set(reserved)).run())
    except Exception:  # whatever slipped through is refused, never a crash
        return [_("Die Vorlage ist ungültig.")]


# ---------------------------------------------------------------- for people reviewing a template

PERSONAL = re.compile(r"e-?mail|telefon|handy|mobil|phone|geburt|birth|adresse|address|straße|strasse|iban|konto", re.IGNORECASE)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def notes(data):
    """Things a reviewer should look at; none of them refuses the template."""
    found = []
    urls = sorted({m.group(0)[:80] for text in _strings({k: v for k, v in data.items() if k not in ("demo", "catalog")}) for m in URL.finditer(text)})
    if urls:
        found.append(_("Links in Texten: %(urls)s") % {"urls": ", ".join(urls[:5])})
    demo = data.get("demo") or {}
    rows = sum(len(r) for r in demo.values() if isinstance(r, list))
    if rows:
        found.append(_("%(n)s Beispieldatensätze – bitte auf echte personenbezogene Daten prüfen.") % {"n": rows})
        personal = sorted({f.get("label", "") for t in data.get("tables", []) for f in t.get("fields") or []
                           if t.get("key") in demo and PERSONAL.search(f.get("label", "") + " " + f.get("key", ""))})
        if personal:
            found.append(_("Beispieldaten enthalten Felder wie %(fields)s.") % {"fields": ", ".join(personal[:6])})
    rights = {right for t in data.get("tables", []) for right in (t.get("rights") or {}).values()}
    if "edit" in rights:
        found.append(_("Die Vorlage gibt Rollen das Recht zu bearbeiten."))
    return found


def summary(data):
    """What a template holds, for a page about it: tables with their fields, record pages, printouts, views."""
    demo = data.get("demo") or {}
    tables = []
    for table in data.get("tables", []):
        fields = [{"key": f["key"], "label": f.get("label", ""), "type": f.get("type", ""),
                   "type_label": str(ft.TYPES[f["type"]].label) if f.get("type") in ft.TYPES else f.get("type", ""),
                   "options": [o.get("value", "") for o in f.get("options") or []][:12]}
                  for f in table.get("fields") or []]
        tables.append({"key": table["key"], "name": table.get("name", ""), "description": table.get("description", ""),
                       "icon": table.get("icon") or "table", "fields": fields, "records": len(demo.get(table["key"]) or [])})
    return {"tables": tables, "masks": [m.get("name", "") for m in data.get("masks") or []],
            "prints": [p.get("name", "") for p in data.get("prints") or []], "views": [v.get("name", "") for v in data.get("views") or []],
            "fields": sum(len(t["fields"]) for t in tables), "records": sum(t["records"] for t in tables)}


def canonical(data, catalog=None):
    """The file as the catalog hands it out: always a database of its own, without the platform's ordering, and
    with where it comes from (DataFluss ignores keys it does not know)."""
    out = copy.deepcopy(data)
    for key in ("order", "listed", "catalog"):
        out.pop(key, None)
    out["database"] = True
    out["requires"] = []
    if catalog:
        out["catalog"] = dict(catalog)
    return out
