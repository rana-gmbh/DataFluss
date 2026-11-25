"""Verwaltung → Tabellen: designing own tables (docs/TABLES-PLAN.md §4). Lists and record pages follow."""
import json
import re

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core import audit, fieldtypes as ft
from core.models import SchoolSettings
from core.permissions import require
from masks import i18n

from . import blueprints, formula, infer, links, look as table_look, masks as table_masks, services, tmdb
from .models import EDIT, NONE, READ, Field, Record, Table

ICONS = ["table", "book", "list", "calendar", "users", "tag", "archive", "laptop", "clipboard", "heart", "school", "file", "image", "key", "wrench",
         "film", "tv", "clapper", "popcorn", "star", "bookmark"]
TABS = ("felder", "seite", "rechte", "allgemein")


@require("tables.design")
def table_list(request):
    error = ""
    if request.method == "POST" and "rename_section" in request.POST:
        old, new = request.POST.get("rename_section", ""), request.POST.get("section", "")
        moved = services.rename_section(old, new, request=request)
        if moved:
            messages.success(request, _("Der Bereich heißt jetzt „%(name)s“.") % {"name": " ".join(new.split()) or _("Tabellen")})
        return redirect("table_list")
    if request.method == "POST":
        try:
            table = services.create_table(request.POST.get("name"), request=request)
            services.set_access(table, request.POST.get("access"), request=request)
            messages.success(request, _("%(table)s wurde angelegt. Fügen Sie jetzt Felder hinzu.") % {"table": table.display_name})
            return redirect("table_settings", key=table.key)
        except ValidationError as exc:
            error = " ".join(exc.messages)
    tables = Table.objects.all()
    counts = {row["table_id"]: row["n"] for row in Record.objects.values("table_id").order_by().annotate(n=Count("id"))}
    rows = [{"table": t, "records": counts.get(t.pk, 0), "fields": t.fields.filter(archived=False).count()} for t in tables]
    groups = services.by_section(rows, section=lambda row: row["table"].section)
    tmdb_ready = bool(SchoolSettings.load().tmdb_key)
    return render(request, "tables/list.html", {"rows": rows, "groups": groups, "several": len(groups) > 1 or bool(groups and groups[0][0]), "error": error, "can_import": request.user.can("data.import"),
                                                "tmdb_ready": tmdb_ready, "templates_offered": blueprints.any_offered(), "template_news": blueprints.newer_versions(request.user),
                                                "tmdb_card": tmdb_ready or any(tmdb.supports(row["table"]) for row in rows),
                                                "access": services.access_choices(), "active_nav": "settings_home"})


def _settings_context(table, tab, **extra):
    fields = services.fields_of(table, include_archived=True)
    return {
        "table": table, "tab": tab, "fields": fields, "types": services.CREATE_TYPES, "icons": ICONS, "sections": services.sections(),
        "link_targets": links.target_choices(),
        "title_choices": [f for f in fields if not f.archived and services.may_name(f)],
        "presets": formula.presets(table), "expression_json": json.dumps(""),
        "type_rules": infer.label_rules(links.target_choices(), table.key),
        "roles": [{"value": value, "label": label, "right": (table.rights or {}).get(value, NONE)} for value, label in services.roles()],
        "rights": [(NONE, _("Kein Zugriff")), (READ, _("Lesen")), (EDIT, _("Bearbeiten"))],
        "has_records": Record.everything.filter(table=table).exists(),
        "masks": table.masks.prefetch_related("versions"), "look": table.look or {}, "look_choices": table_look.choices(table),
        "look_subtitles": ((table.look or {}).get("subtitle") or []) + ["", "", ""],
        "active_nav": "settings_home", **extra,
    }


