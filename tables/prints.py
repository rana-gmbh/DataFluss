"""Printing the records of an own table (prints/records.py module_source, docs/TABLES-PLAN.md §7).

A table is the print source "table:<key>". A record reaches a design as every other source's record:
`fields` (text as shown on screen), `labels`, `photo` (the first image field). Its link fields, and the
records of other tables linking to it, become `lists` for the list element, one per link.
What a record shows follows the rights of the person printing (core.context_user), as on screen.
"""
import base64
import uuid

from django.utils.translation import gettext as _

from core import fieldtypes as ft
from core.context_user import get_current_user

from . import formula, links, services
from .models import Attachment, Record, Table

SAMPLES = {"text": "Beispieltext", "textarea": "Ein längerer Beispieltext\nüber zwei Zeilen.", "number": "12,50", "bool": "Ja",
           "date": "24.12.2026", "datetime": "24.12.2026 18:30", "email": "name@schule.example", "phone": "+49 30 1234567",
           "url": "https://schule.example", "choice": "Auswahl", "multichoice": "Erste, Zweite", "link": "Verknüpfter Datensatz",
           "file": "Dokument.pdf", "image": "", "formula": "42"}


def starter_tree(table):
    """What a new printout of a table starts with, instead of an empty page: the title (and the first image
    as photo) in the head, then every field as name and value in two columns, long texts below."""
    fields = [f for f in services.fields_of(table) if f.type != "secret"]
    named = services.title_field(table, fields)
    photo = next((f for f in fields if f.type == "image"), None)
    short = [f for f in fields if f.type not in ("textarea", "file", "image") and not (named and f.key == named.key)]
    long = [f for f in fields if f.type == "textarea"]
    head = ([{"w": 2, "items": [{"type": "photo", "w_mm": 24, "h_mm": 33.6}]}] if photo else []) + [
        {"w": 8 if photo else 10, "items": [{"type": "name", "size": 17}], "valign": "center"},
        {"w": 2, "items": [{"type": "logo", "w_mm": 22}], "valign": "center"}]
    half = (len(short) + 1) // 2
    rows = [{"cols": head}]
    if short:
        rows.append({"cols": [{"w": 6, "items": [{"type": "field", "key": f.key, "hide_empty": True} for f in short[:half]]},
                              {"w": 6, "items": [{"type": "field", "key": f.key, "hide_empty": True} for f in short[half:]]}]})
    for field in long:
        rows.append({"cols": [{"w": 12, "items": [{"type": "field", "key": field.key, "hide_empty": True}]}]})
    return {"page": {"preset": "a4"}, "rows": rows}


def source_for(source):
    table = Table.objects.filter(key=source.removeprefix("table:")).first()
    return TableSource(table) if table else None


