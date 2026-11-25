"""Module templates (docs/TABLES-PLAN.md §8): tables, fields, record pages, printouts, saved views and sample
records as one JSON file, so a basic module needs no Python.

Templates come with the platform (tables/library/*.json) or in a signed bundle (schule/bundles.py, the
manifest's "templates"). Applying one gives every item an id derived from the template and the item's key,
so a server and its computers end up with the same tables. A newer version only adds: it never deletes and
never overwrites what the school has changed; what differs is reported as kept.

    {"format": 1, "id": "bibliothek", "version": 1, "title": "Bibliothek", "titles": {"en": "Library"},
     "description": "…", "requires": ["schulverwaltung"],
     "tables": [{"key": "buecher", "name": "Bücher", "icon": "book", "title_field": "f_titel", "section": "Bibliothek",
                 "look": {"cover": "f_cover"},  # tables/look.py
                 "rights": {"sekretariat": "edit"}, "fields": [{"key": "f_titel", "label": "Titel", "type": "text"}]}],
     "masks": [{"table": "buecher", "name": "Buch", "tree": {…}}],
     "prints": [{"table": "ausleihen", "name": "Ausleihschein", "tree": {…}}],
     "views": [{"table": "ausleihen", "name": "Offen", "config": {…}}],
     "demo": {"buecher": [{"f_titel": "Momo"}], "ausleihen": [{"f_name": "…", "f_buch": "Momo"}]}}

A link field's config names its target as in tables/links.py ({"target": "record", "table": "buecher"});
a formula field's config holds the stored form ({"expression": "count({@ausleihen.f_buch})"}).

Two kinds of template:
- A **database template** ("database": true, e.g. Filme & Serien, Bibliothek, Termine, Inventar) is what a
  whole database is for. The app offers it when a new database is made, in the order of "order", and the
  page "Vorlage hinzufügen" offers it only in a database without Schulverwaltung that has no other database
  template yet, so a school database and a film database never get mixed.
- Any other template adds tables to the modules it "requires" (an add-on, e.g. of a premium module).
"listed": false retires a template: it is no longer offered, but where it is applied it can still be
updated. "changes": {"2": {"de": "…", "en": "…"}} says what a version brings; where an older one is applied,
the start page and the list of tables say so, with a button that adds it (newer_versions).
"""
import json
import uuid
from pathlib import Path

from django.db import transaction
from django.utils.translation import gettext as _

from core import audit, fieldtypes as ft
from core.models import SavedView
from masks import i18n
from masks.models import Mask, MaskVersion

from .models import RIGHTS, Field, Link, Record, Table
from .template_check import FIELD_KEY, FORMAT, KEY, check, check_untrusted  # noqa: F401 (used by others through here)

NAMESPACE = uuid.UUID("0b7f4f0e-4f3a-4a8e-9d55-1f6c0e2d7a19")
LIBRARY = Path(__file__).resolve().parent / "library"


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
    # Templates from datafluss.de or a file that were applied here (tables/catalog.py), as they were checked then.
    from .models import CatalogTemplate

    for kept in CatalogTemplate.objects.all():
        if isinstance(kept.data, dict) and not check(kept.data):
            found.setdefault(kept.template_id, {**kept.data, "origin": kept.source})
    return found


def source_of(template):
    """Where a template comes from, as Table.template notes it: None for the platform and modules, else catalog or file."""
    origin = (template or {}).get("origin")
    return origin if origin in ("catalog", "file") else None


def is_database(template):
    return bool(template.get("database"))


def listed(template):
    return template.get("listed", True) is not False


def applied_ids():
    """The ids of the templates applied in this database."""
    return {(t.template or {}).get("id") for t in Table.objects.all()} - {None}


def offered(template, found=None):
    """Whether "Vorlage hinzufügen" offers the template here: an applied one always (to update it), a retired one
    never, a database template only in a database without Schulverwaltung and without another one."""
    from core import modules

    here = applied_ids()
    if template["id"] in here:
        return True
    if not listed(template):
        return False
    if is_database(template):
        found = found if found is not None else available()
        others = {tid for tid in here if is_database(found.get(tid) or {})}
        return "schulverwaltung" not in modules.active() and not others
    return True


