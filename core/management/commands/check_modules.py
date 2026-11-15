"""Stops the server before it starts if its database holds data of a module this image lacks.

Run by deploy/start.sh before the migrations, so nothing is changed and no data hides unseen.
"""
from django.core.management.base import BaseCommand, CommandError

from core import modules


class Command(BaseCommand):
    help = "Refuses a database with data of a module that is not loaded."

    def handle(self, *args, **options):
        missing = modules.missing_with_data()
        if missing:
            raise CommandError(modules.missing_message(missing))
        self.stdout.write(f"Module: {modules.titles(modules.manifest())}")
