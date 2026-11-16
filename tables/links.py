"""Link fields and file fields of own tables (docs/TABLES-PLAN.md §2, §6).

A link field points to records of another table, to pupils, staff or devices. Each target is a Link row;
what the user may see of a target follows the target's own rights, so a link never shows a name the user
could not look up anyway: it says "nicht sichtbar" instead.
"""
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from core import fieldtypes as ft
from core.images import TOO_LARGE, image_extension, too_large

from .models import Attachment, Link, Record, Table

NAMESPACE = uuid.UUID("5c1f2a63-3f0e-4d7c-9a51-7d2b6a0e9c41")
BUILT_IN = (Link.PUPIL, Link.STAFF, Link.ASSET)


def link_id(record_id, field_key, target_id=None):
    """The same row on every computer: per record and field, and per target for a field with several."""
    name = f"{record_id}:{field_key}" + (f":{target_id}" if target_id else "")
    return uuid.uuid5(NAMESPACE, name)


# ---------------------------------------------------------------- design

def target_choices():
    """What a link field may point to, for the field dialog: [(value, label)]."""
    from core import modules

    choices = [(f"table:{t.key}", t.display_name) for t in Table.objects.filter(archived=False)]
    if "schulverwaltung" not in modules.active():
        return choices
    return choices + [(Link.PUPIL, _("Schüler")), (Link.STAFF, _("Personal")), (Link.ASSET, _("Geräte"))]


def clean_config(type_, data):
    if type_ in ("file", "image"):
        return {"multiple": True} if data.get("multiple") else {}
    if type_ != "link":
        return {}
    target = (data.get("target") or "").strip()
    if target in BUILT_IN:
        config = {"target": target}
    elif target.startswith("table:") and Table.objects.filter(key=target.removeprefix("table:")).exists():
        config = {"target": Link.RECORD, "table": target.removeprefix("table:")}
    else:
        raise ValidationError(_("Bitte angeben, worauf die Verknüpfung zeigt."))
    if data.get("multiple"):
        config["multiple"] = True
    if reverse := (data.get("reverse") or "").strip()[:100]:
        config["reverse"] = reverse
    return config


def kind_of(field):
    return ft.config(field).get("target", "")


def target_table(field):
    return Table.objects.filter(key=ft.config(field).get("table", "")).first() if kind_of(field) == Link.RECORD else None


def target_label(field):
    kind = kind_of(field)
    if kind == Link.RECORD:
        table = target_table(field)
        return table.display_name if table else _("unbekannte Tabelle")
    return dict(target_choices()).get(kind, kind)


# ---------------------------------------------------------------- links of a record

def targets(record, field):
    return [link.target_id for link in Link.objects.filter(record=record, field=field.key).exclude(target_id=None)]


def _check_targets(field, ids):
    kind = kind_of(field)
    if kind == Link.RECORD:
        table = target_table(field)
        found = set(Record.objects.filter(table=table, pk__in=ids).values_list("pk", flat=True)) if table else set()
    elif kind in (Link.PUPIL, Link.STAFF):
        from people.models import Person

        found = set(Person.objects.filter(kind=kind, pk__in=ids).values_list("pk", flat=True))
    elif kind == Link.ASSET:
        from devices.models import Asset

        found = set(Asset.objects.filter(pk__in=ids).values_list("pk", flat=True))
    else:
        found = set()
    if set(ids) - found:
        raise ValidationError(_("%(field)s: Unbekannter Datensatz.") % {"field": field.display_label})


