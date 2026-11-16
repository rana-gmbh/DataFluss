import json
import uuid
from datetime import date
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core import audit, updates, modules
from core.images import TOO_LARGE, too_large
from core.models import SavedView
from core.permissions import require
from devices.models import Assignment
from masks import services as ms
from masks.models import FieldDefinition
from sync import journal
from vault import crypto
from vault.models import Credential, Platform
from vault.services import generate_login, rate_limited

from .blocks import BLOCK_VIEWS
from .models import Attachment, Card, ClassGroup, Comment, Enrollment, Guardian, Guardianship, Person, SchoolYear
from .services import RolloverRefused, parse_card_input, rollover_commit, rollover_preview


def _grid_columns(entity, user):
    columns = []
    can_edit = user.can("person.edit")
    classes = [{"value": str(c.pk), "label": c.name} for c in ms.class_choices()]
    for fd in ms.fields_for(entity):
        if fd.type == "secret":
            continue
        column = {
            "key": fd.key, "title": fd.display_label, "type": fd.type, "visible": fd.in_grid,
            "editable": can_edit and not fd.read_only and fd.type != "multichoice",
            "options": fd.option_values,
        }
        if fd.type in ("choice", "multichoice"):
            column["optionLabels"] = {value: str(label) for value, label in fd.option_choices}
        if fd.type == "class":
            column["options"] = classes
        columns.append(column)
    columns.insert(0, {"key": "photo_url", "title": _("Bild"), "type": "photo", "visible": True, "editable": False})
    position = next((i for i, c in enumerate(columns) if c["key"] == "email"), len(columns))
    columns.insert(position + 1, {"key": "device", "title": _("Gerät"), "type": "text", "visible": True, "editable": False})
    columns.insert(position + 2, {"key": "serial", "title": _("Seriennummer"), "type": "text", "visible": entity == "pupil", "editable": False})
    return columns


def _grid_page(request, entity, title, nav):
    views = SavedView.objects.filter(entity=entity).filter(Q(owner=None) | Q(owner=request.user))
    from prints.models import PrintTemplate

    config = {
        "entity": entity,
        "columns": _grid_columns(entity, request.user),
        "dataUrl": reverse("api_persons"),
        "canEdit": request.user.can("person.edit"),
        "preset": request.GET.get("preset", ""),
        "views": [{"id": str(v.pk), "name": v.name, "config": v.config, "shared": v.owner_id is None} for v in views],
        "classes": [{"value": str(c.pk), "label": c.name} for c in ms.class_choices()],
        "templates": [{"id": str(t.pk), "name": t.name} for t in PrintTemplate.objects.filter(source=entity, archived=False)],
        "platforms": [{"id": str(p.pk), "name": p.name} for p in Platform.active_for(entity)]
        if request.user.can("vault.edit") else [],
        "canExport": request.user.can("data.export"),
        "canArchive": request.user.can("person.archive"),
        "binUrl": reverse("person_bin"),
    }
    return render(request, "people/grid.html", {"config": config, "title": title, "entity": entity, "active_nav": nav})


@require("person.view")
def pupils(request):
    return _grid_page(request, "pupil", _("Schüler"), "pupils")


@require("person.view")
def staff(request):
    return _grid_page(request, "staff", _("Personal"), "staff")


def person_rows(entity, scope="active", ids=None):
    qs = Person.objects.filter(kind=entity).select_related("current_class").prefetch_related(
        Prefetch("assignments", Assignment.objects.filter(returned_at__isnull=True).select_related("asset"), to_attr="open_assignments")
    )
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    elif scope == "active":
        qs = qs.filter(archived=False)
    elif scope == "archived":
        qs = qs.filter(archived=True)
    fields = [f for f in ms.fields_for(entity) if f.type != "secret"]
    rows = []
    for person in qs:
        row = {"id": str(person.pk), "name": person.display_name, "photo_url": person.thumb_url, "initials": person.initials, "archived": person.archived}
        for fd in fields:
            value = ms.get_value(person, fd)
            if fd.type == "class":
                row[fd.key] = value.name if value else ""
                row["klasse_id"] = str(value.pk) if value else ""
            elif isinstance(value, date):
                row[fd.key] = value.isoformat()
            elif isinstance(value, list):
                row[fd.key] = ", ".join(value)
            else:
                row[fd.key] = value if value is not None else ""
        assets = [a.asset for a in person.open_assignments]
        row["device"] = ", ".join(a.number for a in assets)
        row["serial"] = ", ".join(a.serial for a in assets if a.serial)
        rows.append(row)
    return rows


