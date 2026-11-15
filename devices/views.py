import json
import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q, Value
from django.db.models.functions import Concat
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.html import format_html_join
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from core import audit
from core.images import too_large
from core.permissions import require
from masks.services import parse_date
from people.blocks import BLOCK_VIEWS
from people.models import Attachment, Person
from prints.models import PrintTemplate
from vault import crypto
from vault.services import rate_limited

from .models import Asset, AssetCategory, Assignment, CampaignItem, Place, ReturnCampaign, SupportCase


def _asset_rows(ids=None):
    qs = Asset.objects.select_related("category").prefetch_related(
        Prefetch("assignments", Assignment.objects.select_related("person", "place"))
    )
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    rows = []
    for asset in qs:
        current = asset.open_assignment
        holder = current.holder if current else None
        rows.append({
            "id": str(asset.pk), "number": asset.number, "category": asset.category.name if asset.category else "",
            "serial": asset.serial, "uuid": asset.hardware_uuid, "status": asset.status, "status_label": asset.status_label,
            "holder": str(holder) if holder else "", "holder_archived": bool(current and current.person and current.person.archived),
            "issued": current.issued_at.isoformat() if current else "", "damaged": asset.damaged,
        })
    return rows


@require("device.view")
def assets(request):
    return render(request, "devices/assets.html", {
        "preset": request.GET.get("preset", ""),
        "templates": PrintTemplate.objects.filter(source="asset", archived=False),
    })


@require("device.view")
def api_assets(request):
    record = request.GET.get("id")
    if record:
        try:
            return JsonResponse(_asset_rows(ids=[uuid.UUID(record)]), safe=False)
        except ValueError:
            return HttpResponseBadRequest()
    return JsonResponse(_asset_rows(), safe=False)


@require("device.edit")
@require_http_methods(["GET", "POST"])
def asset_new(request):
    error = ""
    categories = AssetCategory.objects.all()
    if request.method == "POST":
        category = AssetCategory.objects.filter(pk=request.POST.get("category")).first()
        number = request.POST.get("number", "").strip()
        if not number and category:
            number = category.allocate_number()
        if not number:
            error = _("Bitte einen Gerätetyp wählen oder eine Nummer eingeben.")
        elif Asset.objects.filter(number=number).exists():
            error = _("%(number)s gibt es bereits.") % {"number": number}
        else:
            asset = Asset(number=number, category=category, serial=request.POST.get("serial", "").strip(), hardware_uuid=request.POST.get("hardware_uuid", "").strip())
            asset.save()
            key = request.POST.get("product_key", "").strip()
            if key:
                asset.product_key_enc = crypto.encrypt(key, f"asset:{asset.pk}:key")
                asset.save()
            messages.success(request, _("%(number)s wurde angelegt.") % {"number": number})
            return redirect("asset_detail", pk=asset.pk)
    return render(request, "devices/asset_new.html", {"categories": categories, "error": error, "active_nav": "assets"})


@require("device.edit")
def asset_suggest(request):
    free = Asset.objects.filter(sold=False, retired=False).exclude(assignments__returned_at__isnull=True, assignments__isnull=False)[:200]
    return HttpResponse(format_html_join("", '<option value="{}">{}</option>', ((a.number, a.serial) for a in free)))


def _asset_context(asset):
    return {
        "asset": asset, "assignments": asset.assignments.select_related("person", "place"),
        "cases": asset.support_cases.select_related("person"), "places": Place.objects.all(),
        "print_templates": PrintTemplate.objects.filter(source="asset", archived=False), "active_nav": "assets",
    }


@require("device.view")
def asset_detail(request, pk):
    asset = get_object_or_404(Asset.objects.select_related("category"), pk=pk)
    return render(request, "devices/asset_detail.html", _asset_context(asset))


@require("device.view")
def asset_panel(request, pk):
    """The device as a side panel beside the list."""
    asset = get_object_or_404(Asset.objects.select_related("category"), pk=pk)
    return render(request, "devices/_asset_panel.html", _asset_context(asset))


def _one_person(name):
    """Handing a device to the wrong Müller is worse than asking again."""
    found = Person.objects.filter(archived=False).annotate(
        first_last=Concat("first_name", Value(" "), "last_name"), last_first=Concat("last_name", Value(" "), "first_name"),
    ).filter(Q(email__iexact=name) | Q(last_name__iexact=name) | Q(first_last__iexact=name) | Q(last_first__iexact=name))
    people = list(found.select_related("current_class")[:6])
    if not people:
        raise ValueError(_("Niemand gefunden: %(name)s.") % {"name": name})
    if len(people) > 1:
        without_class = _("ohne Klasse")
        options = ", ".join(f"{p.display_name} ({p.current_class or p.email or without_class})" for p in people[:5])
        raise ValueError(_("Mehrere Treffer für %(name)s: %(options)s. Bitte Vorname und Nachname oder die E-Mail eingeben.") % {"name": name, "options": options})
    return people[0]


