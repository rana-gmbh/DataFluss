"""What two-way sync needs to know about every change.

The journal lists changes in the order this installation saw them; its id is the cursor a device
pulls from. Field versions hold the clock of the last change of every field (and of every key of a
JSON dictionary), so a change from elsewhere can be compared with the one already here.
"""
import uuid

from django.conf import settings
from django.db import models

CREATED, DELETED = "+", "-"


class Journal(models.Model):
    """One row per changed field (or created/deleted record), local or applied from elsewhere."""

    id = models.BigAutoField(primary_key=True)
    entity = models.CharField(max_length=60)
    record_id = models.UUIDField()
    key = models.CharField(max_length=160)  # "+", "-", a field name or "field.key" for JSON dictionaries
    hlc = models.CharField(max_length=60)
    origin = models.CharField(max_length=40)  # the node that made the change
    base = models.CharField(max_length=60, blank=True, default="")  # the version it replaced
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["entity", "record_id"])]


class FieldVersion(models.Model):
    entity = models.CharField(max_length=60)
    record_id = models.UUIDField()
    key = models.CharField(max_length=160)
    hlc = models.CharField(max_length=60)
    origin = models.CharField(max_length=40)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["entity", "record_id", "key"], name="sync_field_version_unique")]


class Device(models.Model):
    """A Mac that holds a copy of this server's data, limited to what its user may see."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    node = models.CharField(max_length=40, unique=True)
    name = models.CharField("Name", max_length=120)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sync_devices")
    token_hash = models.CharField(max_length=128, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    last_address = models.CharField(max_length=64, blank=True, default="")
    revoked_at = models.DateTimeField(null=True, blank=True)
    # Pull state: the journal id the device has, and a page it has not yet confirmed.
    cursor = models.BigIntegerField(default=0)
    pending = models.JSONField(default=dict, blank=True)
    # Records to send in full on the next pull, because the device's version lost or was refused.
    resend = models.JSONField(default=list, blank=True)
    app_version = models.CharField(max_length=40, blank=True, default="")
    modules = models.JSONField(default=dict, blank=True)  # {module: version} the device reports

    class Meta:
        verbose_name = "Verbundener Mac"
        ordering = ["-created_at"]


class Holding(models.Model):
    """A record a device has. Records enter and leave a device's copy as its user's scope changes."""

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="holdings")
    entity = models.CharField(max_length=60)
    record_id = models.UUIDField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["device", "entity", "record_id"], name="sync_holding_unique")]


class PushBatch(models.Model):
    """A push a device sent, so a retry after a lost answer changes nothing twice."""

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="batches")
    batch = models.CharField(max_length=64)
    answer = models.JSONField(default=dict)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["device", "batch"], name="sync_push_batch_unique")]


class Conflict(models.Model):
    """Two different changes to the same field. The newer one applies provisionally; a person confirms."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.CharField(max_length=60)
    record_id = models.UUIDField()
    key = models.CharField(max_length=160)
    kept_value = models.JSONField(null=True)
    kept_hlc = models.CharField(max_length=60)
    kept_origin = models.CharField(max_length=40)
    lost_value = models.JSONField(null=True)
    lost_hlc = models.CharField(max_length=60)
    lost_origin = models.CharField(max_length=40)
    reason = models.CharField(max_length=40, default="same-field")
    device = models.ForeignKey(Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    resolution = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]


class Dataset(models.Model):
    """On a server: a marker that changes whenever its data is replaced (a backup or dump restored).

    A restore rewinds the journal, so a connected computer's cursor would point past it and miss
    changes. Computers remember the marker and stop syncing when it changes (sync/views.py)."""

    marker = models.UUIDField(default=uuid.uuid4)

    @classmethod
    def current(cls):
        return str(cls.objects.get_or_create(pk=1)[0].marker)

    @classmethod
    def renew(cls):
        cls.objects.update_or_create(pk=1, defaults={"marker": uuid.uuid4()})


class ClientState(models.Model):
    """On a Mac: the server this database belongs to and how far sync has come. One row."""

    server_url = models.CharField(max_length=300)
    server_node = models.CharField(max_length=40, blank=True, default="")
    device_id = models.UUIDField(null=True)
    user_id = models.UUIDField(null=True)
    pull_cursor = models.BigIntegerField(default=0)
    pull_ack = models.CharField(max_length=64, blank=True, default="")
    pushed_until = models.BigIntegerField(default=0)  # local journal id
    last_success = models.DateTimeField(null=True, blank=True)
    last_attempt = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    revoked = models.BooleanField(default=False)
    dataset = models.CharField(max_length=40, blank=True, default="")  # the server's Dataset marker

    @classmethod
    def load(cls):
        return cls.objects.filter(pk=1).first()
