"""Rights, values and design of own tables (docs/TABLES-PLAN.md §2, §3, §6)."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify
from django.utils.translation import gettext as _

from core import audit, fieldtypes as ft
from core.models import Role
from masks import i18n
from vault import crypto

from .models import EDIT, NONE, READ, RIGHTS, Field, Record, Table

MAX_TEXT = 10_000
# Types a person can choose now; links, files and formulas follow with their own steps of the plan.
VALUE_TYPES = [(key, label) for key, label in ft.choices() if ft.TYPES[key].storage == "value"]
SEARCHABLE = {"text", "textarea", "email", "phone", "url", "choice", "multichoice", "number", "date", "datetime"}
TITLE_TYPES = ("text", "email", "phone", "url", "choice", "number", "date")
# A field may change to these types; values are converted to the new type's text.
COMPATIBLE = {
    "text": {"textarea", "email", "phone", "url"}, "textarea": {"text"}, "email": {"text"}, "phone": {"text"},
    "url": {"text"}, "choice": {"text", "multichoice"}, "number": {"text"}, "date": {"text"}, "datetime": {"text"},
    "bool": {"text"},
}


# ---------------------------------------------------------------- rights

def right(user, table):
    """none, read or edit. IT always edits; an archived table can still be read but not changed."""
    if user is None or not user.is_authenticated:
        return NONE
    granted = EDIT if user.role == Role.IT else (table.rights or {}).get(user.role, NONE)
    if granted not in RIGHTS:
        granted = NONE
    if table.archived and granted == EDIT:
        granted = READ
    return granted


def may_read(user, table):
    return right(user, table) in (READ, EDIT)


def may_edit(user, table):
    return right(user, table) == EDIT


def readable(user, include_archived=False):
    tables = Table.objects.all() if include_archived else Table.objects.filter(archived=False)
    return [t for t in tables if may_read(user, t)]


def roles():
    """The roles a table grants rights to: every role but IT, which always edits."""
    from core.models import role_choices

    return [(value, label) for value, label in role_choices() if value != Role.IT]


def set_rights(table, rights, request=None):
    table.rights = {role: rights.get(role, NONE) if rights.get(role, NONE) in RIGHTS else NONE for role, _label in roles()}
    table.save()
    audit.log("table", f"Rechte der Tabelle {table.name} geändert", request=request, target=table.pk)


# ---------------------------------------------------------------- values

def fields_of(table, include_archived=False):
    qs = Field.objects.filter(table=table)
    return list(qs if include_archived else qs.filter(archived=False))


def secret_context(record, key):
    return f"record:{record.pk}:{key}"


def stored(record, field):
    return (record.values or {}).get(field.key)


def value(record, field):
    """The Python value (core/fieldtypes.py); a secret field gives its stored form, never the plain text."""
    raw = stored(record, field)
    return raw if field.type == "secret" else ft.from_store(field, raw)


def display(record, field, localized=False):
    if field.type == "secret":
        return ft.MASKED if stored(record, field) else ""
    return ft.display(field, value(record, field), localized)


def form(record, field):
    if field.type == "secret":
        return ft.MASKED if stored(record, field) else ""
    return ft.form(field, value(record, field))


def reveal(record, field):
    raw = stored(record, field)
    return crypto.decrypt(raw["secret"], secret_context(record, field.key)) if isinstance(raw, dict) and "secret" in raw else ""


def title_field(table, fields):
    by_key = {f.key: f for f in fields}
    chosen = by_key.get(table.title_field)
    if chosen is not None and chosen.type != "secret":
        return chosen
    return next((f for f in fields if f.type in TITLE_TYPES), None)


def refresh(record, fields=None):
    """The cached title and search text, from the record's current values."""
    fields = fields if fields is not None else fields_of(record.table)
    named = title_field(record.table, fields)
    record.title = str(display(record, named, localized=False))[:300] if named else ""
    words = [str(display(record, f, localized=False)) for f in fields if f.type in SEARCHABLE and stored(record, f) not in (None, "", [])]
    record.search_text = " ".join(words).lower()[:5000]


