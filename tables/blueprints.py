"""Module templates (docs/TABLES-PLAN.md §8): tables, fields, record pages, printouts, saved views and sample
records as one JSON file, so a basic module needs no Python.

Templates come with the platform (tables/library/*.json) or in a signed bundle (schule/bundles.py, the
manifest's "templates"). Applying one gives every item an id derived from the template and the item's key,
so a server and its computers end up with the same tables. A newer version only adds: it never deletes and
never overwrites what the school has changed; what differs is reported as kept.

    {"format": 1, "id": "bibliothek", "version": 1, "title": "Bibliothek", "titles": {"en": "Library"},
     "description": "…", "requires": ["schulverwaltung"],
     "tables": [{"key": "buecher", "name": "Bücher", "icon": "book", "title_field": "f_titel",
                 "rights": {"sekretariat": "edit"}, "fields": [{"key": "f_titel", "label": "Titel", "type": "text"}]}],
     "masks": [{"table": "buecher", "name": "Buch", "tree": {…}}],
     "prints": [{"table": "ausleihen", "name": "Ausleihschein", "tree": {…}}],
     "views": [{"table": "ausleihen", "name": "Offen", "config": {…}}],
     "demo": {"buecher": [{"f_titel": "Momo"}], "ausleihen": [{"f_name": "…", "f_buch": "Momo"}]}}

A link field's config names its target as in tables/links.py ({"target": "record", "table": "buecher"});
a formula field's config holds the stored form ({"expression": "count({@ausleihen.f_buch})"}).
"""
import json
import re
import uuid
from pathlib import Path

from django.db import transaction
from django.utils.translation import gettext as _

from core import audit, fieldtypes as ft
from core.models import SavedView
from masks import i18n
from masks.models import Mask, MaskVersion

from .models import RIGHTS, Field, Record, Table

FORMAT = 1
NAMESPACE = uuid.UUID("0b7f4f0e-4f3a-4a8e-9d55-1f6c0e2d7a19")
LIBRARY = Path(__file__).resolve().parent / "library"
KEY = re.compile(r"^[a-z0-9_]{1,20}$")
FIELD_KEY = re.compile(r"^f_[a-z0-9_]{1,57}$")


class BlueprintError(Exception):
    """The message is shown to the person applying the template."""


def item_id(template_id, *parts):
    return uuid.uuid5(NAMESPACE, ":".join([template_id, *map(str, parts)]))


# ---------------------------------------------------------------- finding

def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def available():
    """{id: template} of the platform's templates and those of the loaded bundles of active modules."""
    from core import modules
    from schule import modules as catalog

    found = {}
    for path in sorted(LIBRARY.glob("*.json")):
        data = _read(path)
        if data and not check(data):
            found[data["id"]] = {**data, "origin": "platform"}
    using = modules.active()
    for module_id, paths in catalog.TEMPLATE_FILES.items():
        if module_id not in using:
            continue
        for path in paths:
            data = _read(path)
            if data and not check(data):
                found.setdefault(data["id"], {**data, "origin": module_id})
    return found


def usable(template):
    """Whether this database uses every module the template needs."""
    from core import modules

    return set(template.get("requires") or []) <= set(modules.active())


def title(template):
    return i18n.pick(template.get("title", template.get("id", "")), template.get("titles"))


def description(template):
    return i18n.pick(template.get("description", ""), template.get("descriptions"))


def installed_version(template_id):
    versions = [(t.template or {}).get("version", 0) for t in Table.objects.all() if (t.template or {}).get("id") == template_id]
    return max(versions) if versions else None


# ---------------------------------------------------------------- checking

def check(data):
    """Problems of a template file, in plain words; empty when it can be applied."""
    problems = []
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return [_("Unbekanntes Vorlagenformat.")]
    if not isinstance(data.get("id"), str) or not KEY.match(data["id"]):
        problems.append(_("Die Vorlage braucht eine Kennung aus Kleinbuchstaben, Ziffern und _."))
    if not isinstance(data.get("version"), int) or data["version"] < 1:
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


# ---------------------------------------------------------------- applying

def _field_differs(field, spec):
    return (field.type != spec["type"] or field.label != spec["label"]
            or (spec.get("options") and [o["value"] for o in field.options] != [o["value"] for o in spec["options"]]))


