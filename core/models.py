import json
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import hlc
from .context_user import get_current_user, tracking_enabled


class Role(models.TextChoices):
    """The platform's roles. Modules add their own (core/modules.py add_capabilities)."""

    IT = "it", _("IT")
    SEKRETARIAT = "sekretariat", _("Sekretariat")
    SCHULLEITUNG = "schulleitung", _("Schulleitung")


def role_choices():
    """IT and the roles of this database (UserRole), or, before those exist, the platform's and the loaded
    modules' roles. A callable, so migrations do not depend on the modules."""
    from .modules import ROLES, active

    try:
        rows = [(r.key, r.display_name) for r in UserRole.objects.all()]
        using = active()
    except Exception:  # noqa: BLE001 - while models load (makemigrations) there is no database to ask
        rows, using = [], {module for _value, _label, module in ROLES}
    if rows:
        modules_roles = {value: module for value, _label, module in ROLES}
        return [(Role.IT, Role.IT.label), *[(key, name) for key, name in rows if modules_roles.get(key, "platform") in using | {"platform"}]]
    return [*Role.choices, *[(value, label) for value, label, module in ROLES if module in using]]


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField("Rolle", max_length=20, choices=role_choices, default=Role.SCHULLEITUNG)
    totp_secret = models.TextField(blank=True, default="")
    totp_confirmed = models.BooleanField(default=False)
    oidc_subject = models.CharField(max_length=255, blank=True, default="")
    # Database defaults, because the old version keeps writing here during an update.
    must_change_password = models.BooleanField(default=False, db_default=False)
    totp_last_step = models.BigIntegerField(default=0, db_default=0)
    # Kept on each server and Mac by itself; the sync leaves both alone.
    language = models.CharField("Sprache", max_length=10, blank=True, default="", db_default="")
    # Renaming a field or mask while another language is shown: "this" language only, "all", or "" to ask.
    name_scope = models.CharField(max_length=5, blank=True, default="", db_default="")
    # Guided tours this person has seen (core/tour.py). Kept on each server and Mac by itself, like the language.
    tours_seen = models.JSONField(default=list, blank=True, db_default=[])

    class Meta:
        verbose_name = "Benutzer"

    def can(self, capability):
        from .permissions import has_capability

        return has_capability(self, capability)

    @property
    def display_name(self):
        return self.get_full_name() or self.username


def _plain(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False, sort_keys=True)
    if isinstance(value, models.Model):
        return str(value.pk)
    return str(value)


class TrackedModel(models.Model):
    """UUID primary key and a per-field change log.

    UUIDs let either server create records. The change log is what two-way sync
    exchanges later, and it also powers the record history in the UI.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    TRACK_EXCLUDE = {"created_at", "updated_at"}
    SECRET_FIELDS = set()

    class Meta:
        abstract = True

    def secret_keys(self, name):
        """Keys of the JSON field `name` whose values are encrypted: the history names the change but
        never shows them (Person's secret custom fields, say)."""
        return set()

    def _changes(self, before, after, existed):
        """(field, history name, old, new, masked) per changed field. A JSON dict counts per key, as sync
        does (sync/journal.py), so the history says which custom field changed and not the whole dict."""
        for name, value in after.items():
            old = before.get(name)
            if existed and _plain(old) == _plain(value):
                continue
            if not existed and value in (None, "", [], {}, False):
                continue
            if name not in self.SECRET_FIELDS and isinstance(value, dict) and isinstance(old if existed else {}, dict):
                old = old if existed else {}
                hidden = self.secret_keys(name)
                for key in sorted(set(old) | set(value), key=str):
                    was, now = old.get(key), value.get(key)
                    if _plain(was) == _plain(now) or (not existed and now in (None, "", [], {}, False)):
                        continue
                    yield name, f"{name}.{key}"[:80], was, now, key in hidden
                continue
            yield name, name, old, value, name in self.SECRET_FIELDS

    def _tracked_values(self):
        values = {}
        for field in self._meta.concrete_fields:
            if field.name in self.TRACK_EXCLUDE or field.primary_key:
                continue
            values[field.name] = getattr(self, field.attname)
        return values

    def save(self, *args, **kwargs):
        if not tracking_enabled():
            return super().save(*args, **kwargs)
        previous = None
        if not self._state.adding:
            previous = type(self)._base_manager.filter(pk=self.pk).first()
        before = previous._tracked_values() if previous else {}
        super().save(*args, **kwargs)
        after = self._tracked_values()
        action = ChangeLog.UPDATE if previous else ChangeLog.CREATE
        rows = []
        user = get_current_user()
        for name, field, old, value, masked in self._changes(before, after, bool(previous)):
            rows.append(
                ChangeLog(
                    entity=self._meta.label_lower,
                    record_id=self.pk,
                    field=field,
                    action=action,
                    old_value="" if masked else _plain(old),
                    new_value="[verschlüsselt]" if masked else _plain(value),
                    user=user,
                    hlc=hlc.tick(),
                    server_id=settings.SERVER_ID,
                )
            )
        if rows:
            ChangeLog.objects.bulk_create(rows)
        from sync import journal

        journal.record(type(self), self.pk, journal.changed_keys(type(self), journal.values_of(previous) if previous else None, journal.values_of(self)))

    def delete(self, *args, **kwargs):
        if tracking_enabled():
            ChangeLog.objects.create(
                entity=self._meta.label_lower,
                record_id=self.pk,
                field="*",
                action=ChangeLog.DELETE,
                user=get_current_user(),
                hlc=hlc.tick(),
                server_id=settings.SERVER_ID,
            )
            from sync import journal

            journal.record(type(self), self.pk, [journal.DELETED])
        return super().delete(*args, **kwargs)


class KeptManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at=None)


