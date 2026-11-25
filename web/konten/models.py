"""Accounts of the template site: an e-mail address and a name, no password (sign-in by link, konten/tokens.py).
Reviewers (is_staff) also confirm a code from an authenticator app before they may review."""
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email, name="", **extra):
        user = self.model(email=self.normalize_email(email).lower(), name=name, **extra)
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, name="", password=None, **extra):
        return self.create_user(email, name, is_staff=True, is_superuser=True, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    name = models.CharField("Anzeigename", max_length=60, blank=True, default="")  # shown with templates and reviews
    is_staff = models.BooleanField("Prüfer", default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)
    totp_secret = models.CharField(max_length=64, blank=True, default="")  # set once the reviewer confirmed a code

    objects = UserManager()
    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"

    def __str__(self):
        return self.name or self.email

    @property
    def shown_name(self):
        return self.name or self.email.split("@")[0]


class Attempt(models.Model):
    """One counted action (a sign-in mail, an upload, a review …) for rate limits. The key is a keyed hash of the
    address or account, never the address itself; rows older than a day are removed (konten/limits.py)."""

    kind = models.CharField(max_length=20)
    key = models.CharField(max_length=64)
    created = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["kind", "key", "created"])]