def apply(data, user=None, demo=False, request=None):
    """Creates what is missing and keeps everything else. Returns {"created", "kept", "changed"} (names)."""
    from . import formula, services

    problems = check(data)
    if problems:
        raise BlueprintError(" ".join(problems))
    if not usable(data):
        raise BlueprintError(_("Diese Vorlage braucht ein Modul, das diese Datenbank nicht nutzt."))
    tid = data["id"]
    report = {"created": [], "kept": [], "changed": []}
    with transaction.atomic():
        tables = {}
        for order, spec in enumerate(data["tables"]):
            pk = item_id(tid, "table", spec["key"])
            table = Table.objects.filter(pk=pk).first()
            if table is None:
                if Table.objects.filter(key=spec["key"]).exists():
                    raise BlueprintError(_("Eine Tabelle mit der Kennung %(key)s gibt es schon.") % {"key": spec["key"]})
                table = Table.objects.create(
                    pk=pk, key=spec["key"], name=spec["name"], names=i18n.clean_names(spec.get("names"), 100),
                    description=(spec.get("description") or "")[:300], icon=spec.get("icon") or "table", order=(order + 1) * 10,
                    in_nav=spec.get("in_nav", True), rights={k: v for k, v in (spec.get("rights") or {}).items() if v in RIGHTS},
                    template={"id": tid, "version": data["version"]},
                )
                report["created"].append(table.name)
            else:
                report["kept"].append(table.name)
                table.template = {"id": tid, "version": max(data["version"], (table.template or {}).get("version", 0))}
                table.save()
            tables[spec["key"]] = table
        fields_later = []
        for spec in data["tables"]:
            table = tables[spec["key"]]
            for order, fspec in enumerate(spec.get("fields") or []):
                pk = item_id(tid, "field", spec["key"], fspec["key"])
                existing = Field.objects.filter(pk=pk).first() or Field.objects.filter(table=table, key=fspec["key"]).first()
                if existing is not None:
                    if _field_differs(existing, fspec):
                        report["changed"].append(f"{table.name} · {existing.display_label}")
                    continue
                if fspec["type"] == "formula":
                    fields_later.append((table, pk, order, fspec))
                    continue
                _create_field(table, pk, order, fspec)
                report["created"].append(f"{table.name} · {fspec['label']}")
            if spec.get("title_field") and not table.title_field:
                table.title_field = spec["title_field"]
                table.save()
        # A formula may use another formula, also of another table: those it needs come first, whatever the
        # order in the file. What still fails when nothing more can be created is a mistake in the template.
        while fields_later:
            waiting, problem = [], None
            for table, pk, order, fspec in fields_later:
                try:
                    result = formula.check(fspec["config"]["expression"], formula.Schema(table))
                except formula.FormulaError as exc:
                    waiting.append((table, pk, order, fspec))
                    problem = problem or (fspec, exc)
                    continue
                config = {**fspec["config"], "result": result if result in formula.FIELD_TYPES.values() else "text"}
                _create_field(table, pk, order, {**fspec, "config": config})
                report["created"].append(f"{table.name} · {fspec['label']}")
            if len(waiting) == len(fields_later):
                raise BlueprintError(_("Formel %(field)s: %(problem)s") % {"field": problem[0]["label"], "problem": problem[1].message})
            fields_later = waiting
        for item in data.get("masks") or []:
            pk = item_id(tid, "mask", item["table"], item["name"])
            table = tables[item["table"]]
            if Mask.objects.filter(pk=pk).exists() or Mask.objects.filter(table=table, is_default=True).exists():
                continue
            mask = Mask.objects.create(pk=pk, entity=Mask.RECORD, table=table, name=item["name"], names=i18n.clean_names(item.get("names"), 80), is_default=True)
            MaskVersion.objects.create(pk=item_id(tid, "maskversion", item["table"], item["name"]), mask=mask, number=1,
                                       status=MaskVersion.PUBLISHED, tree=item.get("tree") or {"tabs": []}, author=user)
            report["created"].append(f"{table.name} · {item['name']}")
        from prints.models import PrintTemplate

        for item in data.get("prints") or []:
            pk = item_id(tid, "print", item["table"], item["name"])
            if PrintTemplate.objects.filter(pk=pk).exists():
                continue
            PrintTemplate.objects.create(pk=pk, name=item["name"], description=(item.get("description") or "")[:200],
                                         source=f"table:{item['table']}", tree=item.get("tree") or {"page": {"preset": "a4"}, "rows": []})
            report["created"].append(item["name"])
        for item in data.get("views") or []:
            pk = item_id(tid, "view", item["table"], item["name"])
            if SavedView.objects.filter(pk=pk).exists():
                continue
            SavedView.objects.create(pk=pk, entity=f"table:{item['table']}", name=item["name"], owner=None, config=item.get("config") or {})
            report["created"].append(item["name"])
        if demo:
            _demo(data, tables)
        for table in tables.values():  # titles and search text follow the fields the template added
            fields = services.fields_of(table)
            for record in Record.everything.filter(table=table):
                before = (record.title, record.search_text)
                services.refresh(record, fields)
                if (record.title, record.search_text) != before:
                    record.save()
    audit.log("table", f"Vorlage {tid} (Version {data['version']}) angewendet: {len(report['created'])} neu", request=request, user=user)
    return report


def _create_field(table, pk, order, spec):
    options = [{"value": str(o["value"])[:100], "color": o.get("color", ""), **({"labels": o["labels"]} if o.get("labels") else {})}
               for o in spec.get("options") or []] if ft.TYPES[spec["type"]].options else []
    return Field.objects.create(
        pk=pk, table=table, key=spec["key"], label=spec["label"][:100], labels=i18n.clean_names(spec.get("labels"), 100),
        help_text=(spec.get("help_text") or "")[:200], type=spec["type"], options=options, config=spec.get("config") or {},
        required=bool(spec.get("required")), unique=bool(spec.get("unique")), in_grid=spec.get("in_grid", True), order=(order + 1) * 10,
    )


def _demo(data, tables):
    """Sample records, made once: their ids come from the template, so applying it again adds nothing."""
    from . import links, services

    tid = data["id"]
    for table_key, rows in (data.get("demo") or {}).items():
        table = tables[table_key]
        fields = {f.key: f for f in services.fields_of(table)}
        for index, row in enumerate(rows):
            pk = item_id(tid, "demo", table_key, index)
            if Record.everything.filter(pk=pk).exists():
                continue
            record = Record(pk=pk, table=table)
            later = {}
            for key, raw in row.items():
                field = fields.get(key)
                if field is None:
                    continue
                if field.type == "link":
                    later[field] = raw
                elif services.holds_value(field):
                    services._assign(record, field, raw)
            services.refresh(record, list(fields.values()))
            record.save()
            for field, raw in later.items():
                target = links.target_table(field)
                names = raw if isinstance(raw, list) else [raw]
                ids = [str(pk) for pk in Record.objects.filter(table=target, title__in=names).values_list("pk", flat=True)] if target else []
                if ids:
                    links.set_targets(record, field, ids if (field.config or {}).get("multiple") else ids[:1])
