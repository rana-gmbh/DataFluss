import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from core import audit, modules
from core.models import role_choices
from core.permissions import require
from people.models import Person
from sync import journal

from . import i18n
from . import services as ms
from .models import FieldDefinition, Mask, MaskVersion
from .registry import BLOCKS, default_tree

USER_TYPES = [t for t in FieldDefinition.TYPES if t[0] != "class"]


def _field_json(fd):
    return {"key": fd.key, "id": str(fd.pk), "label": fd.display_label, "type": fd.type, "typeLabel": str(fd.get_type_display()), "core": fd.is_core, "required": fd.required}


def create_field(entity, label, type_, options_text="", required=False, help_text="", in_grid=False):
    if entity not in dict(FieldDefinition.ENTITIES):
        raise ValidationError(_("Unbekannter Bereich."))
    label = (label or "").strip()
    if not label:
        raise ValidationError(_("Bitte eine Bezeichnung eingeben."))
    if type_ not in dict(USER_TYPES):
        raise ValidationError(_("Unbekannter Feldtyp."))
    options = [{"value": line.strip(), "color": ""} for line in (options_text or "").splitlines() if line.strip()]
    if type_ in ("choice", "multichoice") and not options:
        raise ValidationError(_("Bitte mindestens eine Option angeben."))
    base = slugify(label).replace("-", "_")[:50] or "feld"
    key, n = f"x_{base}", 1
    while FieldDefinition.objects.filter(entity=entity, key=key).exists():
        n += 1
        key = f"x_{base}_{n}"
    return FieldDefinition.objects.create(
        entity=entity, key=key, label=label[:100], type=type_, options=options, required=bool(required),
        help_text=(help_text or "")[:200], in_grid=bool(in_grid),
    )


@require("admin.masks")
@require_http_methods(["GET", "POST"])
def mask_list(request):
    if request.method == "POST":
        entity = request.POST.get("entity", "pupil")
        name = request.POST.get("name", "").strip()
        if name:
            base = Mask.objects.filter(entity=entity, is_default=True).first()
            mask = Mask.objects.create(entity=entity, name=name, match_field=request.POST.get("match_field", ""), match_value=request.POST.get("match_value", "").strip(),
                                       roles=_chosen_roles(request))
            tree = base.published.tree if base and base.published else default_tree(entity)
            MaskVersion.objects.create(mask=mask, number=1, status=MaskVersion.DRAFT, tree=tree, author=request.user)
            return redirect("mask_builder", pk=mask.pk)
    choices = {e: [f for f in ms.fields_for(e) if f.type in ("choice", "class", "bool")] for e, _ in FieldDefinition.ENTITIES}
    labels = {f.key: f.display_label for f in FieldDefinition.objects.filter(key__in=["school_type", "category"])}
    return render(request, "masks/list.html", {"masks": Mask.objects.filter(table=None).prefetch_related("versions"), "choices": choices, "match_labels": labels,
                                               "roles": role_choices(), "active_nav": "settings_home"})


def _chosen_roles(request):
    """The roles a mask is shown to ("Nur für Rollen"); none ticked means everyone."""
    known = dict(role_choices())
    return [value for value in request.POST.getlist("roles") if value in known]


