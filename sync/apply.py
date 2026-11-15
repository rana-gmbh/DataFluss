"""Writes changes that come from another node.

Values are written raw: no model save() logic runs, so applying a change can never cause a new
local change, and side effects (number allocation, history entries) happened where the change was
made. The field versions travel with the values. A server journals what it applies, with the
device as origin, so other devices receive it too; a Mac journals only its own edits.
"""
from django.db import models
from django.utils import timezone

from core import hlc as clock

from . import codec
from .models import CREATED, DELETED, FieldVersion, Journal


def _versions(entity, pk, versions, origin):
    if not versions:
        return
    FieldVersion.objects.bulk_create(
        [FieldVersion(entity=entity, record_id=pk, key=k, hlc=h, origin=origin) for k, h in versions.items()],
        update_conflicts=True,
        unique_fields=["entity", "record_id", "key"],
        update_fields=["hlc", "origin"],
    )
    for stamp in versions.values():
        clock.observe(stamp)


def _journal(entity, pk, versions, origin, base=None):
    Journal.objects.bulk_create(
        [Journal(entity=entity, record_id=pk, key=k, hlc=h, origin=origin, base=(base or {}).get(k, "")) for k, h in versions.items()]
    )


def version_of(entity, pk, key):
    """The version of one key: its own, else the record's creation; "-" when the record is gone."""
    found = dict(FieldVersion.objects.filter(entity=entity, record_id=pk, key__in=[key, CREATED, DELETED]).values_list("key", "hlc"))
    if DELETED in found:
        return DELETED, found[DELETED]
    if key in found:
        return key, found[key]
    return CREATED, found.get(CREATED, "")


def upsert(model, pk, fields, versions, origin, journal=False):
    """A whole record, new or not. `fields` are encoded values by field name."""
    entity = codec.entity_of(model)
    decoded = {}
    for name, raw in fields.items():
        field = model._meta.get_field(name)
        decoded[field.attname] = codec.decode_value(field, raw)
    manager = model._base_manager
    if manager.filter(pk=pk).exists():
        manager.filter(pk=pk).update(**decoded)
    else:
        instance = model(pk=pk, **decoded)
        # A raw save skips auto_now, so a record without its timestamps gets the time it arrived.
        for field in model._meta.concrete_fields:
            if getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
                if getattr(instance, field.attname) is None:
                    setattr(instance, field.attname, timezone.now())
        instance.save_base(raw=True, force_insert=True)
    FieldVersion.objects.filter(entity=entity, record_id=pk, key=DELETED).delete()
    _versions(entity, pk, versions, origin)
    if journal:
        _journal(entity, pk, {CREATED: max(versions.values()) if versions else clock.tick()}, origin)


def set_key(model, pk, key, raw, stamp, origin, journal=False, base=""):
    """One field, or one key of a JSON dictionary. Returns False if the record does not exist."""
    entity = codec.entity_of(model)
    name, sub = codec.split(key)
    field = model._meta.get_field(name)
    manager = model._base_manager
    if sub is None:
        updated = manager.filter(pk=pk).update(**{field.attname: codec.decode_value(field, raw)})
    else:
        current = manager.filter(pk=pk).values_list(field.attname, flat=True).first()
        if current is None and not manager.filter(pk=pk).exists():
            return False
        value = dict(current) if isinstance(current, dict) else {}
        if raw == codec.ABSENT:
            value.pop(sub, None)
        else:
            value[sub] = raw
        updated = manager.filter(pk=pk).update(**{field.attname: value})
    if not updated:
        return False
    _versions(entity, pk, {key: stamp}, origin)
    if journal:
        _journal(entity, pk, {key: stamp}, origin, {key: base})
    return True


def remove(model, pk, stamp, origin, journal=False):
    """Deletes a record the way it was deleted where it happened; cascades follow the same rules."""
    entity = codec.entity_of(model)
    model._base_manager.filter(pk=pk).delete()
    FieldVersion.objects.filter(entity=entity, record_id=pk).delete()
    _versions(entity, pk, {DELETED: stamp}, origin)
    if journal:
        _journal(entity, pk, {DELETED: stamp}, origin)


def evict(model, pks):
    """A Mac drops records that left its user's scope. Nothing is journaled: they still exist elsewhere."""
    entity = codec.entity_of(model)
    model._base_manager.filter(pk__in=pks).delete()
    FieldVersion.objects.filter(entity=entity, record_id__in=pks).delete()


def current_value(model, pk, key, hide=None):
    instance = model._base_manager.filter(pk=pk).first()
    if instance is None:
        return None, False
    return codec.encode_key(instance, key, hide), True


def is_file_field(model, name):
    return isinstance(model._meta.get_field(name), models.FileField)
