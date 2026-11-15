from django.core.management.base import BaseCommand, CommandError

from archive import nightly


class Command(BaseCommand):
    help = "Creates the automatic backup, checks it and copies it off this server."

    def add_arguments(self, parser):
        parser.add_argument("--if-due", action="store_true", help="Do nothing unless tonight's backup is still missing")

    def handle(self, *args, **options):
        nightly.empty_bins()  # Promised on the bin page, whether backups are set up or not.
        if options["if_due"] and not nightly.is_due():
            return
        result = nightly.run()
        if not result:
            self.stdout.write("Es läuft bereits ein Sicherungsvorgang.")
        elif result["state"] != "ok" or result["offsite"] == "failed":
            raise CommandError(f"Automatische Sicherung: {result.get('problem', '')}")
        else:
            self.stdout.write(self.style.SUCCESS(f"Sicherung {result['name']} erstellt und geprüft."))