@require("person.view")
@require_GET
def api_persons(request):
    entity = request.GET.get("entity", "pupil")
    if entity not in ("pupil", "staff"):
        return HttpResponseBadRequest()
    record = request.GET.get("id")
    if record:
        try:
            return JsonResponse(person_rows(entity, ids=[uuid.UUID(record)]), safe=False)
        except ValueError:
            return HttpResponseBadRequest()
    return JsonResponse(person_rows(entity, request.GET.get("scope", "active")), safe=False)


def _save_field(request, person, key, raw):
    if not request.user.can("person.edit"):
        raise PermissionDenied
    fd = FieldDefinition.objects.filter(entity=person.kind, key=key, archived=False).first()
    if fd is None:
        raise Http404
    if fd.type == "secret" and not request.user.can("vault.edit"):
        raise PermissionDenied
    return fd, *ms.set_value(person, fd, raw)


@require("person.edit")
@require_POST
def api_person_field(request, pk, key):
    person = get_object_or_404(Person, pk=pk)
    payload = json.loads(request.body or b"{}")
    try:
        _save_field(request, person, key, payload.get("value"))
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": " ".join(exc.messages)}, status=422)
    return JsonResponse({"ok": True})


@require("person.view")
@require_http_methods(["POST"])
def api_views(request):
    payload = json.loads(request.body or b"{}")
    name = (payload.get("name") or "").strip()[:80]
    entity = payload.get("entity")
    if not name or entity not in ("pupil", "staff", "asset"):
        return HttpResponseBadRequest()
    view, _created = SavedView.objects.update_or_create(
        entity=entity, name=name, owner=request.user, defaults={"config": payload.get("config") or {}}
    )
    return JsonResponse({"id": str(view.pk), "name": view.name, "config": view.config, "shared": False})


@require("person.view")
@require_POST
def api_view_delete(request, pk):
    get_object_or_404(SavedView, pk=pk, owner=request.user).delete()
    return JsonResponse({"ok": True})


def _fill_first_tab(request, person, tabs):
    """What is visible at once comes with the page. A second round trip costs time on a slow connection."""
    for section in tabs[0]["sections"] if tabs else []:
        for item in section["items"]:
            view = BLOCK_VIEWS.get(item.get("block"))
            if item["type"] == "block" and view:
                try:
                    item["html"] = render_to_string(f"people/blocks/{item['block']}.html", {"person": person, **view(request, person)}, request)
                except PermissionDenied:
                    pass


def _detail_context(request, person):
    from prints.models import PrintTemplate

    _mask, version = ms.resolve_mask(person, request.user)
    tree = version.tree if version else {"tabs": []}
    tabs = ms.build_view(tree, person, request.user)
    _fill_first_tab(request, person, tabs)
    return {
        "person": person,
        "tabs": tabs,
        "form_url": reverse("person_form", args=[person.pk]),
        "form_kind": person.kind,
        "print_templates": PrintTemplate.objects.filter(source=person.kind, archived=False),
        "active_nav": "pupils" if person.kind == "pupil" else "staff",
    }


@require("person.view")
def person_detail(request, pk):
    person = get_object_or_404(Person.objects.select_related("current_class"), pk=pk)
    return render(request, "people/detail.html", _detail_context(request, person))


@require("person.view")
def person_form(request, pk):
    person = get_object_or_404(Person.objects.select_related("current_class"), pk=pk)
    return render(request, "masks/_form.html", _detail_context(request, person))


@require("person.view")
def person_panel(request, pk):
    """The record as a side panel beside the list."""
    person = get_object_or_404(Person.objects.select_related("current_class"), pk=pk)
    return render(request, "people/_panel.html", _detail_context(request, person))