@require("device.edit")
@require_POST
def asset_action(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    action = request.POST.get("action")
    try:
        if action == "issue":
            person = Person.objects.filter(pk=request.POST.get("person") or None).first() if request.POST.get("person") else None
            name = " ".join(request.POST.get("person_name", "").split())
            if not person and name:
                person = _one_person(name)
            place = Place.objects.filter(pk=request.POST.get("place")).first() if request.POST.get("place") else None
            asset.issue(person=person, place=place, pen=request.POST.get("pen", ""), keyboard=request.POST.get("keyboard", ""))
        elif action == "return":
            asset.take_back()
        elif action == "sell":
            asset.sell(parse_date(request.POST.get("sold_at")))
            audit.log("device", f"Gerät verkauft: {asset.number}", request=request, target=asset.pk)
        elif action == "toggle_damaged":
            asset.damaged = not asset.damaged
            asset.save()
        elif action == "retire":
            asset.take_back()
            asset.retired = True
            asset.save()
        elif action == "save":
            asset.serial = request.POST.get("serial", "").strip()
            asset.hardware_uuid = request.POST.get("hardware_uuid", "").strip()
            asset.notes = request.POST.get("notes", "").strip()
            asset.category = AssetCategory.objects.filter(pk=request.POST.get("category")).first() or asset.category
            key = request.POST.get("product_key", "").strip()
            if key:
                asset.product_key_enc = crypto.encrypt(key, f"asset:{asset.pk}:key")
            asset.save()
        messages.success(request, _("Gespeichert."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("asset_detail", pk=pk)


@require("vault.reveal")
@require_POST
def asset_key(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    if rate_limited(request):
        return HttpResponse(_("Zu viele Abrufe. Bitte kurz warten."), status=429)
    audit.log("reveal", f"Product Key angezeigt: {asset.number}", request=request, target=asset.pk)
    return render(request, "vault/_secret_value.html", {"value": crypto.decrypt(asset.product_key_enc, f"asset:{asset.pk}:key"), "dom_id": "product-key"})


def _device_block(request, person):
    return render(request, "people/blocks/devices.html", {"person": person, **BLOCK_VIEWS["devices"](request, person)})


@require("device.edit")
@require_POST
def person_issue(request, pk):
    person = get_object_or_404(Person, pk=pk)
    asset = Asset.objects.filter(number__iexact=request.POST.get("number", "").strip()).first()
    response = None
    if asset is None:
        message = _("Dieses Gerät gibt es nicht.")
    else:
        try:
            asset.issue(person=person)
            return _device_block(request, person)
        except ValueError as exc:
            message = str(exc)
    response = _device_block(request, person)
    response["HX-Trigger"] = json.dumps({"toast": message})
    return response


@require("device.edit")
@require_POST
def asset_return(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    asset.take_back()
    person = Person.objects.filter(pk=request.POST.get("person")).first() if request.POST.get("person") else None
    return _device_block(request, person) if person else redirect("asset_detail", pk=pk)


@require("device.view")
@require_http_methods(["GET", "POST"])
def categories(request):
    if request.method == "POST":
        if not request.user.can("device.edit"):
            raise PermissionDenied
        name = request.POST.get("name", "").strip()
        if name:
            AssetCategory.objects.get_or_create(name=name, defaults={
                "prefix": request.POST.get("prefix", "").strip(), "separator": request.POST.get("separator", "").strip(),
                "digits": int(request.POST.get("digits") or 4), "next_number": int(request.POST.get("next_number") or 1),
            })
        return redirect("asset_categories")
    return render(request, "devices/categories.html", {"categories": AssetCategory.objects.annotate(total=Count("asset")), "active_nav": "assets"})


@require("device.view")
@require_http_methods(["GET", "POST"])
def places(request):
    if request.method == "POST":
        if not request.user.can("device.edit"):
            raise PermissionDenied
        name = request.POST.get("name", "").strip()
        if name:
            Place.objects.get_or_create(name=name, defaults={"kind": request.POST.get("kind", Place.ROOM)})
        return redirect("places")
    rows = Place.objects.prefetch_related(Prefetch("assignments", Assignment.objects.filter(returned_at__isnull=True).select_related("asset"), to_attr="open"))
    return render(request, "devices/places.html", {"places": rows, "active_nav": "assets"})


@require("device.view")
@require_http_methods(["GET", "POST"])
def campaigns(request):
    if request.method == "POST":
        if not request.user.can("device.edit"):
            raise PermissionDenied
        name = request.POST.get("name", "").strip()
        date = parse_date(request.POST.get("date")) or timezone.localdate()
        if name:
            campaign = ReturnCampaign.objects.create(name=name, date=date)
            people = Person.objects.filter(kind="pupil")
            if request.POST.get("class"):
                people = people.filter(current_class_id=request.POST["class"])
            elif request.POST.get("level"):
                people = people.filter(grade_level=request.POST["level"])
            for assignment in Assignment.objects.filter(returned_at__isnull=True, person__in=people).select_related("asset"):
                CampaignItem.objects.get_or_create(campaign=campaign, asset=assignment.asset)
            return redirect("campaign_detail", pk=campaign.pk)
        return redirect("campaigns")
    return render(request, "devices/campaigns.html", {"campaigns": ReturnCampaign.objects.annotate(total=Count("items"), returned=Count("items", filter=Q(items__returned=True))), "active_nav": "assets"})


@require("device.view")
@require_http_methods(["GET", "POST"])
def campaign_detail(request, pk):
    campaign = get_object_or_404(ReturnCampaign, pk=pk)
    if request.method == "POST":
        if not request.user.can("device.edit"):
            raise PermissionDenied
        item = get_object_or_404(CampaignItem, pk=request.POST.get("item"), campaign=campaign)
        item.returned = not item.returned
        item.save()
        if item.returned:
            item.asset.take_back()
        return redirect("campaign_detail", pk=pk)
    items = campaign.items.select_related("asset").prefetch_related("asset__assignments__person")
    rows = []
    for item in items:
        history = list(item.asset.assignments.all())
        rows.append({"item": item, "holder": history[0].holder if history else None})
    return render(request, "devices/campaign_detail.html", {"campaign": campaign, "rows": rows, "active_nav": "assets"})


@require("device.view")
def cases(request):
    status = request.GET.get("status", "offen")
    qs = SupportCase.objects.select_related("person", "asset")
    if status == "offen":
        qs = qs.exclude(status="Erledigt")
    elif status == "erledigt":
        qs = qs.filter(status="Erledigt")
    page = Paginator(qs, 50).get_page(request.GET.get("seite"))
    return render(request, "devices/cases.html", {"cases": page, "page": page, "keep": urlencode({"status": status}), "status": status, "templates": PrintTemplate.objects.filter(source="case", archived=False)})


def _apply_case(case, data):
    case.description = data.get("description", "").strip()
    case.parts = [p for p in data.getlist("parts") if p in SupportCase.PARTS]
    case.date = parse_date(data.get("date")) or timezone.localdate()
    case.status = data.get("status") if data.get("status") in SupportCase.STATUSES else case.status
    case.replacement_number = data.get("replacement_number", "").strip()
    case.replacement_serial = data.get("replacement_serial", "").strip()


@require("device.edit")
@require_http_methods(["GET", "POST"])
def case_new(request):
    person = Person.objects.filter(pk=request.GET.get("person") or request.POST.get("person") or None).first() if (request.GET.get("person") or request.POST.get("person")) else None
    error = ""
    if request.method == "POST":
        case = SupportCase(person=person)
        number = request.POST.get("asset", "").strip()
        case.asset = Asset.objects.filter(number__iexact=number).first() if number else None
        if person and not case.asset:
            open_assignment = person.assignments.filter(returned_at__isnull=True).select_related("asset").first()
            case.asset = open_assignment.asset if open_assignment else None
        _apply_case(case, request.POST)
        if not case.description:
            error = _("Bitte den Schaden beschreiben.")
        else:
            case.save()
            if case.asset and not case.asset.damaged:
                case.asset.damaged = True
                case.asset.save()
            for upload in [u for u in request.FILES.getlist("photos")[:3] if not too_large(u)]:
                Attachment.objects.create(support_case=case, file=upload, name=upload.name[:200], uploaded_by=request.user)
            return redirect("case_detail", pk=case.pk)
    return render(request, "devices/case_form.html", {"person": person, "case": None, "parts": SupportCase.part_choices(), "statuses": SupportCase.status_choices(), "error": error, "active_nav": "cases", "today": timezone.localdate().isoformat()})


def _case_context(case):
    return {
        "case": case, "person": case.person, "parts": SupportCase.part_choices(), "statuses": SupportCase.status_choices(),
        "print_templates": PrintTemplate.objects.filter(source="case", archived=False), "active_nav": "cases",
    }


@require("device.view")
@require_http_methods(["GET", "POST"])
def case_detail(request, pk):
    case = get_object_or_404(SupportCase.objects.select_related("person", "asset"), pk=pk)
    if request.method == "POST":
        if not request.user.can("device.edit"):
            raise PermissionDenied
        was_open = case.status != "Erledigt"
        _apply_case(case, request.POST)
        case.save()
        if case.status == "Erledigt" and was_open and case.asset:
            case.asset.damaged = False
            case.asset.save()
        for upload in request.FILES.getlist("photos")[:3]:
            Attachment.objects.create(support_case=case, file=upload, name=upload.name[:200], uploaded_by=request.user)
        messages.success(request, _("Gespeichert."))
        return redirect("case_detail", pk=pk)
    return render(request, "devices/case_form.html", _case_context(case))


@require("device.view")
def case_panel(request, pk):
    """The case as a side panel beside the list."""
    case = get_object_or_404(SupportCase.objects.select_related("person", "asset"), pk=pk)
    return render(request, "devices/_case_panel.html", {**_case_context(case), "panel": True})
