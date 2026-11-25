"""Rights, values and design of own tables (docs/TABLES-PLAN.md §2, §3, §6)."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils.text import slugify
from django.utils.translation import gettext as _

from core import audit, fieldtypes as ft
from core.models import Role
from masks import i18n
from vault import crypto

from .models import EDIT, NONE, READ, RIGHTS, Field, Record, Table

MAX_TEXT = 10_000
# Types whose value lives in Record.values; links and files have rows of their own (tables/links.py).
VALUE_TYPES = [(key, label) for key, label in ft.choices() if ft.TYPES[key].storage == "value"]
# Every type a field of a table may have.
CREATE_TYPES = [(key, label) for key, label in ft.choices() if key != "backlink"]  # a backlink comes with its link
RESULTS = {"number": "number", "date": "date", "datetime": "datetime", "bool": "bool"}  # formula result -> list column type
SEARCHABLE = {"text", "textarea", "email", "phone", "url", "choice", "multichoice", "number", "date", "datetime"}
TITLE_TYPES = ("text", "email", "phone", "url", "choice", "number", "date")
# A field may change to these types; values are converted to the new type's text.
COMPATIBLE = {
    "text": {"textarea", "email", "phone", "url", "choice", "number", "date"}, "textarea": {"text"}, "email": {"text"}, "phone": {"text"},
    "url": {"text"}, "choice": {"text", "multichoice"}, "number": {"text", "rating"}, "date": {"text"}, "datetime": {"text"},
    "bool": {"text"}, "rating": {"number", "text"},
}


# ---------------------------------------------------------------- rights

def right(user, table):
    """none, read or edit. IT always edits; an archived table can still be read but not changed."""
    if user is None or not user.is_authenticated:
        return NONE
    if (own := getattr(user, "table_right", None)) is not None:  # a publication reads only its chosen tables (publish/viewer.py)
        return own(table)
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


ACCESS = {"it": NONE, "read": READ, "edit": EDIT}  # the choice when a table is created: every other role the same


def access_choices():
    return [("it", _("Nur IT")), ("read", _("Alle Rollen lesen")), ("edit", _("Alle Rollen bearbeiten"))]


def set_access(table, access, request=None):
    """The rights of a new table in one choice; finer rights per role stay on the Rechte tab."""
    if access in ACCESS and access != "it":
        set_rights(table, {role: ACCESS[access] for role, _label in roles()}, request=request)


def set_rights(table, rights, request=None):
    table.rights = {role: rights.get(role, NONE) if rights.get(role, NONE) in RIGHTS else NONE for role, _label in roles()}
    table.save()
    audit.log("table", f"Rechte der Tabelle {table.name} geändert", request=request, target=table.pk)


# ---------------------------------------------------------------- values

def fields_of(table, include_archived=False, user=None):
    """The table's fields; with a user, without those the user's role does not see (core/fieldrules.py)."""
    qs = Field.objects.filter(table=table)
    if not include_archived:
        qs = qs.filter(archived=False)
    if user is not None:
        from core import fieldrules

        if unseen := fieldrules.hidden(user, fieldrules.table_entity(table)):
            qs = qs.exclude(key__in=unseen)
    return list(qs)


def secret_context(record, key):
    return f"record:{record.pk}:{key}"


def stored(record, field):
    return (record.values or {}).get(field.key)


def holds_value(field):
    kind = ft.TYPES.get(field.type)
    return kind is not None and kind.storage == "value"


def value(record, field):
    """The Python value (core/fieldtypes.py); a secret field gives its stored form, never the plain text.
    Links and files have no value here (tables/links.py)."""
    if not holds_value(field):
        return None
    raw = stored(record, field)
    return raw if field.type == "secret" else ft.from_store(field, raw)


def display(record, field, localized=False):
    if not holds_value(field):
        return ""
    if field.type == "secret":
        return ft.MASKED if stored(record, field) else ""
    return ft.display(field, value(record, field), localized)


def form(record, field):
    if not holds_value(field):
        return ""
    if field.type == "secret":
        return ft.MASKED if stored(record, field) else ""
    return ft.form(field, value(record, field))


def reveal(record, field):
    raw = stored(record, field)
    return crypto.decrypt(raw["secret"], secret_context(record, field.key)) if isinstance(raw, dict) and "secret" in raw else ""


def may_name(field):
    """Fields that may give a record its title: plain values, and formulas of the record's own fields."""
    if field.type == "formula":
        from . import formula

        return not formula.uses_links(field)
    return field.type in TITLE_TYPES


def title_field(table, fields):
    by_key = {f.key: f for f in fields}
    chosen = by_key.get(table.title_field)
    if chosen is not None and chosen.type != "secret" and (chosen.type != "formula" or may_name(chosen)):
        return chosen
    return next((f for f in fields if f.type in TITLE_TYPES), None)


def _title_text(record, field):
    if field.type == "formula":
        from . import formula

        text, problem = formula.display(record, field)
        return "" if problem else text
    return str(display(record, field, localized=False))


