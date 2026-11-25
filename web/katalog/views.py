"""Pages for visitors (browse, look at, download), for people sharing templates (upload, their own templates) and for
reviews and reports."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from konten import limits
from konten.decorators import named_required
from tables import template_check as tc

from . import services
from .models import CATEGORIES, Picture, Report, Review, Template, Version

SORTS = {"neu": ("-updated",), "beste": ("-rating", "-ratings"), "beliebt": ("-downloads",)}


def listed():
    return Template.objects.filter(published__isnull=False, withdrawn=False).select_related("published", "owner")


def home(request):
    templates = listed()
    q = " ".join(request.GET.get("q", "").split())[:100]
    for word in q.lower().split():
        templates = templates.filter(search__contains=word)
    category = request.GET.get("kategorie", "")
    if category in dict(CATEGORIES):
        templates = templates.filter(category=category)
    sort = request.GET.get("sortierung", "neu")
    templates = templates.order_by(*SORTS.get(sort, SORTS["neu"]), "key")
    page = Paginator(templates, 24).get_page(request.GET.get("seite"))
    return render(request, "katalog/home.html", {"page": page, "q": q, "category": category, "sort": sort, "categories": CATEGORIES,
                                                 "sorts": [("neu", _("Neueste")), ("beste", _("Beste")), ("beliebt", _("Meist geladen"))]})


def _visible(request, key):
    """A template as the visitor may see it: listed, or their own, or anything for a reviewer."""
    template = get_object_or_404(Template.objects.select_related("published", "owner"), key=key)
    if template.listed or (request.user.is_authenticated and (request.user.is_staff or template.owner_id == request.user.pk)):
        return template
    raise Http404


def detail(request, key):
    template = _visible(request, key)
    version = template.published or template.versions.first()
    own = None
    if request.user.is_authenticated:
        own = Review.objects.filter(template=template, user=request.user).first()
    reviews = template.reviews.filter(text_status=Review.APPROVED).select_related("user")
    return render(request, "katalog/detail.html", {
        "template": template, "version": version, "summary": tc.summary(version.data) if version else None,
        "history": template.versions.filter(status__in=[Version.APPROVED, Version.REPLACED]),
        "reviews": reviews, "own": own, "stamp": limits.form_stamp(), "star_choices": services.STARS,
        "may_review": request.user.is_authenticated and template.listed and template.owner_id != request.user.pk and not template.official,
    })


def _file(template, version, attachment=True):
    response = HttpResponse(services.file_bytes(version), content_type="application/json; charset=utf-8")
    disposition = "attachment" if attachment else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="{template.key}-{version.number}.json"'
    response["X-Content-SHA256"] = version.sha256
    return response


def download(request, key):
    template = get_object_or_404(Template, key=key, published__isnull=False, withdrawn=False)
    services.count_download(template)
    return _file(template, template.published)


def picture(request, uid):
    pic = get_object_or_404(Picture.objects.select_related("version__template"), uid=uid)
    version = pic.version
    if version.status not in (Version.APPROVED, Version.REPLACED) or version.template.withdrawn:
        user = request.user
        if not (user.is_authenticated and (user.is_staff or version.template.owner_id == user.pk)):
            raise Http404
    response = FileResponse(pic.image.open("rb"), content_type="image/webp")
    response["Cache-Control"] = "public, max-age=86400"
    return response


@named_required
def upload(request, key=None):
    """A new template, or with `key` a new version of one's own."""
    template = None
    if key is not None:
        template = get_object_or_404(Template, key=key, owner=request.user)
    problems = []
    if request.method == "POST":
        if not limits.looks_human(request):
            problems = [_("Das ging zu schnell. Bitte noch einmal versuchen.")]
        elif not (request.POST.get("license") and request.POST.get("no_personal_data")):
            problems = [_("Bitte den Bedingungen zustimmen.")]
        elif not request.FILES.get("file"):
            problems = [_("Bitte eine Vorlagendatei wählen.")]
        elif not limits.allowed("upload", request.user.pk):
            problems = [_("Heute wurden schon viele Vorlagen hochgeladen. Bitte morgen weiter.")]
        else:
            raw = request.FILES["file"].read(tc.MAX_BYTES + 1)
            version, problems = services.submit(request.user, raw, request.POST.get("category", ""), request.POST.get("changes", ""),
                                                request.FILES.getlist("pictures"), template=template)
            if version is not None:
                messages.success(request, _("Danke! Die Vorlage wird geprüft und erscheint nach der Freigabe."))
                return redirect("my_templates")
    return render(request, "katalog/upload.html", {"template": template, "problems": problems, "categories": CATEGORIES,
                                                   "stamp": limits.form_stamp(), "values": request.POST})


@login_required
def my_templates(request):
    templates = Template.objects.filter(owner=request.user).prefetch_related("versions")
    return render(request, "katalog/mine.html", {"templates": templates})


@require_POST
@login_required
def withdraw(request, key):
    template = get_object_or_404(Template, key=key, owner=request.user)
    template.withdrawn = request.POST.get("action") != "restore"
    template.save(update_fields=["withdrawn"])
    if template.withdrawn:
        template.versions.filter(status=Version.SUBMITTED).update(status=Version.WITHDRAWN)
    messages.success(request, _("Die Vorlage ist zurückgezogen.") if template.withdrawn else _("Die Vorlage wird wieder gezeigt."))
    return redirect("my_templates")


@require_POST
@named_required
def review(request, key):
    template = get_object_or_404(Template, key=key, published__isnull=False, withdrawn=False)
    if template.owner_id == request.user.pk or template.official:
        messages.error(request, _("Eigene Vorlagen lassen sich nicht bewerten."))
    elif request.POST.get("action") == "delete":
        Review.objects.filter(template=template, user=request.user).delete()
        services.recount(template)
        messages.success(request, _("Ihre Bewertung ist gelöscht."))
    elif not limits.looks_human(request):
        messages.error(request, _("Das ging zu schnell. Bitte noch einmal versuchen."))
    elif not limits.allowed("review", request.user.pk):
        messages.error(request, _("Heute wurden schon viele Bewertungen abgegeben. Bitte morgen weiter."))
    else:
        stars = services.parse_stars(request.POST.get("stars"))
        if stars is None:
            messages.error(request, _("Bitte 0,5 bis 5 Sterne wählen."))
        else:
            saved = services.rate(template, request.user, stars, request.POST.get("text", ""))
            messages.success(request, _("Danke! Der Text erscheint nach der Prüfung.") if saved.text_status == Review.SUBMITTED else _("Danke für Ihre Bewertung!"))
    return redirect("template_detail", key=key)


@require_POST
@login_required
def report(request, key):
    template = _visible(request, key)
    reason = " ".join(request.POST.get("reason", "").split())[:1000]
    review = Review.objects.filter(template=template, pk=request.POST.get("review") or 0).first()
    if not reason:
        messages.error(request, _("Bitte kurz sagen, was nicht stimmt."))
    elif limits.allowed("report", request.user.pk):
        Report.objects.create(template=template, review=review, reason=reason, reporter=request.user)
        services._notify_reviewers(_("Neue Meldung"), reason)
        messages.success(request, _("Danke, wir sehen es uns an."))
    else:
        messages.error(request, _("Heute wurde schon viel gemeldet. Bitte morgen weiter."))
    return redirect("template_detail", key=key)


def legal(request, page):
    return render(request, f"recht/{page}.html")


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")