def database_free():
    """Whether this database may still get a database template: no Schulverwaltung and none applied yet."""
    from core import modules

    found = available()
    return "schulverwaltung" not in modules.active() and not any(is_database(found.get(tid) or {}) for tid in applied_ids())


def any_offered():
    """Whether "Vorlage hinzufügen" has anything for this database (the buttons that lead there hide otherwise)."""
    found = available()
    return any(offered(t, found) for t in found.values())


def ordered(templates):
    """Database templates first, in their "order"; then the rest by title."""
    return sorted(templates, key=lambda t: (not is_database(t), t.get("order") or 999, title(t)))


def usable(template):
    """Whether this database uses every module the template needs."""
    from core import modules

    return set(template.get("requires") or []) <= set(modules.active())


def title(template):
    return i18n.pick(template.get("title", template.get("id", "")), template.get("titles"))


def description(template):
    return i18n.pick(template.get("description", ""), template.get("descriptions"))


def newer_versions(user):
    """Templates applied here that have a newer version, for a notice with a button to add what it brings:
    [{"id", "title", "version", "installed", "news"}]. Empty for people who may not design tables."""
    if not user.can("tables.design"):
        return []
    applied = {}
    for table in Table.objects.all():
        tid, version = (table.template or {}).get("id"), (table.template or {}).get("version", 0)
        if tid:
            applied[tid] = max(version, applied.get(tid, 0))
    if not applied:
        return []
    from .models import CatalogTemplate

    rows, found = [], available()
    # The catalog names its newer versions (catalog.check_updates); "Ergänzen" fetches them, on the server only.
    latest = {c.template_id: c for c in CatalogTemplate.objects.filter(source=CatalogTemplate.CATALOG, latest_version__isnull=False)}
    for tid, installed in sorted(applied.items()):
        template = found.get(tid)
        if tid in latest and latest[tid].latest_version > installed:
            changes = latest[tid].latest_changes or {}
            template = {**(template or latest[tid].data), "version": latest[tid].latest_version, "changes": changes}
            from_catalog = True
        else:
            from_catalog = False
        if template is None or template["version"] <= installed or not usable(template):
            continue
        changes = template.get("changes") or {}
        news = [i18n.pick(changes[v].get("de", ""), changes[v]) for v in sorted(changes, key=int)
                if v.isdigit() and int(v) > installed and isinstance(changes[v], dict)]
        rows.append({"id": tid, "title": title(template), "version": template["version"], "installed": installed, "news": " ".join(news),
                     "catalog": from_catalog})
    return rows


def installed_version(template_id):
    versions = [(t.template or {}).get("version", 0) for t in Table.objects.all() if (t.template or {}).get("id") == template_id]
    return max(versions) if versions else None


# ---------------------------------------------------------------- checking

# check() lives in tables/template_check.py, which the template site (web/) shares; check_untrusted() there is the
# stricter check for templates someone else wrote.


# ---------------------------------------------------------------- applying

def _field_differs(field, spec):
    return (field.type != spec["type"] or field.label != spec["label"]
            or (spec.get("options") and [o["value"] for o in field.options] != [o["value"] for o in spec["options"]]))


def apply(data, user=None, demo=False, request=None, source=None):
    """Creates what is missing and keeps everything else. Returns {"created", "kept", "changed"} (names).
    A template that fails while being applied leaves nothing behind and says so, instead of an error page.
    `source` ("catalog" or "file", tables/catalog.py) is noted in Table.template; tables of a template only take a
    version of the same source, so a file never changes a catalog template and neither changes the platform's."""
    from django.core.exceptions import ValidationError
    from django.db import DatabaseError

    try:
        return _apply(data, user, demo, request, source)
    except ValidationError as exc:
        raise BlueprintError(_("Die Vorlage lässt sich nicht anwenden: %(problem)s") % {"problem": " ".join(exc.messages)})
    except (TypeError, KeyError, ValueError, AttributeError, DatabaseError):
        raise BlueprintError(_("Die Vorlage lässt sich nicht anwenden: Sie enthält ungültige Angaben."))


