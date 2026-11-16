from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import TrackedModel


class PrintTemplate(TrackedModel):
    SOURCES = [("pupil", _("Schüler")), ("staff", _("Personal")), ("guardian", _("Eltern")), ("asset", _("Geräte")), ("case", _("Schäden")), ("class", _("Klassenliste")), ("report", _("Zeugnisse"))]

    name = models.CharField("Name", max_length=120)
    description = models.CharField("Beschreibung", max_length=200, blank=True, default="")
    source = models.CharField("Datenquelle", max_length=64, choices=SOURCES, default="pupil")  # or table:<key> (tables/prints.py)
    tree = models.JSONField(default=dict)  # designer document
    code_mode = models.BooleanField("Code-Modus", default=False)
    code_html = models.TextField(blank=True, default="")
    code_css = models.TextField(blank=True, default="")
    contains_secrets = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def source_label(self):
        from .records import available_sources

        return dict(available_sources()).get(self.source, self.source)


class PrintTemplateVersion(TrackedModel):
    template = models.ForeignKey(PrintTemplate, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    tree = models.JSONField(default=dict)
    code_mode = models.BooleanField(default=False)
    code_html = models.TextField(blank=True, default="")
    code_css = models.TextField(blank=True, default="")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-number"]


class PrintJob(models.Model):
    """Records who printed what for whom. PDFs themselves are never stored."""

    id = models.BigAutoField(primary_key=True)
    template_name = models.CharField(max_length=120)
    record_count = models.PositiveIntegerField()
    page_count = models.PositiveIntegerField(default=0)
    with_secrets = models.BooleanField(default=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]
