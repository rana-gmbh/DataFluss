import re
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import SchoolSettings
from devices.models import Asset, AssetCategory, Assignment
from masks import services as ms
from people.models import ClassGroup, Enrollment, Person, SchoolYear
from sync import journal
from vault.models import Credential, Platform

from .models import ImportSession
from .parsers import parse_bool, parse_date

UNDO_WINDOW = timedelta(hours=24)

SYNONYMS = {
    "vorname": "first_name", "nachname": "last_name", "name": "last_name", "e-mail des schülers": "email", "e-mail": "email", "email": "email",
    "e-mail büro": "email", "klasse": "klasse", "last of gerätenummer": "_device_number", "gerätenummer": "_device_number",
    "last of seriennummer": "_device_serial", "seriennummer": "_device_serial", "bild": "_photo_path", "archiviert": "archived",
    "geburtsdatum": "birth_date", "schuleintritt": "entry_date", "eintritt": "entry_date", "schulaustritt": "exit_date", "austritt": "exit_date",
    "schultyp": "school_type", "geburtsort": "birth_place", "geschlecht": "gender", "kategorie": "category", "kürzel": "abbreviation",
    "benutzername bibox": "login:bibox:username", "account name": "login:m365:username",
}


class ImportClosed(Exception):
    """The message is shown to the user."""


def targets(entity, user=None):
    """Everything a column can be mapped to; with a user, only fields the user's role may change."""
    from core import fieldrules

    options = [("", _("Nicht importieren"))]
    options += [(f.key, f.display_label) for f in ms.fields_for(entity) if f.type != "secret" and fieldrules.may_change(user, entity, f.key)]
    options += [("legacy_ninox_id", _("Ninox-ID")), ("_device_number", _("Gerätenummer")), ("_device_serial", _("Seriennummer")), ("_photo_path", _("Bildpfad (Ninox)"))]
    for platform in Platform.active_for(entity):
        for field in platform.fields:
            options.append((f"login:{platform.key}:{field['key']}", f"{platform.name}: {field['label']}"))
    return options


def auto_mapping(headers, entity):
    known = {key for key, _label in targets(entity)}
    mapping = {}
    for header in headers:
        target = SYNONYMS.get(header.strip().lower(), "")
        mapping[header] = target if target in known else ""
    return mapping


def _row_values(session, row):
    """An empty cell says nothing, so it never replaces what is already stored. A field the uploader's role
    may only read or not see is left out, whatever the mapping says."""
    from core import fieldrules

    values = {}
    for index, header in enumerate(session.headers):
        target = session.mapping.get(header)
        if target and not fieldrules.may_change(session.user, session.entity, target):
            continue
        if target and index < len(row) and row[index].strip():
            values[target] = row[index]
    return values


def _legacy_id(values):
    if values.get("legacy_ninox_id"):
        return values["legacy_ninox_id"].strip()
    match = re.match(r"(B\d+)/", values.get("_photo_path", ""))
    return match.group(1) if match else ""


def _find(values, entity, key):
    qs = Person.objects.filter(kind=entity)
    if key == "legacy_ninox_id":
        legacy = _legacy_id(values)
        return qs.filter(legacy_ninox_id=legacy).first() if legacy else None
    if key == "email":
        email = values.get("email", "").strip().lower().removeprefix("mailto:")
        return qs.filter(email=email).first() if email else None
    first, last = values.get("first_name", "").strip(), values.get("last_name", "").strip()
    if not first or not last:
        return None
    match = qs.filter(first_name__iexact=first, last_name__iexact=last)
    if values.get("birth_date"):
        try:
            match = match.filter(birth_date=parse_date(values["birth_date"]))
        except ValueError:
            pass
    return match.first()