class TableSource:
    def __init__(self, table):
        self.table = table

    def may_print(self, user):
        return user.can("print.run") and services.may_read(user, self.table)

    def fields(self):
        return [f for f in services.fields_of(self.table) if f.type != "secret"]

    def field_catalog(self):
        return [{"key": "titel", "label": _("Titel des Datensatzes")}] + [{"key": f.key, "label": str(f.display_label)} for f in self.fields()]

    def lists(self):
        """The lists a list element may show: the table's link fields to other tables, and the tables linking here."""
        out = []
        for field in self.fields():
            if field.type == "link":
                target = links.target_table(field)
                columns = [{"key": "titel", "title": _("Titel")}]
                if target is not None:
                    columns += [{"key": f.key, "title": str(f.display_label)} for f in services.fields_of(target) if f.type != "secret"]
                out.append({"key": field.key, "label": str(field.display_label), "columns": columns})
        schema = formula.Schema(self.table)
        for key, (source, link) in schema.reverse.items():
            columns = [{"key": "titel", "title": _("Titel")}] + [{"key": f.key, "title": str(f.display_label)} for f in services.fields_of(source) if f.type != "secret"]
            out.append({"key": key, "label": schema.reverse_name(key), "columns": columns})
        return out

    @property
    def list_columns(self):
        first = self.lists()[:1]
        return [(c["key"], c["title"]) for c in first[0]["columns"]] if first else []

    def all_ids(self):
        return [str(pk) for pk in Record.objects.filter(table=self.table).order_by("title").values_list("pk", flat=True)]

    def load_records(self, ids, class_id=None):
        user = get_current_user()
        if user is not None and not services.may_read(user, self.table):
            return []
        wanted = []
        for value in ids:
            try:
                wanted.append(uuid.UUID(str(value)))
            except ValueError:
                continue
        found = {r.pk: r for r in Record.objects.filter(table=self.table, pk__in=wanted)}
        records = [found[i] for i in wanted if i in found] if wanted else []
        for record in records:
            record.table = self.table
        context = formula.Context(user)
        context.prefetch(records)
        fields = self.fields()
        lists = self.lists()
        return [self._record(record, fields, lists, context, user) for record in records]

    def _text(self, record, field, context, user):
        if field.type == "formula":
            return formula.display(record, field, context)[0]
        if field.type == "link":
            return links.display(record, field, user)
        if field.type in ("file", "image"):
            return ", ".join(a.name for a in links.files(record, field))
        return str(services.display(record, field, localized=True))

    def _photo(self, record, fields):
        for field in fields:
            if field.type == "image":
                attachment = Attachment.objects.filter(record=record, field=field.key).first()
                if attachment and attachment.content_type in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                    try:
                        with attachment.file.open("rb") as handle:
                            data = handle.read()
                    except OSError:
                        continue
                    return f"data:{attachment.content_type};base64,{base64.b64encode(data).decode()}"
        return ""

    def _rows(self, key, record, context, user):
        """The rows of one list: linked records (a link field) or records linking here (a reverse key)."""
        if key.startswith("@"):
            schema = context.schema(self.table)
            if key not in schema.reverse:
                return []
            source, link = schema.reverse[key]
            if user is not None and not services.may_read(user, source):
                return []
            others = [r for r in context.linking(source.key, link.key, record)]
            table = source
        else:
            field = next((f for f in self.fields() if f.key == key and f.type == "link"), None)
            table = links.target_table(field) if field else None
            if table is None or (user is not None and not services.may_read(user, table)):
                return []
            others = context.fetch(context.targets(record, key), table)
        fields = [f for f in services.fields_of(table) if f.type != "secret"]
        rows = []
        for other in others:
            other.table = table
            row = {"titel": other.title}
            for field in fields:
                row[field.key] = self._text(other, field, context, user)
            rows.append(row)
        return rows

    def _record(self, record, fields, lists, context, user):
        from prints.records import common_fields

        values = {**common_fields(), "titel": record.title, "name": record.title}
        for field in fields:
            values[field.key] = self._text(record, field, context, user)
        return {
            "id": str(record.pk), "title": record.title, "fields": values,
            "labels": {"titel": _("Titel"), **{f.key: str(f.display_label) for f in fields}}, "photo": self._photo(record, fields),
            "logins": [], "devices": [], "cases": [], "members": [],
            "lists": {item["key"]: self._rows(item["key"], record, context, user) for item in lists},
        }

    def sample_record(self):
        from prints.records import common_fields

        fields = self.fields()
        values = {**common_fields(), "titel": _("Beispieldatensatz"), "name": _("Beispieldatensatz")}
        for field in fields:
            sample = SAMPLES.get(field.type, "")
            if field.type == "choice" and field.option_values:
                sample = str(field.option_label(field.option_values[0]))
            if field.type == "number":
                sample = str(ft.display(field, ft.from_store(field, "12.5"), localized=True))
            values[field.key] = sample
        lists = {item["key"]: [{c["key"]: (_("Beispiel %(n)s") % {"n": n} if c["key"] == "titel" else "…") for c in item["columns"]} for n in (1, 2)]
                 for item in self.lists()}
        return {"id": "sample", "title": values["titel"], "fields": values, "labels": {f.key: str(f.display_label) for f in fields},
                "photo": "", "logins": [], "devices": [], "cases": [], "members": [], "lists": lists}
