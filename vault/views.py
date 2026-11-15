import json
import re
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from core import audit
from core.permissions import require
from people.models import Guardian, Person

from . import generator
from .models import Credential, Platform
from .services import generate_login, rate_limited


@require("vault.view")
def overview(request):
    platforms = Platform.objects.filter(active=True).annotate(total=Count("credentials")).order_by("order", "name")
    pupils = Person.objects.filter(kind="pupil", archived=False).count()
    staff = Person.objects.filter(kind="staff", archived=False).count()
    rows = []
    for p in platforms:
        expected = sum([pupils if "pupil" in p.applies_to else 0, staff if "staff" in p.applies_to else 0])
        have = p.credentials.filter(Q(person__archived=False) | Q(guardian__isnull=False)).count()
        rows.append({"platform": p, "have": have, "expected": expected, "percent": min(100, round(100 * have / expected)) if expected else None})
    return render(request, "vault/overview.html", {"rows": rows, "profiles": generator.PROFILES})


@require("vault.view")
def platform_list(request, key):
    platform = get_object_or_404(Platform, key=key)
    query = request.GET.get("q", "").strip()
    credentials = platform.credentials.select_related("person", "person__current_class", "guardian")
    if query:
        credentials = credentials.filter(
            Q(person__last_name__icontains=query) | Q(person__first_name__icontains=query) | Q(guardian__last_name__icontains=query) | Q(values__username__icontains=query)
        )
    credentials = credentials.annotate(owner_name=Coalesce("person__last_name", "guardian__last_name")).order_by("owner_name", "pk")
    page = Paginator(credentials, 50).get_page(request.GET.get("seite"))
    rows = [{"credential": c, "fields": c.read_all()} for c in page]
    return render(request, "vault/platform_list.html", {"platform": platform, "rows": rows, "page": page, "keep": urlencode({"q": query}), "q": query, "active_nav": "credentials"})


def _owner(kind, pk):
    model = {"person": Person, "guardian": Guardian}.get(kind)
    if model is None:
        raise Http404
    return get_object_or_404(model, pk=pk)


@require("vault.view")
@require_POST
def login_card(request, kind, owner, platform):
    """Save, generate or clear one login, then redraw its card."""
    if not request.user.can("vault.edit"):
        raise PermissionDenied
    person = _owner(kind, owner)
    platform_obj = get_object_or_404(Platform, pk=platform)
    action = request.POST.get("action")
    credential = None
    if action == "generate":
        credential = generate_login(person, platform_obj, rotate=True)
        audit.log("credential", f"Zugang erzeugt: {platform_obj.name} für {person.display_name}", request=request, target=person.pk)
    else:
        credential = Credential.get_for(platform_obj, person=person if kind == "person" else None, guardian=person if kind == "guardian" else None)
        for field in platform_obj.fields:
            value = request.POST.get(f"f_{field['key']}", "").strip()
            if field.get("secret"):
                if value:
                    credential.set_secret(field["key"], value)
            else:
                credential.values[field["key"]] = value
        credential.save()
        audit.log("credential", f"Zugang geändert: {platform_obj.name} für {person.display_name}", request=request, target=person.pk)
    return render(request, "vault/_login_card.html", {
        "platform": platform_obj, "credential": credential, "fields": credential.read_all(), "owner_kind": kind, "owner_id": person.pk,
    })


@require("vault.reveal")
@require_POST
def reveal(request, pk, field):
    credential = get_object_or_404(Credential.objects.select_related("platform", "person", "guardian"), pk=pk)
    if field not in credential.platform.secret_keys:
        raise Http404
    if rate_limited(request):
        return HttpResponse(_("Zu viele Abrufe. Bitte kurz warten."), status=429)
    audit.log("reveal", f"Passwort angezeigt: {credential.platform.name} von {credential.owner.display_name}", request=request, target=credential.pk)
    return render(request, "vault/_secret_value.html", {"value": credential.get_secret(field), "dom_id": f"s-{credential.pk}-{field}"})


def _parse_fields(text):
    """One field per line: 'Benutzername' or 'Passwort*' where * marks a secret."""
    fields = []
    for line in text.splitlines():
        label = line.strip()
        if not label:
            continue
        secret = label.endswith("*")
        label = label.rstrip("*").strip()
        key = "password" if secret and not any(f["key"] == "password" for f in fields) else slugify(label).replace("-", "_") or f"feld{len(fields) + 1}"
        if label.lower() in ("benutzername", "username", "nutzername"):
            key = "username"
        fields.append({"key": key, "label": label, "secret": secret})
    return fields


def _fields_text(platform):
    return "\n".join(f["label"] + ("*" if f.get("secret") else "") for f in platform.fields)


@require("admin.platforms")
@require_http_methods(["GET", "POST"])
def platforms(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if name:
            Platform.objects.create(
                key=slugify(name)[:40] or name[:40], name=name, order=Platform.objects.count() * 10 + 10,
                fields=_parse_fields(request.POST.get("fields") or "Benutzername\nPasswort*"),
                applies_to=request.POST.getlist("applies_to") or ["pupil"], login_url=request.POST.get("login_url", "").strip(),
            )
            audit.log("platform", f"Plattform angelegt: {name}", request=request)
            messages.success(request, _("%(name)s wurde angelegt.") % {"name": name})
        return redirect("platforms")
    return render(request, "vault/platforms.html", {"platforms": Platform.objects.annotate(total=Count("credentials")).order_by("order", "name"), "active_nav": "settings_home"})


@require("admin.platforms")
@require_http_methods(["GET", "POST"])
def platform_edit(request, pk):
    platform = get_object_or_404(Platform, pk=pk)
    if request.method == "POST":
        if request.POST.get("order_action"):
            return redirect("platforms")
        platform.name = request.POST.get("name", platform.name).strip() or platform.name
        platform.login_url = request.POST.get("login_url", "").strip()
        platform.order = int(request.POST.get("order") or platform.order)
        platform.applies_to = request.POST.getlist("applies_to") or platform.applies_to
        platform.on_sheet = bool(request.POST.get("on_sheet"))
        platform.active = bool(request.POST.get("active"))
        if not platform.credentials.exists():
            platform.fields = _parse_fields(request.POST.get("fields", ""))
        platform.save()
        audit.log("platform", f"Plattform geändert: {platform.name}", request=request)
        messages.success(request, _("Gespeichert."))
        return redirect("platforms")
    return render(request, "vault/platform_edit.html", {"platform": platform, "fields_text": _fields_text(platform), "locked": platform.credentials.exists(), "active_nav": "settings_home"})
