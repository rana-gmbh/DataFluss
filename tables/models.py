"""Own tables (docs/TABLES-PLAN.md §2): tables, their fields and their records.

Records of every table share one database table, so designing a table never needs a migration and
the sync schema stays the same. Values live in Record.values by field key, as plain JSON
(core/fieldtypes.py to_store); a secret value is stored as {"secret": ciphertext}, so backups and
devices recognise it without knowing the table.
"""
from django.db import models

from core.models import RecycledModel, TrackedModel
from masks import i18n

NONE, READ, EDIT = "none", "read", "edit"
RIGHTS = (NONE, READ, EDIT)


class Table(TrackedModel):
    key = models.SlugField(max_length=20, unique=True)  # never changes: URLs, sync, print source "table:<key>"
    name = models.CharField(max_length=100)  # as first typed; see masks/i18n.py
    names = models.JSONField(default=dict, blank=True)  # {language: own name}
    description = models.CharField(max_length=300, blank=True, default="")
    icon = models.CharField(max_length=30, default="table")
    order = models.PositiveIntegerField(default=500)
    archived = models.BooleanField(default=False)
    in_nav = models.BooleanField(default=True)  # in the sidebar
    section = models.CharField(max_length=60, blank=True, default="", db_default="")  # its sidebar section; empty: "Tabellen"
    title_field = models.CharField(max_length=60, blank=True, default="")  # names a record; empty: the first text field
    rights = models.JSONField(default=dict, blank=True)  # {role: none|read|edit}; IT always edits
    template = models.JSONField(null=True, blank=True)  # {"id", "version"} when a module template made it

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return i18n.pick(self.name, self.names)

    @property
    def has_names(self):
        return bool(self.names)


class Field(TrackedModel):
    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name="fields")
    key = models.SlugField(max_length=60)  # "f_<slug>", never changes
    label = models.CharField(max_length=100)
    labels = models.JSONField(default=dict, blank=True)
    help_text = models.CharField(max_length=200, blank=True, default="")
    type = models.CharField(max_length=20, default="text")  # a key of core.fieldtypes.TYPES
    options = models.JSONField(default=list, blank=True)  # [{"value", "color", "label"?, "labels"?}]
    config = models.JSONField(default=dict, blank=True)  # decimals, unit, min, max; later link target, formula
    required = models.BooleanField(default=False)
    unique = models.BooleanField(default=False)
    in_grid = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=500)
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["order", "label"]
        constraints = [models.UniqueConstraint(fields=["table", "key"], name="table_field_key")]

    def __str__(self):
        return self.display_label

    @property
    def display_label(self):
        return i18n.pick(self.label, self.labels)

    @property
    def option_values(self):
        return [o["value"] for o in self.options]

    @property
    def option_choices(self):
        return [(o["value"], i18n.option_label(o)) for o in self.options]

    def option_label(self, value):
        for option in self.options:
            if option["value"] == value:
                return i18n.option_label(option)
        return value

    @property
    def has_names(self):
        return bool(self.labels) or i18n.has_option_names(self.options)

    @property
    def type_label(self):
        from core import fieldtypes

        kind = fieldtypes.TYPES.get(self.type)
        return kind.label if kind else self.type


class Record(RecycledModel):
    table = models.ForeignKey(Table, on_delete=models.PROTECT, related_name="records")
    values = models.JSONField(default=dict, blank=True)
    title = models.CharField(max_length=300, blank=True, default="")  # cached: links, search, sorting
    search_text = models.TextField(blank=True, default="")  # cached lowercase text of the searchable fields

    class Meta:
        indexes = [models.Index(fields=["table", "deleted_at"])]

    def __str__(self):
        return self.title or str(self.pk)[:8]

    def secret_keys(self, name):
        """The history names a change of a secret field but never shows it."""
        if name != "values":
            return set()
        return set(Field.objects.filter(table_id=self.table_id, type="secret").values_list("key", flat=True))


def _upload_path(attachment, filename):
    return f"tables/{attachment.record.table.key}/{attachment.record_id}/{attachment.pk}-{filename}"[:250]


class Link(TrackedModel):
    """One target of a link field. The target is a plain UUID, not a foreign key: a device's scope closes over
    foreign keys, and a link must never pull pupils or other tables onto a device whose role may not see them.
    Ids are derived from the record and field (and the target, for a field with several), so two computers
    changing a link offline change the same row (tables/links.py link_id)."""

    RECORD, PUPIL, STAFF, ASSET = "record", "pupil", "staff", "asset"
    KINDS = (RECORD, PUPIL, STAFF, ASSET)

    record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name="links")
    field = models.CharField(max_length=60)
    target_kind = models.CharField(max_length=10)
    target_id = models.UUIDField(null=True, blank=True, db_index=True)  # empty: a single link that was removed
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "created_at"]
        indexes = [models.Index(fields=["record", "field"])]


class Attachment(TrackedModel):
    """A file of a file or image field. Served by tables/views_records.py record_file, never as plain media."""

    record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name="attachments")
    field = models.CharField(max_length=60)
    file = models.FileField(upload_to=_upload_path, max_length=255)
    name = models.CharField(max_length=200)
    size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True, default="")
    uploaded_by = models.ForeignKey("core.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["created_at"]