def refresh(record, fields=None):
    """The cached title and search text, from the record's current values."""
    fields = fields if fields is not None else fields_of(record.table)
    named = title_field(record.table, fields)
    record.title = _title_text(record, named)[:300] if named else ""
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
    # Keys that are pages of their own under /einstellungen/tabellen/ are never given to a table.
    key = _unique_key(_slug(name, 20) or "tabelle", set(Table.objects.values_list("key", flat=True)) | {"vorlagen", "tmdb"}, 20)
    with transaction.atomic():
        table = Table.objects.create(key=key, name=name, order=Table.objects.count() * 10 + 10)
        first = Field.objects.create(table=table, key="f_name", label=_("Name"), type="text", order=10)
        table.title_field = first.key
        table.save()
    audit.log("table", f"Tabelle angelegt: {name}", request=request, target=table.pk)
    return table


def formula_config(table, data, field_key=None):
    """The stored formula (field keys), its result type and decimals, from the dialog; refuses what cannot work."""
    from . import formula

    if table is None:
        raise ValidationError(_("Unbekannte Tabelle."))
    typed = (data.get("expression") or "").strip()
    schema = formula.Schema(table)
    try:
        stored = formula.to_keys(typed, schema)
        result = formula.check(stored, schema, field_key)
    except formula.FormulaError as exc:
        raise ValidationError(_("Formel: %(problem)s") % {"problem": exc.message})
    config = {"expression": stored, "result": result if result in formula.FIELD_TYPES.values() or result in ("datetime",) else "text"}
    places = (data.get("decimals") or "").strip() if isinstance(data.get("decimals"), str) else data.get("decimals")
    if places not in (None, ""):
        try:
            places = int(places)
        except ValueError:
            raise ValidationError(_("Bitte die Zahl der Nachkommastellen angeben (0 bis 6)."))
        if not 0 <= places <= 6:
            raise ValidationError(_("Bitte die Zahl der Nachkommastellen angeben (0 bis 6)."))
        config["decimals"] = places
    return config


def clean_config(type_, data, table=None):
    """The type's settings from a form (number: decimals, unit, min, max; links and files: tables/links.py)."""
    if type_ in ("link", "file", "image"):
        from . import links

        return links.clean_config(type_, data)
    if type_ == "formula":
        return formula_config(table, data)
    if type_ == "rating":
        return rating_config(data)
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


def rating_config(data):
    """Stars (3 to 10) and whether half stars may be given."""
    try:
        top = int(data.get("max") or 5)
    except (TypeError, ValueError):
        top = 0
    if not 3 <= top <= 10:
        raise ValidationError(_("Bitte 3 bis 10 Sterne angeben."))
    config = {"max": top}
    if data.get("half", True) in (False, "0", "false"):
        config["half"] = False
    return config


def create_field(table, label, type_, options_text="", required=False, help_text="", in_grid=True, config=None, request=None):
    label = (label or "").strip()[:100]
    if not label:
        raise ValidationError(_("Bitte eine Bezeichnung eingeben."))
    if type_ not in dict(CREATE_TYPES):
        raise ValidationError(_("Unbekannter Feldtyp."))
    if type_ == "link" and not (config or {}).get("target"):
        raise ValidationError(_("Bitte angeben, worauf die Verknüpfung zeigt."))
    if type_ == "formula" and not (config or {}).get("expression"):
        raise ValidationError(_("Bitte eine Formel eingeben."))
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
    records = [r for r in Record.everything.filter(table_id=field.table_id) if (r.values or {}).get(field.key) not in (None, "", [])]
    options, config, stored = field.options, {}, {}
    if new_type == "rating" or field.type == "rating" and new_type == "number":
        # Numbers or text that are ratings; a rating becomes a number with one decimal place, as stored.
        config = {"decimals": 1} if new_type == "number" else {}
        spec = Field(type=new_type, options=[], config=config, label=field.label, labels=field.labels)
        unfit = []
        for record in records:
            raw = str(record.values[field.key]).strip()
            try:
                stored[record.pk] = ft.to_store(spec, ft.clean(spec, raw, check_required=False))
            except (ValidationError, ValueError, TypeError):
                unfit.append(raw)
        if unfit:
            raise ValidationError(_("%(n)s Werte passen nicht zum neuen Typ, etwa „%(examples)s“. Bitte zuerst korrigieren.")
                                  % {"n": len(unfit), "examples": "“, „".join(unfit[:3])})
    elif field.type == "text" and new_type in ("choice", "number", "date"):
        # Text from an import that turns out to be a choice, a number or a date: every value must fit.
        from transfer.parsers import parse_date

        texts = [str(r.values[field.key]).strip() for r in records]
        if new_type == "choice":
            options = [{"value": v[:100], "color": ""} for v in dict.fromkeys(t for t in texts if t)]
            if not options or len(options) > 50:
                raise ValidationError(_("Eine Auswahl braucht 1 bis 50 verschiedene Werte; hier sind es %(n)s.") % {"n": len(options)})
        if new_type == "number":
            from . import infer

            guess = infer.column(field.label, texts)
            config = guess["config"] if guess["type"] == "number" else {}
        spec = Field(type=new_type, options=options, config=config, label=field.label, labels=field.labels)
        unfit = []
        for record in records:
            raw = str(record.values[field.key]).strip()
            try:
                value = parse_date(raw) if new_type == "date" else ft.clean(spec, raw, check_required=False)
                stored[record.pk] = value.isoformat() if new_type == "date" else ft.to_store(spec, value)
            except (ValidationError, ValueError, TypeError):
                unfit.append(raw)
        if unfit:
            raise ValidationError(_("%(n)s Werte passen nicht zum neuen Typ, etwa „%(examples)s“. Bitte zuerst korrigieren.")
                                  % {"n": len(unfit), "examples": "“, „".join(unfit[:3])})
    with transaction.atomic():
        field.type = new_type
        field.options = options if ft.TYPES[new_type].options else []
        field.config = config
        field.save()
        for record in records:
            raw = record.values[field.key]
            if record.pk in stored:
                converted = stored[record.pk]
            elif new_type == "multichoice":
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
    field.unique = bool(data.get("unique")) and holds_value(field) and field.type not in ("bool", "secret", "multichoice", "textarea", "rating")
    field.in_grid = bool(data.get("in_grid"))
    if ft.TYPES.get(field.type) and ft.TYPES[field.type].options:
        field.options = _edited_options(field, request, scope)
        if not field.options:
            raise ValidationError(_("Bitte mindestens eine Option angeben."))
    if field.type in ("number", "rating"):
        field.config = clean_config(field.type, data)
    elif field.type in ("link", "file", "image"):
        field.config = _changed_link_config(field, data)
    elif field.type == "formula":
        field.config = formula_config(field.table, data, field.key)
    field.save()