def set_targets(record, field, raw):
    """Replace the field's targets with `raw` (ids). Single fields keep one row and change its target."""
    if field.archived:
        raise ValidationError(_("%(field)s ist archiviert und kann nicht geändert werden.") % {"field": field.display_label})
    ids = [uuid.UUID(i) for i in ft.clean(field, raw)]
    _check_targets(field, ids)
    kind = kind_of(field)
    with transaction.atomic():
        if not ft.config(field).get("multiple"):
            pk = link_id(record.pk, field.key)
            link = Link.objects.filter(pk=pk).first() or Link(pk=pk, record=record, field=field.key, target_kind=kind)
            target = ids[0] if ids else None
            if link._state.adding and target is None:
                return
            if link.target_id != target or link.target_kind != kind:
                link.target_id, link.target_kind = target, kind
                link.save()
            return
        current = {link.target_id: link for link in Link.objects.filter(record=record, field=field.key)}
        for target, link in current.items():
            if target not in ids:
                link.delete()
        for order, target in enumerate(ids):
            link = current.get(target)
            if link is None:
                Link.objects.create(pk=link_id(record.pk, field.key, target), record=record, field=field.key, target_kind=kind, target_id=target, order=order)
            elif link.order != order:
                link.order = order
                link.save()


def add_target(record, field, target):
    now = [str(t) for t in targets(record, field)]
    set_targets(record, field, (now + [str(target)]) if ft.config(field).get("multiple") else [str(target)])


def remove_target(record, field, target):
    set_targets(record, field, [str(t) for t in targets(record, field) if str(t) != str(target)])


# ---------------------------------------------------------------- what a user sees of a target

def _hidden():
    return {"title": _("nicht sichtbar"), "url": "", "visible": False}


def resolve(user, kind, ids, table=None):
    """{id: {"title", "url", "visible"}} for the targets of one kind, as far as `user` may see them.
    Without a user (a system task) every target is visible."""
    from . import services

    ids = [i for i in ids if i]
    out = {i: _hidden() for i in ids}
    if not ids:
        return out
    if kind == Link.RECORD:
        tables = {t.pk: t for t in Table.objects.all()} if user is None else {t.pk: t for t in services.readable(user, include_archived=True)}
        rows = Record.objects.filter(pk__in=ids, table_id__in=tables)
        if table is not None:
            rows = rows.filter(table=table)
        for record in rows:
            t = tables[record.table_id]
            out[record.pk] = {"title": record.title or _("(ohne Titel)"), "url": f"/tabellen/{t.key}/{record.pk}/", "visible": True}
    elif kind in (Link.PUPIL, Link.STAFF) and (user is None or user.can("person.view")):
        from people.models import Person

        for person in Person.objects.filter(pk__in=ids, kind=kind):
            out[person.pk] = {"title": person.display_name, "url": f"/personen/{person.pk}/", "visible": True}
    elif kind == Link.ASSET and (user is None or user.can("device.view")):
        from devices.models import Asset

        for asset in Asset.objects.filter(pk__in=ids):
            out[asset.pk] = {"title": asset.number, "url": f"/geraete/{asset.pk}/", "visible": True}
    return out


def shown_targets(record, field, user):
    ids = targets(record, field)
    found = resolve(user, kind_of(field), ids, target_table(field))
    return [{"id": str(i), **found[i]} for i in ids]


def display(record, field, user=None):
    return ", ".join(str(t["title"]) for t in shown_targets(record, field, user))


def candidates(user, field, query, limit=20):
    """What the picker offers for `query`: only targets the user may see."""
    from . import services

    query = (query or "").strip().lower()
    kind = kind_of(field)
    out = []
    if kind == Link.RECORD:
        table = target_table(field)
        if table is None or not services.may_read(user, table):
            return []
        rows = Record.objects.filter(table=table)
        for term in query.split():
            rows = rows.filter(search_text__contains=term)
        out = [{"id": str(r.pk), "title": r.title or _("(ohne Titel)"), "sub": table.display_name} for r in rows.order_by("title")[:limit]]
    elif kind in (Link.PUPIL, Link.STAFF) and user.can("person.view"):
        from django.db.models import Q

        from people.models import Person

        people = Person.objects.filter(kind=kind, archived=False).select_related("current_class")
        for term in query.split():
            people = people.filter(Q(first_name__icontains=term) | Q(last_name__icontains=term) | Q(email__icontains=term))
        out = [{"id": str(p.pk), "title": p.display_name, "sub": p.current_class.name if p.current_class else ""} for p in people.order_by("last_name", "first_name")[:limit]]
    elif kind == Link.ASSET and user.can("device.view"):
        from django.db.models import Q

        from devices.models import Asset

        assets = Asset.objects.all()
        if query:
            assets = assets.filter(Q(number__icontains=query) | Q(serial__icontains=query))
        out = [{"id": str(a.pk), "title": a.number, "sub": a.serial} for a in assets.order_by("number")[:limit]]
    return out


