from django.apps import AppConfig


class HomeConfig(AppConfig):
    name = "home"
    verbose_name = "Hauptseite"

    def ready(self):
        from . import checks  # noqa: F401 - registers the deploy check
