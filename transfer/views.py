import json
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from core import audit
from core.permissions import require
from devices.views import _asset_rows
from people.views import person_rows
from sync import journal
from vault.models import Platform

from . import exporters, importer, targets
from .models import ImportPreset, ImportSession
from .parsers import read_table

MAX_BYTES = 20 * 1024 * 1024
# Left in the history by the Ninox takeover, which is no longer part of the app. Such a run is taken back
# only by restoring a backup, so it never offers the normal undo.
NINOX = "ninox"


def _download(content, filename, kind):
    types = {"csv": "text/csv; charset=utf-8", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    response = HttpResponse(content, content_type=types[kind])
    response["Content-Disposition"] = f'attachment; filename="{filename}.{kind}"'
    response["Cache-Control"] = "no-store, private"
    return response


@require("data.export")
def home(request):
    journal.delete(ImportSession.objects.filter(status__in=[ImportSession.UPLOADED, ImportSession.PREVIEWED], created_at__lt=timezone.now() - timedelta(days=1)))
    can_import = request.user.can("data.import")
    sessions = list(ImportSession.objects.select_related("user")[:10])
    for session in sessions:
        target = targets.target_for(session.entity)
        if session.entity == NINOX:
            session.undo_blocker = _("Über die Sicherung zurückzunehmen.")
        elif target is None:
            session.undo_blocker = _("Die Tabelle gibt es nicht mehr.")
        else:
            session.target_label = target.label
            session.undo_blocker = target.undo_blocker(session) if target.may_import(request.user) and session.status == ImportSession.DONE else ""
    from core import modules

    return render(request, "transfer/home.html", {"sessions": sessions, "can_import": can_import, "entities": targets.choices(request.user),
                                                  "basic": "schulverwaltung" in modules.active(),
                                                  "chosen": request.GET.get("bereich", "")})


@require("data.import")
@require_POST
def upload(request):
    file = request.FILES.get("file")
    entity = request.POST.get("entity", "pupil")
    target = targets.target_for(entity)
    if target is None or not target.may_import(request.user):
        raise PermissionDenied
    if not file:
        messages.error(request, _("Bitte eine Datei wählen."))
        return redirect("transfer")
    if file.size > MAX_BYTES:
        messages.error(request, _("Die Datei ist größer als 20 MB."))
        return redirect("transfer")
    try:
        headers, rows = read_table(file.name, file.read())
    except Exception:
        messages.error(request, _("Die Datei konnte nicht gelesen werden. Bitte CSV oder XLSX verwenden."))
        return redirect("transfer")
    if not headers or not rows:
        messages.error(request, _("In der Datei wurden keine Daten gefunden."))
        return redirect("transfer")
    session = ImportSession.objects.create(
        filename=file.name[:200], entity=entity, headers=headers, rows=rows, user=request.user,
        mapping=target.auto_mapping(headers), match_key=target.default_match(),
    )
    return redirect("transfer_mapping", pk=session.pk)


@require("data.import")
@require_http_methods(["GET", "POST"])
def mapping(request, pk):
    session = get_object_or_404(ImportSession, pk=pk, status__in=[ImportSession.UPLOADED, ImportSession.PREVIEWED])
    target = _target(request, session)
    if request.method == "POST":
        known = {key for key, _label in target.targets()}
        session.mapping = {h: value if (value := request.POST.get(f"map_{i}", "")) in known else "" for i, h in enumerate(session.headers)}
        chosen = request.POST.get("match_key", session.match_key)
        session.match_key = chosen if chosen in dict(target.match_keys()) else target.default_match()
        session.report = target.analyse(session)
        session.status = ImportSession.PREVIEWED
        session.save()
        return redirect("transfer_preview", pk=pk)
    columns = []
    for index, header in enumerate(session.headers):
        samples = [row[index] for row in session.rows[:40] if index < len(row) and row[index]][:3]
        columns.append({"index": index, "header": header, "target": session.mapping.get(header, ""), "samples": samples})
    return render(request, "transfer/mapping.html", {
        "session": session, "columns": columns, "targets": target.targets(), "match_keys": target.match_keys(),
        "match_label": target.match_label(), "target_label": target.label, "active_nav": "transfer",
    })


def _target(request, session):
    target = targets.target_for(session.entity)
    if target is None or not target.may_import(request.user):
        raise PermissionDenied
    return target


@require("data.import")
def preview(request, pk):
    session = get_object_or_404(ImportSession, pk=pk, status=ImportSession.PREVIEWED)
    _target(request, session)
    return render(request, "transfer/preview.html", {"session": session, "report": session.report, "rows": session.report.get("rows", [])[:300], "total": len(session.rows), "active_nav": "transfer"})


@require("data.import")
@require_POST
def run_commit(request, pk):
    session = get_object_or_404(ImportSession, pk=pk)
    target = _target(request, session)
    try:
        result = target.commit(session, request.user)
    except importer.ImportClosed as exc:
        messages.error(request, str(exc))
        return redirect("transfer")
    audit.log("import", f"Import {session.filename}: {result['created']} neu, {result['updated']} geändert, {result['skipped']} übersprungen", request=request, target=session.pk)
    messages.success(request, _("Import abgeschlossen: %(created)s neu, %(updated)s geändert, %(skipped)s übersprungen.") % result)
    return redirect("transfer")


@require("data.import")
@require_POST
def undo(request, pk):
    session = get_object_or_404(ImportSession, pk=pk)
    if session.entity == NINOX:
        return HttpResponseBadRequest()
    target = _target(request, session)
    try:
        target.rollback(session)
    except importer.ImportClosed as exc:
        messages.error(request, str(exc))
        return redirect("transfer")
    audit.log("import", f"Import zurückgenommen: {session.filename}", request=request, target=session.pk)
    messages.success(request, _("Der Import wurde zurückgenommen."))
    return redirect("transfer")


@require("data.import")
@require_POST
def discard(request, pk):
    get_object_or_404(ImportSession, pk=pk).delete()
    return redirect("transfer")


@require("data.export")
@require_POST
def export_rows(request):
    payload = json.loads(request.POST.get("payload") or "{}")
    entity = payload.get("entity")
    fmt = payload.get("format", "csv")
    columns = [c for c in payload.get("columns", []) if isinstance(c, dict) and c.get("key")]
    ids = payload.get("ids") or []
    if fmt not in ("csv", "xlsx") or not columns:
        return HttpResponseBadRequest()
    if (entity or "").startswith("table:"):
        return _export_table(request, entity.removeprefix("table:"), fmt, columns, ids)
    if entity not in ("pupil", "staff"):
        return HttpResponseBadRequest()
    if not request.user.can(f"{entity}.view"):
        raise PermissionDenied
    by_id = {r["id"]: r for r in person_rows(entity, ids=ids)}
    rows = [[by_id[i].get(c["key"], "") for c in columns] for i in ids if i in by_id]
    titles = [c.get("title") or c["key"] for c in columns]
    content = exporters.to_csv(titles, rows) if fmt == "csv" else exporters.to_xlsx(titles, rows)
    audit.log("export", f"Export {entity}: {len(rows)} Zeilen als {fmt}", request=request)
    return _download(content, f"export-{entity}-{timezone.localdate().isoformat()}", fmt)


def _export_table(request, key, fmt, columns, ids):
    from tables import services as table_services
    from tables.models import Table
    from tables.views_records import export_rows as table_rows

    table = Table.objects.filter(key=key).first()
    if table is None or not table_services.may_read(request.user, table):
        raise PermissionDenied
    rows = table_rows(table, request.user, ids, [c["key"] for c in columns])
    titles = [c.get("title") or c["key"] for c in columns]
    content = exporters.to_csv(titles, rows) if fmt == "csv" else exporters.to_xlsx(titles, rows, sheet=table.display_name)
    audit.log("export", f"Export Tabelle {table.name}: {len(rows)} Zeilen als {fmt}", request=request, target=table.pk)
    return _download(content, f"export-{table.key}-{timezone.localdate().isoformat()}", fmt)


@require("data.export")
def export_assets(request):
    fmt = request.GET.get("format", "xlsx")
    if fmt not in ("csv", "xlsx"):
        return HttpResponseBadRequest()
    columns = [("number", _("Gerätenummer")), ("category", _("Typ")), ("serial", _("Seriennummer")), ("status", _("Status")), ("holder", _("Bei")), ("issued", _("Ausgabe"))]
    rows = [[row[key] for key, _title in columns] for row in _asset_rows()]
    titles = [title for _key, title in columns]
    audit.log("export", f"Export Geräte: {len(rows)} Zeilen als {fmt}", request=request)
    content = exporters.to_csv(titles, rows) if fmt == "csv" else exporters.to_xlsx(titles, rows)
    return _download(content, f"export-geraete-{timezone.localdate().isoformat()}", fmt)


@require("vault.export")
@require_POST
def export_credentials(request, key):
    """Only for IT, only after confirmation, always logged."""
    platform = get_object_or_404(Platform, key=key)
    if request.POST.get("confirm") != "1":
        raise PermissionDenied
    titles = [_("Name"), _("Klasse")] + [f["label"] for f in platform.fields]
    rows = []
    for credential in platform.credentials.select_related("person", "person__current_class", "guardian"):
        person = credential.person
        klasse = person.current_class.name if person and person.current_class else ""
        rows.append([credential.owner.display_name, klasse] + [f["value"] for f in credential.read_all(include_secrets=True)])
    audit.log("export_secrets", f"Zugänge exportiert: {platform.name} ({len(rows)})", request=request)
    return _download(exporters.to_csv(titles, rows), f"zugaenge-{slugify(platform.key)}", "csv")
