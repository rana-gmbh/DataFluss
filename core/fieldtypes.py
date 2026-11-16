"""Field types shared by the fields of people (masks) and of own tables (docs/TABLES-PLAN.md §3).

A field is described by a *spec*: any object with `type`, `required`, `display_label`, `option_values`,
`option_label(value)` and, optionally, `config` (a dict: decimals, unit, min, max, link target, …).
masks.FieldDefinition is one; tables.Field will be another.

Values come in three forms:
- the Python value (`clean` returns it; `display` and `form` take it): date, datetime, int or Decimal, …
- the stored value in a JSON column (`to_store` / `from_store`): ISO text for dates, decimal text for
  numbers with decimals, so that sync compares and merges plain JSON
- text: `display` for screens, prints and exports; `form` for an input's value

Person keeps its own storage (some fields are columns, the rest ISO text in `custom`) and passes
values to `display` and `form` as it always did, so nothing changes for people.
"""
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.utils import formats, timezone
from django.utils.translation import gettext as _, gettext_lazy

MASKED = "••••••••"


@dataclass(frozen=True)
class FieldType:
    key: str
    label: str
    grid: str = "text"          # column type of static/js/grid.js
    storage: str = "value"      # value (in the record), links, files, or computed (never stored)
    people: bool = True         # offered for fields of pupils and staff
    tables: bool = True         # offered for fields of own tables
    options: bool = False       # has a list of choices


TYPES = {t.key: t for t in (
    FieldType("text", gettext_lazy("Text")),
    FieldType("textarea", gettext_lazy("Text (mehrzeilig)")),
    FieldType("number", gettext_lazy("Zahl"), grid="number"),
    FieldType("bool", gettext_lazy("Ja/Nein"), grid="bool"),
    FieldType("choice", gettext_lazy("Auswahl"), grid="choice", options=True),
    FieldType("multichoice", gettext_lazy("Mehrfachauswahl"), grid="multichoice", options=True),
    FieldType("date", gettext_lazy("Datum"), grid="date"),
    FieldType("datetime", gettext_lazy("Datum und Uhrzeit"), grid="datetime", people=False),
    FieldType("email", gettext_lazy("E-Mail")),
    FieldType("phone", gettext_lazy("Telefon")),
    FieldType("url", gettext_lazy("Link")),
    FieldType("secret", gettext_lazy("Geheim (verschlüsselt)")),
    FieldType("class", gettext_lazy("Klasse"), grid="class", tables=False),
    FieldType("link", gettext_lazy("Verknüpfung"), grid="link", storage="links", people=False),
    FieldType("file", gettext_lazy("Datei"), grid="file", storage="files", people=False),
    FieldType("image", gettext_lazy("Bild"), grid="image", storage="files", people=False),
    FieldType("formula", gettext_lazy("Berechnung"), grid="formula", storage="computed", people=False),
    # The other side of a link of another table (tables/links.py backlinks): it shows and changes the links
    # that point here, without storing anything of its own. Made together with its link, never chosen.
    FieldType("backlink", gettext_lazy("Gegenseite einer Verknüpfung"), grid="link", storage="backlinks", people=False),
)}


def choices(for_tables=True):
    """[(key, label)] of the types a person may choose for a new field of a table or of people."""
    return [(t.key, t.label) for t in TYPES.values() if (t.tables if for_tables else t.people)]


def config(spec):
    return getattr(spec, "config", None) or {}


def decimals(spec):
    try:
        return max(0, min(int(config(spec).get("decimals") or 0), 6))
    except (TypeError, ValueError):
        return 0


