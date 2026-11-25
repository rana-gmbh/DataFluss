"""Publications: a read-only copy of chosen tables as a static website (docs/publish.md).

A publication is not synced: each server or computer keeps its own, with its own target and the manifest
of what it last put there. What it shows is decided by `choice` (opt-in: tables and fields that are not
listed are never published) and by the role it is bound to (publish/viewer.py).
"""
import uuid
from datetime import time

from django.db import models
from django.utils.translation import gettext_lazy as _


class Publication(models.Model):
    ZIP, FOLDER, SERVER, WEBDAV, FTP = "zip", "folder", "server", "webdav", "ftp"
    SFTP, S3, NETLIFY, GITHUB, HOSTING = "sftp", "s3", "netlify", "github", "hosting"
    TARGETS = (ZIP, FOLDER, SERVER, HOSTING, WEBDAV, FTP, SFTP, S3, NETLIFY, GITHUB)
    TARGET_CHOICES = [(ZIP, _("ZIP-Datei")), (FOLDER, _("Ordner")), (SERVER, _("Dieser Server")), (HOSTING, _("DataFluss-Hosting")),
                      (WEBDAV, "WebDAV"), (FTP, "FTP / FTPS"), (SFTP, "SFTP"), (S3, _("S3-kompatibel")), (NETLIFY, "Netlify"),
                      (GITHUB, "GitHub Pages")]
    IDLE, RUNNING, DONE, FAILED = "idle", "running", "done", "failed"
    OFF, CHANGES, DAILY = "off", "changes", "daily"
    AUTO_CHOICES = [(OFF, _("Nur von Hand")), (CHANGES, _("Nach Änderungen")), (DAILY, _("Täglich"))]
    QUIET_CHOICES = [(5, _("5 Minuten")), (15, _("15 Minuten")), (60, _("1 Stunde"))]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=40, unique=True)  # its address on this server: /p/<slug>/
    language = models.CharField(max_length=10, default="de")
    role = models.CharField(max_length=40, default="gast")  # it never shows more than this role sees
    indexable = models.BooleanField(default=True)  # search engines may list it
    password = models.CharField(max_length=128, blank=True, default="")  # hash; a site on this server asks for it (publish/guard.py)
    choice = models.JSONField(default=dict, blank=True)  # {table key: {"fields": [keys], "layout": "list"|"gallery", "files": bool}}
    target = models.CharField(max_length=10, default=ZIP, choices=TARGET_CHOICES)
    target_config = models.JSONField(default=dict, blank=True)  # url, path, user, tls … (never the password)
    target_secret = models.TextField(blank=True, default="")  # the target's password, vault-encrypted (publish/targets.py)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    published_choice = models.JSONField(null=True, blank=True)  # the choice that was confirmed last; a new one asks again
    manifest = models.JSONField(default=dict, blank=True)  # {"target": fingerprint, "files": {path: sha256}} of what is at the target now
    status = models.CharField(max_length=10, default=IDLE)
    progress = models.CharField(max_length=200, blank=True, default="")
    error = models.TextField(blank=True, default="")
    busy_until = models.DateTimeField(null=True, blank=True)
    # Publishing by itself (publish/auto.py): after changes, once nothing changed for `auto_quiet` minutes,
    # or every day at `auto_time`. Never more than was confirmed (`published_choice`).
    auto = models.CharField(max_length=10, default=OFF, choices=AUTO_CHOICES)
    auto_quiet = models.PositiveSmallIntegerField(default=15)
    auto_time = models.TimeField(default=time(3, 0))
    auto_marker = models.BigIntegerField(default=0)  # the last change (sync.Journal id) the website already holds
    auto_day = models.DateField(null=True, blank=True)  # the day of the last daily publish
    auto_retry_at = models.DateTimeField(null=True, blank=True)  # after a failed automatic publish, not before this
    auto_note = models.CharField(max_length=300, blank=True, default="")  # why it waits, shown on its page

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def tables(self):
        return {key: spec for key, spec in (self.choice or {}).items() if isinstance(spec, dict)}

    def fields_of(self, table_key):
        return set((self.tables().get(table_key) or {}).get("fields") or [])


class HostedSite(models.Model):
    """A site another computer publishes to this server with a token (publish/hosting.py), shown under
    /p/<slug>/ like the server's own publications. Only the token's hash is kept."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=40, unique=True)
    token_hash = models.CharField(max_length=64, unique=True)
    quota_mb = models.PositiveIntegerField(default=500)
    indexable = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def quota(self):
        return self.quota_mb * 1024 * 1024