def _assign(record, field, raw):
    """Clean `raw` for `field` and put it into the record's values (not saved)."""
    if field.archived:
        raise ValidationError(_("%(field)s ist archiviert und kann nicht geändert werden.") % {"field": field.display_label})
    if ft.TYPES.get(field.type) is None or ft.TYPES[field.type].storage != "value":
        raise ValidationError(_("%(field)s kann hier nicht eingegeben werden.") % {"field": field.display_label})
    if field.type == "secret" and raw == ft.MASKED:
        return  # the masked placeholder came back unchanged
    if isinstance(raw, str) and len(raw) > MAX_TEXT:
        raise ValidationError(_("%(field)s: höchstens %(n)s Zeichen.") % {"field": field.display_label, "n": MAX_TEXT})
    cleaned = ft.clean(field, raw)
    values = dict(record.values or {})
    if cleaned in (None, "", []):
        values.pop(field.key, None)
    elif field.type == "secret":
        values[field.key] = {"secret": crypto.encrypt(cleaned, secret_context(record, field.key))}
    else:
        stored_value = ft.to_store(field, cleaned)
        if field.unique and _taken(record, field, stored_value):
            raise ValidationError(_("%(field)s: Diesen Wert hat schon ein anderer Datensatz.") % {"field": field.display_label})
        values[field.key] = stored_value
    record.values = values


def _taken(record, field, stored_value):
    others = Record.objects.filter(table_id=record.table_id).exclude(pk=record.pk)
    return others.filter(**{f"values__{field.key}": stored_value}).exists()


def set_value(record, field, raw):
    """Save one field and return (saved value for the form, previous value for the form)."""
    previous = form(record, field)
    _assign(record, field, raw)
    refresh(record)
    record.save()
    return form(record, field), "" if field.type == "secret" else previous


def create_record(table, raw_values=None):
    """A new record; `raw_values` are form inputs by field key. Required fields are asked for when edited."""
    record = Record(table=table)
    fields = {f.key: f for f in fields_of(table)}
    for key, raw in (raw_values or {}).items():
        if key in fields and raw not in (None, "", []):
            _assign(record, fields[key], raw)
    refresh(record, list(fields.values()))
    record.save()
    return record


def check_record(record):
    """Problems of a record as a device left it: unknown fields and values the field would not take."""
    fields = {f.key: f for f in fields_of(record.table, include_archived=True)}
    problems = []
    for key, raw in (record.values or {}).items():
        field = fields.get(key)
        if field is None:
            problems.append(_("Unbekanntes Feld %(key)s.") % {"key": key})
            continue
        try:
            ft.check_stored(field, raw)
        except ValidationError as exc:
            problems.extend(exc.messages)
    return problems


# ---------------------------------------------------------------- design

UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def _slug(text, limit):
    """A key from a name: "Bücher" gives buecher, not bucher."""
    return slugify((text or "").translate(UMLAUTS)).replace("-", "_")[:limit]


def _unique_key(base, taken, limit):
    base = base[:limit] or "x"
    key, n = base, 1
    while key in taken:
        n += 1
        suffix = f"_{n}"
        key = base[: limit - len(suffix)] + suffix
    return key


def create_table(name, request=None):
    name = (name or "").strip()[:100]
    if not name:
        raise ValidationError(_("Bitte einen Namen für die Tabelle eingeben."))
    key = _unique_key(_slug(name, 20) or "tabelle", set(Table.objects.values_list("key", flat=True)), 20)
    with transaction.atomic():
        table = Table.objects.create(key=key, name=name, order=Table.objects.count() * 10 + 10)
        first = Field.objects.create(table=table, key="f_name", label=_("Name"), type="text", order=10)
        table.title_field = first.key
        table.save()
    audit.log("table", f"Tabelle angelegt: {name}", request=request, target=table.pk)
    return table


def clean_config(type_, data):
    """The type's settings from a form (number: decimals, unit, min, max)."""
    if type_ != "number":
        return {}
    config = {}
    try:
        decimals = int(data.get("decimals") or 0)
    except ValueError:
        raise ValidationError(_("Bitte die Zahl der Nachkommastellen angeben (0 bis 6)."))
    if not 0 <= decimals <= 6:
        raise ValidationError(_("Bitte die Zahl der Nachkommastellen angeben (0 bis 6)."))
    if decimals:
        config["decimals"] = decimals
    if unit := (data.get("unit") or "").strip()[:10]:
        config["unit"] = unit
    for bound in ("min", "max"):
        text = (data.get(bound) or "").strip().replace(",", ".")
        if text:
            try:
                float(text)
            except ValueError:
                raise ValidationError(_("Bitte als Grenze eine Zahl eingeben."))
            config[bound] = text
    return config


