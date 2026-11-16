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
from django.utils.translation import gettext as _, ngettext
from django.views.decorators.http import require_GET, require_POST

from core import audit, fieldtypes as ft
from core.models import ChangeLog, SavedView
from vault.services import rate_limited

from . import formula, links, masks as table_masks, services
from .models import Attachment, Record, Table

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
    fields = services.fields_of(table)
    named = services.title_field(table, fields)
    columns = []
    for field in fields:
        kind = ft.TYPES.get(field.type)
        if kind is None or field.type == "secret":
            continue
        grid = services.RESULTS.get((field.config or {}).get("result"), "text") if field.type == "formula" else kind.grid
        columns.append({
            "key": field.key, "title": str(field.display_label), "type": grid, "visible": field.in_grid,
            "editable": editable and field.type in GRID_EDITABLE, "options": field.option_values,
            "optionLabels": {v: str(label) for v, label in field.option_choices},
            "decimals": None if field.type == "formula" and "decimals" not in (field.config or {}) else ft.decimals(field),
            "unit": ft.config(field).get("unit", ""), "wide": bool(named and field.key == named.key) or field.type == "textarea",
        })
    return columns


def _grid_value(field, raw):
    value = ft.from_store(field, raw)
    if isinstance(value, Decimal):
        return float(value)
    return ft.to_store(field, value) if value is not None else None


def _related_texts(table, records, fields, user):
    """{(record id, field key): text} for link and file fields, fetched once for the whole list."""
    from .models import Link

    texts = {}
    ids = [r.pk for r in records]
    for field in fields:
        if field.type == "link":
            rows = list(Link.objects.filter(record_id__in=ids, field=field.key).exclude(target_id=None).order_by("order", "created_at"))
            found = links.resolve(user, links.kind_of(field), [r.target_id for r in rows], links.target_table(field))
            for row in rows:
                texts.setdefault((row.record_id, field.key), []).append(str(found[row.target_id]["title"]))
        elif field.type in ("file", "image"):
            for row in Attachment.objects.filter(record_id__in=ids, field=field.key):
                texts.setdefault((row.record_id, field.key), []).append(row.name)
    return {key: ", ".join(values) for key, values in texts.items()}


def _formula_cell(record, field, context):
    try:
        return formula.grid_value(field, formula.compute(record, field, context))
    except formula.Hidden:
        return _("nicht sichtbar")
    except formula.FormulaError:
        return "#Fehler"


def rows(table, user, ids=None):
    fields = [f for f in services.fields_of(table) if f.type != "secret" and ft.TYPES.get(f.type)]
    records = Record.objects.filter(table=table)
    if ids is not None:
        records = records.filter(pk__in=ids)
    records = list(records.order_by("title"))
    for record in records:  # the list knows its table: no query per record when formulas ask for it
        record.table = table
    related = _related_texts(table, records, [f for f in fields if f.type in ("link", "file", "image")], user)
    formulas = [f for f in fields if f.type == "formula"]
    context = formula.Context(user)
    if formulas:
        context.prefetch(records)
    out = []
    for record in records:
        row = {"id": str(record.pk), "name": record.title or _("(ohne Titel)")}
        for field in fields:
            if field.type == "formula":
                row[field.key] = _formula_cell(record, field, context)
                continue
            if not services.holds_value(field):
                row[field.key] = related.get((record.pk, field.key))
                continue
            raw = (record.values or {}).get(field.key)
            row[field.key] = _grid_value(field, raw) if raw not in (None, "") else None
        out.append(row)
    return out


def _print_templates(table, user):
    from prints.models import PrintTemplate

    if not user.can("print.run"):
        return PrintTemplate.objects.none()
    return PrintTemplate.objects.filter(source=f"table:{table.key}", archived=False)


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
        "templates": [{"id": str(t.pk), "name": t.name} for t in _print_templates(table, request.user)], "canEdit": can_edit, "canArchive": False, "canDelete": can_edit, "preset": "",
    }
    return render(request, "tables/records.html", {"config": config, "table": table, "can_edit": can_edit, "active_nav": f"/tabellen/{table.key}/"})


@require_GET
def api_rows(request, key):
    table = _table(request, key)
    record = request.GET.get("id")
    if record:
        try:
            return JsonResponse(rows(table, request.user, ids=[uuid.UUID(record)]), safe=False)
        except ValueError:
            return HttpResponseBadRequest()
    return JsonResponse(rows(table, request.user), safe=False)


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
    return redirect(reverse("record_detail", args=[table.key, record.pk]) + "?neu=1")