@require("tables.design")
def table_settings(request, key):
    table = get_object_or_404(Table, key=key)
    tab = request.GET.get("tab") if request.GET.get("tab") in TABS else "felder"
    if request.method == "POST":
        action = request.POST.get("action", "save")
        try:
            if action == "archive":
                table.archived = True
                table.save()
                audit.log("table", f"Tabelle archiviert: {table.name}", request=request, target=table.pk)
                messages.success(request, _("%(table)s wurde archiviert. Die Datensätze bleiben erhalten.") % {"table": table.display_name})
            elif action == "restore":
                table.archived = False
                table.save()
                audit.log("table", f"Tabelle wiederhergestellt: {table.name}", request=request, target=table.pk)
            elif action == "look":
                table.look = table_look.clean(table, request.POST)
                table.save()
                audit.log("table", f"Darstellung von {table.name} geändert", request=request, target=table.pk)
                messages.success(request, _("Gespeichert."))
            elif action == "delete":
                services.delete_table(table, request=request)
                messages.success(request, _("Die Tabelle wurde gelöscht."))
                return redirect("table_list")
            else:
                scope = i18n.scope_for(request, table.has_names)
                table.name, table.names = i18n.rename(table.name, table.names, request.POST.get("name", "")[:100], scope)
                table.description = request.POST.get("description", "").strip()[:300]
                table.icon = request.POST.get("icon") if request.POST.get("icon") in ICONS else table.icon
                table.in_nav = bool(request.POST.get("in_nav"))
                table.section = " ".join(request.POST.get("section", "").split())[:60]
                chosen = request.POST.get("title_field", "")
                named = Field.objects.filter(table=table, key=chosen, archived=False).first() if chosen else None
                if chosen and (named is None or not services.may_name(named)):
                    raise ValidationError(_("Unbekanntes Feld."))
                title_changed = chosen != table.title_field
                table.title_field = chosen
                table.save()
                if title_changed:
                    _refresh_titles(table)
                messages.success(request, _("Gespeichert."))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(f"/einstellungen/tabellen/{table.key}/?tab={tab}")
    return render(request, "tables/settings.html", _settings_context(table, tab))


def _refresh_titles(table):
    fields = services.fields_of(table)
    for record in Record.everything.filter(table=table).iterator():
        before = (record.title, record.search_text)
        services.refresh(record, fields)
        if (record.title, record.search_text) != before:
            record.save()


@require("tables.design")
@require_POST
def table_mask(request, key):
    """Opens the mask designer for the table's record page; the first time, the automatic layout becomes its mask."""
    table = get_object_or_404(Table, key=key)
    mask = table_masks.ensure_mask(table, request.user)
    return redirect("mask_builder", pk=mask.pk)


@require("tables.design")
@require_POST
def table_rights(request, key):
    table = get_object_or_404(Table, key=key)
    services.set_rights(table, {role: request.POST.get(f"right_{role}", NONE) for role, _label in services.roles()}, request=request)
    messages.success(request, _("Die Rechte wurden gespeichert."))
    return redirect(f"/einstellungen/tabellen/{table.key}/?tab=rechte")


@require("tables.design")
@require_POST
def table_field_new(request, key):
    table = get_object_or_404(Table, key=key)
    if request.content_type == "application/json":  # the mask designer's dialog
        payload = json.loads(request.body or b"{}")
        try:
            field = services.create_field(table, payload.get("label"), payload.get("type", "text"), payload.get("options"),
                                          payload.get("required"), payload.get("help_text"), payload.get("in_grid", True),
                                          config=services.clean_config(payload.get("type", "text"), payload, table), request=request)
        except ValidationError as exc:
            return JsonResponse({"error": " ".join(exc.messages)}, status=422)
        return JsonResponse(table_masks.field_json(field))
    try:
        type_ = request.POST.get("type", "text")
        field = services.create_field(
            table, request.POST.get("label"), type_, request.POST.get("options"), request.POST.get("required"),
            request.POST.get("help_text"), request.POST.get("in_grid"),
            config=services.clean_config(type_, request.POST, table), request=request,
        )
        messages.success(request, _("%(field)s wurde angelegt.") % {"field": field.display_label})
        if type_ == "link" and request.POST.get("backlink") and links.kind_of(field) == "record":
            # The other side as a field of its own: Räume.Inhaber gives Personen "Inhaber von", editable there too.
            label = (request.POST.get("reverse") or "").strip() or _("%(field)s von %(table)s") % {"field": field.display_label, "table": table.display_name}
            other = links.make_backlink(field, label, request=request)
            if other is not None:
                messages.success(request, _("In %(table)s gibt es jetzt das Feld %(field)s.") % {"table": other.table.display_name, "field": other.display_label})
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect(f"/einstellungen/tabellen/{table.key}/?tab=felder")