def create_field(table, label, type_, options_text="", required=False, help_text="", in_grid=True, config=None, request=None):
    label = (label or "").strip()[:100]
    if not label:
        raise ValidationError(_("Bitte eine Bezeichnung eingeben."))
    if type_ not in dict(VALUE_TYPES):
        raise ValidationError(_("Unbekannter Feldtyp."))
    options = [{"value": line.strip()[:100], "color": ""} for line in (options_text or "").splitlines() if line.strip()]
    if ft.TYPES[type_].options and not options:
        raise ValidationError(_("Bitte mindestens eine Option angeben."))
    taken = set(Field.objects.filter(table=table).values_list("key", flat=True))
    key = _unique_key("f_" + (_slug(label, 50) or "feld"), taken, 60)
    field = Field.objects.create(
        table=table, key=key, label=label, type=type_, options=options if ft.TYPES[type_].options else [],
        config=config or {}, required=bool(required), help_text=(help_text or "").strip()[:200], in_grid=bool(in_grid),
        order=(max(Field.objects.filter(table=table).values_list("order", flat=True), default=0) + 10),
    )
    audit.log("table", f"Feld {label} in {table.name} angelegt", request=request, target=table.pk)
    return field


def change_type(field, new_type, request=None):
    """To a compatible type only; every record's value becomes the new type's text."""
    if new_type == field.type:
        return
    if new_type not in COMPATIBLE.get(field.type, set()):
        raise ValidationError(_("Dieser Typ passt nicht zu den eingetragenen Werten. Legen Sie besser ein neues Feld an."))
    old = Field(type=field.type, options=field.options, config=field.config, label=field.label, labels=field.labels)
    with transaction.atomic():
        field.type = new_type
        if not ft.TYPES[new_type].options:
            field.options = []
        field.config = {}
        field.save()
        for record in Record.everything.filter(table_id=field.table_id).iterator():
            raw = (record.values or {}).get(field.key)
            if raw in (None, "", []):
                continue
            if new_type == "multichoice":
                converted = [raw] if isinstance(raw, str) else raw
            else:
                converted = str(ft.display(old, ft.from_store(old, raw), localized=False))
            record.values = {**record.values, field.key: converted}
            refresh(record)
            record.save()
    audit.log("table", f"Typ des Feldes {field.label} in {field.table.name} geändert", request=request, target=field.table_id)


def edit_field(field, data, scope, request=None):
    """Name, help, options, settings and flags from the field form."""
    from masks.views import _edited_options

    field.label, field.labels = i18n.rename(field.label, field.labels, (data.get("label") or "")[:100], scope)
    field.help_text = (data.get("help_text") or "").strip()[:200]
    field.required = bool(data.get("required"))
    field.unique = bool(data.get("unique")) and field.type not in ("bool", "secret", "multichoice", "textarea")
    field.in_grid = bool(data.get("in_grid"))
    if ft.TYPES.get(field.type) and ft.TYPES[field.type].options:
        field.options = _edited_options(field, request, scope)
        if not field.options:
            raise ValidationError(_("Bitte mindestens eine Option angeben."))
    if field.type == "number":
        field.config = clean_config("number", data)
    field.save()


def set_order(table, keys):
    by_key = {f.key: f for f in Field.objects.filter(table=table)}
    for index, key in enumerate(k for k in keys if k in by_key):
        field = by_key[key]
        if field.order != (index + 1) * 10:
            field.order = (index + 1) * 10
            field.save()


def delete_table(table, request=None):
    """Only a table without records goes for good; otherwise it is archived."""
    if Record.everything.filter(table=table).exists():
        raise ValidationError(_("Die Tabelle enthält Datensätze. Archivieren Sie sie stattdessen."))
    name = table.name
    with transaction.atomic():
        for field in Field.objects.filter(table=table):
            field.delete()
        table.delete()
    audit.log("table", f"Tabelle gelöscht: {name}", request=request)
