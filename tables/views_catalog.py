"""Vorlage hinzufügen → Von datafluss.de and Aus Datei (tables/catalog.py, docs/tables.md).

A chosen template is fetched and checked first and shown as a preview (tables, fields, rights, sample records,
origin and licence); only "Anwenden" applies it. The checked file waits in the session in between, never in the
form, so what is applied is exactly what was checked. All of this runs on the server or on a computer with its own
database (sync/middleware.py SERVER_ONLY).
"""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from archive import plan
from core import audit
from core.models import SchoolSettings
from core.permissions import require

from . import blueprints, catalog, template_check as tc
from .models import CatalogTemplate

PREVIEW = "template_preview"


def _may_add(data):
    """A database template only where blueprints.offered would offer one; an applied one again to bring it up to date."""
    return data["id"] in blueprints.applied_ids() or not data.get("database") or blueprints.database_free()


@require("tables.design")
def catalog_list(request):
    """The catalog's templates, loaded into "Vorlage hinzufügen" after the page (the catalog may answer slowly)."""
    context = {"enabled": catalog.enabled(), "connected": plan.is_connected(), "free": blueprints.database_free(), "rows": [], "error": ""}
    if context["enabled"] and not context["connected"]:
        try:
            applied, built_in = blueprints.applied_ids(), catalog.reserved_ids()  # the platform's own come with DataFluss
            context["rows"] = [{**e, "applied": e["id"] in applied, "installed": blueprints.installed_version(e["id"])}
                               for e in catalog.index() if e["id"] not in built_in]
        except catalog.CatalogError as exc:
            context["error"] = str(exc)
    return render(request, "tables/_catalog.html", context)


@require("tables.design")
def template_preview(request):
    if request.method == "POST" and request.POST.get("pick"):
        try:
            item = catalog.entry(request.POST["pick"])
            if item is None:
                raise catalog.CatalogError(_("Diese Vorlage gibt es im Katalog nicht (mehr)."))
            data = catalog.fetch(item)
        except catalog.CatalogError as exc:
            messages.error(request, str(exc))
            return redirect("table_blueprints")
        request.session[PREVIEW] = {"data": data, "source": CatalogTemplate.CATALOG, "details": catalog.info(item)}
        return redirect("table_template_preview")
    if request.method == "POST" and request.FILES.get("file"):
        data, problems = catalog.read_file(request.FILES["file"].read(tc.MAX_BYTES + 1))
        if problems:
            return render(request, "tables/template_refused.html", {"problems": problems[:20], "more": max(len(problems) - 20, 0),
                                                                    "active_nav": "settings_home"})
        request.session[PREVIEW] = {"data": data, "source": CatalogTemplate.FILE, "details": {}}
        return redirect("table_template_preview")
    waiting = request.session.get(PREVIEW)
    if not waiting:
        return redirect("table_blueprints")
    data, source = waiting["data"], waiting["source"]
    if request.method == "POST" and request.POST.get("action") == "cancel":
        request.session.pop(PREVIEW, None)
        return redirect("table_blueprints")
    if request.method == "POST" and request.POST.get("action") == "apply":
        request.session.pop(PREVIEW, None)
        if not _may_add(data):
            messages.error(request, _("Eine Datenbank-Vorlage passt nur in eine Datenbank ohne Schulverwaltung und ohne andere Datenbank-Vorlage."))
            return redirect("table_blueprints")
        try:
            report = catalog.install(data, source, request.user, demo=bool(request.POST.get("demo")), request=request, details=waiting["details"])
        except blueprints.BlueprintError as exc:
            messages.error(request, str(exc))
            return redirect("table_blueprints")
        messages.success(request, _("%(template)s: %(n)s neu angelegt.") % {"template": blueprints.title(data), "n": len(report["created"])})
        return redirect("table_list")
    from core.models import role_choices

    roles = dict(role_choices())
    return render(request, "tables/template_preview.html", {
        "data": data, "source": source, "details": waiting["details"], "title": blueprints.title(data),
        "description": blueprints.description(data), "summary": tc.summary(data), "notes": tc.notes(data),
        "rights": sorted({(t.get("name", ""), roles.get(role, role), right) for t in data["tables"] for role, right in (t.get("rights") or {}).items()}),
        "installed": blueprints.installed_version(data["id"]), "may_add": _may_add(data), "active_nav": "settings_home",
    })


@require_POST
@require("tables.design")
def catalog_update(request, template_id):
    """"Ergänzen" of the update notice for a catalog template: fetch the newer version, check it, add what it brings."""
    try:
        report = catalog.update(template_id, request.user, request=request)
    except (catalog.CatalogError, blueprints.BlueprintError) as exc:
        messages.error(request, str(exc))
        return redirect("table_list")
    messages.success(request, _("%(template)s: %(n)s neu angelegt.") % {"template": template_id, "n": len(report["created"])})
    return redirect("table_list")


@require_POST
@require("tables.design")
def catalog_switch(request):
    on = request.POST.get("action") == "on"
    SchoolSettings.objects.filter(pk=SchoolSettings.load().pk).update(catalog_updates=on)
    audit.log("settings", f"Tägliche Prüfung auf neue Vorlagen-Versionen {'eingeschaltet' if on else 'ausgeschaltet'}", request=request)
    return redirect("table_blueprints")
