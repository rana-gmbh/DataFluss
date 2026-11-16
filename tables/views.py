"""Verwaltung → Tabellen: designing own tables (docs/TABLES-PLAN.md §4). Lists and record pages follow."""
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core import audit, fieldtypes as ft
from core.permissions import require
from masks import i18n

from . import formula, links, masks as table_masks, services
from .models import EDIT, NONE, READ, Field, Record, Table

ICONS = ["table", "book", "list", "calendar", "users", "tag", "archive", "laptop", "clipboard", "heart", "school", "file", "image", "key", "wrench"]
TABS = ("felder", "seite", "rechte", "allgemein")


@require("tables.design")
def table_list(request):
    error = ""
    if request.method == "POST":
        try:
            table = services.create_table(request.POST.get("name"), request=request)
            messages.success(request, _("%(table)s wurde angelegt. Fügen Sie jetzt Felder hinzu.") % {"table": table.display_name})
            return redirect("table_settings", key=table.key)
        except ValidationError as exc:
            error = " ".join(exc.messages)
    tables = Table.objects.all()
    counts = {row["table_id"]: row["n"] for row in Record.objects.values("table_id").order_by().annotate(n=Count("id"))}
    rows = [{"table": t, "records": counts.get(t.pk, 0), "fields": t.fields.filter(archived=False).count()} for t in tables]
    return render(request, "tables/list.html", {"rows": rows, "error": error, "active_nav": "settings_home"})


def _settings_context(table, tab, **extra):
    fields = services.fields_of(table, include_archived=True)
    return {
        "table": table, "tab": tab, "fields": fields, "types": services.CREATE_TYPES, "icons": ICONS,
        "link_targets": links.target_choices(),
        "title_choices": [f for f in fields if not f.archived and services.may_name(f)],
        "presets": formula.presets(table), "expression_json": json.dumps(""),
        "roles": [{"value": value, "label": label, "right": (table.rights or {}).get(value, NONE)} for value, label in services.roles()],
        "rights": [(NONE, _("Kein Zugriff")), (READ, _("Lesen")), (EDIT, _("Bearbeiten"))],
        "has_records": Record.everything.filter(table=table).exists(),
        "masks": table.masks.prefetch_related("versions"),
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
    return render(request, "tables/field.html", {"table": table, "field": field, "options": options, "compatible": compatible,
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