def _untouched(record):
    return not any(v not in (None, "", []) for v in record.values.values()) and not record.links.exclude(target_id=None).exists() \
        and not Attachment.objects.filter(record=record).exists()


@require_POST
def record_delete(request, key, pk):
    """The record page's delete: into the bin. A new record that was never filled in is dropped for good,
    so "Neuer Datensatz" and back leaves nothing behind."""
    table = _table(request, key, edit=True)
    record = _record(table, pk)
    if request.POST.get("discard") and _untouched(record):
        record.delete()
    else:
        record.recycle()
        messages.success(request, _("%(name)s liegt im Papierkorb.") % {"name": record.title or _("(ohne Titel)")})
    return redirect("table_records", key=table.key)


def _context(request, table, record):
    tabs = table_masks.build_view(table_masks.tree_for(table, request.user), record, request.user)
    _fill_first_tab(request, record, tabs)
    return {"table": table, "record": record, "tabs": tabs, "form_url": reverse("record_form", args=[table.key, record.pk]),
            "form_kind": f"table-{table.key}", "can_edit": services.may_edit(request.user, table),
            "print_templates": _print_templates(table, request.user), "active_nav": f"/tabellen/{table.key}/"}


def _fill_first_tab(request, record, tabs):
    for section in tabs[0]["sections"] if tabs else []:
        for item in section["items"]:
            if item["type"] == "block" and item.get("block") in BLOCK_VIEWS:
                item["html"] = render_block(request, record, item["block"])


