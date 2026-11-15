from pathlib import Path

from django.conf import settings
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from core.images import image_extension
from core.models import RecycledModel, TrackedModel


class SchoolYear(TrackedModel):
    name = models.CharField(gettext_lazy("Schuljahr"), max_length=20, unique=True)
    start = models.DateField(gettext_lazy("Beginn"))
    end = models.DateField(gettext_lazy("Ende"))
    is_current = models.BooleanField(gettext_lazy("Aktuell"), default=False)

    class Meta:
        ordering = ["-start"]

    def __str__(self):
        return self.name

    @classmethod
    def current(cls):
        return cls.objects.filter(is_current=True).first() or cls.objects.first()


class ClassGroup(TrackedModel):
    KLASSE, GRUPPE, AUSTRITT = "klasse", "gruppe", "austritt"
    KINDS = [(KLASSE, gettext_lazy("Klasse")), (GRUPPE, gettext_lazy("Kindergartengruppe")), (AUSTRITT, gettext_lazy("Austritt"))]

    name = models.CharField(gettext_lazy("Name"), max_length=40)
    kind = models.CharField(gettext_lazy("Art"), max_length=10, choices=KINDS, default=KLASSE)
    school_year = models.ForeignKey(SchoolYear, null=True, blank=True, on_delete=models.CASCADE, related_name="classes")
    level = models.PositiveSmallIntegerField(gettext_lazy("Klassenstufe"), null=True, blank=True)
    color = models.CharField(gettext_lazy("Farbe"), max_length=9, blank=True, default="")

    class Meta:
        ordering = ["-kind", "level", "name"]
        constraints = [models.UniqueConstraint(fields=["name", "school_year"], name="class_unique_per_year")]

    def __str__(self):
        return self.name


