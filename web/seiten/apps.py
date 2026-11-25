from django.apps import AppConfig


class SeitenConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "seiten"
    verbose_name = "Websites"

    def ready(self):
        from . import checks  # noqa: F401 - registers the deploy checks