def record_detail(request, key, pk):
    table = _table(request, key)
    context = _context(request, table, _record(table, pk))
    context["is_new"] = context["can_edit"] and request.GET.get("neu") == "1"
    return render(request, "tables/record.html", context)


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
    has_formulas = any(f.type == "formula" for f in services.fields_of(table))  # computed values follow every change
    reload_form = has_formulas or field_key in table_masks.condition_fields(tree) or bool(named and field_key == named.key)
    undoable = field.type not in ("secret", "multichoice") and str(previous) != str(saved)
    response["HX-Trigger"] = json.dumps({"fieldSaved": {
        "key": field_key, "label": str(field.display_label), "undoable": undoable, "old": str(previous),
        "url": reverse("record_field", args=[table.key, record.pk, field_key]), "reload": reload_form,
        "title": record.title or _("(ohne Titel)"),
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


def _history_text(field, text):
    """A value as the history stored it (core/models.py _plain: str() of the JSON value) as the field shows it."""
    if field is None or not text or field.type == "secret":
        return text
    try:
        raw = json.loads(text) if field.type == "multichoice" else (text == "True" if field.type == "bool" else text)
        return str(ft.display(field, ft.from_store(field, raw), localized=True))
    except (ValueError, TypeError):
        return text


def history(request, record):
    fields = {f"values.{f.key}": f for f in services.fields_of(record.table, include_archived=True)}
    rows = ChangeLog.objects.filter(entity="tables.record", record_id=record.pk).exclude(field__in=["values", "title", "search_text", "table"]).select_related("user")[:25]
    changes = []
    for r in rows:
        field = fields.get(r.field)
        changes.append({"row": r, "label": field.display_label if field else r.field.removeprefix("values."),
                        "old": _history_text(field, r.old_value), "new": _history_text(field, r.new_value)})
    return {"changes": changes}


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
    if request.method == "POST" and request.POST.get("all"):
        chosen = list(Record.in_the_bin().filter(table=table))
        for record in chosen:
            record.bring_back()
        messages.success(request, ngettext("%(count)d Datensatz ist wieder da.", "%(count)d Datensätze sind wieder da.", len(chosen)) % {"count": len(chosen)})
        return redirect("table_records", key=table.key)
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


def _widget(request, table, record, field, template, problem="", refocus=False):
    entry = table_masks._field_entry({"w": 12}, record, field, request.user, services.may_edit(request.user, table))
    entry["label"] = field.display_label
    entry["refocus"] = refocus and entry.get("multiple")
    response = render(request, template, {"item": entry})
    if problem:
        response["HX-Trigger"] = json.dumps({"toast": problem})
    return response


def _typed_field(table, key, types):
    field = _field(table, key)
    if field.type not in types:
        raise Http404
    return field


def record_link_options(request, key, pk, field_key):
    """What the picker offers: targets the user may see, without those already linked."""
    table = _table(request, key, edit=True)
    record = _record(table, pk)
    field = _typed_field(table, field_key, ("link",))
    linked = {str(t) for t in links.targets(record, field)}
    options = [o for o in links.candidates(request.user, field, request.GET.get("q", "")) if o["id"] not in linked]
    return render(request, "tables/_link_options.html", {"options": options, "field_key": field.key, "q": request.GET.get("q", ""),
                                                         "link_url": reverse("record_link", args=[table.key, record.pk, field.key])})


@require_POST
def record_link(request, key, pk, field_key):
    table = _table(request, key, edit=True)
    record = _record(table, pk)
    field = _typed_field(table, field_key, ("link",))
    problem = ""
    try:
        target = str(uuid.UUID(request.POST.get("target", "")))
        if request.POST.get("action") == "remove":
            links.remove_target(record, field, target)
        else:
            found = links.resolve(request.user, links.kind_of(field), [uuid.UUID(target)], links.target_table(field))
            if not found[uuid.UUID(target)]["visible"]:
                raise PermissionDenied
            links.add_target(record, field, target)
    except ValueError:
        return HttpResponseBadRequest()
    except ValidationError as exc:
        problem = " ".join(exc.messages)
    # Several targets: the search keeps the focus, so the next one is a few keys away.
    return _widget(request, table, record, field, "tables/_link_widget.html", problem, refocus=request.POST.get("action") != "remove")


@require_POST
def record_files(request, key, pk, field_key):
    table = _table(request, key, edit=True)
    record = _record(table, pk)
    field = _typed_field(table, field_key, ("file", "image"))
    problem = ""
    if request.POST.get("delete"):
        attachment = Attachment.objects.filter(record=record, field=field.key, pk=request.POST["delete"]).first()
        if attachment:
            audit.log("attachment", f"Datei {attachment.name} aus {table.name} gelöscht", request=request, target=record.pk)
            links.remove_file(attachment)
    elif request.FILES.get("file"):
        try:
            links.add_file(record, field, request.FILES["file"], request.user)
        except ValidationError as exc:
            problem = " ".join(exc.messages)
    return _widget(request, table, record, field, "tables/_file_widget.html", problem)


def record_file(request, key, pk, attachment):
    """A file of a record, for whoever may read the table. Pictures open in the browser, everything else downloads."""
    from django.http import FileResponse

    from core.views import INLINE_TYPES

    table = _table(request, key)
    item = get_object_or_404(Attachment, pk=attachment, record__table=table, record_id=pk)
    field = next((f for f in services.fields_of(table, include_archived=True) if f.key == item.field), None)
    inline = item.content_type in INLINE_TYPES and field is not None and field.type == "image"
    if not inline:
        audit.log("download", f"Datei {item.name} geöffnet", request=request, target=pk)
    response = FileResponse(item.file.open("rb"), as_attachment=not inline, filename=item.name)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    response["Cache-Control"] = "private, max-age=300"
    return response


def backlinks(request, kind, pk):
    """The section "Verknüpft in eigenen Tabellen" on a person's or device's page: empty when nothing links there."""
    if kind == "record":
        target = Record.objects.select_related("table").filter(pk=pk).first()
        if target is None or not services.may_read(request.user, target.table):
            raise PermissionDenied
    elif kind not in ("pupil", "staff", "asset"):
        raise Http404
    elif not request.user.can("device.view" if kind == "asset" else "person.view"):
        raise PermissionDenied
    groups = links.backlinks(request.user, kind, pk)
    if not groups:
        return HttpResponse("")
    return render(request, "tables/_backlinks.html", {"groups": groups})


def export_rows(table, user, ids, keys):
    """Rows for CSV or Excel: each cell as shown on screen, in the order of `ids` (what the list shows)."""
    fields = {f.key: f for f in services.fields_of(table) if f.type != "secret"}
    wanted = [k for k in keys]
    try:
        chosen = [uuid.UUID(str(i)) for i in ids][:50000]
    except ValueError:
        chosen = []
    records = {r.pk: r for r in Record.objects.filter(table=table, pk__in=chosen)}
    for record in records.values():
        record.table = table
    listed = [records[i] for i in chosen if i in records]
    related = _related_texts(table, listed, [f for f in fields.values() if f.type in ("link", "file", "image")], user)
    context = formula.Context(user)
    if any(f.type == "formula" for f in fields.values()):
        context.prefetch(listed)
    out = []
    for record in listed:
        row = []
        for key in wanted:
            field = fields.get(key)
            if key == "name":
                row.append(record.title)
            elif field is None:
                row.append("")
            elif field.type == "formula":
                row.append(formula.display(record, field, context)[0])
            elif field.type in ("link", "file", "image"):
                row.append(related.get((record.pk, key), ""))
            else:
                row.append(services.display(record, field, localized=True))
        out.append(row)
    return out