class Person(RecycledModel):
    PUPIL, STAFF = "pupil", "staff"
    KINDS = [(PUPIL, gettext_lazy("Schüler")), (STAFF, gettext_lazy("Personal"))]

    kind = models.CharField(max_length=10, choices=KINDS, default=PUPIL)
    legacy_ninox_id = models.CharField(gettext_lazy("Ninox-ID"), max_length=20, blank=True, default="", db_index=True)

    first_name = models.CharField(gettext_lazy("Vorname"), max_length=120)
    last_name = models.CharField(gettext_lazy("Nachname"), max_length=120)
    family_name_first = models.BooleanField(gettext_lazy("Familienname zuerst"), default=False)
    gender = models.CharField(gettext_lazy("Geschlecht"), max_length=20, blank=True, default="")
    birth_date = models.DateField(gettext_lazy("Geburtsdatum"), null=True, blank=True)
    birth_place = models.CharField(gettext_lazy("Geburtsort"), max_length=120, blank=True, default="")
    email = models.CharField(gettext_lazy("E-Mail"), max_length=200, blank=True, default="", db_index=True)
    entry_date = models.DateField(gettext_lazy("Eintritt"), null=True, blank=True)
    exit_date = models.DateField(gettext_lazy("Austritt"), null=True, blank=True)
    archived = models.BooleanField(gettext_lazy("Archiviert"), default=False, db_index=True)
    photo = models.ImageField(gettext_lazy("Bild"), upload_to="photos/", blank=True)
    legacy_photo_path = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(gettext_lazy("Bemerkungen"), blank=True, default="")
    new_codes_needed = models.BooleanField(gettext_lazy("Neue Codes/Accounts"), default=False)

    # Pupils
    current_class = models.ForeignKey(
        ClassGroup, verbose_name=gettext_lazy("Klasse"), null=True, blank=True, on_delete=models.SET_NULL, related_name="members"
    )
    school_type = models.CharField(gettext_lazy("Schultyp"), max_length=10, blank=True, default="", db_index=True)
    grade_level = models.PositiveSmallIntegerField(gettext_lazy("Klassenstufe"), null=True, blank=True)
    dyslexia = models.BooleanField(gettext_lazy("Lese-/Rechtschreibschwäche"), default=False)
    bus = models.BooleanField(gettext_lazy("Bus"), default=False)

    # Staff
    category = models.CharField(gettext_lazy("Kategorie"), max_length=30, blank=True, default="")
    salutation = models.CharField(gettext_lazy("Anrede"), max_length=20, blank=True, default="")
    abbreviation = models.CharField(gettext_lazy("Kürzel"), max_length=20, blank=True, default="")
    mobile = models.CharField(gettext_lazy("Mobiltelefon"), max_length=40, blank=True, default="")
    phone_private = models.CharField(gettext_lazy("Tel. privat"), max_length=40, blank=True, default="")
    phone_office = models.CharField(gettext_lazy("Tel. Büro"), max_length=40, blank=True, default="")
    phone_emergency = models.CharField(gettext_lazy("Tel. Notruf"), max_length=40, blank=True, default="")
    private_email = models.CharField(gettext_lazy("E-Mail privat"), max_length=200, blank=True, default="")
    compound = models.CharField(gettext_lazy("Compound"), max_length=120, blank=True, default="")
    street = models.CharField(gettext_lazy("Straße / Nr."), max_length=160, blank=True, default="")
    district = models.CharField(gettext_lazy("Bezirk"), max_length=120, blank=True, default="")
    city = models.CharField(gettext_lazy("Stadt / PLZ"), max_length=120, blank=True, default="")
    street_office = models.CharField(gettext_lazy("Straßenbüro"), max_length=160, blank=True, default="")
    # Marked here, the person stands in the DaZ or Kindergarten section for choosing (people/section_staff.py).
    daz_staff = models.BooleanField(gettext_lazy("DaZ-Fachkraft"), default=False)
    kiga_staff = models.BooleanField(gettext_lazy("Kindergarten-Fachkraft"), default=False)
    # Flags that put staff into a module's section (people/section_staff.py). Without the module their fields stay hidden.
    SECTION_FLAGS = ("daz_staff", "kiga_staff")

    custom = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [models.Index(fields=["kind", "archived"]), models.Index(fields=["last_name", "first_name"])]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        if self.family_name_first:
            return f"{self.last_name}{self.first_name}" if _is_cjk(self.last_name + self.first_name) else f"{self.last_name} {self.first_name}"
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def sort_name(self):
        return f"{self.last_name}, {self.first_name}"

    @property
    def initials(self):
        return (self.first_name[:1] + self.last_name[:1]).upper()

    @property
    def in_kindergarten(self):
        """A class decides, because a move to a school class leaves the Schultyp as it was."""
        group = self.current_class
        return self.school_type == "Kiga" if group is None else group.kind == ClassGroup.GRUPPE

    def kept_by_section(self):
        """Why the person cannot go to the recycle bin, or "". A module's records (DaZ, Kindergarten, grades)
        keep the person, and would block deleting the person for good after KEEP_DAYS."""
        from core import modules

        return next((reason for kept_by in modules.hooks("kept_by") if (reason := kept_by(self))), "")

    # A copy is a new person: what identifies or belongs to one person stays behind.
    NOT_COPIED = {"id", "legacy_ninox_id", "photo", "legacy_photo_path", "created_at", "updated_at", "deleted_at", "archived", "exit_date"}

    def duplicate(self):
        """Data and form fields, marked as a copy. Secret form fields (passwords) are left out."""
        from masks.models import FieldDefinition

        values = {f.attname: getattr(self, f.attname) for f in self._meta.concrete_fields if f.name not in self.NOT_COPIED and f.attname not in self.NOT_COPIED}
        secret = set(FieldDefinition.objects.filter(type="secret").values_list("key", flat=True))
        values["custom"] = {k: v for k, v in (self.custom or {}).items() if k not in secret}
        values["first_name"] = _("%(name)s (Kopie)") % {"name": self.first_name}
        copy = Person(**values)
        copy.save()
        if copy.current_class_id:
            Enrollment.objects.create(person=copy, class_group_id=copy.current_class_id, start=timezone.localdate())
        return copy

    @property
    def active_card(self):
        return next((c for c in self.cards.all() if c.active), None)

    @property
    def thumb_url(self):
        """The name changes with the photo, so the browser can keep this for a week."""
        if not self.photo:
            return ""
        return f"{reverse('person_thumb', args=[self.pk])}?v={Path(self.photo.name).name}"

    def store_photo(self, upload):
        """Names the file after its real image type, because /media serves it by that name. False if it is no such image."""
        extension = image_extension(upload)
        if not extension:
            return False
        self.photo.save(f"{self.pk}.{extension}", upload)
        return True

    @transaction.atomic
    def move_to_class(self, group, when=None):
        """Change class and keep the history as an enrollment."""
        when = when or timezone.localdate()
        if group == self.current_class:
            return
        from core import modules
        from sync import journal

        journal.update(self.enrollments.filter(end__isnull=True), end=when)
        if group is not None:
            Enrollment.objects.create(person=self, class_group=group, start=when)
            self.grade_level = group.level or self.grade_level
        old = self.current_class
        self.current_class = group
        self.save()
        for moved in modules.hooks("class_moved"):
            moved(self, old, group, when)


def _is_cjk(text):
    return any("一" <= ch <= "鿿" for ch in text)


class Enrollment(TrackedModel):
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="enrollments")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.CASCADE, related_name="enrollments")
    start = models.DateField()
    end = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-start"]


class Guardian(TrackedModel):
    first_name = models.CharField("Vorname", max_length=120)
    last_name = models.CharField("Nachname", max_length=120)
    email = models.CharField("E-Mail", max_length=200, blank=True, default="", db_index=True)
    phone = models.CharField("Telefon", max_length=40, blank=True, default="")
    notes = models.TextField("Bemerkungen", blank=True, default="")
    legacy_slot = models.CharField(max_length=60, blank=True, default="")

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return f"{self.first_name} {self.last_name}".strip()


class Guardianship(TrackedModel):
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="guardianships")
    guardian = models.ForeignKey(Guardian, on_delete=models.CASCADE, related_name="guardianships")
    role = models.CharField("Rolle", max_length=30, default="Elternteil")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["person", "guardian"], name="guardian_once_per_child")]


class Card(TrackedModel):
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="cards")
    code = models.CharField("Kartennummer", max_length=40, db_index=True)
    raw_input = models.CharField(max_length=60, blank=True, default="")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]


class Comment(TrackedModel):
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    text = models.TextField("Kommentar")

    class Meta:
        ordering = ["-created_at"]


class Attachment(TrackedModel):
    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.CASCADE, related_name="attachments")
    support_case = models.ForeignKey(
        "devices.SupportCase", null=True, blank=True, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to="attachments/")
    name = models.CharField(max_length=200)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-created_at"]
