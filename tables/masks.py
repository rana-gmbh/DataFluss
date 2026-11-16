"""Record pages of own tables, drawn by the mask designer (masks app; docs/TABLES-PLAN.md §4).

A table without a mask shows all its fields in order. Once a mask exists, fields it does not place
still show, in a last section "Weitere Felder", so a new field is never invisible.
"""
from django.urls import reverse
from django.utils.translation import gettext as _, gettext_lazy

from core import fieldtypes as ft
from masks import i18n
from masks.models import Mask, MaskVersion

from . import links, services

# Blocks a record page may hold, besides fields and notes. Links and attachments follow with their step.
BLOCKS = {"history": (gettext_lazy("Änderungsverlauf"), "history")}


def _width(field):
    return 12 if field.type in ("textarea", "multichoice") else 6


def default_tree(table):
    fields = services.fields_of(table)
    return {"tabs": [{"id": "t1", "title": _("Datensatz"), "sections": [
        {"id": "s1", "title": "", "items": [{"type": "field", "field": f.key, "w": _width(f)} for f in fields]},
        {"id": "s2", "title": "", "items": [{"type": "block", "block": "history", "w": 12}]},
    ]}]}


def mask_for(table, user):
    """The published mask this user sees: one made for the user's role first, then the table's default."""
    masks = [m for m in Mask.objects.filter(table=table).prefetch_related("versions") if m.published]
    for mask in masks:
        if user.role in (mask.roles or []):
            return mask
    return next((m for m in masks if m.is_default), masks[0] if masks else None)


def ensure_mask(table, user):
    """The table's default mask, made from the automatic layout the first time someone designs it."""
    mask = Mask.objects.filter(table=table, is_default=True).first()
    if mask is None:
        mask = Mask.objects.create(entity=Mask.RECORD, table=table, name=table.name, names=table.names, is_default=True)
        MaskVersion.objects.create(mask=mask, number=1, status=MaskVersion.PUBLISHED, tree=default_tree(table), author=user)
    return mask


def tree_for(table, user):
    mask = mask_for(table, user)
    return mask.published.tree if mask else default_tree(table)


def field_json(field):
    """A field as the mask designer lists it (masks/views.py _field_json)."""
    return {"key": field.key, "id": str(field.pk), "label": field.display_label, "type": field.type,
            "typeLabel": str(field.type_label), "core": False, "required": field.required}


def _condition_met(item, record, fields):
    rule = item.get("show_if")
    if not rule or not rule.get("field"):
        return True
    field = fields.get(rule["field"])
    if field is None:
        return True
    current = str(services.display(record, field))
    target = str(rule.get("value", ""))
    return current != target if rule.get("op") == "ne" else current == target


def _field_entry(item, record, field, user, editable):
    base = f"/tabellen/{record.table.key}/{record.pk}/"
    step = None
    if field.type == "number":
        places = ft.decimals(field)
        step = f"{10 ** -places:.{places}f}" if places else "1"
    entry = {
        "w": item.get("w", 12), "type": "field", "fd": field,
        "value": services.form(record, field), "display": services.display(record, field, localized=True),
        "editable": editable, "url": f"{base}feld/{field.key}/",
        "reveal_url": f"{base}geheim/{field.key}/" if field.type == "secret" else "",
        "label": i18n.pick(item.get("label", ""), item.get("labels")) or field.display_label,
        "help": item.get("help") or field.help_text,
        "choices": field.option_choices if field.type in ("choice", "multichoice") else None, "step": step,
    }
    if field.type == "link":
        entry.update(targets=links.shown_targets(record, field, user), multiple=bool(ft.config(field).get("multiple")),
                     picker_url=f"{base}verknuepfung/{field.key}/suche/", link_url=f"{base}verknuepfung/{field.key}/")
    elif field.type in ("file", "image"):
        entry.update(files=[{"id": str(a.pk), "name": a.name, "size": a.size, "url": f"{base}datei/{a.pk}/"} for a in links.files(record, field)],
                     files_url=f"{base}dateien/{field.key}/")
    return entry


def editable_type(field):
    kind = ft.TYPES.get(field.type)
    return kind is not None and kind.storage in ("value", "links", "files")


def build_view(tree, record, user, readonly=False):
    """Resolve a mask tree into what masks/_form.html renders, for one record."""
    fields = {f.key: f for f in services.fields_of(record.table)}
    editable = services.may_edit(user, record.table) and not readonly
    placed = {item.get("field") for tab in tree.get("tabs", []) for section in tab.get("sections", [])
              for item in section.get("items", []) if item.get("type") == "field"}
    tabs = []

    def entry_for(item):
        kind = item.get("type")
        if kind == "field":
            field = fields.get(item.get("field"))
            if field is None or (field.type == "secret" and not user.can("vault.view")):
                return None
            return _field_entry(item, record, field, user, editable and editable_type(field))
        if kind == "block" and item.get("block") in BLOCKS:
            name = item["block"]
            return {"w": item.get("w", 12), "type": "block", "block": name, "title": BLOCKS[name][0],
                    "block_url": reverse("record_block", args=[record.table.key, record.pk, name])}
        if kind == "note":
            return {"w": item.get("w", 12), "type": "note", "text": item.get("text", "")}
        return None

    for tab in tree.get("tabs", []):
        sections = []
        for section in tab.get("sections", []):
            items = [e for item in section.get("items", []) if _condition_met(item, record, fields) and (e := entry_for(item))]
            title = i18n.pick(section.get("title", ""), section.get("titles"))
            if items or title:
                sections.append({"title": title, "items": items})
        if sections:
            tabs.append({"id": tab.get("id"), "title": i18n.pick(tab.get("title", ""), tab.get("titles")), "sections": sections})
    rest = [f for f in fields.values() if f.key not in placed and not (f.type == "secret" and not user.can("vault.view"))]
    if rest:
        items = [entry_for({"type": "field", "field": f.key, "w": _width(f)}) for f in rest]
        section = {"title": _("Weitere Felder"), "items": [i for i in items if i]}
        if tabs:
            tabs[-1]["sections"].append(section)
        else:
            tabs.append({"id": "t1", "title": _("Datensatz"), "sections": [section]})
    return tabs


def condition_fields(tree):
    keys = set()
    for tab in tree.get("tabs", []):
        for section in tab.get("sections", []):
            for item in section.get("items", []):
                if (item.get("show_if") or {}).get("field"):
                    keys.add(item["show_if"]["field"])
    return keys