@require("admin.masks")
def mask_builder(request, pk):
    mask = get_object_or_404(Mask, pk=pk)
    version = mask.draft or mask.published
    config = {
        "id": str(mask.pk), "name": mask.display_name, "entity": mask.entity, "hasNames": bool(mask.names),
        "tree": version.tree if version else {"tabs": []},
        "isDraft": bool(mask.draft), "published": mask.published.number if mask.published else None,
        "fields": [_field_json(f) for f in ms.fields_for(mask.entity)],
        "blocks": [{"key": k, "label": str(v[0]), "icon": v[1]} for k, v in BLOCKS.items() if modules.block_active(k)],
        "types": [(value, str(label)) for value, label in USER_TYPES],
    }
    if mask.table_id:  # the record page of an own table: its fields and blocks, fields made in its settings
        from tables import masks as table_masks, services as table_services

        table = mask.table
        config.update(
            fields=[table_masks.field_json(f) for f in table_services.fields_of(table)],
            blocks=[{"key": k, "label": str(v[0]), "icon": v[1]} for k, v in table_masks.BLOCKS.items()],
            types=[(value, str(label)) for value, label in table_services.VALUE_TYPES],
            fieldCreateUrl=f"/einstellungen/tabellen/{table.key}/felder/neu/",
            fieldEditUrl=f"/einstellungen/tabellen/{table.key}/felder/{{id}}/", canDuplicate=False,
            backUrl=f"/einstellungen/tabellen/{table.key}/?tab=seite",
        )
    versions = mask.versions.select_related("author").order_by("-number")[:12]
    return render(request, "masks/builder.html", {"mask": mask, "config": config, "versions": versions, "active_nav": "settings_home"})


def _with_titles(clean, node, limit):
    titles = i18n.clean_names(node.get("titles"), limit)
    if titles:
        clean["titles"] = titles
    return clean


def _blocks_of(mask):
    if mask.table_id:
        from tables.masks import BLOCKS as TABLE_BLOCKS

        return TABLE_BLOCKS
    return BLOCKS


def _clean_tree(tree, blocks=BLOCKS):
    """Keep only the structure a mask may contain."""
    clean = {"tabs": []}
    for tab in tree.get("tabs", []):
        sections = []
        for section in tab.get("sections", []):
            items = []
            for item in section.get("items", []):
                kind = item.get("type")
                entry = {"type": kind, "w": min(max(int(item.get("w") or 12), 2), 12)}
                if kind == "field" and item.get("field"):
                    entry["field"] = str(item["field"])
                    for prop in ("label", "help"):
                        if item.get(prop):
                            entry[prop] = str(item[prop])[:200]
                    if i18n.clean_names(item.get("labels")):
                        entry["labels"] = i18n.clean_names(item.get("labels"))
                elif kind == "block" and item.get("block") in blocks:
                    entry["block"] = item["block"]
                elif kind == "note":
                    entry["text"] = str(item.get("text", ""))[:1000]
                else:
                    continue
                rule = item.get("show_if")
                if rule and rule.get("field"):
                    entry["show_if"] = {"field": str(rule["field"]), "op": "ne" if rule.get("op") == "ne" else "eq", "value": str(rule.get("value", ""))[:100]}
                items.append(entry)
            sections.append(_with_titles({"id": str(section.get("id", ""))[:20], "title": str(section.get("title", ""))[:120], "items": items}, section, 120))
        clean["tabs"].append(_with_titles({"id": str(tab.get("id", ""))[:20], "title": str(tab.get("title", ""))[:80], "sections": sections}, tab, 80))
    return clean


@require("admin.masks")
@require_POST
def mask_save(request, pk):
    mask = get_object_or_404(Mask, pk=pk)
    payload = json.loads(request.body or b"{}")
    tree = _clean_tree(payload.get("tree") or {}, _blocks_of(mask))
    if not tree["tabs"]:
        return JsonResponse({"error": _("Eine Maske braucht mindestens einen Reiter.")}, status=422)
    if payload.get("publish"):
        version = ms.publish_tree(mask, tree, request.user)
        audit.log("mask", f"Maske veröffentlicht: {mask.name} (Version {version.number})", request=request, target=mask.pk)
        return JsonResponse({"ok": True, "published": version.number})
    draft = mask.draft
    if draft:
        draft.tree = tree
        draft.author = request.user
        draft.save()
    else:
        latest = mask.versions.order_by("-number").first()
        MaskVersion.objects.create(mask=mask, number=(latest.number + 1) if latest else 1, status=MaskVersion.DRAFT, tree=tree, author=request.user)
    return JsonResponse({"ok": True})


