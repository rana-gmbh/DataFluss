"""The records of an own table: list, record page, inline editing, bin and saved views (docs/TABLES-PLAN.md §4).

Every view checks the table's right for the user (tables/services.py right): read to look, edit to change.
"""
import json
import uuid
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from core import audit, fieldtypes as ft
from core.models import ChangeLog, SavedView
from vault.services import rate_limited

from . import masks as table_masks, services
from .models import Record, Table

GRID_EDITABLE = {"text", "textarea", "number", "bool", "choice", "date", "email", "phone", "url"}


def _table(request, key, edit=False):
    """The table, if the user may read (or edit) it. Only designers learn that a table does not exist:
    for everyone else a missing table and a forbidden one look the same."""
    table = Table.objects.filter(key=key).first()
    if table is None:
        if request.user.can("tables.design"):
            raise Http404
        raise PermissionDenied
    if not (services.may_edit if edit else services.may_read)(request.user, table):
        raise PermissionDenied
    return table


def _record(table, pk):
    return get_object_or_404(Record.objects.select_related("table"), table=table, pk=pk)


def _columns(table, user):
    editable = services.may_edit(user, table)
    columns = []
    for field in services.fields_of(table):
        kind = ft.TYPES.get(field.type)
        if kind is None or kind.storage != "value" or field.type == "secret":
            continue
        columns.append({
            "key": field.key, "title": str(field.display_label), "type": kind.grid, "visible": field.in_grid,
            "editable": editable and field.type in GRID_EDITABLE, "options": field.option_values,
            "optionLabels": {v: str(label) for v, label in field.option_choices}, "decimals": ft.decimals(field),
            "unit": ft.config(field).get("unit", ""),
        })
    return columns


def _grid_value(field, raw):
    value = ft.from_store(field, raw)
    if isinstance(value, Decimal):
        return float(value)
    return ft.to_store(field, value) if value is not None else None


def rows(table, ids=None):
    fields = [f for f in services.fields_of(table) if f.type != "secret" and ft.TYPES.get(f.type) and ft.TYPES[f.type].storage == "value"]
    records = Record.objects.filter(table=table)
    if ids is not None:
        records = records.filter(pk__in=ids)
    out = []
    for record in records.order_by("title"):
        row = {"id": str(record.pk), "name": record.title or _("(ohne Titel)")}
        for field in fields:
            raw = (record.values or {}).get(field.key)
            row[field.key] = _grid_value(field, raw) if raw not in (None, "") else None
        out.append(row)
    return out


def _views(table, user):
    entity = f"table:{table.key}"
    views = SavedView.objects.filter(entity=entity).filter(Q(owner=None) | Q(owner=user))
    return [{"id": str(v.pk), "name": v.name, "config": v.config, "shared": v.owner_id is None} for v in views]


def records_page(request, key):
    table = _table(request, key)
    fields = services.fields_of(table)
    named = services.title_field(table, fields)
    can_edit = services.may_edit(request.user, table)
    config = {
        "entity": f"table:{table.key}", "records": True, "columns": _columns(table, request.user),
        "dataUrl": reverse("table_rows", args=[table.key]), "recordBase": f"/tabellen/{table.key}/",
        "actionUrl": reverse("records_action", args=[table.key]), "fieldUrl": f"/api/tabellen/{table.key}/{{id}}/feld/{{key}}/",
        "viewsUrl": reverse("table_views", args=[table.key]), "viewDeleteUrl": f"/api/tabellen/{table.key}/ansichten/{{id}}/loeschen/",
        "initialSort": named.key if named else "name", "views": _views(table, request.user), "classes": [], "platforms": [],
        "templates": [], "canEdit": can_edit, "canArchive": False, "canDelete": can_edit, "preset": "",
    }
    return render(request, "tables/records.html", {"config": config, "table": table, "can_edit": can_edit, "active_nav": f"/tabellen/{table.key}/"})


@require_GET
def api_rows(request, key):
    table = _table(request, key)
    record = request.GET.get("id")
    if record:
        try:
            return JsonResponse(rows(table, ids=[uuid.UUID(record)]), safe=False)
        except ValueError:
            return HttpResponseBadRequest()
    return JsonResponse(rows(table), safe=False)


def _field(table, key):
    field = next((f for f in services.fields_of(table) if f.key == key), None)
    if field is None:
        raise Http404
    return field


def _save(request, table, record, key, raw):
    field = _field(table, key)
    if field.type == "secret" and not request.user.can("vault.edit"):
        raise PermissionDenied
    return field, *services.set_value(record, field, raw)


@require_POST
def api_field(request, key, pk, field_key):
    table = _table(request, key, edit=True)
    record = _record(table, pk)
    try:
        _save(request, table, record, field_key, json.loads(request.body or b"{}").get("value"))
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": " ".join(exc.messages)}, status=422)
    return JsonResponse({"ok": True})


@require_POST
def record_new(request, key):
    table = _table(request, key, edit=True)
    record = services.create_record(table)
    return redirect("record_detail", key=table.key, pk=record.pk)


def _context(request, table, record):
    tabs = table_masks.build_view(table_masks.tree_for(table, request.user), record, request.user)
    _fill_first_tab(request, record, tabs)
    return {"table": table, "record": record, "tabs": tabs, "form_url": reverse("record_form", args=[table.key, record.pk]),
            "form_kind": f"table-{table.key}", "can_edit": services.may_edit(request.user, table),
            "print_templates": [], "active_nav": f"/tabellen/{table.key}/"}


