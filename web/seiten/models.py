"""Websites people publish from DataFluss to seiten.datafluss.de (docs/website.md "Websites").

An account asks for websites with a short reason; a reviewer unlocks it (Access). Then it may make a few sites,
each with a token that DataFluss uses to publish (publish/hosting.py; only the token's hash is kept). Anyone
may report a site; reviewers can switch a site off or block the account's access."""
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Access(models.Model):
    REQUESTED, GRANTED, REFUSED, BLOCKED = "requested", "granted", "refused", "blocked"
    STATES = [(REQUESTED, _("beantragt")), (GRANTED, _("freigeschaltet")), (REFUSED, _("abgelehnt")), (BLOCKED, _("gesperrt"))]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="site_access")
    status = models.CharField(max_length=10, choices=STATES, default=REQUESTED)
    reason = models.TextField(max_length=1000)  # what the account wants to publish, for the reviewer
    note = models.TextField(max_length=1000, blank=True, default="")  # the reviewer's message, sent by mail
    requested = models.DateTimeField(default=timezone.now)
    decided = models.DateTimeField(null=True, blank=True)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    @property
    def granted(self):
        return self.status == self.GRANTED


class Site(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sites")
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=40, unique=True)
    token_hash = models.CharField(max_length=64, unique=True)
    created = models.DateTimeField(default=timezone.now)
    disabled = models.BooleanField(default=False)  # switched off by a reviewer: nothing is served or accepted
    disabled_reason = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SiteReport(models.Model):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="reports")
    reason = models.TextField(max_length=1000)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created = models.DateTimeField(default=timezone.now)
    handled = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created"]
