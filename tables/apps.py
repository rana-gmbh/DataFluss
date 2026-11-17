import logging

from django.apps import AppConfig


class TablesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tables"

    def ready(self):
        from core import modules

        from . import services
        from .models import Record
        from .sync_scope import TablesScope

        def nightly():
            from datetime import timedelta

            from django.utils import timezone

            from . import links
            from .models import Attachment

            # Records leaving the bin for good take their files along, which the database alone would leave on disk.
            gone = timezone.now() - timedelta(days=Record.KEEP_DAYS)
            for attachment in Attachment.objects.filter(record__deleted_at__lt=gone):
                links.remove_file(attachment)
            Record.empty_old()
            links.cleanup()
            try:  # TMDB's terms: no title's data older than six months (tables/tmdb.py refresh_due)
                from . import tmdb

                tmdb.refresh_due()
            except Exception:  # noqa: BLE001 - never stands in the way of the bins and the backup
                logging.getLogger(__name__).exception("TMDB refresh failed")

        # Files of records are served by their own view, which checks the table's right (views_records.record_file).
        modules.contribute(sync=TablesScope(), nightly=nightly, search=services.search, private_media=("tables/",))