@require("tables.design")
def table_field(request, key, pk):
    table = get_object_or_404(Table, key=key)
    field = get_object_or_404(Field, table=table, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action", "save")
        try:
            if action == "archive":
                field.archived = True
                field.save()
                audit.log("table", f"Feld {field.label} in {table.name} archiviert", request=request, target=table.pk)
                messages.success(request, _("%(field)s wurde archiviert. Die Werte bleiben erhalten.") % {"field": field.display_label})
                return redirect(f"/einstellungen/tabellen/{table.key}/?tab=felder")
            if action == "restore":
                field.archived = False
                field.save()
                return redirect("table_field", key=table.key, pk=field.pk)
            if action == "rights":
                from core import views_roles

                views_roles.save_field_rights(request, f"table:{table.key}", field.key)
                return redirect("table_field", key=table.key, pk=field.pk)
            if action == "type":
                services.change_type(field, request.POST.get("type", ""), request=request)
                messages.success(request, _("Der Typ wurde geändert."))
                return redirect("table_field", key=table.key, pk=field.pk)
            services.edit_field(field, request.POST, i18n.scope_for(request, field.has_names), request=request)
            _refresh_titles(table)
            messages.success(request, _("Gespeichert."))
            return redirect(f"/einstellungen/tabellen/{table.key}/?tab=felder")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("table_field", key=table.key, pk=field.pk)
    options = [{"value": o["value"], "shown": i18n.option_label(o)} for o in field.options]
    compatible = [(k, ft.TYPES[k].label) for k in sorted(services.COMPATIBLE.get(field.type, set()))]
    from core import views_roles

    return render(request, "tables/field.html", {"table": table, "field": field, "options": options, "compatible": compatible,
                                                 "field_rights": views_roles.field_rights(request, f"table:{table.key}", field.key),
                                                 "target_label": links.target_label(field) if field.type == "link" else "",
                                                 "expression_json": json.dumps(formula.to_names(field.config.get("expression", ""), formula.Schema(table)) if field.type == "formula" else ""),
                                                 "presets": formula.presets(table) if field.type == "formula" else [],
                                                 "filled": _filled(table, field),
                                                 "active_nav": "settings_home"})


def _filled(table, field):
    from .models import Attachment, Link

    if field.type == "link":
        return Link.objects.filter(record__table=table, field=field.key).exclude(target_id=None).values("record_id").distinct().count()
    if field.type in ("file", "image"):
        return Attachment.objects.filter(record__table=table, field=field.key).values("record_id").distinct().count()
    return Record.everything.filter(table=table, values__has_key=field.key).count()


@require("tables.design")
@require_POST
def table_field_order(request, key):
    table = get_object_or_404(Table, key=key)
    try:
        keys = json.loads(request.body or b"{}").get("keys", [])
    except ValueError:
        return JsonResponse({"error": _("Ungültige Anfrage.")}, status=400)
    services.set_order(table, [str(k) for k in keys][:500])
    return JsonResponse({"ok": True})


@require("tables.design")
@require_POST
def table_formula_check(request, key):
    """The field dialog's check: the result type and the first records' values, or what is wrong and where."""
    table = get_object_or_404(Table, key=key)
    typed = request.POST.get("expression", "")
    field_key = request.POST.get("field", "") or None
    schema = formula.Schema(table)
    try:
        stored = formula.to_keys(typed, schema)
        result = formula.check(stored, schema, field_key)
    except formula.FormulaError as exc:
        marker = typed[: exc.position] if exc.position is not None else None
        return render(request, "tables/_formula_check.html", {"problem": exc.message, "before": marker, "typed": typed})
    labels = {"number": _("Zahl"), "text": _("Text"), "date": _("Datum"), "datetime": _("Datum und Uhrzeit"), "bool": _("Ja/Nein"), "null": _("leer")}
    return render(request, "tables/_formula_check.html", {
        "result": labels.get(result, _("Liste") if result.startswith("list") else _("Text")),
        "rows": formula.preview(table, stored, user=request.user),
    })


@require("tables.design")
def table_blueprints(request):
    """Verwaltung → Tabellen → Vorlage hinzufügen: the templates of the platform and of the modules in use."""
    found = blueprints.available()
    if request.method == "POST":
        template = found.get(request.POST.get("template", ""))
        if template is None or not blueprints.offered(template, found):
            messages.error(request, _("Diese Vorlage gibt es nicht."))
            return redirect("table_blueprints")
        try:
            report = blueprints.apply(template, request.user, demo=bool(request.POST.get("demo")), request=request)
        except blueprints.BlueprintError as exc:
            messages.error(request, str(exc))
            return redirect("table_blueprints")
        text = _("%(template)s: %(n)s neu angelegt.") % {"template": blueprints.title(template), "n": len(report["created"])}
        if report["changed"]:
            text += " " + _("Angepasst und deshalb nicht aktualisiert: %(items)s.") % {"items": ", ".join(report["changed"])}
        messages.success(request, text)
        return redirect("table_list")
    rows = []
    shown = [t for t in found.values() if blueprints.offered(t, found)]
    for template in sorted(blueprints.ordered(shown), key=lambda t: not blueprints.usable(t)):  # usable ones first
        installed = blueprints.installed_version(template["id"])
        rows.append({"id": template["id"], "title": blueprints.title(template), "description": blueprints.description(template),
                     "version": template["version"], "installed": installed, "usable": blueprints.usable(template),
                     "tables": [i18n.pick(t["name"], t.get("names")) for t in template["tables"]], "has_demo": bool(template.get("demo"))})
    return render(request, "tables/blueprints.html", {"rows": rows, "active_nav": "settings_home"})


@require("tables.design")
def table_export(request):
    """Tabellen → Als Vorlage exportieren: chosen tables as a template file, to share on datafluss.de or to use in
    another database (blueprints.export)."""
    from django.http import HttpResponse
    from django.utils.text import slugify

    tables = list(Table.objects.filter(archived=False))
    groups = services.by_section(tables)
    chosen = set(request.POST.getlist("tables")) if request.method == "POST" else set()
    first_section = next((t.section for t in tables if t.section), "")
    values = {"id": "", "title": first_section, "description": "", "demo": False}
    problems, skipped = [], []
    if request.method == "POST":
        values = {"id": request.POST.get("id", "").strip(), "title": request.POST.get("title", "").strip()[:100],
                  "description": request.POST.get("description", "").strip()[:1000], "demo": bool(request.POST.get("demo"))}
        picked = [t for t in tables if str(t.pk) in chosen]
        if not picked:
            problems = [_("Bitte mindestens eine Tabelle wählen.")]
        else:
            if not values["id"]:
                values["id"] = slugify(values["title"]).replace("-", "_")[:20]
            data, problems, skipped = blueprints.export(picked, values, demo=values["demo"], user=request.user)
            if not problems:
                audit.log("table", f"Als Vorlage exportiert: {', '.join(t.name for t in picked)}", request=request, user=request.user)
                answer = HttpResponse(json.dumps(data, ensure_ascii=False, indent=1), content_type="application/json; charset=utf-8")
                answer["Content-Disposition"] = f'attachment; filename="{data["id"]}.json"'
                return answer
    else:
        chosen = {str(t.pk) for t in tables if first_section and t.section == first_section} or {str(t.pk) for t in tables}
    return render(request, "tables/export.html", {"groups": groups, "chosen": chosen, "values": values, "problems": problems,
                                                  "skipped": skipped, "active_nav": "settings_home"})


# ---------------------------------------------------------------- a new table from a file

NEW_TABLE = "newtable"  # ImportSession.entity of a file that is still waiting to become a table
FILE_TYPES = ("text", "textarea", "number", "bool", "choice", "date", "datetime", "email", "phone", "url")


def _may_import_file(request):
    if not (request.user.can("tables.design") and request.user.can("data.import")):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied


@require_POST
def table_from_file(request):
    """Step 1: read the file. Its columns are offered as fields with a guessed type (tables/infer.py)."""
    from django.utils import timezone
    from datetime import timedelta
    from sync import journal
    from transfer.models import ImportSession
    from transfer.parsers import read_table
    from transfer.views import MAX_BYTES

    _may_import_file(request)
    file = request.FILES.get("file")
    if not file:
        messages.error(request, _("Bitte eine Datei wählen."))
        return redirect("table_list")
    if file.size > MAX_BYTES:
        messages.error(request, _("Die Datei ist größer als 20 MB."))
        return redirect("table_list")
    try:
        headers, rows = read_table(file.name, file.read())
    except Exception:
        messages.error(request, _("Die Datei konnte nicht gelesen werden. Bitte CSV oder XLSX verwenden."))
        return redirect("table_list")
    # Columns are matched by their heading, so every heading must be there and different.
    named = []
    for index, header in enumerate(str(h).strip() for h in headers):
        header = header or _("Spalte %(n)s") % {"n": index + 1}
        base, n = header, 2
        while header in named:
            header, n = f"{base} ({n})", n + 1
        named.append(header)
    headers = named
    if not rows or not headers:
        messages.error(request, _("In der Datei wurden keine Daten gefunden."))
        return redirect("table_list")
    journal.delete(ImportSession.objects.filter(entity=NEW_TABLE, created_at__lt=timezone.now() - timedelta(days=1)))
    session = ImportSession.objects.create(filename=file.name[:200], entity=NEW_TABLE, headers=headers, rows=rows, user=request.user)
    return redirect("table_from_file_columns", pk=session.pk)


def _file_columns(session):
    from . import infer

    columns = []
    for index, header in enumerate(session.headers):
        values = [str(row[index]) if index < len(row) else "" for row in session.rows[:2000]]
        guess = infer.column(header, values)
        filled = [v.strip() for v in values if v.strip()]
        columns.append({"index": index, "header": header, "guess": guess,
                        "samples": list(dict.fromkeys(filled))[:3], "unique": infer.is_unique(values), "empty": not filled,
                        "identifier": guess["type"] in ("text", "number", "email") and infer.is_identifier(header, values)})
    naming = re.compile(r"name|bezeichnung|titel|title|benennung|名称|名稱|标题|標題", re.IGNORECASE)
    title = next((c for c in columns if c["guess"]["type"] == "text" and naming.search(c["header"]) and not c["empty"]), None) \
        or next((c for c in columns if c["guess"]["type"] == "text" and c["unique"]), None) \
        or next((c for c in columns if c["guess"]["type"] == "text" and not c["empty"]), None) or (columns[0] if columns else None)
    return columns, title["index"] if title else 0


def table_from_file_columns(request, pk):
    """Step 2: name the table, check the columns, and create it with all rows in one go."""
    import os
    from transfer.models import ImportSession

    from .importer import ALWAYS_NEW, TableTarget

    _may_import_file(request)
    session = get_object_or_404(ImportSession, pk=pk, entity=NEW_TABLE, status=ImportSession.UPLOADED, user=request.user)
    columns, title_index = _file_columns(session)
    name = os.path.splitext(session.filename)[0].replace("_", " ").strip()[:100]
    error = ""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:100] or name
        try:
            table = _table_from_columns(request, session, columns, name)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            target = TableTarget(table)
            session.match_key = ALWAYS_NEW
            session.report = target.analyse(session)
            session.status = ImportSession.PREVIEWED
            session.save()
            result = target.commit(session, request.user)
            audit.log("import", f"Tabelle {table.name} aus {session.filename}: {result['created']} Datensätze", request=request, target=session.pk)
            text = _("%(table)s wurde mit %(count)s Datensätzen angelegt.") % {"table": table.display_name, "count": result["created"]}
            if result["skipped"]:
                text += " " + _("%(n)s Zeilen passten nicht und wurden übersprungen (Details unter Import & Export).") % {"n": result["skipped"]}
            messages.success(request, text)
            return redirect("table_records", key=table.key)
    return render(request, "tables/from_file.html", {
        "session": session, "columns": columns, "title_index": title_index, "name": name, "error": error,
        "types": [(value, label) for value, label in services.CREATE_TYPES if value in FILE_TYPES], "rows": len(session.rows),
        "access": services.access_choices(),
        "active_nav": "settings_home",
    })