@require("admin.masks")
@require_POST
def mask_preview(request, pk):
    mask = get_object_or_404(Mask, pk=pk)
    payload = json.loads(request.body or b"{}")
    tree = _clean_tree(payload.get("tree") or {}, _blocks_of(mask))
    if mask.table_id:
        from tables import masks as table_masks
        from tables.models import Record

        record = Record.objects.filter(table_id=mask.table_id).select_related("table").first()
        if record is None:
            return JsonResponse({"error": _("Für die Vorschau fehlt ein Datensatz.")}, status=422)
        return render(request, "masks/_preview.html", {"tabs": table_masks.build_view(tree, record, request.user, readonly=True)})
    person = Person.objects.filter(kind=mask.entity, archived=False).select_related("current_class").first()
    if person is None:
        return JsonResponse({"error": _("Für die Vorschau fehlt ein Datensatz.")}, status=422)
    tabs = ms.build_view(tree, person, request.user, readonly=True)
    return render(request, "masks/_preview.html", {"person": person, "tabs": tabs})


@require("admin.masks")
@require_POST
def mask_action(request, pk):
    mask = get_object_or_404(Mask, pk=pk)
    action = request.POST.get("action")
    if action == "restore":
        version = get_object_or_404(mask.versions, number=request.POST.get("number"))
        ms.publish_tree(mask, version.tree, request.user)
        messages.success(request, _("Version %(number)s ist wieder aktiv.") % {"number": version.number})
        return redirect("mask_builder", pk=pk)
    if action == "discard":
        journal.delete(mask.versions.filter(status=MaskVersion.DRAFT))
        return redirect("mask_builder", pk=pk)
    if action == "rename":
        mask.name, mask.names = i18n.rename(mask.name, mask.names, request.POST.get("name", "")[:80], i18n.scope_for(request, bool(mask.names)))
        mask.save()
        return redirect("mask_list") if request.POST.get("next") == "list" else redirect("mask_builder", pk=pk)
    if action == "roles":
        mask.roles = _chosen_roles(request)
        mask.save()
        return redirect("mask_list")
    if action == "delete" and not mask.is_default:
        for version in mask.versions.all():
            version.delete()
        mask.delete()
    if mask.table_id:
        return redirect(f"/einstellungen/tabellen/{mask.table.key}/?tab=seite")
    return redirect("mask_list")


@require("admin.fields")
@require_http_methods(["GET", "POST"])
def field_list(request):
    entity = request.GET.get("bereich", "pupil")
    if entity not in dict(FieldDefinition.ENTITIES):
        entity = "pupil"
    error = ""
    if request.method == "POST":
        try:
            fd = create_field(
                request.POST.get("entity"), request.POST.get("label"), request.POST.get("type"), request.POST.get("options"),
                request.POST.get("required"), request.POST.get("help_text"), request.POST.get("in_grid"),
            )
            audit.log("field", f"Feld angelegt: {fd.label}", request=request)
            messages.success(request, _("%(field)s wurde angelegt. Ziehen Sie das Feld jetzt in eine Maske.") % {"field": fd.display_label})
            return redirect(f"/einstellungen/felder/?bereich={fd.entity}")
        except ValidationError as exc:
            error = " ".join(exc.messages)
    fields = FieldDefinition.objects.filter(entity=entity)
    return render(request, "masks/fields.html", {"fields": fields, "entity": entity, "entities": FieldDefinition.ENTITIES, "types": USER_TYPES, "error": error, "active_nav": "settings_home"})


