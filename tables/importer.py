"""Importing a CSV or Excel file into an own table (transfer/targets.py, docs/TABLES-PLAN.md §7).

The same steps as for pupils: map the columns, see a dry run, import, and take it back within 24 hours.
Cells are cleaned by the field types (core/fieldtypes.py); link columns name their targets by title
(records), name or e-mail (people) or number (devices), several separated by ";" or a line break.
"""
import re
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from core import fieldtypes as ft
from transfer.models import ImportSession
from transfer.parsers import parse_bool, parse_date

from . import links, services
from .models import Link, Record

UNDO_WINDOW = timedelta(hours=24)
ALWAYS_NEW = "_new"


class ImportClosed(Exception):
    """The message is shown to the user."""


def _option_value(field, raw):
    """A choice written in a file: by its stored value or its name in any language."""
    if not raw or raw in field.option_values:
        return raw
    wanted = raw.casefold()
    for option in field.options:
        names = [option["value"], option.get("label", ""), *(option.get("labels") or {}).values()]
        if any(name and name.casefold() == wanted for name in names):
            return option["value"]
    return raw


def _parts(raw):
    return [part.strip() for part in re.split(r"[;\n]", raw) if part.strip()]


class TableTarget:
    def __init__(self, table):
        self.table = table
        self.entity = f"table:{table.key}"
        self._fields = None

    @property
    def label(self):
        return self.table.display_name

    def may_import(self, user):
        return user.can("data.import") and services.may_edit(user, self.table)

    @property
    def fields(self):
        if self._fields is None:
            self._fields = {f.key: f for f in services.fields_of(self.table)}
        return self._fields

    def importable(self):
        return [f for f in self.fields.values() if f.type not in ("secret", "formula", "file", "image")]

    def targets(self, user=None):
        from core import fieldrules

        options = [("", _("Nicht importieren"))]
        for field in self.importable():
            if not fieldrules.may_change(user, self.entity, field.key):
                continue  # the role may only read this field, or does not see it
            label = _("%(field)s (Verknüpfung über den Namen)") % {"field": field.display_label} if field.type == "link" else field.display_label
            options.append((field.key, label))
        return options

    def auto_mapping(self, headers):
        by_name = {}
        for field in self.importable():
            for name in (field.display_label, field.label, field.key):
                by_name.setdefault(str(name).strip().casefold(), field.key)
        return {header: by_name.get(header.strip().casefold(), "") for header in headers}

    def match_keys(self):
        named = services.title_field(self.table, list(self.fields.values()))
        # A unique field recognises a record for sure; the title only when no other one is unique.
        unique = [(f.key, f.display_label) for f in self.importable() if f.unique and f.type != "link"]
        title = [(named.key, named.display_label)] if named and named.type != "link" and named.key not in dict(unique) else []
        return unique + title + [(ALWAYS_NEW, _("Immer neue Datensätze anlegen"))]

    def default_match(self):
        return self.match_keys()[0][0]

    def match_label(self):
        return _("Bestehende Datensätze erkennen über")

    # ------------------------------------------------------------ reading rows

    def _row_values(self, session, row):
        """An empty cell says nothing, so it never replaces what is stored. Fields the uploader's role may only
        read or not see are left out."""
        from core import fieldrules

        values = {}
        for index, header in enumerate(session.headers):
            key = session.mapping.get(header)
            if key in self.fields and not fieldrules.may_change(session.user, self.entity, key):
                continue
            if key in self.fields and index < len(row) and str(row[index]).strip():
                values[key] = str(row[index]).strip()
        return values

    def _clean(self, values, warnings, errors):
        """{key: stored value} for value fields and {key: [target ids]} for link fields."""
        clean, linked = {}, {}
        for key, raw in values.items():
            field = self.fields[key]
            try:
                if field.type == "link":
                    linked[key] = self._targets(field, raw, warnings)
                    continue
                if field.type == "date":
                    raw = parse_date(raw).isoformat()
                elif field.type == "datetime":
                    raw = ft.parse_datetime(raw).isoformat()
                elif field.type == "bool":
                    raw = "1" if parse_bool(raw) else ""
                elif field.type == "choice":
                    raw = _option_value(field, raw)
                elif field.type == "multichoice":
                    raw = [_option_value(field, part) for part in re.split(r"[;,\n]", raw) if part.strip()]
                elif field.type == "email" and raw.lower().startswith("mailto:"):
                    raw = raw[7:]
                value = ft.clean(field, raw, check_required=False)
                clean[key] = ft.to_store(field, value) if field.type != "bool" else value
            except ValidationError as exc:
                errors.extend(f"{field.display_label}: {m}" if field.display_label not in m else m for m in exc.messages)
            except (ValueError, TypeError):
                errors.append(_("%(field)s: „%(value)s“ passt nicht.") % {"field": field.display_label, "value": raw})
        return clean, linked

    def _targets(self, field, raw, warnings):
        names = _parts(raw) if (field.config or {}).get("multiple") else [raw.strip()]
        found = []
        for name in names:
            target = self._find_target(field, name)
            if target is None:
                warnings.append(_("%(field)s: „%(value)s“ nicht gefunden") % {"field": field.display_label, "value": name})
            else:
                found.append(target)
        return list(dict.fromkeys(found))

    def _find_target(self, field, name):
        kind = links.kind_of(field)
        if kind == Link.RECORD:
            table = links.target_table(field)
            if table is None:
                return None
            match = Record.objects.filter(table=table, title__iexact=name).values_list("pk", flat=True).first()
            if match is None:
                try:
                    match = Record.objects.filter(table=table, pk=uuid.UUID(name)).values_list("pk", flat=True).first()
                except ValueError:
                    pass
            return match
        if kind in (Link.PUPIL, Link.STAFF):
            from people.models import Person

            people = Person.objects.filter(kind=kind)
            if "@" in name:
                return people.filter(email__iexact=name).values_list("pk", flat=True).first()
            if "," in name:
                last, first = [p.strip() for p in name.split(",", 1)]
            else:
                first, _space, last = name.rpartition(" ")
            matches = list(people.filter(Q(first_name__iexact=first, last_name__iexact=last)).values_list("pk", flat=True)[:2])
            return matches[0] if len(matches) == 1 else None
        if kind == Link.ASSET:
            from devices.models import Asset

            return Asset.objects.filter(number__iexact=name).values_list("pk", flat=True).first()
        return None

    def _find(self, session, clean):
        key = session.match_key
        if key == ALWAYS_NEW or key not in self.fields or clean.get(key) in (None, "", []):
            return None
        return Record.objects.filter(table=self.table, **{f"values__{key}": clean[key]}).first()

    def _shown(self, field, raw):
        if raw in (None, "", []):
            return _("leer")
        return str(ft.display(field, ft.from_store(field, raw), localized=True))

    def _new_options(self, session):
        """Values of choice columns that are not options yet. Someone who designs tables gets them added (named
        in the dry run, saved with the import); for everyone else they stay errors. [(field, value)]"""
        user = session.user
        if user is None or not user.can("tables.design"):
            return []
        added = []
        for index, header in enumerate(session.headers):
            field = self.fields.get(session.mapping.get(header))
            if field is None or field.type not in ("choice", "multichoice"):
                continue
            for row in session.rows:
                raw = str(row[index]).strip() if index < len(row) else ""
                for part in (_parts(raw.replace(",", ";")) if field.type == "multichoice" else [raw]):
                    if part and len(field.options) < 200 and _option_value(field, part) == part and part not in field.option_values:
                        field.options = [*field.options, {"value": part[:100], "color": ""}]
                        added.append((field, part[:100]))
        return added

    # ------------------------------------------------------------ steps

    def analyse(self, session):
        """Dry run: nothing is written."""
        report = {"create": 0, "update": 0, "same": 0, "error": 0, "warnings": 0, "rows": [], "new_classes": []}
        report["new_options"] = [f"{field.display_label}: {value}" for field, value in self._new_options(session)]
        named = services.title_field(self.table, list(self.fields.values()))
        seen = set()
        for line, row in enumerate(session.rows, start=2):
            values = self._row_values(session, row)
            warnings, errors = [], []
            clean, linked = self._clean(values, warnings, errors)
            record = self._find(session, clean)
            identity = clean.get(session.match_key)
            if identity not in (None, "") and identity in seen:
                warnings.append(_("Kommt in der Datei mehrfach vor"))
            seen.add(identity)
            if record is None:
                missing = [f.display_label for f in self.fields.values() if f.required and f.key not in clean and f.key not in linked and f.type != "formula"]
                errors.extend(_("%(field)s fehlt") % {"field": name} for name in missing)
            changes = []
            if record is not None:
                for key, value in clean.items():
                    old = (record.values or {}).get(key)
                    if str(old if old is not None else "") != str(value if value is not None else ""):
                        changes.append({"label": str(self.fields[key].display_label), "old": self._shown(self.fields[key], old), "new": self._shown(self.fields[key], value)})
                for key, ids in linked.items():
                    if set(links.targets(record, self.fields[key])) != set(ids):
                        changes.append({"label": str(self.fields[key].display_label), "old": "", "new": values[key]})
            action = "error" if errors else "create" if record is None else "update" if changes else "same"
            report[action] += 1
            report["warnings"] += len(warnings)
            if action != "same" or warnings:
                name = values.get(named.key, "") if named else ""
                report["rows"].append({"line": line, "action": action, "name": name or (record.title if record else ""),
                                       "changes": changes, "warnings": warnings, "errors": errors})
        return report

    def _claim(self, session, status):
        if ImportSession.objects.select_for_update().values_list("status", flat=True).get(pk=session.pk) != status:
            raise ImportClosed(_("Dieser Import wurde bereits bearbeitet."))

    def commit(self, session, user=None):
        from transfer import importer as person_importer

        with transaction.atomic():
            try:
                self._claim(session, ImportSession.PREVIEWED)
            except ImportClosed as exc:
                raise person_importer.ImportClosed(str(exc))
            undo = {"records": [], "values": {}, "links": {}}
            result = {"created": 0, "updated": 0, "skipped": 0}
            for field in {field for field, _value in self._new_options(session)}:
                field.save()
            fields = list(self.fields.values())
            for row in session.rows:
                warnings, errors = [], []
                values = self._row_values(session, row)
                clean, linked = self._clean(values, warnings, errors)
                record = self._find(session, clean)
                if record is None and any(f.required and f.key not in clean and f.key not in linked and f.type != "formula" for f in fields):
                    errors.append("required")
                if errors:
                    result["skipped"] += 1
                    continue
                try:
                    with transaction.atomic():
                        created = record is None
                        if created:
                            record = Record(table=self.table)
                        else:
                            undo["values"].setdefault(str(record.pk), dict(record.values or {}))
                            undo["links"].setdefault(str(record.pk), {k: [str(t) for t in links.targets(record, self.fields[k])] for k in linked})
                        for key, value in clean.items():
                            field = self.fields[key]
                            if field.unique and services._taken(record, field, value):
                                raise ValidationError(_("%(field)s: Diesen Wert hat schon ein anderer Datensatz.") % {"field": field.display_label})
                        record.values = {**(record.values or {}), **{k: v for k, v in clean.items() if v not in (None, "", [])}}
                        services.refresh(record, fields)
                        record.save()
                        for key, ids in linked.items():
                            links.set_targets(record, self.fields[key], [str(i) for i in ids])
                        if linked:
                            services.refresh(record, fields)
                            record.save()
                except ValidationError:
                    result["skipped"] += 1
                    continue
                if created:
                    undo["records"].append(str(record.pk))
                    result["created"] += 1
                else:
                    result["updated"] += 1
            session.status = session.DONE
            session.undo = undo
            session.report = {"result": result}
            session.rows = []  # the file's data does not stay in the import session
            session.save()
        return result

    def undo_blocker(self, session):
        if timezone.now() - session.updated_at > UNDO_WINDOW:
            return _("Nur innerhalb von 24 Stunden möglich.")
        undo = session.undo or {}
        touched = [*undo.get("records", []), *undo.get("values", {})]
        if Record.everything.filter(pk__in=touched, updated_at__gt=session.updated_at).exists():
            return _("Datensätze aus diesem Import wurden seither geändert.")
        if Link.objects.filter(record_id__in=touched, updated_at__gt=session.updated_at).exists():
            return _("Datensätze aus diesem Import wurden seither geändert.")
        return ""

    def rollback(self, session):
        from transfer import importer as person_importer

        with transaction.atomic():
            try:
                self._claim(session, ImportSession.DONE)
            except ImportClosed as exc:
                raise person_importer.ImportClosed(str(exc))
            blocker = self.undo_blocker(session)
            if blocker:
                raise person_importer.ImportClosed(_("Der Import wurde nicht zurückgenommen. %(reason)s") % {"reason": blocker})
            undo = session.undo or {}
            for record in Record.everything.filter(pk__in=undo.get("records", [])):
                for link in Link.objects.filter(record=record):
                    link.delete()
                record.delete()
            fields = list(self.fields.values())
            for pk, old in undo.get("values", {}).items():
                record = Record.everything.filter(pk=pk).first()
                if record is None:
                    continue
                for key, ids in undo.get("links", {}).get(pk, {}).items():
                    if key in self.fields:
                        links.set_targets(record, self.fields[key], ids)
                record.values = old
                services.refresh(record, fields)
                record.save()
            session.status = session.UNDONE
            session.save()