def _fill_first_tab(request, record, tabs):
    for section in tabs[0]["sections"] if tabs else []:
        for item in section["items"]:
            if item["type"] == "block" and item.get("block") in BLOCK_VIEWS:
                item["html"] = render_block(request, record, item["block"])


def record_detail(request, key, pk):
    table = _table(request, key)
    return render(request, "tables/record.html", _context(request, table, _record(table, pk)))


def record_panel(request, key, pk):
    table = _table(request, key)
    return render(request, "tables/_panel.html", _context(request, table, _record(table, pk)))


def record_form(request, key, pk):
    table = _table(request, key)
    return render(request, "masks/_form.html", _context(request, table, _record(table, pk)))


@require_POST
def record_field(request, key, pk, field_key):
    """A field of the record page saved on change (htmx), answered like people/views.py person_field."""
    table = _table(request, key, edit=True)
    record = _record(table, pk)
    field = _field(table, field_key)
    values = request.POST.getlist("value")
    raw = [v for v in values if v] if field.type == "multichoice" else (values[-1] if values else "")
    response = HttpResponse(status=204)
    try:
        field, saved, previous = _save(request, table, record, field_key, raw)
    except ValidationError as exc:
        response["HX-Trigger"] = json.dumps({"fieldError": {"key": field_key, "message": " ".join(exc.messages)}})
        return response
    tree = table_masks.tree_for(table, request.user)
    named = services.title_field(table, services.fields_of(table))
    reload_form = field_key in table_masks.condition_fields(tree) or bool(named and field_key == named.key)
    undoable = field.type not in ("secret", "multichoice") and str(previous) != str(saved)
    response["HX-Trigger"] = json.dumps({"fieldSaved": {
        "key": field_key, "label": str(field.display_label), "undoable": undoable, "old": str(previous),
        "url": reverse("record_field", args=[table.key, record.pk, field_key]), "reload": reload_form,
    }})
    return response


@require_POST
def record_secret_reveal(request, key, pk, field_key):
    table = _table(request, key)
    if not request.user.can("vault.reveal"):
        raise PermissionDenied
    record = _record(table, pk)
    field = _field(table, field_key)
    if field.type != "secret":
        raise Http404
    if rate_limited(request):
        return HttpResponse(_("Zu viele Abrufe. Bitte kurz warten."), status=429)
    audit.log("reveal", f"Geheimes Feld angezeigt: {field.label} in {table.name} ({record.title})", request=request, target=record.pk)
    return render(request, "vault/_secret_value.html", {"value": services.reveal(record, field), "dom_id": f"secret-value-{field_key}"})


def history(request, record):
    labels = {f"values.{f.key}": f.display_label for f in services.fields_of(record.table, include_archived=True)}
    rows = ChangeLog.objects.filter(entity="tables.record", record_id=record.pk).exclude(field__in=["values", "title", "search_text", "table"]).select_related("user")[:25]
    return {"changes": [{"row": r, "label": labels.get(r.field, r.field.removeprefix("values."))} for r in rows]}


BLOCK_VIEWS = {"history": history}


def render_block(request, record, name):
    from django.template.loader import render_to_string

    return render_to_string(f"tables/blocks/{name}.html", {"record": record, **BLOCK_VIEWS[name](request, record)}, request)


def record_block(request, key, pk, name):
    table = _table(request, key)
    if name not in BLOCK_VIEWS:
        raise Http404
    return HttpResponse(render_block(request, _record(table, pk), name))


@require_POST
def records_action(request, key):
    """Bulk actions from the list: delete into the bin and bring back."""
    table = _table(request, key, edit=True)
    payload = json.loads(request.body or b"{}")
    try:
        ids = [uuid.UUID(str(i)) for i in payload.get("ids", [])][:5000]
    except ValueError:
        return HttpResponseBadRequest()
    action = payload.get("action")
    if action == "delete":
        chosen = list(Record.objects.filter(table=table, pk__in=ids))
        for record in chosen:
            record.recycle()
        audit.log("table", f"{len(chosen)} Datensätze aus {table.name} in den Papierkorb gelegt", request=request, target=table.pk)
    elif action == "bring_back":
        chosen = list(Record.everything.filter(table=table, pk__in=ids).exclude(deleted_at=None))
        for record in chosen:
            record.bring_back()
    else:
        return HttpResponseBadRequest()
    return JsonResponse({"count": len(chosen), "refused": []})


def records_bin(request, key):
    table = _table(request, key, edit=True)
    if request.method == "POST":
        record = get_object_or_404(Record.everything.filter(table=table).exclude(deleted_at=None), pk=request.POST.get("record"))
        record.bring_back()
        messages.success(request, _("%(name)s ist wieder da.") % {"name": record.title or _("(ohne Titel)")})
        return redirect("records_bin", key=table.key)
    records = Record.in_the_bin().filter(table=table)
    return render(request, "tables/bin.html", {"table": table, "records": records, "keep_days": Record.KEEP_DAYS, "active_nav": f"/tabellen/{table.key}/"})


@require_POST
def api_views(request, key):
    table = _table(request, key)
    payload = json.loads(request.body or b"{}")
    name = (payload.get("name") or "").strip()[:80]
    if not name:
        return HttpResponseBadRequest()
    view, _created = SavedView.objects.update_or_create(entity=f"table:{table.key}", name=name, owner=request.user,
                                                        defaults={"config": payload.get("config") or {}})
    return JsonResponse({"id": str(view.pk), "name": view.name, "config": view.config, "shared": False})


@require_POST
def api_view_delete(request, key, pk):
    table = _table(request, key)
    get_object_or_404(SavedView, pk=pk, entity=f"table:{table.key}", owner=request.user).delete()
    return JsonResponse({"ok": True})