@require("admin.fields")
@require_POST
def field_duplicate(request, pk):
    """A new field like this one, for the builder (JSON) and the list of fields (a form)."""
    fd = get_object_or_404(FieldDefinition, pk=pk)
    wants_json = request.content_type == "application/json"
    try:
        copy = create_field(fd.entity, _("%(field)s (Kopie)") % {"field": fd.display_label}, fd.type, "\n".join(o["value"] for o in fd.options or []), fd.required, fd.help_text, fd.in_grid)
    except ValidationError as exc:
        if wants_json:
            return JsonResponse({"error": " ".join(exc.messages)}, status=422)
        messages.error(request, _("Dieses eingebaute Feld lässt sich nicht duplizieren."))
        return redirect(f"/einstellungen/felder/?bereich={fd.entity}")
    copy.options = [dict(o) for o in fd.options or []]  # with their colours and names
    copy.save()
    audit.log("field", f"Feld dupliziert: {fd.label}", request=request)
    if wants_json:
        return JsonResponse(_field_json(copy))
    messages.success(request, _("%(field)s angelegt.") % {"field": copy.display_label})
    return redirect("field_edit", pk=copy.pk)


@require("admin.fields")
@require_POST
def field_create(request):
    payload = json.loads(request.body or b"{}")
    try:
        fd = create_field(payload.get("entity"), payload.get("label"), payload.get("type"), payload.get("options"), payload.get("required"), payload.get("help_text"), payload.get("in_grid"))
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=422)
    audit.log("field", f"Feld angelegt: {fd.label}", request=request)
    return JsonResponse(_field_json(fd))


@require("admin.fields")
@require_http_methods(["GET", "POST"])
def field_edit(request, pk):
    fd = get_object_or_404(FieldDefinition, pk=pk)
    usage = ms.usage_of(fd.entity, fd.key)
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "rights":
            from core import views_roles

            views_roles.save_field_rights(request, fd.entity, fd.key)
            return redirect("field_edit", pk=pk)
        if action == "archive" and not fd.is_core:
            fd.archived = True
            fd.save()
            audit.log("field", f"Feld archiviert: {fd.label}", request=request)
            messages.success(request, _("%(field)s wurde archiviert. Die Daten bleiben erhalten.") % {"field": fd.display_label})
            return redirect(f"/einstellungen/felder/?bereich={fd.entity}")
        if action == "restore":
            fd.archived = False
            fd.save()
            return redirect("field_edit", pk=pk)
        scope = i18n.scope_for(request, fd.has_names)
        fd.label, fd.labels = i18n.rename(fd.label, fd.labels, request.POST.get("label", "")[:100], scope)
        fd.help_text = request.POST.get("help_text", "").strip()[:200]
        fd.in_grid = bool(request.POST.get("in_grid"))
        fd.read_only = bool(request.POST.get("read_only"))
        if fd.key not in ("first_name", "last_name"):
            fd.required = bool(request.POST.get("required"))
        if fd.type in ("choice", "multichoice"):
            fd.options = _edited_options(fd, request, scope)
        fd.save()
        messages.success(request, _("Gespeichert."))
        return redirect(f"/einstellungen/felder/?bereich={fd.entity}")
    options = [{"value": o["value"], "shown": i18n.option_label(o)} for o in fd.options]
    from core import views_roles

    return render(request, "masks/field_edit.html", {"fd": fd, "usage": usage, "options": options, "active_nav": "settings_home",
                                                    "field_rights": views_roles.field_rights(request, fd.entity, fd.key)})


def _edited_options(fd, request, scope):
    """The option rows of the form: existing ones by their stored value (renamed in place, so records
    keep pointing at them), new ones with their name as value. Removed rows go."""
    known = {o["value"]: o for o in fd.options}
    values = request.POST.getlist("option_value")
    shown = request.POST.getlist("option_label")
    if not values and "options" in request.POST:  # one option per line, as the form had before
        by_name = {i18n.option_label(o): v for v, o in known.items()}
        shown = [line.strip() for line in request.POST["options"].splitlines() if line.strip()]
        values = [line if line in known else by_name.get(line, "") for line in shown]
    options, seen = [], set()
    for value, text in zip(values, shown):
        text = text.strip()[:100]
        if value in known:
            if value in seen:
                continue
            option = i18n.rename_option(known[value], text, scope) if text else known[value]
        elif text and text not in known and text not in seen:
            option, value = {"value": text, "color": ""}, text
        else:
            continue
        seen.add(value)
        options.append(option)
    return options