def _clean(values, fields, warnings, errors):
    """Convert mapped text into stored values."""
    clean = {}
    for key, raw in values.items():
        if key.startswith(("_", "login:")) or key in ("legacy_ninox_id", "klasse"):
            continue
        fd = fields.get(key)
        if fd is None:
            continue
        raw = raw.strip()
        if fd.type == "email" and raw.lower().startswith("mailto:"):
            raw = raw[7:]
        try:
            if fd.type == "date":
                clean[key] = parse_date(raw)
            elif fd.type == "bool":
                clean[key] = parse_bool(raw)
            elif fd.type == "email":
                clean[key] = raw.lower()
                if raw and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw):
                    warnings.append(_("E-Mail ungültig: %(value)s") % {"value": raw})
            elif fd.type == "number":
                clean[key] = int(raw) if raw else None
            elif fd.type == "choice":
                value = _option_value(fd, raw)
                if value and value not in fd.option_values:
                    warnings.append(_("%(field)s: unbekannter Wert „%(value)s“ wird übernommen") % {"field": fd.display_label, "value": value})
                clean[key] = value
            else:
                clean[key] = raw
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))
    if "first_name" in fields and not values.get("first_name", "").strip():
        errors.append(_("Vorname fehlt"))
    if "last_name" in fields and not values.get("last_name", "").strip():
        errors.append(_("Nachname fehlt"))
    return clean


def _shown(value):
    if value in (None, ""):
        return _("leer")
    if isinstance(value, bool):
        return _("Ja") if value else _("Nein")
    return value.strftime("%d.%m.%Y") if hasattr(value, "strftime") else str(value)


def analyse(session):
    """Dry run: nothing is written."""
    fields = ms.field_map(session.entity)
    domain = SchoolSettings.load().email_domain
    report = {"create": 0, "update": 0, "same": 0, "error": 0, "warnings": 0, "rows": [], "new_classes": set()}
    existing_classes = set(ClassGroup.objects.values_list("name", flat=True))
    seen = set()
    for line, row in enumerate(session.rows, start=2):
        values = _row_values(session, row)
        warnings, errors = [], []
        clean = _clean(values, fields, warnings, errors)
        person = _find(values, session.entity, session.match_key)
        email = clean.get("email", "")
        if domain and email and not email.endswith("@" + domain):
            warnings.append(_("E-Mail außerhalb von %(domain)s: %(email)s") % {"domain": domain, "email": email})
        if email and values.get("email", "").strip().removeprefix("mailto:") != email:
            warnings.append(_("E-Mail wird kleingeschrieben"))
        serial = values.get("_device_serial", "").strip()
        if serial.isdigit() and len(serial) < 12:
            warnings.append(_("Seriennummer %(serial)s: führende Nullen fehlen möglicherweise") % {"serial": serial})
        klasse = values.get("klasse", "").strip()
        if klasse and klasse not in existing_classes:
            report["new_classes"].add(klasse)
        identity = (_legacy_id(values) or email or f"{values.get('first_name')}|{values.get('last_name')}|{values.get('birth_date')}")
        if identity in seen:
            warnings.append(_("Kommt in der Datei mehrfach vor"))
        seen.add(identity)
        changes = []
        if person:
            for key, value in clean.items():
                fd = fields[key]
                old = ms.get_value(person, fd)
                if str(old if old is not None else "") != str(value if value is not None else ""):
                    changes.append({"label": str(fd.display_label), "old": _shown(old), "new": _shown(value)})
            if klasse and (not person.current_class or person.current_class.name != klasse):
                changes.append({"label": _("Klasse"), "old": _shown(person.current_class and person.current_class.name), "new": klasse})
        if errors:
            action = "error"
        elif person is None:
            action = "create"
        elif changes:
            action = "update"
        else:
            action = "same"
        report[action] += 1
        report["warnings"] += len(warnings)
        if action != "same" or warnings:
            report["rows"].append({
                "line": line, "action": action, "name": f"{values.get('first_name', '')} {values.get('last_name', '')}".strip(),
                "changes": changes, "warnings": warnings, "errors": errors,
            })
    report["new_classes"] = sorted(report["new_classes"])
    return report


