import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.utils.translation import gettext as _, gettext_lazy
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from jinja2 import TemplateError
from weasyprint.urls import URLFetchingError

from core import audit
from core.permissions import require
from devices.models import Asset, SupportCase
from people.models import ClassGroup, Guardian, Person, SchoolYear

from .engine import render_pdf
from .models import PrintJob, PrintTemplate, PrintTemplateVersion
from .presets import PRESETS
from .records import available_sources, field_catalog, list_columns, load_records, module_source, sample_record

# Labels of the page formats in the designer's menu. presets.py keeps the German text; new formats fall back to it.
PRESET_LABELS = {
    "a4": gettext_lazy("A4 Hochformat"), "a4l": gettext_lazy("A4 Querformat"), "a5": gettext_lazy("A5 Hochformat"),
    "a4-2up": gettext_lazy("A4, 2 Blätter zum Schneiden"), "a4-4up": gettext_lazy("A4, 4 Blätter zum Schneiden"),
    "label-3474": gettext_lazy("Etiketten 70 × 37 mm (3 × 8)"), "label-l7160": gettext_lazy("Etiketten 63,5 × 38,1 mm (3 × 7)"),
    "card": gettext_lazy("Ausweiskarte 85,6 × 54 mm"),
}


def _doc(template):
    return {
        "name": template.name, "source": template.source, "tree": template.tree, "code_mode": template.code_mode,
        "code_html": template.code_html, "code_css": template.code_css, "contains_secrets": template.contains_secrets,
    }


def _contains_logins(tree, code_html=""):
    for row in (tree or {}).get("rows", []):
        for col in row.get("cols", []):
            if any(el.get("type") == "logins" for el in col.get("items", [])):
                return True
    return "logins" in (code_html or "")


def _pdf_response(pdf, name, warnings=()):
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{slugify(name) or "bericht"}.pdf"'
    response["Cache-Control"] = "no-store, private"
    if warnings:
        response["X-PDF-Warnings"] = json.dumps(list(warnings)[:10], ensure_ascii=True)
    return response


@require("print.run")
def reports(request):
    groups = {}
    labels = dict(available_sources(request.user))  # templates of a module that is not loaded, or of a table the user may not read, stay hidden
    for template in PrintTemplate.objects.filter(archived=False, source__in=labels):
        groups.setdefault(labels[template.source], []).append(template)
    return render(request, "prints/reports.html", {
        "groups": groups, "jobs": PrintJob.objects.select_related("user")[:12], "sources": labels.items(),
        "classes": ClassGroup.objects.filter(school_year=SchoolYear.current()),
    })


@require("print.run")
@require_POST
def report_run(request):
    template = get_object_or_404(PrintTemplate, pk=request.POST.get("template"), archived=False)
    raw_ids = [i for i in request.POST.get("ids", "").replace(" ", "").split(",") if i]
    class_id = request.POST.get("class_id") or None
    source = template.source
    if source not in dict(available_sources(request.user)):
        raise Http404
    if (extra := module_source(source)) and not extra.may_print(request.user):
        raise PermissionDenied
    if request.POST.get("scope") == "class" and class_id and source in ("pupil",):
        raw_ids = [str(i) for i in Person.objects.filter(current_class_id=class_id, archived=False).values_list("pk", flat=True)]
    elif request.POST.get("scope") == "all":
        if source in ("pupil", "staff"):
            raw_ids = [str(i) for i in Person.objects.filter(kind=source, archived=False).values_list("pk", flat=True)]
        elif source == "guardian":
            raw_ids = [str(i) for i in Guardian.objects.values_list("pk", flat=True)]
        elif source == "asset":
            raw_ids = [str(i) for i in Asset.objects.values_list("pk", flat=True)]
        elif source == "case":
            raw_ids = [str(i) for i in SupportCase.objects.exclude(status="Erledigt").values_list("pk", flat=True)]
        elif extra is not None and hasattr(extra, "all_ids"):
            raw_ids = extra.all_ids()
    with_secrets = template.contains_secrets
    if with_secrets and not request.user.can("print.secrets"):
        raise PermissionDenied
    records = load_records(source, raw_ids, include_secrets=with_secrets, class_id=class_id)
    if not records:
        return HttpResponse(_("Keine Datensätze ausgewählt."), status=400)
    pdf, pages, warnings = render_pdf(_doc(template), records)
    PrintJob.objects.create(template_name=template.name, record_count=len(records), page_count=pages, with_secrets=with_secrets, user=request.user)
    audit.log("print", f"Gedruckt: {template.name} ({len(records)} Datensätze, {pages} Seiten)" + (" mit Passwörtern" if with_secrets else ""), request=request)
    return _pdf_response(pdf, template.name, warnings)