class RecycledModel(TrackedModel):
    """Deleting moves a record to the recycle bin. After KEEP_DAYS it and its history are really gone."""

    KEEP_DAYS = 30
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    objects = KeptManager()
    everything = models.Manager()

    class Meta:
        abstract = True

    def recycle(self):
        self.deleted_at = timezone.now()
        self.save()

    def bring_back(self):
        self.deleted_at = None
        self.save()

    @classmethod
    def in_the_bin(cls):
        cls.empty_old()
        return cls.everything.exclude(deleted_at=None).order_by("-deleted_at")

    @classmethod
    def empty_old(cls):
        old = cls.everything.filter(deleted_at__lt=timezone.now() - timedelta(days=cls.KEEP_DAYS))
        for record in old:
            ChangeLog.objects.filter(entity=cls._meta.label_lower, record_id=record.pk).update(old_value="", new_value="")
            record.delete()


class ChangeLog(models.Model):
    CREATE, UPDATE, DELETE = "create", "update", "delete"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.CharField(max_length=60)
    record_id = models.UUIDField()
    field = models.CharField(max_length=80)
    action = models.CharField(max_length=10)
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    hlc = models.CharField(max_length=40)
    server_id = models.CharField(max_length=20)
    synced = models.BooleanField(default=False)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["entity", "record_id", "-at"]), models.Index(fields=["synced", "hlc"])]
        ordering = ["-at"]


class AuditEvent(models.Model):
    """Security-relevant actions. Never holds secret values."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action = models.CharField(max_length=40)
    summary = models.CharField(max_length=300)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    target_id = models.CharField(max_length=64, blank=True, default="")
    ip = models.GenericIPAddressField(null=True, blank=True)
    server_id = models.CharField(max_length=20, default="")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]
        indexes = [models.Index(fields=["action", "-at"]), models.Index(fields=["-at"])]


def default_require_2fa():
    return settings.REQUIRE_2FA


class SchoolSettings(models.Model):
    name = models.CharField("Name der Schule", max_length=200, default="Meine Schule")
    short_name = models.CharField("Kurzname", max_length=60, default="Schule")
    logo = models.ImageField("Logo", upload_to="school/", blank=True)
    key_check = models.CharField(max_length=128, blank=True, default="", db_default="")
    confidential_footer = models.CharField(
        "Fußzeile für vertrauliche Blätter", max_length=120, default="Vertraulich – nicht weitergeben"
    )
    email_domain = models.CharField("E-Mail-Domain", max_length=120, blank=True, default="")
    require_2fa = models.BooleanField("Zwei-Faktor-Anmeldung verlangen", default=default_require_2fa)
    # The interface languages people may pick here. Empty: all of them.
    languages = models.JSONField("Sprachen", default=list, blank=True, db_default=[])
    # The optional modules this database uses (core.modules.active). None: all that are loaded, as before.
    modules = models.JSONField("Module", null=True, blank=True, default=None)
    # Always-loaded modules this database does not use: ["schulverwaltung"] for a database of own tables only
    # (the start screen's card "Leer"). Empty in every database made before.
    modules_off = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = "Schuleinstellungen"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class UpdateState(models.Model):
    """What the update page knows. Lives in the database, so it survives the restart of an update."""

    latest = models.JSONField(null=True, blank=True)
    behind = models.IntegerField(default=0)
    commits = models.JSONField(default=list)
    checked_at = models.DateTimeField(null=True, blank=True)
    check_error = models.CharField(max_length=200, blank=True, default="")
    deployment_uuid = models.CharField(max_length=64, blank=True, default="")
    deployment_status = models.CharField(max_length=30, blank=True, default="")
    deployment_url = models.CharField(max_length=300, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    from_version = models.CharField(max_length=40, blank=True, default="")
    backup_name = models.CharField(max_length=120, blank=True, default="")
    restored = models.CharField(max_length=10, blank=True, default="")

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SavedView(TrackedModel):
    """A grid view: columns, filters and sort, for one user or for everyone."""

    entity = models.CharField(max_length=30)
    name = models.CharField("Name", max_length=80)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    config = models.JSONField(default=dict)
    order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["order", "name"]


ROLE_NAMESPACE = uuid.UUID("5b0f9a3e-2c1d-4e8b-9a7f-3d6c2e1b0a94")


class UserRole(TrackedModel):
    """A role and its rights (core/rights.py): per area none/read/edit, extra switches, and per field
    "read" or "hidden". IT is not a row: it always has every right. The roles that existed before
    are created with the same rights (core/permissions.py ensure_roles); their ids follow from the key,
    so a server and its computers make the same rows."""

    key = models.CharField(max_length=20, unique=True)  # what User.role stores
    name = models.CharField("Name", max_length=80)
    names = models.JSONField(default=dict, blank=True)  # {language: own name}, masks/i18n.py
    description = models.CharField("Beschreibung", max_length=300, blank=True, default="")
    areas = models.JSONField(default=dict, blank=True)
    extras = models.JSONField(default=list, blank=True)
    fields = models.JSONField(default=dict, blank=True)  # {entity: {field key: "read" | "hidden"}}
    order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        from masks import i18n

        return i18n.pick(self.name, self.names)

    @staticmethod
    def id_for(key):
        return uuid.uuid5(ROLE_NAMESPACE, key)