def _class_for(name, year, created):
    group = ClassGroup.objects.filter(name=name).filter(school_year=year).first() or ClassGroup.objects.filter(name=name, school_year__isnull=True).first()
    if group:
        return group
    digits = "".join(ch for ch in name if ch.isdigit())
    if name.lower().startswith("austritt"):
        group = ClassGroup.objects.create(name=name, kind=ClassGroup.AUSTRITT, school_year=None)
    elif "gruppe" in name.lower() or not digits:
        group = ClassGroup.objects.create(name=name, kind=ClassGroup.GRUPPE, school_year=year)
    else:
        group = ClassGroup.objects.create(name=name, kind=ClassGroup.KLASSE, level=int(digits), school_year=year)
    created.append(str(group.pk))
    return group


def _category_for(number):
    prefix = re.match(r"[A-Za-zÄÖÜäöü]+", number)
    if not prefix:
        return None
    for category in AssetCategory.objects.all():
        if category.prefix and category.prefix.lower() == prefix.group(0).lower():
            return category
    return None


def _claim(session, status):
    """Holds the row until the transaction ends, so a second click waits and then finds it finished."""
    if ImportSession.objects.select_for_update().values_list("status", flat=True).get(pk=session.pk) != status:
        raise ImportClosed(_("Dieser Import wurde bereits bearbeitet."))


@transaction.atomic
def commit(session):
    _claim(session, ImportSession.PREVIEWED)
    fields = ms.field_map(session.entity)
    year = SchoolYear.current()
    undo = {"people": [], "updates": {}, "custom": {}, "class": {}, "logins": {}, "assets": [], "assignments": [], "credentials": [], "classes": []}
    result = {"created": 0, "updated": 0, "skipped": 0}
    for row in session.rows:
        values = _row_values(session, row)
        warnings, errors = [], []
        clean = _clean(values, fields, warnings, errors)
        if errors:
            result["skipped"] += 1
            continue
        person = _find(values, session.entity, session.match_key)
        created = person is None
        if created:
            person = Person(kind=session.entity)
        else:
            undo["updates"].setdefault(str(person.pk), {k: str(getattr(person, k)) if getattr(person, k, None) is not None else None for k in clean if ms.PERSON_COLUMNS.__contains__(k)})
            undo["custom"].setdefault(str(person.pk), dict(person.custom))
            undo["class"].setdefault(str(person.pk), str(person.current_class_id or ""))
        custom = {}
        for key, value in clean.items():
            if fields[key].is_core:
                setattr(person, key, value)
            else:
                custom[key] = value.isoformat() if hasattr(value, "isoformat") else value
        person.custom = {**person.custom, **custom}
        person.legacy_ninox_id = _legacy_id(values) or person.legacy_ninox_id
        person.legacy_photo_path = values.get("_photo_path", person.legacy_photo_path)
        person.save()
        if created:
            undo["people"].append(str(person.pk))
            result["created"] += 1
        else:
            result["updated"] += 1
        klasse = values.get("klasse", "").strip()
        if klasse:
            person.move_to_class(_class_for(klasse, year, undo["classes"]), person.entry_date)
        number = values.get("_device_number", "").strip()
        if number:
            asset = Asset.objects.filter(number=number).first()
            if asset is None:
                asset = Asset.objects.create(number=number, serial=values.get("_device_serial", "").strip(), category=_category_for(number))
                undo["assets"].append(str(asset.pk))
            if not asset.assignments.filter(returned_at__isnull=True).exists():
                assignment = asset.issue(person=person, when=person.entry_date)
                undo["assignments"].append(str(assignment.pk))
        logins = {}
        for key, value in values.items():
            if key.startswith("login:") and value.strip():
                _prefix, platform_key, field = key.split(":", 2)
                logins.setdefault(platform_key, {})[field] = value.strip()
        for platform_key, data in logins.items():
            platform = Platform.objects.get(key=platform_key)
            existed = person.credentials.filter(platform=platform).exists()
            credential = Credential.get_for(platform, person=person)
            if existed:
                undo["logins"].setdefault(str(credential.pk), {"values": dict(credential.values), "secrets": dict(credential.secrets)})
            for field in platform.fields:
                if field["key"] in data:
                    if field.get("secret"):
                        credential.set_secret(field["key"], data[field["key"]])
                    else:
                        credential.values[field["key"]] = data[field["key"]]
            credential.save()
            if not existed:
                undo["credentials"].append(str(credential.pk))
    session.status = session.DONE
    session.undo = undo
    session.report = {"result": result}
    session.rows = []  # personal data does not stay in the import session
    session.save()
    return result


