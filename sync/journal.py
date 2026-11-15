"""Records local changes for sync: from TrackedModel.save/delete and from the bulk helpers below.

Keys: "+" a record was created (it travels in full), "-" it was deleted, a field name, or
"field.key" for one key of a JSON dictionary, so two people editing different form fields of the
same pupil do not collide.

Changes applied from another node are written by sync.apply, never through here.
"""
import json

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core import hlc

from .models import CREATED, DELETED, FieldVersion, Journal  # noqa: F401 (DELETED is used by core.models)

_MISSING = object()

# Called after every local change, so a Mac syncs soon (sync.client.Worker).
listeners = []


def node():
    return settings.SERVER_ID


def _plain(value):
    """The same text form the change log compares, so "2026-01-05" and date(2026, 1, 5) are equal."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False, sort_keys=True)
    if isinstance(value, models.Model):
        return str(value.pk)
    return str(value)


def _same(a, b):
    if a is _MISSING or b is _MISSING:
        return a is b
    return _plain(a) == _plain(b)


def tracked_fields(model):
    """The concrete fields sync carries: everything but the primary key and the automatic timestamps."""
    skip = getattr(model, "TRACK_EXCLUDE", set())
    return [f for f in model._meta.concrete_fields if not f.primary_key and f.name not in skip]


def values_of(instance):
    return {f.name: getattr(instance, f.attname) for f in tracked_fields(type(instance))}


def changed_keys(model, before, after):
    if before is None:
        return [CREATED]
    keys = []
    for name, new in after.items():
        old = before.get(name)
        field = model._meta.get_field(name)
        if isinstance(field, models.JSONField) and isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(set(old) | set(new), key=str):
                if not _same(old.get(key, _MISSING), new.get(key, _MISSING)):
                    keys.append(f"{name}.{key}")
        elif not _same(old, new):
            keys.append(name)
    return keys


def record(model, record_id, keys):
    """Journals local changes to one record under one clock reading."""
    from core.context_user import tracking_enabled

    if not keys or not tracking_enabled():
        return
    entity = model._meta.label_lower
    stamp, me = hlc.tick(), node()
    current = dict(
        FieldVersion.objects.filter(entity=entity, record_id=record_id, key__in=[*keys, CREATED]).values_list("key", "hlc")
    )
    created = current.get(CREATED, "")
    Journal.objects.bulk_create(
        [Journal(entity=entity, record_id=record_id, key=k, hlc=stamp, origin=me, base=current.get(k, created)) for k in keys]
    )
    if DELETED in keys:
        # The tombstone is the only version left, so an edit made elsewhere can be compared with it.
        FieldVersion.objects.filter(entity=entity, record_id=record_id).exclude(key=DELETED).delete()
    FieldVersion.objects.bulk_create(
        [FieldVersion(entity=entity, record_id=record_id, key=k, hlc=stamp, origin=me) for k in keys],
        update_conflicts=True,
        unique_fields=["entity", "record_id", "key"],
        update_fields=["hlc", "origin"],
    )
    for listener in listeners:
        listener()


# ---------------------------------------------------------------- bulk helpers
# QuerySet.update(), bulk_create() and QuerySet.delete() skip TrackedModel.save/delete. Code that
# changes tracked records in bulk uses these instead, so sync sees every change.


def update(queryset, **values):
    """QuerySet.update() that journals what really changed, row by row."""
    model = queryset.model
    names = list(values)
    before = {row["pk"]: row for row in queryset.values("pk", *[model._meta.get_field(n).attname for n in names])}
    count = queryset.update(**values)
    after = model._base_manager.filter(pk__in=list(before)).values("pk", *[model._meta.get_field(n).attname for n in names])
    for row in after:
        old = before[row["pk"]]
        as_names = lambda r: {n: r[model._meta.get_field(n).attname] for n in names}  # noqa: E731
        record(model, row["pk"], changed_keys(model, as_names(old), as_names(row)))
    return count


def bulk_create(model, objects, **kwargs):
    created = model._base_manager.bulk_create(objects, **kwargs)
    for obj in created:
        record(model, obj.pk, [CREATED])
    return created


def delete(queryset):
    """QuerySet.delete() with a tombstone per record. Cascades happen on every node by the same rules."""
    model = queryset.model
    pks = list(queryset.values_list("pk", flat=True))
    result = queryset.delete()
    for pk in pks:
        record(model, pk, [DELETED])
    return result