def _apply(data, user, demo, request, source=None):
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
                    pk=pk, key=spec["key"], name=str(spec["name"])[:100], names=i18n.clean_names(spec.get("names"), 100),
                    description=(spec.get("description") or "")[:300], icon=str(spec.get("icon") or "table")[:30], order=(order + 1) * 10,
                    in_nav=spec.get("in_nav", True), rights={k: v for k, v in (spec.get("rights") or {}).items() if v in RIGHTS},
                    template={"id": tid, "version": data["version"], **({"source": source} if source else {})}, section=" ".join(str(spec.get("section") or "").split())[:60],
                    look=spec.get("look") if isinstance(spec.get("look"), dict) else {},
                )
                report["created"].append(table.name)
            else:
                if (table.template or {}).get("source") != source:
                    raise BlueprintError(_("Die Tabelle %(name)s stammt aus einer anderen Quelle und wird nicht verändert.") % {"name": table.name})
                report["kept"].append(table.name)
                table.template = {"id": tid, "version": max(data["version"], (table.template or {}).get("version", 0)), **({"source": source} if source else {})}
                if isinstance(spec.get("look"), dict):  # a newer version may bring more of the look; what is set here stays
                    table.look = _add_missing(table.look or {}, spec["look"])
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
            mask = Mask.objects.create(pk=pk, entity=Mask.RECORD, table=table, name=str(item["name"])[:80], names=i18n.clean_names(item.get("names"), 80), is_default=True)
            MaskVersion.objects.create(pk=item_id(tid, "maskversion", item["table"], item["name"]), mask=mask, number=1,
                                       status=MaskVersion.PUBLISHED, tree=item.get("tree") or {"tabs": []}, author=user)
            report["created"].append(f"{table.name} · {item['name']}")
        from prints.models import PrintTemplate

        for item in data.get("prints") or []:
            pk = item_id(tid, "print", item["table"], item["name"])
            if PrintTemplate.objects.filter(pk=pk).exists():
                continue
            PrintTemplate.objects.create(pk=pk, name=str(item["name"])[:120], description=(item.get("description") or "")[:200],
                                         source=f"table:{item['table']}", tree=item.get("tree") or {"page": {"preset": "a4"}, "rows": []})
            report["created"].append(item["name"])
        for item in data.get("views") or []:
            pk = item_id(tid, "view", item["table"], item["name"])
            if SavedView.objects.filter(pk=pk).exists():
                continue
            SavedView.objects.create(pk=pk, entity=f"table:{item['table']}", name=str(item["name"])[:80], owner=None, config=item.get("config") or {})
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


def _add_missing(have, wanted):
    """`have` with the keys of `wanted` it lacks, also inside nested dictionaries; nothing in `have` changes."""
    merged = dict(have)
    for key, value in wanted.items():
        if key not in merged:
            merged[key] = value
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _add_missing(merged[key], value)
    return merged


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


# ---------------------------------------------------------------- exporting

DEMO_ROWS = 20  # sample records per table an export takes along at most


def export(tables, meta, demo=False, user=None):
    """A template file of these tables, for sharing (datafluss.de) or another database: fields, look, record pages,
    printouts and shared views; sample records only when asked, without files, pictures and secrets.
    Links to tables not chosen and to pupils, staff or devices are left out, so the file stands on its own.
    Returns (data, problems, skipped): problems from template_check.check_untrusted, skipped names what was left out."""
    from schule.bundles import platform_version

    from . import template_check as tc

    keys = {t.key for t in tables}
    skipped = []
    specs, kept = [], {}
    for table in tables:
        fields = _export_fields(table, keys, skipped)
        kept[table.key] = {f["key"]: f for f in fields}
        spec = {"key": table.key, "name": table.name, "icon": table.icon or "table", "fields": fields}
        for name, value in (("names", table.names), ("description", table.description), ("section", table.section)):
            if value:
                spec[name] = value
        if not table.in_nav:
            spec["in_nav"] = False
        if table.title_field in kept[table.key]:
            spec["title_field"] = table.title_field
        rights = {role: right for role, right in (table.rights or {}).items() if role in tc.UNTRUSTED_ROLES and right in ("read", "edit")}
        if rights:
            spec["rights"] = rights
        look = _export_look(table.look or {}, kept[table.key])
        if look:
            spec["look"] = look
        specs.append(spec)
    for spec in specs:  # a person table of TMDB may be left out: the look of the titles then forgets it
        tmdb = (spec.get("look") or {}).get("tmdb")
        if tmdb and any((p or {}).get("table") not in keys for p in (tmdb.get("people") or {}).values()):
            tmdb.pop("people", None)
        if tmdb and ((tmdb.get("watch") or {}).get("viewings") or {}).get("table") not in (keys | {None}):
            tmdb.pop("watch", None)
    data = {"format": FORMAT, "id": meta.get("id", ""), "version": 1, "title": meta.get("title", ""), "database": True, "requires": [],
            "datafluss": platform_version(), "tables": specs}
    if meta.get("description"):
        data["description"] = meta["description"]
    data["masks"] = [m for t in tables if (m := _export_mask(t, kept[t.key]))]
    data["prints"] = _export_prints(tables, kept, skipped)
    data["views"] = _export_views(tables, kept)
    if demo:
        data["demo"] = {t.key: rows for t in tables if (rows := _export_demo(t, kept[t.key], keys, user))}
    for group in ("masks", "prints", "views"):
        if not data[group]:
            del data[group]
    return data, tc.check_untrusted(data), skipped


