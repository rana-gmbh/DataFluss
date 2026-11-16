from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _ensure_roles(sender, using="default", **kwargs):
    from .permissions import ensure_roles

    ensure_roles(using)


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = "core"

    def ready(self):
        # After every migrate: the built-in roles get their rows (core/permissions.py ensure_roles).
        post_migrate.connect(_ensure_roles, dispatch_uid="core.ensure_roles")
