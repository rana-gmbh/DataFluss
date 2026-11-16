from django.apps import AppConfig


class TablesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tables"

    def ready(self):
        from core import modules

        from .models import Record
        from .sync_scope import TablesScope

        modules.contribute(sync=TablesScope(), nightly=Record.empty_old)