@require("person.edit")
@require_POST
def person_field(request, pk, key):
    person = get_object_or_404(Person.objects.select_related("current_class"), pk=pk)
    values = request.POST.getlist("value")
    fd_probe = FieldDefinition.objects.filter(entity=person.kind, key=key).first()
    raw = [v for v in values if v] if fd_probe and fd_probe.type == "multichoice" else (values[-1] if values else "")
    response = HttpResponse(status=204)
    try:
        fd, saved, previous = _save_field(request, person, key, raw)
    except ValidationError as exc:
        response["HX-Trigger"] = json.dumps({"fieldError": {"key": key, "message": " ".join(exc.messages)}})
        return response
    _mask, version = ms.resolve_mask(person, request.user)
    reload_form = bool(version and key in ms.condition_fields(version.tree)) or key in ("first_name", "last_name")
    undoable = fd.type not in ("secret", "multichoice") and str(previous) != str(saved)
    response["HX-Trigger"] = json.dumps(
        {"fieldSaved": {
            "key": key, "label": str(fd.display_label), "undoable": undoable, "old": str(previous),
            "url": reverse("person_field", args=[person.pk, key]), "reload": reload_form,
        }}
    )
    return response


@require("vault.reveal")
@require_POST
def person_secret_reveal(request, pk, key):
    person = get_object_or_404(Person, pk=pk)
    fd = get_object_or_404(FieldDefinition, entity=person.kind, key=key, type="secret")
    if rate_limited(request):
        return HttpResponse(_("Zu viele Abrufe. Bitte kurz warten."), status=429)
    audit.log("reveal", f"Geheimes Feld angezeigt: {fd.label} von {person.display_name}", request=request, target=person.pk)
    return render(request, "vault/_secret_value.html", {"value": ms.reveal_custom(person, fd), "dom_id": f"secret-value-{key}"})


@require("person.view")
def person_block(request, pk, name):
    person = get_object_or_404(Person.objects.select_related("current_class"), pk=pk)
    view = BLOCK_VIEWS.get(name)
    if view is None or not modules.block_active(name):
        raise Http404
    return render(request, f"people/blocks/{name}.html", {"person": person, **view(request, person)})


def _render_block(request, person, name):
    return render(request, f"people/blocks/{name}.html", {"person": person, **BLOCK_VIEWS[name](request, person)})


@require("person.edit")
@require_POST
def person_photo(request, pk):
    person = get_object_or_404(Person, pk=pk)
    upload = request.FILES.get("photo")
    if request.POST.get("remove"):
        person.photo = ""
        person.save()
    elif upload and not person.store_photo(upload):
        return HttpResponse(_("Diese Datei ist kein gültiges Bild."), status=422)
    return _render_block(request, person, "photo")


@require("person.edit")
@require_POST
def person_card(request, pk):
    person = get_object_or_404(Person, pk=pk)
    action = request.POST.get("action", "assign")
    if action == "revoke":
        journal.update(person.cards.filter(active=True), active=False)
        audit.log("card", f"Karte gesperrt: {person.display_name}", request=request, target=person.pk)
    else:
        raw = request.POST.get("raw", "")
        code = parse_card_input(raw, settings.CARD_INPUT_FORMAT)
        if not code:
            response = HttpResponse(status=204)
            response["HX-Trigger"] = json.dumps({"toast": _("Diese Karte konnte nicht gelesen werden.")})
            return response
        other = Card.objects.filter(code=code, active=True).exclude(person=person).select_related("person").first()
        if other:
            response = HttpResponse(status=204)
            response["HX-Trigger"] = json.dumps({"toast": _("Diese Karte gehört bereits zu %(name)s.") % {"name": other.person.display_name}})
            return response
        with transaction.atomic():
            journal.update(person.cards.filter(active=True), active=False)
            Card.objects.create(person=person, code=code, raw_input=raw[:60])
        audit.log("card", f"Karte zugewiesen: {person.display_name}", request=request, target=person.pk)
    person = Person.objects.get(pk=pk)
    return _render_block(request, person, "card")


@require("person.view")
@require_POST
def person_comment(request, pk):
    person = get_object_or_404(Person, pk=pk)
    text = request.POST.get("text", "").strip()
    if text:
        Comment.objects.create(person=person, author=request.user, text=text[:4000])
    return _render_block(request, person, "comments")