def _table_from_columns(request, session, columns, name):
    """The table with a field per chosen column; the mapping for the import is stored on the session."""
    from django.db import transaction

    chosen = [c for c in columns if request.POST.get(f"use_{c['index']}")]
    if not chosen:
        raise ValidationError(_("Bitte mindestens eine Spalte übernehmen."))
    try:
        title_index = int(request.POST.get("title", ""))
    except ValueError:
        title_index = chosen[0]["index"]
    if title_index not in [c["index"] for c in chosen]:
        title_index = chosen[0]["index"]
    with transaction.atomic():
        table = services.create_table(name, request=request)
        services.set_access(table, request.POST.get("access"), request=request)
        first = Field.objects.get(table=table, key="f_name")
        mapping = {}
        for column in chosen:
            type_ = request.POST.get(f"type_{column['index']}", column["guess"]["type"])
            type_ = type_ if type_ in FILE_TYPES else "text"
            label = (request.POST.get(f"label_{column['index']}") or column["header"]).strip()[:100]
            guess = column["guess"] if type_ == column["guess"]["type"] else {"config": {}, "options": []}
            options = guess["options"]
            if type_ == "choice" and not options:
                values = [str(row[column["index"]]).strip() for row in session.rows if column["index"] < len(row)]
                options = list(dict.fromkeys(v for v in values if v))[:50] or [_("Ja")]
            if column["index"] == title_index and type_ == "text":
                first.label = label
                first.save()
                field = first
            else:
                field = services.create_field(table, label, type_, options_text="\n".join(options), config=guess["config"], request=request)
            if column["identifier"] and type_ in ("text", "number", "email"):
                field.unique = True  # a later import of the same list recognises its rows by it
                field.save()
            if column["index"] == title_index:
                table.title_field = field.key
            mapping[session.headers[column["index"]]] = field.key
        if table.title_field != first.key:
            first.delete()
        table.save()
        session.entity = f"table:{table.key}"
        session.mapping = mapping
        session.save()
    return table