def _changed_link_config(field, data):
    """A link keeps its target; it may allow several targets (or files), or go back to one while no record has more."""
    from .models import Attachment, Link

    config = dict(field.config or {})
    multiple = bool(data.get("multiple"))
    if config.get("multiple") and not multiple:
        rows = Link.objects.exclude(target_id=None) if field.type == "link" else Attachment.objects
        counts = rows.filter(record__table_id=field.table_id, field=field.key).values("record_id").order_by().annotate(n=Count("id"))
        if any(row["n"] > 1 for row in counts):
            raise ValidationError(_("Einige Datensätze haben hier mehrere Einträge. Entfernen Sie diese zuerst."))
    if multiple:
        config["multiple"] = True
    else:
        config.pop("multiple", None)
    if field.type == "link":
        reverse = (data.get("reverse") or "").strip()[:100]
        if reverse:
            config["reverse"] = reverse
        else:
            config.pop("reverse", None)
    return config


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
        for mask in table.masks.all():
            for version in mask.versions.all():
                version.delete()
            mask.delete()
        for field in Field.objects.filter(table=table):
            field.delete()
        table.delete()
    audit.log("table", f"Tabelle gelöscht: {name}", request=request)


def search(user, terms):
    """Global search (core/modules.py "search"): records of readable tables whose title or text match every term."""
    tables = {t.pk: t for t in readable(user)}
    if not tables or not terms:
        return []
    from core import fieldrules

    # A table with fields this role does not see is searched by title only: its search text holds them all.
    private = [pk for pk, t in tables.items() if fieldrules.hidden(user, fieldrules.table_entity(t))]
    records = Record.objects.filter(table_id__in=tables)
    for term in terms:
        records = records.filter(Q(search_text__contains=term.lower()) & ~Q(table_id__in=private) | Q(title__icontains=term, table_id__in=private))
    return [{"title": r.title or _("(ohne Titel)"), "sub": tables[r.table_id].display_name, "url": f"/tabellen/{tables[r.table_id].key}/{r.pk}/",
             "icon": tables[r.table_id].icon} for r in records.order_by("title")[:10]]


def nav_items(user):
    """The sidebar's tables: readable tables that show there, as (label, url, icon, active key, section)."""
    return [(t.display_name, f"/tabellen/{t.key}/", t.icon, f"table:{t.key}", t.section) for t in readable(user) if t.in_nav]


def by_section(items, section=lambda t: t.section):
    """Items grouped by their table's section, sections in the order of their first table, as the sidebar
    shows them: [(section, [items])], section "" for tables without one."""
    groups = {}
    for item in items:
        groups.setdefault(section(item) or "", []).append(item)
    return list(groups.items())


def rename_section(old, new, request=None):
    """Move every table of section `old` to section `new` (empty: back to "Tabellen"). Returns how many."""
    new = " ".join((new or "").split())[:60]
    moved = 0
    for table in Table.objects.filter(section=old):
        table.section = new
        table.save()
        moved += 1
    if moved:
        audit.log("table", f"Bereich umbenannt: {old} → {new or 'Tabellen'}", request=request)
    return moved


def sections():
    """The sidebar sections tables are in, in the order of their first table (empty: "Tabellen")."""
    return list(dict.fromkeys(Table.objects.filter(archived=False).exclude(section="").values_list("section", flat=True)))