@require("person.edit")
@require_POST
def person_attachment(request, pk):
    person = get_object_or_404(Person, pk=pk)
    upload = request.FILES.get("file")
    problem = ""
    if request.POST.get("delete"):
        item = person.attachments.filter(pk=request.POST["delete"]).first()
        if item:
            audit.log("attachment", f"Datei {item.name} gelöscht bei {person.display_name}", request=request, target=person.pk)
            stored = item.file
            item.delete()
            transaction.on_commit(lambda: stored.delete(save=False))
    elif upload and too_large(upload):
        problem = str(TOO_LARGE)
    elif upload:
        Attachment.objects.create(person=person, file=upload, name=upload.name[:200], uploaded_by=request.user)
    response = _render_block(request, person, "attachments")
    if problem:
        response["HX-Trigger"] = json.dumps({"toast": problem})
    return response


@require("person.view")
def attachment_download(request, pk, attachment):
    item = get_object_or_404(Attachment, pk=attachment, person_id=pk)
    audit.log("download", f"Datei {item.name} geöffnet", request=request, target=pk)
    return FileResponse(item.file.open("rb"), as_attachment=True, filename=item.name)


@require("person.edit")
@require_POST
def person_guardian(request, pk):
    person = get_object_or_404(Person, pk=pk)
    action = request.POST.get("action")
    if action == "add":
        first, last = request.POST.get("first_name", "").strip(), request.POST.get("last_name", "").strip()
        if first and last:
            email = request.POST.get("email", "").strip().lower()
            guardian = Guardian.objects.filter(email=email).first() if email else None
            guardian = guardian or Guardian.objects.create(first_name=first, last_name=last, email=email, phone=request.POST.get("phone", "").strip())
            Guardianship.objects.get_or_create(person=person, guardian=guardian, defaults={"role": request.POST.get("role", "Elternteil")[:30]})
    elif action == "link":
        guardian = Guardian.objects.filter(pk=request.POST.get("guardian")).first()
        if guardian:
            Guardianship.objects.get_or_create(person=person, guardian=guardian)
    elif action == "unlink":
        journal.delete(person.guardianships.filter(pk=request.POST.get("link")))
    return _render_block(request, person, "guardians")


@require("person.edit")
@require_http_methods(["GET", "POST"])
def person_new(request, entity):
    if entity not in ("pupil", "staff"):
        raise Http404
    error, data = "", {}
    fields = ms.field_map(entity)
    if request.method == "POST":
        data = request.POST
        first, last = data.get("first_name", "").strip(), data.get("last_name", "").strip()
        if not first or not last:
            error = _("Bitte Vor- und Nachname eingeben.")
        else:
            try:
                with transaction.atomic():
                    person = Person.objects.create(
                        kind=entity, first_name=first, last_name=last,
                        birth_date=ms.parse_date(data.get("birth_date")), entry_date=ms.parse_date(data.get("entry_date")) or timezone.localdate(),
                        email=data.get("email", "").strip().lower(), school_type=data.get("school_type", ""),
                        category=data.get("category", ""), gender=data.get("gender", ""),
                    )
                    group = ClassGroup.objects.filter(pk=data.get("klasse")).first() if data.get("klasse") else None
                    if group:
                        person.move_to_class(group)
                    created = []
                    if request.user.can("vault.edit"):
                        for platform in Platform.objects.filter(pk__in=data.getlist("platforms")):
                            generate_login(person, platform)
                            created.append(platform.name)
                audit.log("person_created", f"Neu angelegt: {person.display_name}", request=request, target=person.pk)
                if created:
                    messages.success(request, _("%(name)s wurde angelegt. Zugänge erzeugt: %(platforms)s.") % {"name": person.display_name, "platforms": ", ".join(created)})
                else:
                    messages.success(request, _("%(name)s wurde angelegt.") % {"name": person.display_name})
                return redirect("person_detail", pk=person.pk)
            except ValidationError as exc:
                error = " ".join(exc.messages)
    return render(request, "people/new.html", {
        "entity": entity, "error": error, "data": data, "fields": fields, "classes": ms.class_choices(), "today": timezone.localdate().isoformat(),
        "platforms": Platform.active_for(entity) if request.user.can("vault.edit") else [],
        "active_nav": "pupils" if entity == "pupil" else "staff",
    })


@require("person.view")
def person_duplicates(request):
    first, last = request.GET.get("first_name", "").strip(), request.GET.get("last_name", "").strip()
    found = []
    if len(last) >= 2:
        found = Person.objects.filter(last_name__iexact=last, first_name__istartswith=first[:2])[:5] if first else Person.objects.filter(last_name__iexact=last)[:5]
    return render(request, "people/_duplicates.html", {"found": found})