def _export_fields(table, keys, skipped):
    from . import formula, services
    from . import template_check as tc

    out = []
    for field in services.fields_of(table):
        config = {k: v for k, v in (field.config or {}).items() if k in tc.FIELD_CONFIG.get(field.type, ())}
        if field.type == "link" and (config.get("target") != Link.RECORD or config.get("table") not in keys):
            skipped.append(f"{table.name} · {field.display_label}")
            continue
        if field.type == "backlink" and str(config.get("link", "")).partition(".")[0] not in keys:
            skipped.append(f"{table.name} · {field.display_label}")
            continue
        spec = {"key": field.key, "label": field.label, "type": field.type}
        if field.labels:
            spec["labels"] = field.labels
        if field.help_text:
            spec["help_text"] = field.help_text
        if field.options:
            spec["options"] = [{"value": o["value"], **({"color": o["color"]} if tc.COLOR.match(str(o.get("color") or "")) else {}),
                                **({"labels": o["labels"]} if o.get("labels") else {})} for o in field.options]
        if config:
            spec["config"] = config
        for flag in ("required", "unique"):
            if getattr(field, flag):
                spec[flag] = True
        if not field.in_grid:
            spec["in_grid"] = False
        out.append(spec)
    # A formula naming a field or table that stays behind goes too, and so on, until all that is left works.
    while True:
        present = {f["key"] for f in out}
        broken = [f for f in out if f["type"] == "formula" and not _formula_fits(f["config"].get("expression", ""), present, keys, formula)]
        if not broken:
            return out
        for f in broken:
            skipped.append(f"{table.name} · {f['label']}")
            out.remove(f)


def _formula_fits(expression, present, keys, formula):
    try:
        refs = [inner for _start, _end, inner in formula._ref_tokens(expression)]
    except formula.FormulaError:
        return False
    if any(inner.startswith("@") and inner[1:].partition(".")[0] not in keys for inner in refs):
        return False
    return formula.used_fields(expression) <= present


def _export_look(look, fields):
    from . import template_check as tc

    out = {}
    for name in ("cover", "backdrop", "flag", "rating", "text"):
        if look.get(name) in fields:
            out[name] = look[name]
    subtitle = [k for k in look.get("subtitle") or [] if k in fields][:3]
    if subtitle:
        out["subtitle"] = subtitle
    if look.get("layout") in tc.LAYOUTS and "cover" in out:
        out["layout"] = look["layout"]
    if look.get("shape") in tc.SHAPES:
        out["shape"] = look["shape"]
    if isinstance(look.get("tmdb"), dict):
        out["tmdb"] = json.loads(json.dumps(look["tmdb"]))
    return out


def _clean(obj, allowed):
    return {k: v for k, v in obj.items() if k in allowed} if isinstance(obj, dict) else {}


