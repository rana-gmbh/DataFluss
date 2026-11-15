from django.core.management.base import BaseCommand

from vault import crypto


class Command(BaseCommand):
    help = "Creates the vault key file if none exists. Keep a separate copy in a safe place."

    def handle(self, *args, **options):
        created = crypto.ensure_key_file()
        self.stdout.write("Schlüssel erzeugt." if created else "Schlüssel ist bereits vorhanden.")