def _has_later_rows(model, pks, imported):
    """Anything that points at these records and did not come from the import. New sections count by themselves."""
    for relation in model._meta.related_objects:
        related = relation.related_model
        if related is Enrollment:
            continue
        rows = related._base_manager.filter(**{f"{relation.field.name}__in": pks})
        if rows.exclude(pk__in=imported.get(related, [])).exists():
            return True
    return False


def undo_blocker(session):
    """Why this import can no longer be taken back, or an empty string."""
    if timezone.now() - session.updated_at > UNDO_WINDOW:
        return _("Nur innerhalb von 24 Stunden möglich.")
    undo = session.undo
    imported = {Credential: undo.get("credentials", []), Assignment: undo.get("assignments", [])}
    people = undo.get("people", [])
    if Person.objects.filter(pk__in=[*people, *undo.get("updates", {})], updated_at__gt=session.updated_at).exists():
        return _("Personen aus diesem Import wurden seither geändert.")
    if Credential.objects.filter(pk__in=undo.get("logins", {}), updated_at__gt=session.updated_at).exists():
        return _("Zugänge aus diesem Import wurden seither geändert.")
    if _has_later_rows(Person, people, imported) or _has_later_rows(Asset, undo.get("assets", []), imported):
        return _("Zu diesem Import gibt es inzwischen weitere Einträge.")
    return ""


@transaction.atomic
def rollback(session):
    _claim(session, ImportSession.DONE)
    blocker = undo_blocker(session)
    if blocker:
        raise ImportClosed(_("Der Import wurde nicht zurückgenommen. %(reason)s") % {"reason": blocker})
    undo = session.undo
    journal.delete(Credential.objects.filter(pk__in=undo.get("credentials", [])))
    journal.delete(Assignment.objects.filter(pk__in=undo.get("assignments", [])))
    journal.delete(Asset.objects.filter(pk__in=undo.get("assets", [])))
    for person in Person.objects.filter(pk__in=undo.get("people", [])):
        person.delete()
    for pk, old in undo.get("updates", {}).items():
        person = Person.objects.filter(pk=pk).first()
        if person is None:
            continue
        for key, value in old.items():
            field = Person._meta.get_field(key)
            setattr(person, key, field.to_python(value) if value is not None else None)
        person.custom = undo.get("custom", {}).get(pk, person.custom)
        person.save()
        if pk in undo.get("class", {}):
            old_class = undo["class"][pk]
            person.move_to_class(ClassGroup.objects.filter(pk=old_class).first() if old_class else None)
    for pk, old in undo.get("logins", {}).items():
        credential = Credential.objects.filter(pk=pk).first()
        if credential:
            credential.values, credential.secrets = old["values"], old["secrets"]
            credential.save()
    journal.delete(ClassGroup.objects.filter(pk__in=undo.get("classes", []), members__isnull=True))
    session.status = session.UNDONE
    session.save()


def _option_value(fd, raw):
    """The stored value for a choice written in a file: by its value or its name in any language."""
    if not raw or raw in fd.option_values:
        return raw
    wanted = raw.casefold()
    for option in fd.options:
        names = [option["value"], option.get("label", ""), *(option.get("labels") or {}).values()]
        if any(name and name.casefold() == wanted for name in names):
            return option["value"]
    return raw