@require("person.edit")
@require_POST
def bulk_action(request):
    payload = json.loads(request.body or b"{}")
    ids, action = payload.get("ids") or [], payload.get("action")
    people = Person.objects.filter(pk__in=ids)
    count = 0
    if action == "class":
        group = ClassGroup.objects.filter(pk=payload.get("value")).first()
        for person in people:
            person.move_to_class(group)
            count += 1
    elif action in ("archive", "restore"):
        if not request.user.can("person.archive"):
            raise PermissionDenied
        for person in people:
            person.archived = action == "archive"
            if person.archived and person.exit_date is None:
                person.exit_date = timezone.localdate()
            person.save()
            count += 1
        audit.log("bulk", f"{count} Personen {'archiviert' if action == 'archive' else 'wiederhergestellt'}", request=request)
    elif action in ("delete", "bring_back"):
        if not request.user.can("person.archive"):
            raise PermissionDenied
        if action == "bring_back":
            people = Person.everything.filter(pk__in=ids).exclude(deleted_at=None)
        refused = []
        for person in people:
            if action == "delete" and (reason := person.kept_by_section()):
                refused.append(reason)
                continue
            person.recycle() if action == "delete" else person.bring_back()
            count += 1
        verb = "in den Papierkorb gelegt" if action == "delete" else "aus dem Papierkorb geholt"
        if count:
            audit.log("bulk", f"{count} Personen {verb}", request=request)
        return JsonResponse({"ok": True, "count": count, "refused": refused, "keep_days": Person.KEEP_DAYS})
    elif action == "logins":
        if not request.user.can("vault.edit"):
            raise PermissionDenied
        platform = get_object_or_404(Platform, pk=payload.get("value"))
        for person in people:
            existing = person.credentials.filter(platform=platform).first()
            if existing is None or not existing.secrets:
                generate_login(person, platform)
                count += 1
        audit.log("bulk", f"{count} Zugänge für {platform.name} erzeugt", request=request)
    else:
        return HttpResponseBadRequest()
    return JsonResponse({"ok": True, "count": count})


@require("person.edit")
@require_POST
def person_duplicate(request, pk):
    person = get_object_or_404(Person, pk=pk)
    copy = person.duplicate()
    audit.log("person", f"{person.display_name} dupliziert", request=request, target=copy.pk)
    messages.success(request, _("Kopie von %(name)s angelegt. Bitte den Namen anpassen.") % {"name": person.display_name})
    return redirect("person_detail", pk=copy.pk)


@require("person.archive")
@require_http_methods(["GET", "POST"])
def person_bin(request):
    if request.method == "POST":
        person = get_object_or_404(Person.everything.exclude(deleted_at=None), pk=request.POST.get("person"))
        person.bring_back()
        audit.log("person", f"{person.display_name} aus dem Papierkorb geholt", request=request, target=person.pk)
        messages.success(request, _("%(name)s ist wieder da.") % {"name": person.display_name})
        return redirect("person_bin")
    people = Person.in_the_bin().select_related("current_class")
    return render(request, "people/bin.html", {"people": people, "keep_days": Person.KEEP_DAYS, "active_nav": "pupils"})


@require("person.view")
@require_http_methods(["GET", "POST"])
def card_lookup(request):
    person, searched = None, False
    if request.method == "POST":
        searched = True
        code = parse_card_input(request.POST.get("raw", ""), settings.CARD_INPUT_FORMAT)
        card = Card.objects.filter(code=code, active=True).select_related("person").first() if code else None
        audit.log("card_lookup", "Karte gesucht" + (f": {card.person.display_name}" if card else " (kein Treffer)"), request=request)
        if card:
            return redirect("person_detail", pk=card.person_id)
    return render(request, "people/card_lookup.html", {"searched": searched})


@require("person.view")
def guardians(request):
    query = request.GET.get("q", "").strip()
    qs = Guardian.objects.annotate(children=Count("guardianships")).prefetch_related("guardianships__person")
    if query:
        qs = qs.filter(Q(last_name__icontains=query) | Q(first_name__icontains=query) | Q(email__icontains=query))
    page = Paginator(qs.order_by("last_name", "first_name"), 50).get_page(request.GET.get("seite"))
    return render(request, "people/guardians.html", {"guardians": page, "page": page, "q": query, "keep": urlencode({"q": query})})


