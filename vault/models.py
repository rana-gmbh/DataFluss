from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import TrackedModel
from people.models import Guardian, Person

from . import crypto


class Platform(TrackedModel):
    PUPIL, STAFF, GUARDIAN = "pupil", "staff", "guardian"

    key = models.SlugField(_("Kennung"), max_length=40, unique=True)
    name = models.CharField(_("Name"), max_length=80)
    order = models.PositiveIntegerField(_("Reihenfolge"), default=100)
    fields = models.JSONField(_("Felder"), default=list)  # [{"key", "label", "secret"}]
    applies_to = models.JSONField(_("Gilt für"), default=list)
    on_sheet = models.BooleanField(_("Auf dem Zugangsblatt"), default=True)
    login_url = models.URLField(_("Anmelde-Adresse"), blank=True, default="")
    active = models.BooleanField(_("Aktiv"), default=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    @classmethod
    def active_for(cls, kind):
        """Filtered in Python, because SQLite (the desktop client) has no JSON `contains` lookup. There are only a few platforms."""
        return [p for p in cls.objects.filter(active=True) if kind in p.applies_to]

    @property
    def secret_keys(self):
        return [f["key"] for f in self.fields if f.get("secret")]


class Credential(TrackedModel):
    SECRET_FIELDS = {"secrets"}

    platform = models.ForeignKey(Platform, on_delete=models.CASCADE, related_name="credentials")
    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.CASCADE, related_name="credentials")
    guardian = models.ForeignKey(Guardian, null=True, blank=True, on_delete=models.CASCADE, related_name="credentials")
    values = models.JSONField(default=dict, blank=True)  # non-secret fields such as the username
    secrets = models.JSONField(default=dict, blank=True)  # field key -> ciphertext
    must_change = models.BooleanField(_("Muss geändert werden"), default=False)
    rotated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["platform", "person"], condition=models.Q(person__isnull=False), name="one_login_per_person"),
            models.UniqueConstraint(fields=["platform", "guardian"], condition=models.Q(guardian__isnull=False), name="one_login_per_guardian"),
            models.CheckConstraint(
                condition=models.Q(person__isnull=False, guardian__isnull=True) | models.Q(person__isnull=True, guardian__isnull=False),
                name="credential_has_one_owner",
            ),
        ]

    @property
    def owner(self):
        return self.person or self.guardian

    def _context(self, field_key):
        return f"credential:{self.pk}:{field_key}"

    def set_secret(self, field_key, plain):
        if plain:
            self.secrets[field_key] = crypto.encrypt(plain, self._context(field_key))
            self.rotated_at = timezone.now()
        else:
            self.secrets.pop(field_key, None)

    def get_secret(self, field_key):
        return crypto.decrypt(self.secrets.get(field_key, ""), self._context(field_key))

    def has_secret(self, field_key):
        return bool(self.secrets.get(field_key))

    def read_all(self, include_secrets=False):
        """Field label/value pairs in platform order."""
        rows = []
        for field in self.platform.fields:
            if field.get("secret"):
                value = self.get_secret(field["key"]) if include_secrets else ("••••••••" if self.has_secret(field["key"]) else "")
            else:
                value = self.values.get(field["key"], "")
            rows.append({"key": field["key"], "label": field["label"], "secret": bool(field.get("secret")), "value": value})
        return rows

    @classmethod
    @transaction.atomic
    def get_for(cls, platform, person=None, guardian=None):
        lookup = {"platform": platform, "person": person, "guardian": guardian}
        return cls.objects.get_or_create(**lookup)[0]
