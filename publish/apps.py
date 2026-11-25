from django.apps import AppConfig


class PublishConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "publish"

    def ready(self):
        from core import modules

        # Built sites on a server are served by publish/views.py site_file, never as plain media.
        modules.contribute(private_media=("published/",))
