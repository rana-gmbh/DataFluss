from django.conf import settings
from django.db import models

from core.models import TrackedModel


class ImportPreset(TrackedModel):
    name = models.CharField(max_length=80, unique=True)
    mapping = models.JSONField(default=dict)


class ImportSession(TrackedModel):
    UPLOADED, PREVIEWED, DONE, UNDONE = "uploaded", "previewed", "done", "undone"
    # The file can hold passwords and must not outlive the import in the change log.
    TRACK_EXCLUDE = TrackedModel.TRACK_EXCLUDE | {"headers", "rows", "report", "undo"}

    filename = models.CharField(max_length=200)
    entity = models.CharField(max_length=40, default="pupil")  # pupil, staff, or table:<key> (transfer/targets.py)
    status = models.CharField(max_length=10, default=UPLOADED)
    headers = models.JSONField(default=list)
    rows = models.JSONField(default=list)  # deleted after the import has been committed
    mapping = models.JSONField(default=dict)
    match_key = models.CharField(max_length=60, default="legacy_ninox_id")
    report = models.JSONField(default=dict)
    undo = models.JSONField(default=dict)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-created_at"]
