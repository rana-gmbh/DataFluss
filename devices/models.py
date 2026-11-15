from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

from core.models import TrackedModel
from people.models import Person


class AssetCategory(TrackedModel):
    name = models.CharField(_("Gerätetyp"), max_length=60, unique=True)
    prefix = models.CharField(_("Präfix"), max_length=12, blank=True, default="")
    separator = models.CharField(_("Trennzeichen"), max_length=2, blank=True, default="")
    digits = models.PositiveSmallIntegerField(_("Stellen"), default=4)
    next_number = models.PositiveIntegerField(_("Nächste Nummer"), default=1)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @transaction.atomic
    def allocate_number(self):
        """Hand out the next number from this server's own range."""
        category = AssetCategory.objects.select_for_update().get(pk=self.pk)
        step = max(settings.DEVICE_NUMBER_STEP, 1)
        number = category.next_number
        while number % step != settings.DEVICE_NUMBER_OFFSET % step:
            number += 1
        category.next_number = number + 1
        category.save()
        return f"{category.prefix}{category.separator}{number:0{category.digits}d}"


class Place(TrackedModel):
    """A room or group that can hold a device, instead of a fake staff record."""

    ROOM, GROUP = "raum", "gruppe"
    name = models.CharField(_("Name"), max_length=120, unique=True)
    kind = models.CharField(_("Art"), max_length=10, choices=[(ROOM, _("Raum")), (GROUP, _("Gruppe"))], default=ROOM)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Asset(TrackedModel):
    SECRET_FIELDS = {"product_key_enc"}
    # The status values stay German: printouts, the list filter and the badge colours key on them.
    STATUS_LABELS = {
        "Ausgegeben": _("Ausgegeben"), "Verfügbar": _("Verfügbar"), "Beschädigt": _("Beschädigt"),
        "Verkauft": _("Verkauft"), "Ausgemustert": _("Ausgemustert"),
    }

    number = models.CharField(_("Gerätenummer"), max_length=40, db_index=True)
    category = models.ForeignKey(AssetCategory, verbose_name=_("Gerätetyp"), null=True, blank=True, on_delete=models.SET_NULL)
    serial = models.CharField(_("Seriennummer"), max_length=80, blank=True, default="", db_index=True)
    hardware_uuid = models.CharField(_("UUID"), max_length=80, blank=True, default="")
    product_key_enc = models.TextField(blank=True, default="")
    damaged = models.BooleanField(_("Beschädigt"), default=False)
    sold = models.BooleanField(_("Verkauft"), default=False)
    sold_at = models.DateField(_("Verkaufsdatum"), null=True, blank=True)
    retired = models.BooleanField(_("Ausgemustert"), default=False)
    notes = models.TextField(_("Bemerkungen"), blank=True, default="")

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return self.number

    @property
    def open_assignment(self):
        return next((a for a in self.assignments.all() if a.returned_at is None), None)

    @property
    def status(self):
        if self.sold:
            return "Verkauft"
        if self.retired:
            return "Ausgemustert"
        if self.damaged:
            return "Beschädigt"
        return "Ausgegeben" if self.open_assignment else "Verfügbar"

    @property
    def status_label(self):
        return str(self.STATUS_LABELS.get(self.status, self.status))

    @transaction.atomic
    def issue(self, person=None, place=None, when=None, pen="", keyboard="", notes=""):
        if self.assignments.filter(returned_at__isnull=True).exists():
            raise ValueError(gettext("Das Gerät ist noch ausgegeben."))
        if bool(person) == bool(place):
            raise ValueError(gettext("Bitte genau eine Person oder einen Ort wählen."))
        return Assignment.objects.create(
            asset=self, person=person, place=place, issued_at=when or timezone.localdate(),
            pen=pen, keyboard=keyboard, notes=notes,
        )

    @transaction.atomic
    def take_back(self, when=None):
        for assignment in self.assignments.filter(returned_at__isnull=True):
            assignment.returned_at = when or timezone.localdate()
            assignment.save()

    @transaction.atomic
    def sell(self, when=None):
        self.take_back(when)
        self.sold, self.sold_at = True, when or timezone.localdate()
        self.save()


class Assignment(TrackedModel):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="assignments")
    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.CASCADE, related_name="assignments")
    place = models.ForeignKey(Place, null=True, blank=True, on_delete=models.CASCADE, related_name="assignments")
    issued_at = models.DateField(_("Ausgabe"))
    returned_at = models.DateField(_("Rückgabe"), null=True, blank=True)
    pen = models.CharField(_("Stift"), max_length=80, blank=True, default="")
    keyboard = models.CharField(_("Tastatur"), max_length=80, blank=True, default="")
    notes = models.TextField(_("Bemerkungen"), blank=True, default="")

    class Meta:
        ordering = ["-issued_at"]
        constraints = [
            models.UniqueConstraint(fields=["asset"], condition=models.Q(returned_at__isnull=True), name="one_open_assignment"),
        ]

    @property
    def holder(self):
        return self.person or self.place


class SupportCase(TrackedModel):
    # Stored values stay German (they are compared in code and printed); only the labels are translated.
    STATUSES = ["Offen", "In Reparatur", "Wartet auf Ersatz", "Erledigt"]
    STATUS_LABELS = {"Offen": _("Offen"), "In Reparatur": _("In Reparatur"), "Wartet auf Ersatz": _("Wartet auf Ersatz"), "Erledigt": _("Erledigt")}
    PARTS = ["Display", "Interne Hardware", "Ständer/Scharnier", "Tastatur", "Stift", "Sonstiges"]
    PART_LABELS = {
        "Display": _("Display"), "Interne Hardware": _("Interne Hardware"), "Ständer/Scharnier": _("Ständer/Scharnier"),
        "Tastatur": _("Tastatur"), "Stift": _("Stift"), "Sonstiges": _("Sonstiges"),
    }

    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL, related_name="support_cases")
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.SET_NULL, related_name="support_cases")
    description = models.TextField(_("Beschreibung des Schadens"))
    parts = models.JSONField(_("Schaden an"), default=list, blank=True)
    date = models.DateField(_("Datum"), default=timezone.localdate)
    status = models.CharField(_("Status"), max_length=30, default="Offen")
    replacement_number = models.CharField(_("Gerätenummer Austauschgerät"), max_length=40, blank=True, default="")
    replacement_serial = models.CharField(_("Seriennummer Austauschgerät"), max_length=80, blank=True, default="")
    legacy_ninox_id = models.CharField(_("Ninox-ID"), max_length=20, blank=True, default="", db_index=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    @classmethod
    def status_choices(cls):
        return [(s, str(cls.STATUS_LABELS[s])) for s in cls.STATUSES]

    @classmethod
    def part_choices(cls):
        return [(p, str(cls.PART_LABELS[p])) for p in cls.PARTS]

    @property
    def status_label(self):
        return str(self.STATUS_LABELS.get(self.status, self.status))

    @property
    def part_labels(self):
        return [str(self.PART_LABELS.get(p, p)) for p in self.parts]


class ReturnCampaign(TrackedModel):
    name = models.CharField(_("Name"), max_length=120)
    date = models.DateField(_("Stichtag"))
    done = models.BooleanField(_("Abgeschlossen"), default=False)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return self.name


class CampaignItem(TrackedModel):
    campaign = models.ForeignKey(ReturnCampaign, on_delete=models.CASCADE, related_name="items")
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="campaign_items")
    returned = models.BooleanField(_("Zurückgegeben"), default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["campaign", "asset"], name="asset_once_per_campaign")]
