from django.apps import AppConfig


class KontenConfig(AppConfig):
    name = "konten"
    verbose_name = "Konten"

    def ready(self):
        from . import checks  # noqa: F401 - registers the deploy checks