def backlinks(user, kind, target_id):
    """Records of readable tables that link to this target, grouped by table and field."""
    from . import services

    tables = {t.pk: t for t in services.readable(user, include_archived=True)}
    if not tables:
        return []
    links = Link.objects.filter(target_kind=kind, target_id=target_id, record__deleted_at=None, record__table_id__in=tables).select_related("record")
    groups = {}
    fields = {}
    for link in links:
        key = (link.record.table_id, link.field)
        if key not in fields:
            fields[key] = services.Field.objects.filter(table_id=link.record.table_id, key=link.field, archived=False).first()
        field = fields[key]
        if field is None:
            continue
        group = groups.setdefault(key, {"table": tables[link.record.table_id], "field": field,
                                        "title": ft.config(field).get("reverse") or f"{tables[link.record.table_id].display_name} · {field.display_label}",
                                        "records": []})
        group["records"].append({"title": link.record.title or _("(ohne Titel)"), "url": f"/tabellen/{group['table'].key}/{link.record_id}/"})
    return list(groups.values())


# ---------------------------------------------------------------- files

def files(record, field):
    return list(Attachment.objects.filter(record=record, field=field.key))


def add_file(record, field, upload, user=None):
    if field.archived:
        raise ValidationError(_("%(field)s ist archiviert und kann nicht geändert werden.") % {"field": field.display_label})
    if too_large(upload):
        raise ValidationError(str(TOO_LARGE))
    if field.type == "image" and not image_extension(upload):
        raise ValidationError(_("%(field)s: Bitte ein Bild hochladen (JPEG, PNG, GIF oder WebP).") % {"field": field.display_label})
    with transaction.atomic():
        if not ft.config(field).get("multiple"):
            for old in Attachment.objects.filter(record=record, field=field.key):
                remove_file(old)
        attachment = Attachment(record=record, field=field.key, name=upload.name[:200], size=upload.size,
                                content_type=(getattr(upload, "content_type", "") or "")[:100], uploaded_by=user)
        attachment.file.save(upload.name[:120], upload, save=False)
        attachment.save()
    return attachment


def remove_file(attachment):
    stored = attachment.file
    attachment.delete()
    transaction.on_commit(lambda: stored.delete(save=False))


# ---------------------------------------------------------------- night

def cleanup():
    """Links whose target is gone for good: deleted records after the bin, deleted people and devices."""
    from devices.models import Asset
    from people.models import Person

    existing = {
        Link.RECORD: lambda ids: set(Record.everything.filter(pk__in=ids).values_list("pk", flat=True)),
        Link.PUPIL: lambda ids: set(Person.everything.filter(pk__in=ids).values_list("pk", flat=True)),
        Link.STAFF: lambda ids: set(Person.everything.filter(pk__in=ids).values_list("pk", flat=True)),
        Link.ASSET: lambda ids: set(Asset.objects.filter(pk__in=ids).values_list("pk", flat=True)),
    }
    for kind, exists in existing.items():
        ids = set(Link.objects.filter(target_kind=kind).exclude(target_id=None).values_list("target_id", flat=True))
        gone = ids - exists(ids)
        for link in Link.objects.filter(target_kind=kind, target_id__in=gone):
            link.delete()
