"""The catalog: templates, their versions (each reviewed before it is shown), pictures, reviews and reports."""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

CATEGORIES = [
    ("sammlung", _("Sammlungen und Haushalt")),
    ("verein", _("Verein und Ehrenamt")),
    ("bildung", _("Schule und Bildung")),
    ("arbeit", _("Arbeit und Büro")),
    ("freizeit", _("Freizeit und Hobby")),
    ("sonstiges", _("Sonstiges")),
]
LICENSE = "CC-BY-4.0"


class Template(models.Model):
    key = models.SlugField("Kennung", max_length=20, unique=True)  # the template's "id"; one owner, never reused
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="templates")
    category = models.CharField("Kategorie", max_length=20, choices=CATEGORIES, default="sonstiges")
    official = models.BooleanField("von DataFluss", default=False)
    withdrawn = models.BooleanField("zurückgezogen", default=False)  # by its owner or a reviewer: not shown, not offered
    published = models.ForeignKey("Version", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    search = models.TextField(blank=True, default="")  # lower-case title, description and table names of the published version
    downloads = models.PositiveIntegerField(default=0)
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0"))  # average stars
    ratings = models.PositiveIntegerField(default=0)
    created = models.DateTimeField(default=timezone.now)
    updated = models.DateTimeField(default=timezone.now)  # when a version was last published

    class Meta:
        ordering = ["-updated"]

    def __str__(self):
        return self.key

    @property
    def listed(self):
        return self.published_id is not None and not self.withdrawn

    @property
    def author(self):
        if self.official:
            return "DataFluss"
        return self.owner.shown_name if self.owner else gettext("gelöschtes Konto")


class Version(models.Model):
    SUBMITTED, APPROVED, REJECTED, REPLACED, WITHDRAWN = "submitted", "approved", "rejected", "replaced", "withdrawn"
    STATUS = [(SUBMITTED, _("eingereicht")), (APPROVED, _("freigegeben")), (REJECTED, _("abgelehnt")), (REPLACED, _("ersetzt")),
              (WITHDRAWN, _("zurückgezogen"))]

    template = models.ForeignKey(Template, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    data = models.JSONField()  # the uploaded template as checked (template_check.canonical, without "catalog")
    title = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    changes = models.CharField("Was ist neu", max_length=500, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS, default=SUBMITTED)
    notes = models.JSONField(default=list, blank=True)  # hints for the reviewer (template_check.notes)
    file = models.TextField(blank=True, default="")  # the file as handed out, written when approved
    sha256 = models.CharField(max_length=64, blank=True, default="")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed = models.DateTimeField(null=True, blank=True)
    decision = models.TextField("Begründung", blank=True, default="")
    created = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-number"]
        constraints = [models.UniqueConstraint(fields=["template", "number"], name="version_number")]

    def __str__(self):
        return f"{self.template.key} {self.number}"


def _picture_path(picture, filename):
    return f"bilder/{picture.uid}.webp"


class Picture(models.Model):
    """A screenshot of a version, stored as a WebP the site made itself (katalog/pictures.py)."""

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    version = models.ForeignKey(Version, on_delete=models.CASCADE, related_name="pictures")
    image = models.FileField(upload_to=_picture_path)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "pk"]


class Review(models.Model):
    """Stars count at once; a text appears once a reviewer approved it."""

    NONE, SUBMITTED, APPROVED, REJECTED = "none", "submitted", "approved", "rejected"
    TEXT_STATUS = [(NONE, _("kein Text")), (SUBMITTED, _("eingereicht")), (APPROVED, _("freigegeben")), (REJECTED, _("abgelehnt"))]

    template = models.ForeignKey(Template, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    stars = models.DecimalField(max_digits=2, decimal_places=1)  # 0.5 to 5 in half steps
    text = models.TextField(blank=True, default="")
    text_status = models.CharField(max_length=10, choices=TEXT_STATUS, default=NONE)
    version = models.PositiveIntegerField(default=0)  # the version it was written about
    created = models.DateTimeField(default=timezone.now)
    updated = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-updated"]
        constraints = [models.UniqueConstraint(fields=["template", "user"], name="one_review_per_person")]


class Report(models.Model):
    template = models.ForeignKey(Template, on_delete=models.CASCADE, related_name="reports")
    review = models.ForeignKey(Review, null=True, blank=True, on_delete=models.CASCADE, related_name="reports")
    reason = models.TextField(max_length=1000)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created = models.DateTimeField(default=timezone.now)
    handled = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created"]


@models.signals.post_delete.connect
def _remove_picture_file(sender, instance, **kwargs):
    if sender is Picture and instance.image:
        instance.image.delete(save=False)