def _export_mask(table, fields):
    from . import template_check as tc

    mask = Mask.objects.filter(table=table, is_default=True).first()
    version = mask.published if mask else None
    if version is None:
        return None
    tabs = []
    for index, tab in enumerate((version.tree or {}).get("tabs", [])):
        sections = []
        for section in tab.get("sections", []):
            items = []
            for item in section.get("items", []):
                item = _clean(item, tc.MASK_ITEM_KEYS)
                if item.get("type") == "field" and item.get("field") not in fields:
                    continue
                if item.get("type") == "block" and item.get("block") not in tc.MASK_BLOCKS:
                    continue
                if (item.get("show_if") or {}).get("field") not in (None, *fields):
                    item.pop("show_if")
                items.append(item)
            section = {**_clean(section, tc.SECTION_KEYS), "items": items}
            section["id"] = section["id"] if tc.TOKEN.match(str(section.get("id", ""))) else f"s{len(sections) + 1}"
            sections.append(section)
        tab = {**_clean(tab, tc.TAB_KEYS), "sections": sections}
        tab["id"] = tab["id"] if tc.TOKEN.match(str(tab.get("id", ""))) else f"t{index + 1}"
        tabs.append(tab)
    out = {"table": table.key, "name": mask.name, "tree": {"tabs": tabs}}
    if mask.names:
        out["names"] = mask.names
    return out


def _export_prints(tables, kept, skipped):
    from prints.models import PrintTemplate

    from . import template_check as tc

    out = []
    for table in tables:
        for template in PrintTemplate.objects.filter(source=f"table:{table.key}", archived=False).order_by("name"):
            if template.code_mode:
                skipped.append(template.name)
                continue
            tree = template.tree or {}
            rows = []
            for row in tree.get("rows", []):
                cols = []
                for col in row.get("cols", []):
                    items = []
                    for el in col.get("items", []):
                        el = _clean(el, tc.ELEMENT_KEYS)
                        if el.get("type") not in tc.PRINT_ELEMENTS or (el.get("type") == "field" and el.get("key") not in kept[table.key]):
                            continue
                        if el.get("type") == "list":
                            listed = _list_table(table.key, el.get("link"), kept)
                            if listed is None:
                                continue
                            # a column of a field that stayed behind would print empty
                            el["columns"] = [c for c in el.get("columns") or [] if not FIELD_KEY.match(str(c.get("key", ""))) or c["key"] in kept[listed]]
                        items.append(el)
                    cols.append({**_clean(col, tc.COLUMN_KEYS), "items": items})
                rows.append({"cols": cols})
            item = {"table": table.key, "name": template.name, "tree": {"page": _clean(tree.get("page"), tc.PAGE_KEYS), "rows": rows}}
            if template.description:
                item["description"] = template.description
            out.append(item)
    return out


def _list_table(table_key, link, kept):
    """The table whose records a printed list shows: "@other.f_link" for records of another table linking here,
    or a link field of the table itself. None when that table or field is not part of the export."""
    if isinstance(link, str) and link.startswith("@"):
        other, _dot, key = link[1:].partition(".")
        return other if key in kept.get(other, {}) else None
    field = kept[table_key].get(link)
    target = ((field or {}).get("config") or {}).get("table")
    return target if target in kept else None


def _export_views(tables, kept):
    from . import template_check as tc

    out = []
    for table in tables:
        for view in SavedView.objects.filter(entity=f"table:{table.key}", owner=None).order_by("name"):
            config = _clean(view.config, tc.VIEW_KEYS)
            if isinstance(config.get("filters"), dict):
                config["filters"] = {k: v for k, v in config["filters"].items() if k in kept[table.key]}
            out.append({"table": table.key, "name": view.name, "config": config})
    return out


def _export_demo(table, fields, keys, user):
    from . import links, services

    if user is not None and not services.may_read(user, table):
        return []
    rows = []
    for record in Record.objects.filter(table=table).order_by("created_at")[:DEMO_ROWS]:
        row = {}
        for key, spec in fields.items():
            if spec["type"] in ("secret", "file", "image", "formula", "backlink"):
                continue
            if spec["type"] == "link":
                ids = links.targets(record, Field.objects.get(table=table, key=key))
                titles = list(Record.objects.filter(pk__in=ids).values_list("title", flat=True))
                if titles:
                    row[key] = titles if (spec.get("config") or {}).get("multiple") else titles[0]
            elif record.values.get(key) not in (None, "", []):
                row[key] = record.values[key]
        if row:
            rows.append(row)
    return rows
