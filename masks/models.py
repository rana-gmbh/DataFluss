from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import TrackedModel

from . import i18n


class FieldDefinition(TrackedModel):
    """Every field the app knows, built-in or added by an admin."""

    TYPES = [
        ("text", _("Text")), ("textarea", _("Text (mehrzeilig)")), ("number", _("Zahl")), ("bool", _("Ja/Nein")),
        ("choice", _("Auswahl")), ("multichoice", _("Mehrfachauswahl")), ("date", _("Datum")), ("email", _("E-Mail")),
        ("phone", _("Telefon")), ("url", _("Link")), ("secret", _("Geheim (verschlüsselt)")), ("class", _("Klasse")),
    ]
    ENTITIES = [("pupil", _("Schüler")), ("staff", _("Personal"))]

    entity = models.CharField(max_length=20, choices=ENTITIES)
    key = models.SlugField(max_length=60)
    label = models.CharField("Bezeichnung", max_length=100)  # as first typed; see masks/i18n.py
    labels = models.JSONField(default=dict, blank=True, db_default={})  # {language: own name}
    type = models.CharField("Typ", max_length=20, choices=TYPES, default="text")
    options = models.JSONField("Optionen", default=list, blank=True)  # [{"value", "color", "label"?, "labels"?}]
    required = models.BooleanField("Pflichtfeld", default=False)
    default = models.CharField("Vorgabewert", max_length=200, blank=True, default="")
    help_text = models.CharField("Hilfetext", max_length=200, blank=True, default="")
    read_only = models.BooleanField("Nur lesen", default=False)
    is_core = models.BooleanField(default=False)
    archived = models.BooleanField("Archiviert", default=False)
    in_grid = models.BooleanField("In der Liste", default=False)
    order = models.PositiveIntegerField(default=500)

    class Meta:
        ordering = ["order", "label"]
        constraints = [models.UniqueConstraint(fields=["entity", "key"], name="field_key_per_entity")]

    def __str__(self):
        return self.display_label

    @property
    def display_label(self):
        """The name in the language on screen."""
        return i18n.pick(self.label, self.labels)

    @property
    def option_values(self):
        return [o["value"] for o in self.options]

    @property
    def option_choices(self):
        """[(stored value, name on screen)]"""
        return [(o["value"], i18n.option_label(o)) for o in self.options]

    def option_label(self, value):
        for option in self.options:
            if option["value"] == value:
                return i18n.option_label(option)
        return value

    @property
    def has_names(self):
        return bool(self.labels) or i18n.has_option_names(self.options)


class Mask(TrackedModel):
    RECORD = "record"  # the record page of an own table (tables app); `table` says which
    ENTITIES = FieldDefinition.ENTITIES + [(RECORD, _("Tabelle"))]

    entity = models.CharField(max_length=20, choices=ENTITIES)
    table = models.ForeignKey("tables.Table", null=True, blank=True, on_delete=models.CASCADE, related_name="masks")
    name = models.CharField("Name", max_length=80)  # as first typed; see masks/i18n.py
    names = models.JSONField(default=dict, blank=True, db_default={})  # {language: own name}
    is_default = models.BooleanField("Standard", default=False)
    match_field = models.CharField("Gilt wenn Feld", max_length=60, blank=True, default="")
    match_value = models.CharField("den Wert hat", max_length=100, blank=True, default="")
    roles = models.JSONField("Nur für Rollen", default=list, blank=True)

    class Meta:
        ordering = ["entity", "-is_default", "name"]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return i18n.pick(self.name, self.names)

    @property
    def published(self):
        return self.versions.filter(status=MaskVersion.PUBLISHED).order_by("-number").first()

    @property
    def draft(self):
        return self.versions.filter(status=MaskVersion.DRAFT).order_by("-number").first()


class MaskVersion(TrackedModel):
    DRAFT, PUBLISHED, RETIRED = "draft", "published", "retired"

    mask = models.ForeignKey(Mask, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=10, default=DRAFT)
    tree = models.JSONField(default=dict)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-number"]
        constraints = [models.UniqueConstraint(fields=["mask", "number"], name="mask_version_unique")]
