from django.apps import AppConfig
from django.db.models.signals import post_save, pre_save


def _role_saved(sender, instance, **kwargs):
    """A role's rights changed: its accounts' computers get their records again (sync/server.py resend_ruled)."""
    from .models import Device
    from .server import resend_ruled

    resend_ruled(Device.objects.filter(user__role=instance.key, revoked_at=None))


def _remember_role(sender, instance, update_fields=None, **kwargs):
    if update_fields is not None and "role" not in update_fields:
        instance._role_before = None  # a save that does not touch the role (the last sign-in, say)
        return
    instance._role_before = sender.objects.filter(pk=instance.pk).values_list("role", flat=True).first() if instance.pk else None


def _user_saved(sender, instance, created, **kwargs):
    if not created and getattr(instance, "_role_before", None) not in (None, instance.role):
        from .models import Device
        from .server import resend_ruled

        resend_ruled(Device.objects.filter(user=instance, revoked_at=None))


class SyncConfig(AppConfig):
    name = "sync"
    verbose_name = "Synchronisation"

    def ready(self):
        from django.contrib.auth import get_user_model

        from core.models import UserRole

        post_save.connect(_role_saved, sender=UserRole, dispatch_uid="sync.role_saved")
        pre_save.connect(_remember_role, sender=get_user_model(), dispatch_uid="sync.remember_role")
        post_save.connect(_user_saved, sender=get_user_model(), dispatch_uid="sync.user_saved")