def parse_date(text):
    text = (text or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    raise ValidationError(_("Bitte ein gültiges Datum eingeben, z. B. 24.12.2015."))


def parse_datetime(text):
    """A moment in the database's time zone, from an input or ISO text."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        moment = None
        for pattern in ("%d.%m.%Y %H:%M", "%d.%m.%Y, %H:%M", "%d.%m.%y %H:%M"):
            try:
                moment = datetime.strptime(text, pattern)
                break
            except ValueError:
                pass
    if moment is None:
        raise ValidationError(_("Bitte Datum und Uhrzeit eingeben, z. B. 24.12.2015 18:30."))
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return moment


def normalize_number(text):
    """A number as typed or found in a file, as Python reads it: "1.234,5" and "1234,5" (German),
    "1,234.5" (English). One comma or one point is the decimal mark; several are grouping."""
    text = re.sub(r"[\s\u00a0\u202f']", "", str(text))
    if "," in text and "." in text:
        return text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    if "," in text:
        return text.replace(",", ".") if text.count(",") == 1 else text.replace(",", "")
    if text.count(".") > 1:
        return text.replace(".", "")
    return text


def _number(spec, raw):
    places = decimals(spec)
    text = str(raw).strip()
    unit = config(spec).get("unit")
    if unit and text.endswith(unit):  # "12,50 €" from a file
        text = text[: -len(unit)]
    elif unit and text.startswith(unit):
        text = text[len(unit):]
    message = _("%(field)s: Bitte eine ganze Zahl eingeben.") if places == 0 else _("%(field)s: Bitte eine Zahl eingeben.")
    try:
        value = Decimal(normalize_number(text))
        if not value.is_finite():
            raise InvalidOperation
        if places == 0:
            if value != value.to_integral_value():
                raise InvalidOperation
            value = int(value)
        else:
            value = value.quantize(Decimal(1).scaleb(-places))
    except InvalidOperation:
        raise ValidationError(message % {"field": spec.display_label})
    low, high = config(spec).get("min"), config(spec).get("max")
    if low not in (None, "") and value < Decimal(str(low)):
        raise ValidationError(_("%(field)s: mindestens %(n)s.") % {"field": spec.display_label, "n": low})
    if high not in (None, "") and value > Decimal(str(high)):
        raise ValidationError(_("%(field)s: höchstens %(n)s.") % {"field": spec.display_label, "n": high})
    return value


def _ids(spec, raw):
    values = raw if isinstance(raw, list) else [raw]
    try:
        ids = [str(uuid.UUID(str(v))) for v in values if v not in (None, "")]
    except ValueError:
        raise ValidationError(_("%(field)s: Unbekannter Datensatz.") % {"field": spec.display_label})
    if len(ids) > 1 and not config(spec).get("multiple"):
        raise ValidationError(_("%(field)s: Hier passt nur ein Datensatz.") % {"field": spec.display_label})
    return list(dict.fromkeys(ids))


def clean(spec, raw, check_required=True):
    """Turn form input into the Python value, or raise ValidationError. Without check_required an empty
    value passes even for a required field (sync and import judge the stored value, not a form)."""
    if isinstance(raw, str):
        raw = raw.strip()
    empty = raw in (None, "", [])
    kind = spec.type
    if kind == "formula":
        raise ValidationError(_("%(field)s wird berechnet und kann nicht eingegeben werden.") % {"field": spec.display_label})
    if kind in ("file", "image"):
        raise ValidationError(_("%(field)s: Dateien werden hochgeladen, nicht eingegeben.") % {"field": spec.display_label})
    if check_required and spec.required and empty:
        raise ValidationError(_("%(field)s ist ein Pflichtfeld.") % {"field": spec.display_label})
    if kind == "bool":
        return str(raw).lower() in ("1", "true", "on", "ja", "yes")
    if empty:
        return [] if kind in ("multichoice", "link") else None if kind in ("date", "datetime", "number") else ""
    if kind == "date":
        return parse_date(raw)
    if kind == "datetime":
        return parse_datetime(raw)
    if kind == "number":
        return _number(spec, raw)
    if kind == "email":
        value = str(raw).lower()
        try:
            validate_email(value)
        except ValidationError:
            raise ValidationError(_("Bitte eine gültige E-Mail-Adresse eingeben."))
        return value
    if kind == "url":
        URLValidator(message=_("Bitte einen gültigen Link eingeben."))(raw)
        return raw
    if kind == "phone":
        if not re.fullmatch(r"[0-9+()\-./ ]{3,40}", raw):
            raise ValidationError(_("Bitte eine gültige Telefonnummer eingeben."))
        return raw
    if kind == "choice":
        if raw not in spec.option_values:
            raise ValidationError(_("%(field)s: Unbekannte Auswahl.") % {"field": spec.display_label})
        return raw
    if kind == "multichoice":
        values = raw if isinstance(raw, list) else [raw]
        unknown = [v for v in values if v not in spec.option_values]
        if unknown:
            raise ValidationError(_("%(field)s: Unbekannte Auswahl.") % {"field": spec.display_label})
        return values
    if kind == "link":
        return _ids(spec, raw)
    return raw


def check_stored(spec, raw):
    """Raise ValidationError unless `raw` is a stored value that `clean` and `to_store` could have made:
    the server judges what a device sends this way, as a browser save would be judged."""
    kind = TYPES.get(spec.type)
    if kind is None or kind.storage != "value":
        raise ValidationError(_("%(field)s nimmt keine Werte auf.") % {"field": spec.display_label})
    if raw in (None, "", []):
        return
    if spec.type == "secret":
        if not (isinstance(raw, dict) and set(raw) == {"secret"} and isinstance(raw["secret"], str)):
            raise ValidationError(_("%(field)s: Ungültiger Wert.") % {"field": spec.display_label})
        return
    if spec.type == "bool":
        if not isinstance(raw, bool):
            raise ValidationError(_("%(field)s: Ungültiger Wert.") % {"field": spec.display_label})
        return
    if spec.type in ("date", "datetime", "number"):
        value = from_store(spec, raw)
        if value is raw and isinstance(raw, str):  # did not parse
            raise ValidationError(_("%(field)s: Ungültiger Wert.") % {"field": spec.display_label})
        if spec.type == "number" and _number(spec, format(value, "f") if isinstance(value, Decimal) else str(value)) != value:
            raise ValidationError(_("%(field)s: Ungültiger Wert.") % {"field": spec.display_label})
        if spec.type == "datetime" and timezone.is_naive(value):
            raise ValidationError(_("%(field)s: Ungültiger Wert.") % {"field": spec.display_label})
        return
    if spec.type == "multichoice" and not (isinstance(raw, list) and all(isinstance(v, str) for v in raw)):
        raise ValidationError(_("%(field)s: Ungültiger Wert.") % {"field": spec.display_label})
    if spec.type != "multichoice" and not isinstance(raw, str):
        raise ValidationError(_("%(field)s: Ungültiger Wert.") % {"field": spec.display_label})
    if to_store(spec, clean(spec, raw, check_required=False)) != raw:
        raise ValidationError(_("%(field)s: Ungültiger Wert.") % {"field": spec.display_label})


def to_store(spec, value):
    """The Python value as plain JSON for a record's values."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def from_store(spec, raw):
    """The stored JSON value as the Python value. Anything that no longer fits the field (it changed type,
    or a computer sent an old value) comes back unchanged, so nothing is lost and display still shows it."""
    if raw in (None, ""):
        return None if spec.type in ("date", "datetime", "number") else raw
    try:
        if spec.type == "date" and isinstance(raw, str):
            return date.fromisoformat(raw)
        if spec.type == "datetime" and isinstance(raw, str):
            return datetime.fromisoformat(raw)
        if spec.type == "number" and isinstance(raw, (str, int, float)) and not isinstance(raw, bool):
            return Decimal(str(raw)) if decimals(spec) else int(raw)
    except (ValueError, InvalidOperation):
        return raw
    return raw


def form(spec, value):
    """The value for a form input."""
    if spec.type == "secret":
        return MASKED if value else ""
    if value is None:
        return "" if spec.type not in ("multichoice", "link") else []
    if isinstance(value, datetime):
        return timezone.localtime(value).strftime("%Y-%m-%dT%H:%M") if timezone.is_aware(value) else value.strftime("%Y-%m-%dT%H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def _with_unit(spec, text):
    unit = config(spec).get("unit")
    return f"{text} {unit}" if unit else text


def display(spec, value, localized=False):
    """The value as text. Rules and mask conditions compare the stored, language-free text; screens pass
    localized=True for Ja/Nein and choices in the language on screen."""
    if spec.type == "secret":
        return MASKED if value else ""
    if value is None:
        return ""
    if isinstance(value, bool):
        if localized:
            return _("Ja") if value else _("Nein")
        return "Ja" if value else "Nein"
    if isinstance(value, datetime):
        moment = timezone.localtime(value) if timezone.is_aware(value) else value
        return moment.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, Decimal):
        return _with_unit(spec, formats.number_format(value, decimals(spec), use_l10n=localized, force_grouping=localized))
    if isinstance(value, int) and config(spec).get("unit"):
        return _with_unit(spec, value)
    if localized and spec.type in ("choice", "multichoice"):
        values = value if isinstance(value, list) else [value]
        return ", ".join(str(spec.option_label(v)) for v in values)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return value
