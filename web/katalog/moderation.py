"""The reviewers' queue: submitted versions, review texts waiting for approval, and reports."""
import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from konten.decorators import reviewer_required
from tables import template_check as tc

from . import services
from .models import Report, Review, Template, Version


@reviewer_required
def queue(request):
    return render(request, "katalog/queue.html", {
        "versions": Version.objects.filter(status=Version.SUBMITTED).select_related("template", "template__owner").order_by("created"),
        "reviews": Review.objects.filter(text_status=Review.SUBMITTED).select_related("template", "user").order_by("updated"),
        "reports": Report.objects.filter(handled=False).select_related("template", "review", "reporter").order_by("created"),
        "recent": Version.objects.exclude(status=Version.SUBMITTED).exclude(reviewed=None).select_related("template", "reviewer").order_by("-reviewed")[:15],
    })


def _diff(version):
    """What a version adds to the one shown now: new tables and fields by name, and what it no longer has."""
    published = version.template.published
    if published is None or published.pk == version.pk:
        return None
    def fields(data):
        return {(t["key"], f["key"]): f"{t.get('name')} · {f.get('label')}" for t in data.get("tables", []) for f in t.get("fields") or []}
    old, new = fields(published.data), fields(version.data)
    return {"from": published.number, "added": [new[k] for k in new if k not in old], "removed": [old[k] for k in old if k not in new]}


@reviewer_required
def version_detail(request, pk):
    version = get_object_or_404(Version.objects.select_related("template", "template__owner"), pk=pk)
    if request.method == "POST" and version.status == Version.SUBMITTED:
        note = request.POST.get("decision", "").strip()[:2000]
        if request.POST.get("action") == "approve":
            problems = tc.check_untrusted(version.data, reserved=set() if version.template.official else services.reserved_keys(version.template.owner))
            if problems:  # checked on upload; checked again, since the rules may have become stricter since
                messages.error(request, " ".join(problems[:5]))
                return redirect("review_version", pk=pk)
            services.approve(version, request.user, note)
            messages.success(request, _("Freigegeben: %(title)s") % {"title": version.title})
        elif request.POST.get("action") == "reject":
            if not note:
                messages.error(request, _("Bitte eine Begründung für den Einsender schreiben."))
                return redirect("review_version", pk=pk)
            services.reject(version, request.user, note)
            messages.success(request, _("Abgelehnt: %(title)s") % {"title": version.title})
        return redirect("review_queue")
    data = version.data
    return render(request, "katalog/review_version.html", {
        "version": version, "summary": tc.summary(data), "diff": _diff(version), "demo": data.get("demo") or {},
        "texts": sorted({t for t in tc._strings({k: v for k, v in data.items() if k not in ("demo", "tables")}) if t.strip()})[:200],
        "raw": json.dumps(data, ensure_ascii=False, indent=1)[:200000],
    })


@require_POST
@reviewer_required
def review_text(request, pk):
    review = get_object_or_404(Review, pk=pk)
    review.text_status = Review.APPROVED if request.POST.get("action") == "approve" else Review.REJECTED
    review.save(update_fields=["text_status"])
    return redirect("review_queue")


@require_POST
@reviewer_required
def report_done(request, pk):
    report = get_object_or_404(Report, pk=pk)
    action = request.POST.get("action")
    if action == "hide_template":
        Template.objects.filter(pk=report.template_id).update(withdrawn=True)
    elif action == "hide_review" and report.review_id:
        Review.objects.filter(pk=report.review_id).update(text_status=Review.REJECTED)
    report.handled = True
    report.save(update_fields=["handled"])
    return redirect("review_queue")