def _guardian_context(request, guardian):
    from prints.models import PrintTemplate

    logins = []
    if request.user.can("vault.view"):
        existing = {c.platform_id: c for c in guardian.credentials.select_related("platform")}
        for platform in Platform.active_for(Platform.GUARDIAN):
            credential = existing.get(platform.pk)
            logins.append({"platform": platform, "credential": credential, "fields": credential.read_all() if credential else []})
    return {
        "guardian": guardian, "children": guardian.guardianships.select_related("person", "person__current_class"),
        "logins": logins, "print_templates": PrintTemplate.objects.filter(source="guardian", archived=False), "active_nav": "guardians",
    }


@require("person.view")
@require_http_methods(["GET", "POST"])
def guardian_detail(request, pk):
    guardian = get_object_or_404(Guardian, pk=pk)
    if request.method == "POST":
        if not request.user.can("person.edit"):
            raise PermissionDenied
        for name in ("first_name", "last_name", "email", "phone", "notes"):
            setattr(guardian, name, request.POST.get(name, "").strip())
        guardian.email = guardian.email.lower()
        guardian.save()
        messages.success(request, _("Gespeichert."))
        return redirect("guardian_detail", pk=pk)
    return render(request, "people/guardian_detail.html", _guardian_context(request, guardian))


@require("person.view")
def guardian_panel(request, pk):
    """The guardian as a side panel beside the list."""
    guardian = get_object_or_404(Guardian, pk=pk)
    return render(request, "people/_guardian_panel.html", _guardian_context(request, guardian))


@require("person.view")
@require_http_methods(["GET", "POST"])
def classes(request):
    year = SchoolYear.current()
    if request.method == "POST":
        if not request.user.can("class.manage"):
            raise PermissionDenied
        name = request.POST.get("name", "").strip()
        if name:
            digits = "".join(ch for ch in name if ch.isdigit())
            kind = request.POST.get("kind", ClassGroup.KLASSE)
            ClassGroup.objects.get_or_create(name=name, school_year=year, defaults={"kind": kind, "level": int(digits) if digits and kind == ClassGroup.KLASSE else None})
        return redirect("classes")
    groups = ClassGroup.objects.filter(school_year=year).annotate(
        count=Count("members", filter=Q(members__archived=False))
    ).order_by("-kind", "level", "name")
    return render(request, "people/classes.html", {"groups": groups, "year": year, "kinds": ClassGroup.KINDS[:2]})


@require("person.view")
def class_detail(request, pk):
    group = get_object_or_404(ClassGroup, pk=pk)
    from prints.models import PrintTemplate

    members = group.members.filter(archived=False).prefetch_related(
        Prefetch("assignments", Assignment.objects.filter(returned_at__isnull=True).select_related("asset"), to_attr="open_assignments")
    )
    return render(request, "people/class_detail.html", {
        "group": group, "members": members,
        "print_templates": PrintTemplate.objects.filter(source__in=["pupil", "class"], archived=False),
        "active_nav": "classes",
    })


@require("class.manage")
@require_http_methods(["GET", "POST"])
def rollover(request):
    today = timezone.localdate()
    start_year = SchoolYear.current().end.year if SchoolYear.current() else today.year
    name = request.POST.get("name") or f"{start_year}/{str(start_year + 1)[-2:]}"
    if request.method == "POST" and request.POST.get("confirm") == "1":
        try:
            backup = updates.backup_database("vor-schuljahreswechsel")
            moved, left = rollover_commit(name, date(start_year, 8, 1), date(start_year + 1, 7, 31))
        except (updates.UpdateError, RolloverRefused) as exc:
            messages.error(request, str(exc))
            return redirect("rollover")
        audit.log("rollover", f"Schuljahreswechsel {name}: {moved} versetzt, {left} ausgetreten. Sicherung: {backup}", request=request)
        messages.success(request, _("Schuljahr %(name)s gestartet: %(moved)s Schüler versetzt, %(left)s ausgetreten.") % {"name": name, "moved": moved, "left": left})
        return redirect("classes")
    rows = rollover_preview(name)
    return render(request, "people/rollover.html", {
        "rows": rows, "name": name, "total": sum(r["count"] for r in rows), "leaving": sum(r["count"] for r in rows if r["target"] == "Austritt"),
        "active_nav": "classes",
    })