@require("print.design")
@require_http_methods(["GET", "POST"])
def template_new(request):
    if request.method == "POST":
        source = request.POST.get("source", "pupil")
        if source not in dict(available_sources(request.user)):
            raise Http404
        name = request.POST.get("name", "").strip() or _("Neue Vorlage")
        template = PrintTemplate.objects.create(name=name, source=source, tree={"page": {"preset": "a4"}, "rows": []})
        return redirect("template_designer", pk=template.pk)
    return redirect("reports")


def _variables(source):
    return field_catalog(source)


@require("print.design")
def template_designer(request, pk):
    template = get_object_or_404(PrintTemplate, pk=pk)
    from vault.models import Platform

    config = {
        "id": str(template.pk), "name": template.name, "source": template.source, "tree": template.tree, "codeMode": template.code_mode,
        "codeHtml": template.code_html, "codeCss": template.code_css, "fields": _variables(template.source),
        "presets": [{"key": k, "label": str(PRESET_LABELS.get(k, v["label"])), "w": v["w"], "h": v["h"]} for k, v in PRESETS.items()],
        "platforms": [{"key": p.key, "name": p.name} for p in Platform.objects.filter(active=True)],
        "listColumns": [{"key": k, "title": t} for k, t in list_columns(template.source)],
        "lists": extra.lists() if (extra := module_source(template.source)) and hasattr(extra, "lists") else None,
        "canCode": request.user.can("print.code"),
    }
    versions = template.versions.select_related("author")[:10]
    return render(request, "prints/designer.html", {"template": template, "config": config, "versions": versions, "active_nav": "reports"})


@require("print.design")
@require_POST
def template_preview(request, pk):
    """Renders the unsaved document with sample data, so the preview is the real PDF."""
    template = get_object_or_404(PrintTemplate, pk=pk)
    payload = json.loads(request.body or b"{}")
    if payload.get("codeMode") and not request.user.can("print.code"):
        raise PermissionDenied
    doc = {
        "name": template.name, "source": template.source, "tree": payload.get("tree") or {}, "code_mode": bool(payload.get("codeMode")),
        "code_html": payload.get("codeHtml", ""), "code_css": payload.get("codeCss", ""),
        "contains_secrets": _contains_logins(payload.get("tree"), payload.get("codeHtml", "")),
    }
    record = sample_record(template.source)
    count = max(1, min(int(payload.get("copies") or 1), 8))
    try:
        pdf, pages, warnings = render_pdf(doc, [record] * count)
    except (TemplateError, URLFetchingError, ValueError, TypeError) as exc:
        return JsonResponse({"error": _("Vorlage fehlerhaft: %(error)s") % {"error": exc}}, status=422)
    return _pdf_response(pdf, template.name, warnings)


@require("print.design")
@require_POST
def template_save(request, pk):
    template = get_object_or_404(PrintTemplate, pk=pk)
    payload = json.loads(request.body or b"{}")
    code_mode = bool(payload.get("codeMode"))
    if code_mode and not request.user.can("print.code"):
        raise PermissionDenied
    with transaction.atomic():
        PrintTemplateVersion.objects.create(
            template=template, number=template.version, tree=template.tree, code_mode=template.code_mode,
            code_html=template.code_html, code_css=template.code_css, author=request.user,
        )
        template.tree = payload.get("tree") or {}
        template.code_mode, template.code_html, template.code_css = code_mode, payload.get("codeHtml", ""), payload.get("codeCss", "")
        template.name = (payload.get("name") or template.name).strip()[:120]
        template.contains_secrets = _contains_logins(template.tree, template.code_html)
        template.version += 1
        template.save()
    audit.log("template", f"Vorlage gespeichert: {template.name} (Version {template.version})", request=request, target=template.pk)
    return JsonResponse({"ok": True, "version": template.version})


@require("print.design")
@require_POST
def template_restore(request, pk, number):
    template = get_object_or_404(PrintTemplate, pk=pk)
    version = get_object_or_404(template.versions, number=number)
    template.tree, template.code_mode, template.code_html, template.code_css = version.tree, version.code_mode, version.code_html, version.code_css
    template.contains_secrets = _contains_logins(template.tree, template.code_html)
    template.version += 1
    template.save()
    audit.log("template", f"Vorlage {template.name}: Version {number} wiederhergestellt", request=request, target=template.pk)
    messages.success(request, _("Version %(number)s wiederhergestellt.") % {"number": number})
    return redirect("template_designer", pk=pk)


@require("print.design")
@require_POST
def template_action(request, pk):
    template = get_object_or_404(PrintTemplate, pk=pk)
    if request.POST.get("action") == "duplicate":
        copy = PrintTemplate.objects.create(
            name=_("%(name)s (Kopie)") % {"name": template.name}, description=template.description, source=template.source, tree=template.tree,
            code_mode=template.code_mode, code_html=template.code_html, code_css=template.code_css, contains_secrets=template.contains_secrets,
        )
        return redirect("template_designer", pk=copy.pk)
    template.archived = True
    template.save()
    return redirect("reports")
