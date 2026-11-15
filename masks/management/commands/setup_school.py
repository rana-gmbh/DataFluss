from django.core.management.base import BaseCommand

from core.models import SchoolSettings
from masks.defaults import setup
from vault import crypto


class Command(BaseCommand):
    help = "Prepares a new installation. Safe to run repeatedly."

    def handle(self, *args, **options):
        # A missing key must stop the start. A new key would make every stored password unreadable.
        crypto.check_key()
        setup()
        SchoolSettings.load()
        SchoolSettings.objects.filter(key_check="").update(key_check=crypto.derived_password("key-check"))
        self.stdout.write("Grundeinrichtung abgeschlossen.")
