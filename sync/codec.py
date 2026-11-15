"""Field values as JSON and back, the same on every node.

Encrypted values (vault secrets, 2FA secrets) travel as the ciphertext they are stored as. Every
node of one school holds the same vault key, so they stay readable everywhere.
"""
import datetime
import decimal
import uuid
from functools import lru_cache

from django.apps import apps
from django.db import models
from django.utils.dateparse import parse_date, parse_datetime, parse_time

from core.models import TrackedModel

ABSENT = {"__absent__": True}  # a key that is not in a JSON dictionary (any more)

# Workflow state that stays where it happened. Its effects (the people an import made) sync.
LOCAL_ONLY = {"transfer.importsession"}


@lru_cache(maxsize=None)
def synced_models():
    """Tracked models in an order where every model comes after the ones it points to."""
    found = [m for m in apps.get_models() if issubclass(m, TrackedModel) and m._meta.label_lower not in LOCAL_ONLY]
    ordered, seen = [], set()

    def visit(model, path=()):
        if model in seen or model in path:
            return
        for field in model._meta.concrete_fields:
            target = field.related_model if field.is_relation else None
            if target in found and target is not model:
                visit(target, (*path, model))
        seen.add(model)
        ordered.append(model)

    for model in sorted(found, key=lambda m: m._meta.label_lower):
        visit(model)
    return tuple(ordered)


def model_of(entity):
    model = apps.get_model(entity)
    if model not in synced_models():
        raise LookupError(entity)
    return model


def entity_of(model):
    return model._meta.label_lower


def split(key):
    """"custom.telefon" -> ("custom", "telefon"); "last_name" -> ("last_name", None)."""
    name, _, sub = key.partition(".")
    return name, (sub or None)


def encode_value(field, value):
    if value is None:
        return None
    if isinstance(field, models.FileField):
        return getattr(value, "name", value) or ""
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    return value


def decode_value(field, raw):
    if raw is None:
        return None
    if field.is_relation:
        return uuid.UUID(str(raw)) if isinstance(field.target_field, models.UUIDField) else field.target_field.to_python(raw)
    if isinstance(field, models.DateTimeField):
        return parse_datetime(raw)
    if isinstance(field, models.DateField):
        return parse_date(raw)
    if isinstance(field, models.TimeField):
        return parse_time(raw)
    if isinstance(field, models.FileField):
        return raw
    if isinstance(field, models.JSONField):
        return raw
    return field.to_python(raw)


def snapshot_fields(model):
    """Everything a new copy of a record needs, including when it was made."""
    return [f for f in model._meta.concrete_fields if not f.primary_key]


def snapshot(instance, hide=None):
    """All fields of a record, for a node that does not have it yet.

    `hide` maps field names to functions that mask a value for a user who may not see it."""
    hide = hide or {}
    data = {}
    for field in snapshot_fields(type(instance)):
        value = encode_value(field, getattr(instance, field.attname))
        data[field.name] = hide[field.name](value) if field.name in hide else value
    return data


def encode_key(instance, key, hide=None):
    """The current value of one key of a record, or ABSENT for a dictionary key that is gone."""
    hide = hide or {}
    name, sub = split(key)
    field = type(instance)._meta.get_field(name)
    value = encode_value(field, getattr(instance, field.attname))
    if name in hide:
        value = hide[name](value)
    if sub is None:
        return value
    return value[sub] if isinstance(value, dict) and sub in value else ABSENT
